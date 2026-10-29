import json
import subprocess
from pathlib import Path

import pytest

from pedigree.attestation import SCHEMA, Attestation, sign, to_json
from pedigree.cli import main
from pedigree.gitio import notes_add

KEY_ENV = "PEDIGREE_HMAC_KEY"
SECRET = "top-secret"
CLAUDE = "Co-Authored-By: Claude <noreply@anthropic.com>"
POLICY_TOML = """\
[[rule]]
name = "agent-src-needs-attestation"
paths = ["src/**"]
applies_to = "agent"
require = ["attestation"]
"""
SUMMARY = "3 commits, 2 attested, 1 errors, 1 warnings"


def run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def commit(repo: Path, name: str, lines: int, subject: str, trailer: str = "") -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n" * lines)
    run_git(repo, "add", "-A")
    args = ["commit", "-q", "-m", subject]
    if trailer:
        args += ["-m", trailer]
    run_git(repo, *args)
    return run_git(repo, "rev-parse", "HEAD")


def attest(repo: Path, sha: str, tool: str, signed: bool) -> None:
    att = Attestation(SCHEMA, sha, "agent", tool, "m", "", "", "2026-01-01T00:00:00Z")
    notes_add(repo, sha, to_json(sign(att, SECRET.encode()) if signed else att), True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(KEY_ENV, SECRET)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    path = tmp_path / "repo"
    path.mkdir()
    run_git(path, "init", "-q", "-b", "main")
    run_git(path, "config", "user.name", "Ada Tester")
    run_git(path, "config", "user.email", "ada@example.com")
    run_git(path, "config", "commit.gpgsign", "false")
    commit(path, "README.md", 1, "initial readme")  # human, 1 line
    commit(path, "docs/a.md", 2, "add docs")  # human, 2 lines
    attest(path, commit(path, "src/a.py", 3, "agent one"), "claude-code", signed=True)
    attest(path, commit(path, "src/b.py", 4, "agent two"), "claude-code", signed=False)
    attest(path, commit(path, "src/c.py", 2, "agent three"), "codex", signed=True)
    commit(path, "src/d.py", 5, "trailer agent", CLAUDE)  # unattested, classified by trailer
    (path / "pedigree.toml").write_text(POLICY_TOML, encoding="utf-8")
    return path


def test_report_json_numbers_and_shape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sha = run_git(repo, "rev-parse", "HEAD")

    code = main(["--repo", str(repo), "report", "--format", "json", "HEAD~5..HEAD"])

    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data == {
        "version": 1,
        "total_commits": 5,
        "attested": 3,
        "attested_percent": 60.0,
        "signed_valid": 2,
        "human_commits": 1,
        "agent_commits": 4,
        "ai_changed_lines": 14,
        "total_changed_lines": 16,
        "ai_share_percent": 87.5,
        "tools": {"claude-code": 2, "codex": 1},
        "unattested_agent_commits": [{"sha": sha[:7], "subject": "trailer agent"}],
    }


def test_report_whole_history_counts(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--repo", str(repo), "report", "--format", "json", "HEAD"])

    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["total_commits"] == 6
    assert data["attested"] == 3
    assert data["attested_percent"] == 50.0
    assert data["human_commits"] == 2
    assert data["agent_commits"] == 4
    assert data["ai_changed_lines"] == 14
    assert data["total_changed_lines"] == 17
    assert data["ai_share_percent"] == 82.4
    assert "findings" not in data


def test_report_text_table(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sha = run_git(repo, "rev-parse", "HEAD")

    code = main(["--repo", str(repo), "report", "HEAD"])

    lines = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert code == 0
    assert lines[0] == ["Pedigree", "report"]
    assert ["attested", "3", "(50.0%)"] in lines
    assert ["signed", "(valid)", "2"] in lines
    assert ["AI", "share", "of", "lines", "82.4%", "(14", "of", "17)"] in lines
    assert ["claude-code", "2"] in lines
    assert ["codex", "1"] in lines
    assert [sha[:7], "trailer", "agent"] in lines


def test_report_with_policy_adds_findings_counts(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--repo", str(repo), "report", "--format", "json", "--policy", str(repo / "pedigree.toml"), "HEAD"])

    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["findings"] == {"errors": 1, "warnings": 1}


def test_report_bad_range_exits_3(repo: Path) -> None:
    assert main(["--repo", str(repo), "report", "no-such-ref..HEAD"]) == 3


def test_report_missing_policy_file_exits_3(repo: Path, tmp_path: Path) -> None:
    assert main(["--repo", str(repo), "report", "--policy", str(tmp_path / "nope.toml")]) == 3


def test_report_non_repo_exits_3(tmp_path: Path) -> None:
    assert main(["--repo", str(tmp_path), "report"]) == 3


def test_verify_json_has_stable_shape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--repo", str(repo), "verify", "--format", "json", "HEAD~3..HEAD"])

    data = json.loads(capsys.readouterr().out)
    assert code == 1
    assert list(data) == ["version", "commits", "summary"]
    assert data["summary"] == {"commits": 3, "attested": 2, "errors": 1, "warnings": 1}
    assert [c["author_type"] for c in data["commits"]] == ["agent", "agent", "agent"]
    assert [c["signature"] for c in data["commits"]] == ["none", "valid", "unsigned"]


def test_verify_sarif_matches_findings(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sha = run_git(repo, "rev-parse", "HEAD")
    unsigned_sha = run_git(repo, "rev-parse", "HEAD~2")

    code = main(["--repo", str(repo), "verify", "--format", "sarif", "HEAD~3..HEAD"])

    sarif = json.loads(capsys.readouterr().out)
    assert code == 1
    assert sarif["version"] == "2.1.0"
    (run,) = sarif["runs"]
    driver = run["tool"]["driver"]
    assert driver["name"] == "Pedigree"
    assert driver["version"]
    assert {r["id"] for r in driver["rules"]} == {"agent-src-needs-attestation", "attestation_signature"}
    results = {r["ruleId"]: r for r in run["results"]}
    assert len(run["results"]) == 2
    assert results["agent-src-needs-attestation"]["level"] == "error"
    assert results["attestation_signature"]["level"] == "warning"
    error_loc = results["agent-src-needs-attestation"]["locations"][0]
    assert error_loc["physicalLocation"]["artifactLocation"]["uri"] == "src/d.py"
    assert error_loc["logicalLocations"][0]["name"] == sha
    warn_loc = results["attestation_signature"]["locations"][0]
    assert warn_loc["physicalLocation"]["artifactLocation"]["uri"] == "src/b.py"
    assert warn_loc["logicalLocations"][0]["name"] == unsigned_sha


def test_verify_output_writes_file_and_summary_to_stderr(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "pedigree.sarif"

    code = main(["--repo", str(repo), "verify", "--format", "sarif", "--output", str(target), "HEAD~3..HEAD"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.strip() == SUMMARY
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == "2.1.0"


def test_verify_output_text_file(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "verify.txt"

    code = main(["--repo", str(repo), "verify", "--output", str(target), "HEAD~3..HEAD"])

    assert code == 1
    assert target.read_text(encoding="utf-8").strip().endswith(SUMMARY)
    assert capsys.readouterr().err.strip() == SUMMARY


def test_verify_output_unwritable_exits_3(repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "missing-dir" / "out.json"

    assert main(["--repo", str(repo), "verify", "--format", "json", "--output", str(target)]) == 3
