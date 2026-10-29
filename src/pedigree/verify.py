"""Verify engine: classify commit authors and evaluate a policy against a commit range."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pedigree.attestation import Attestation, AttestationError, bound_to, from_json, verify_signature
from pedigree.gitio import Commit, branch_name, notes_show, read_commits
from pedigree.policy import Policy, Rule, glob_match

AuthorType = Literal["human", "agent"]
SignatureState = Literal["valid", "invalid", "unsigned", "no_key", "none"]
Severity = Literal["error", "warning"]

AGENT_TRAILER = "pedigree-author-type"
CO_AUTHOR_TRAILER = "co-authored-by"
AGENT_CO_AUTHOR_MARKERS = ("claude", "copilot", "gpt", "gemini", "bot")
AGENT_EMAIL_SUFFIX = "[bot]@users.noreply.github.com"
REVIEW_TRAILERS = ("reviewed-by", "human-reviewed")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    message: str


@dataclass(frozen=True)
class Verdict:
    commit: Commit
    author_type: AuthorType
    attested: bool
    signature_state: SignatureState
    findings: tuple[Finding, ...]
    tool: str = ""


def classify_author(commit: Commit, attestation: Attestation | None) -> AuthorType:
    """Attestation wins; otherwise fall back to trailer and e-mail heuristics."""
    if attestation is not None:
        return "agent" if attestation.author_type == "agent" else "human"
    declared = commit.trailers.get(AGENT_TRAILER, [])
    if any(value.strip().lower() == "agent" for value in declared):
        return "agent"
    for value in commit.trailers.get(CO_AUTHOR_TRAILER, []):
        lowered = value.lower()
        if any(marker in lowered for marker in AGENT_CO_AUTHOR_MARKERS):
            return "agent"
    if commit.author_email.lower().endswith(AGENT_EMAIL_SUFFIX.lower()):
        return "agent"
    return "human"


def _error(rule: str, message: str) -> Finding:
    return Finding(rule, "error", message)


def _signature_state(att: Attestation | None, bound: bool, key: bytes | None) -> SignatureState:
    if att is None:
        return "none"
    if not bound:
        return "invalid"
    if not att.signature:
        return "unsigned"
    if key is None:
        return "no_key"
    return "valid" if verify_signature(att, key) else "invalid"


def _signature_findings(state: SignatureState, bound: bool) -> list[Finding]:
    if state == "invalid" and bound:
        return [_error("attestation_signature", "attestation signature does not verify with the configured key")]
    if state == "unsigned":
        return [Finding("attestation_signature", "warning", "attestation is unsigned")]
    if state == "no_key":
        return [Finding("attestation_signature", "warning", "attestation is signed but no key is configured")]
    return []


def _branch_findings(policy: Policy, branch: str | None, has_note: bool, state: SignatureState) -> list[Finding]:
    if branch is None or not any(glob_match(pattern, branch) for pattern in policy.require_attestation_branches):
        return []
    if not has_note:
        return [_error("attestation_required", f"branch '{branch}' requires an attestation on every commit")]
    if policy.signed_required and state != "valid":
        return [
            _error(
                "attestation_required",
                f"branch '{branch}' requires a validly signed attestation (signature state: {state})",
            )
        ]
    return []


def _first_match(rule: Rule, commit: Commit) -> str | None:
    for path in commit.files:
        if any(glob_match(pattern, path) for pattern in rule.paths):
            return path
    return None


def _rule_applies(rule: Rule, author_type: AuthorType, commit: Commit) -> bool:
    if rule.applies_to not in ("any", author_type):
        return False
    return not rule.paths or _first_match(rule, commit) is not None


def _requirement_findings(rule: Rule, commit: Commit, att: Attestation | None, state: SignatureState) -> list[Finding]:
    findings: list[Finding] = []
    for requirement in rule.require:
        if requirement == "attestation" and att is None:
            findings.append(_error(rule.name, "commit has no valid attestation"))
        elif requirement == "signed_attestation" and state != "valid":
            findings.append(_error(rule.name, f"attestation is not validly signed (signature state: {state})"))
        elif requirement == "human_review_trailer" and not any(t in commit.trailers for t in REVIEW_TRAILERS):
            findings.append(_error(rule.name, "commit lacks a Reviewed-by or Human-reviewed trailer"))
        elif requirement == "contract_id" and (att is None or not att.contract_id):
            findings.append(_error(rule.name, "attestation carries no contract_id"))
    return findings


def _rule_findings(
    rule: Rule, commit: Commit, author_type: AuthorType, att: Attestation | None, state: SignatureState
) -> list[Finding]:
    findings = _requirement_findings(rule, commit, att, state)
    changed = commit.insertions + commit.deletions
    if rule.max_diff_lines is not None and changed > rule.max_diff_lines:
        findings.append(_error(rule.name, f"diff changes {changed} lines, limit is {rule.max_diff_lines}"))
    if rule.forbid:
        path = _first_match(rule, commit) or (commit.files[0] if commit.files else "(no files)")
        findings.append(_error(rule.name, f"{author_type} commits may not change {path} (forbidden by rule)"))
    return findings


def _verify_commit(repo: Path, commit: Commit, policy: Policy, key: bytes | None, branch: str | None) -> Verdict:
    findings: list[Finding] = []
    note = notes_show(repo, commit.sha)
    att: Attestation | None = None
    if note is not None:
        try:
            att = from_json(note)
        except AttestationError as exc:
            findings.append(_error("attestation_malformed", str(exc)))
    bound = att is not None and bound_to(att, commit.sha)
    if att is not None and not bound:
        findings.append(_error("attestation_binding", f"attestation is bound to {att.commit}, not {commit.sha}"))
    state = _signature_state(att, bound, key)
    findings.extend(_signature_findings(state, bound))
    trusted = att if bound else None
    author_type = classify_author(commit, trusted)
    findings.extend(_branch_findings(policy, branch, note is not None, state))
    for rule in policy.rules:
        if _rule_applies(rule, author_type, commit):
            findings.extend(_rule_findings(rule, commit, author_type, trusted, state))
    tool = trusted.tool if trusted is not None else ""
    return Verdict(commit, author_type, trusted is not None, state, tuple(findings), tool)


def verify_range(repo: Path, rev_range: str | None, policy: Policy, key: bytes | None) -> list[Verdict]:
    """Verify every commit in ``rev_range`` (``None`` = HEAD only); raises GitError on git problems."""
    commits = read_commits(repo, rev_range)
    branch = branch_name(repo)
    return [_verify_commit(repo, commit, policy, key, branch) for commit in commits]
