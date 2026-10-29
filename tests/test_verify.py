import json
import subprocess
from pathlib import Path

import pytest

from pedigree.attestation import MAX_NOTE_BYTES, SCHEMA, Attestation, sign, to_json
from pedigree.gitio import Commit, notes_add
from pedigree.policy import Policy, Rule
from pedigree.verify import Verdict, classify_author, verify_range

KEY_ENV = "PEDIGREE_HMAC_KEY"
SECRET = b"top-secret"
CLAUDE = "Co-Authored-By: Claude <noreply@anthropic.com>"


def run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    path = tmp_path / "repo"
    path.mkdir()
    run_git(path, "init", "-q", "-b", "main")
    run_git(path, "config", "user.name", "Ada Tester")
    run_git(path, "config", "user.email", "ada@example.com")
    run_git(path, "config", "commit.gpgsign", "false")
    commit(path, {"README.md": "hello\n"}, "initial")
    return path


def commit(
    repo: Path, files: dict[str, str], subject: str = "change", trailers: tuple[str, ...] = (), author: str = ""
) -> str:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    run_git(repo, "add", "-A")
    args = ["commit", "-q", "-m", subject]
    if trailers:
        args += ["-m", "\n".join(trailers)]
    if author:
        args += ["--author", author]
    run_git(repo, *args)
    return run_git(repo, "rev-parse", "HEAD")


def attest(
    repo: Path,
    sha: str,
    author_type: str = "agent",
    contract_id: str = "",
    key: bytes | None = None,
    bound_to_sha: str | None = None,
) -> None:
    att = Attestation(
        schema=SCHEMA,
        commit=bound_to_sha or sha,
        author_type=author_type,
        tool="t",
        model="m",
        contract_id=contract_id,
        evidence_sha256="",
        created_at="2026-01-01T00:00:00Z",
    )
    if key is not None:
        att = sign(att, key)
    notes_add(repo, sha, to_json(att), True)


def head_verdict(repo: Path, policy: Policy | None = None, key: bytes | None = None) -> Verdict:
    return verify_range(repo, None, policy or Policy(), key)[0]


def rules_hit(verdict: Verdict) -> list[str]:
    return [f.rule for f in verdict.findings]


def agent_rule(
    paths: tuple[str, ...] = ("src/**",),
    applies_to: str = "agent",
    require: tuple[str, ...] = (),
    max_diff_lines: int | None = None,
    forbid: bool = False,
    name: str = "r",
) -> Policy:
    rule = Rule(
        name=name,
        paths=paths,
        applies_to=applies_to,
        require=require,
        max_diff_lines=max_diff_lines,
        forbid=forbid,
    )
    return Policy(rules=(rule,))


def make_commit(
    trailers: dict[str, list[str]] | None = None, author_email: str = "ada@example.com"
) -> Commit:
    return Commit(
        sha="a" * 40,
        author_name="Ada",
        author_email=author_email,
        author_date="2026-01-01T00:00:00+00:00",
        subject="s",
        trailers=trailers or {},
        insertions=0,
        deletions=0,
        files=(),
    )


# --- classification -------------------------------------------------------------------------------


def test_co_authored_by_claude_is_agent(repo: Path) -> None:
    commit(repo, {"a.txt": "x\n"}, trailers=(CLAUDE,))

    assert head_verdict(repo).author_type == "agent"


def test_plain_commit_is_human(repo: Path) -> None:
    commit(repo, {"a.txt": "x\n"})

    assert head_verdict(repo).author_type == "human"


@pytest.mark.parametrize("who", ["Copilot <c@x.io>", "GPT-5 <g@x.io>", "Gemini <g@x.io>", "dependabot <d@x.io>"])
def test_co_author_markers_classify_as_agent(who: str) -> None:
    assert classify_author(make_commit({"co-authored-by": [who]}), None) == "agent"


def test_human_co_author_stays_human() -> None:
    c = make_commit({"co-authored-by": ["Grace Hopper <grace@example.com>"]})

    assert classify_author(c, None) == "human"


def test_author_type_trailer_marks_agent() -> None:
    assert classify_author(make_commit({"pedigree-author-type": ["agent"]}), None) == "agent"


def test_bot_noreply_email_marks_agent() -> None:
    c = make_commit(author_email="12345+renovate[bot]@users.noreply.github.com")

    assert classify_author(c, None) == "agent"


def test_attestation_beats_trailers(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"}, trailers=(CLAUDE,))
    attest(repo, sha, author_type="human")

    verdict = head_verdict(repo)

    assert verdict.author_type == "human"
    assert verdict.attested is True


def test_attested_agent_without_trailers_is_agent(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, author_type="agent")

    assert head_verdict(repo).author_type == "agent"


# --- attestation integrity ------------------------------------------------------------------------


def test_malformed_note_is_error(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    notes_add(repo, sha, "{not json", True)

    verdict = head_verdict(repo)

    assert ("attestation_malformed", "error") in [(f.rule, f.severity) for f in verdict.findings]
    assert verdict.attested is False


def test_tampered_binding_is_error(repo: Path) -> None:
    first = commit(repo, {"a.txt": "x\n"})
    second = commit(repo, {"b.txt": "y\n"})
    attest(repo, second, bound_to_sha=first, key=SECRET)

    verdict = head_verdict(repo, key=SECRET)

    assert "attestation_binding" in rules_hit(verdict)
    assert verdict.attested is False
    assert verdict.signature_state != "valid"


def test_tampered_binding_does_not_satisfy_requirements(repo: Path) -> None:
    first = commit(repo, {"src/a.py": "x\n"}, trailers=(CLAUDE,))
    second = commit(repo, {"src/b.py": "y\n"}, trailers=(CLAUDE,))
    attest(repo, second, bound_to_sha=first, key=SECRET)

    verdict = head_verdict(repo, agent_rule(require=("signed_attestation", "attestation")), key=SECRET)

    assert rules_hit(verdict).count("r") == 2


def test_valid_signature(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, key=SECRET)

    verdict = head_verdict(repo, key=SECRET)

    assert verdict.signature_state == "valid"
    assert verdict.findings == ()


def test_invalid_signature_with_wrong_key(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, key=SECRET)

    verdict = head_verdict(repo, key=b"wrong")

    assert verdict.signature_state == "invalid"
    assert [(f.rule, f.severity) for f in verdict.findings] == [("attestation_signature", "error")]


def test_signed_without_key_is_no_key_warning(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, key=SECRET)

    verdict = head_verdict(repo, key=None)

    assert verdict.signature_state == "no_key"
    assert [(f.rule, f.severity) for f in verdict.findings] == [("attestation_signature", "warning")]


def test_unsigned_attestation_warns(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha)

    verdict = head_verdict(repo, key=SECRET)

    assert verdict.signature_state == "unsigned"
    assert [(f.rule, f.severity) for f in verdict.findings] == [("attestation_signature", "warning")]


def test_no_attestation_has_state_none_and_no_findings(repo: Path) -> None:
    verdict = head_verdict(repo)

    assert verdict.signature_state == "none"
    assert verdict.findings == ()


# --- branch-level requirement ---------------------------------------------------------------------


def test_branch_glob_requires_attestation(repo: Path) -> None:
    commit(repo, {"a.txt": "x\n"})

    verdict = head_verdict(repo, Policy(require_attestation_branches=("ma*",)))

    assert [(f.rule, f.severity) for f in verdict.findings] == [("attestation_required", "error")]


def test_branch_glob_satisfied_by_attestation(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, key=SECRET)

    assert head_verdict(repo, Policy(require_attestation_branches=("main",)), key=SECRET).findings == ()


def test_branch_glob_not_matching_current_branch_is_ignored(repo: Path) -> None:
    commit(repo, {"a.txt": "x\n"})

    assert head_verdict(repo, Policy(require_attestation_branches=("release/**",))).findings == ()


def test_branch_with_slash_matches_double_star(repo: Path) -> None:
    run_git(repo, "switch", "-q", "-c", "release/1.0")
    commit(repo, {"a.txt": "x\n"})

    verdict = head_verdict(repo, Policy(require_attestation_branches=("release/**",)))

    assert rules_hit(verdict) == ["attestation_required"]


@pytest.mark.parametrize("signed", [False, True])
def test_signed_required_escalates_weak_signatures(repo: Path, signed: bool) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, key=SECRET if signed else None)
    policy = Policy(require_attestation_branches=("main",), signed_required=True)

    verdict = head_verdict(repo, policy, key=None)

    assert "attestation_required" in [f.rule for f in verdict.findings if f.severity == "error"]


def test_signed_required_escalates_invalid_signature(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, key=SECRET)
    policy = Policy(require_attestation_branches=("main",), signed_required=True)

    assert "attestation_required" in rules_hit(head_verdict(repo, policy, key=b"wrong"))


def test_signed_required_passes_with_valid_signature(repo: Path) -> None:
    sha = commit(repo, {"a.txt": "x\n"})
    attest(repo, sha, key=SECRET)
    policy = Policy(require_attestation_branches=("main",), signed_required=True)

    assert head_verdict(repo, policy, key=SECRET).findings == ()


# --- policy rules ---------------------------------------------------------------------------------


def test_attestation_requirement_fires_and_passes(repo: Path) -> None:
    sha = commit(repo, {"src/a.py": "x\n"}, trailers=(CLAUDE,))
    policy = agent_rule(require=("attestation",))
    assert rules_hit(head_verdict(repo, policy)) == ["r"]

    attest(repo, sha)

    assert "r" not in rules_hit(head_verdict(repo, policy))


def test_signed_attestation_requirement(repo: Path) -> None:
    sha = commit(repo, {"src/a.py": "x\n"})
    policy = agent_rule(require=("signed_attestation",))
    attest(repo, sha)
    assert "r" in rules_hit(head_verdict(repo, policy, key=SECRET))

    attest(repo, sha, key=SECRET)

    assert "r" not in rules_hit(head_verdict(repo, policy, key=SECRET))
    assert "r" in rules_hit(head_verdict(repo, policy, key=b"wrong"))


@pytest.mark.parametrize("trailer", ["Reviewed-by: Grace <g@example.com>", "Human-reviewed: yes"])
def test_human_review_trailer_requirement(repo: Path, trailer: str) -> None:
    policy = agent_rule(require=("human_review_trailer",))
    commit(repo, {"src/a.py": "x\n"}, trailers=(CLAUDE,))
    assert rules_hit(head_verdict(repo, policy)) == ["r"]

    commit(repo, {"src/b.py": "y\n"}, trailers=(CLAUDE, trailer))

    assert head_verdict(repo, policy).findings == ()


def test_contract_id_requirement(repo: Path) -> None:
    sha = commit(repo, {"src/a.py": "x\n"})
    policy = agent_rule(require=("contract_id",))
    attest(repo, sha)
    assert "r" in rules_hit(head_verdict(repo, policy))

    attest(repo, sha, contract_id="pg-005")

    assert "r" not in rules_hit(head_verdict(repo, policy))


def test_contract_id_requirement_fails_without_attestation(repo: Path) -> None:
    commit(repo, {"src/a.py": "x\n"}, trailers=(CLAUDE,))

    assert rules_hit(head_verdict(repo, agent_rule(require=("contract_id",)))) == ["r"]


def test_max_diff_lines_exceeded_and_boundary(repo: Path) -> None:
    commit(repo, {"src/big.py": "line\n" * 11}, trailers=(CLAUDE,))

    assert rules_hit(head_verdict(repo, agent_rule(max_diff_lines=10))) == ["r"]
    assert head_verdict(repo, agent_rule(max_diff_lines=11)).findings == ()


def test_max_diff_lines_counts_insertions_and_deletions(repo: Path) -> None:
    commit(repo, {"src/f.py": "a\nb\nc\n"}, trailers=(CLAUDE,))
    commit(repo, {"src/f.py": "x\ny\nz\n"}, trailers=(CLAUDE,))

    assert rules_hit(head_verdict(repo, agent_rule(max_diff_lines=5))) == ["r"]
    assert head_verdict(repo, agent_rule(max_diff_lines=6)).findings == ()


def test_forbid_names_rule_author_type_and_first_path(repo: Path) -> None:
    commit(repo, {"docs/x.md": "d\n", ".github/ci.yml": "a\n", ".github/b.yml": "b\n"}, trailers=(CLAUDE,))
    policy = agent_rule(name="no-ci", paths=(".github/**",), forbid=True)

    (finding,) = head_verdict(repo, policy).findings

    assert finding.rule == "no-ci"
    assert finding.severity == "error"
    assert "agent" in finding.message
    assert ".github/b.yml" in finding.message


def test_rule_not_applied_to_other_author_type(repo: Path) -> None:
    commit(repo, {"src/a.py": "x\n"})

    assert head_verdict(repo, agent_rule(forbid=True)).findings == ()
    assert rules_hit(head_verdict(repo, agent_rule(applies_to="human", forbid=True))) == ["r"]
    assert rules_hit(head_verdict(repo, agent_rule(applies_to="any", forbid=True))) == ["r"]


def test_rule_not_applied_when_paths_do_not_match(repo: Path) -> None:
    commit(repo, {"docs/a.md": "x\n"}, trailers=(CLAUDE,))

    assert head_verdict(repo, agent_rule(forbid=True)).findings == ()


def test_empty_paths_match_every_commit(repo: Path) -> None:
    commit(repo, {"docs/a.md": "x\n"}, trailers=(CLAUDE,))

    assert rules_hit(head_verdict(repo, agent_rule(paths=(), forbid=True))) == ["r"]


def test_empty_policy_yields_no_findings_over_range(repo: Path) -> None:
    commit(repo, {"src/a.py": "x\n"}, trailers=(CLAUDE,))

    verdicts = verify_range(repo, "main", Policy(), None)

    assert len(verdicts) == 2
    assert all(v.findings == () for v in verdicts)


def test_range_limits_commits(repo: Path) -> None:
    commit(repo, {"a.txt": "1\n"})
    commit(repo, {"b.txt": "2\n"})

    assert len(verify_range(repo, "HEAD~2..HEAD", Policy(), None)) == 2


def test_oversized_note_is_malformed_without_crash(repo: Path) -> None:
    sha = commit(repo, {"src/a.py": "x\n"})
    padding = "a" * (MAX_NOTE_BYTES + 1)
    notes_add(repo, sha, json.dumps({"schema": SCHEMA, "padding": padding}), True)

    verdict = head_verdict(repo)

    malformed = [f for f in verdict.findings if f.rule == "attestation_malformed"]
    assert len(malformed) == 1
    assert malformed[0].severity == "error"
    assert "size" in malformed[0].message
    assert not verdict.attested


def test_normal_size_note_still_parses(repo: Path) -> None:
    sha = commit(repo, {"src/a.py": "x\n"})
    attest(repo, sha)

    verdict = head_verdict(repo)

    assert "attestation_malformed" not in rules_hit(verdict)
    assert verdict.attested
