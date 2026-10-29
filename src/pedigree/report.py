"""Rendering of verification verdicts: text, JSON, SARIF 2.1.0 and the provenance report."""

import json
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from pedigree import __version__
from pedigree.verify import Finding, Verdict

JSON_VERSION = 1
SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SRCROOT = "%SRCROOT%"
TOP_UNATTESTED = 5
UNKNOWN_TOOL = "(unknown)"


def _count_findings(verdicts: Sequence[Verdict], severity: str) -> int:
    return sum(1 for v in verdicts for f in v.findings if f.severity == severity)


def _summary(verdicts: Sequence[Verdict]) -> dict[str, int]:
    return {
        "commits": len(verdicts),
        "attested": sum(1 for v in verdicts if v.attested),
        "errors": _count_findings(verdicts, "error"),
        "warnings": _count_findings(verdicts, "warning"),
    }


def render_summary_line(verdicts: Sequence[Verdict]) -> str:
    s = _summary(verdicts)
    return f"{s['commits']} commits, {s['attested']} attested, {s['errors']} errors, {s['warnings']} warnings"


def render_verify_text(verdicts: Sequence[Verdict]) -> str:
    lines = [f"{v.commit.sha[:7]} {f.rule} {f.severity} {f.message}" for v in verdicts for f in v.findings]
    lines.append(render_summary_line(verdicts))
    return "\n".join(lines)


def render_verify_json(verdicts: Sequence[Verdict]) -> str:
    payload = {
        "version": JSON_VERSION,
        "commits": [
            {
                "sha": v.commit.sha,
                "author_type": v.author_type,
                "attested": v.attested,
                "signature": v.signature_state,
                "findings": [{"rule": f.rule, "severity": f.severity, "message": f.message} for f in v.findings],
            }
            for v in verdicts
        ],
        "summary": _summary(verdicts),
    }
    return json.dumps(payload, indent=2)


def _sarif_rules(verdicts: Sequence[Verdict]) -> list[dict[str, object]]:
    names: list[str] = []
    for v in verdicts:
        for f in v.findings:
            if f.rule not in names:
                names.append(f.rule)
    return [{"id": name, "name": name, "shortDescription": {"text": name}} for name in names]


def _sarif_result(verdict: Verdict, finding: Finding) -> dict[str, object]:
    sha = verdict.commit.sha
    location: dict[str, object] = {"logicalLocations": [{"name": sha, "fullyQualifiedName": sha, "kind": "commit"}]}
    if verdict.commit.files:
        location["physicalLocation"] = {
            "artifactLocation": {"uri": quote(verdict.commit.files[0]), "uriBaseId": SRCROOT}
        }
    return {
        "ruleId": finding.rule,
        "level": finding.severity,
        "message": {"text": finding.message},
        "locations": [location],
    }


def render_verify_sarif(verdicts: Sequence[Verdict], repo: Path) -> str:
    run: dict[str, object] = {
        "tool": {"driver": {"name": "Pedigree", "version": __version__, "rules": _sarif_rules(verdicts)}},
        "originalUriBaseIds": {SRCROOT: {"uri": repo.resolve().as_uri().rstrip("/") + "/"}},
        "results": [_sarif_result(v, f) for v in verdicts for f in v.findings],
    }
    payload = {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": [run]}
    return json.dumps(payload, indent=2)


def _percent(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def build_report(verdicts: Sequence[Verdict], include_findings: bool = False) -> dict[str, object]:
    total = len(verdicts)
    attested = sum(1 for v in verdicts if v.attested)
    agent = [v for v in verdicts if v.author_type == "agent"]
    ai_lines = sum(v.commit.insertions + v.commit.deletions for v in agent)
    all_lines = sum(v.commit.insertions + v.commit.deletions for v in verdicts)
    tools: dict[str, int] = {}
    for v in verdicts:
        if v.attested:
            name = v.tool or UNKNOWN_TOOL
            tools[name] = tools.get(name, 0) + 1
    unattested = [v for v in agent if not v.attested][:TOP_UNATTESTED]
    report: dict[str, object] = {
        "version": JSON_VERSION,
        "total_commits": total,
        "attested": attested,
        "attested_percent": _percent(attested, total),
        "signed_valid": sum(1 for v in verdicts if v.signature_state == "valid"),
        "human_commits": total - len(agent),
        "agent_commits": len(agent),
        "ai_changed_lines": ai_lines,
        "total_changed_lines": all_lines,
        "ai_share_percent": _percent(ai_lines, all_lines),
        "tools": dict(sorted(tools.items())),
        "unattested_agent_commits": [{"sha": v.commit.sha[:7], "subject": v.commit.subject} for v in unattested],
    }
    if include_findings:
        errors = _count_findings(verdicts, "error")
        report["findings"] = {"errors": errors, "warnings": _count_findings(verdicts, "warning")}
    return report


def _report_text(report: dict[str, object]) -> str:
    rows = [
        ("commits", f"{report['total_commits']}"),
        ("attested", f"{report['attested']} ({report['attested_percent']}%)"),
        ("signed (valid)", f"{report['signed_valid']}"),
        ("human commits", f"{report['human_commits']}"),
        ("agent commits", f"{report['agent_commits']}"),
        (
            "AI share of lines",
            f"{report['ai_share_percent']}% ({report['ai_changed_lines']} of {report['total_changed_lines']})",
        ),
    ]
    findings = report.get("findings")
    if isinstance(findings, dict):
        rows.append(("findings", f"{findings['errors']} errors, {findings['warnings']} warnings"))
    width = max(len(label) for label, _ in rows)
    lines = ["Pedigree report", *(f"{label.ljust(width)}  {value}" for label, value in rows)]
    tools = report["tools"]
    if isinstance(tools, dict) and tools:
        lines.append("tools:")
        lines.extend(f"  {name}  {count}" for name, count in tools.items())
    unattested = report["unattested_agent_commits"]
    if isinstance(unattested, list) and unattested:
        lines.append("top unattested agent commits:")
        lines.extend(f"  {entry['sha']} {entry['subject']}" for entry in unattested)
    return "\n".join(lines)


def render_report(verdicts: Sequence[Verdict], fmt: str = "text", include_findings: bool = False) -> str:
    """Render the provenance report as ``text`` (default) or ``json``."""
    report = build_report(verdicts, include_findings)
    return json.dumps(report, indent=2) if fmt == "json" else _report_text(report)
