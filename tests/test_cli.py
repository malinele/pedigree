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
    commit(path, "README.md", "hello\n")
    return path


def commit(repo: Path, name: str, content: str, trailer: str = "") -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    run_git(repo, "add", "-A")
    args = ["commit", "-q", "-m", "change"]
    if trailer:
        args += ["-m", trailer]
    run_git(repo, *args)
    return run_git(repo, "rev-parse", "HEAD")


def attest(repo: Path, sha: str, key: str = "") -> None:
    att = Attestation(SCHEMA, sha, "agent", "t", "m", "", "", "2026-01-01T00:00:00Z")
    notes_add(repo, sha, to_json(sign(att, key.encode()) if key else att), True)


def write_policy(repo: Path, text: str = POLICY_TOML) -> Path:
    path = repo / "pedigree.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_unknown_flag_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--no-such-flag"])

    assert excinfo.value.code == 2


def test_missing_subcommand_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2


def test_verify_empty_policy_exits_0(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_policy(repo, "")

    code = main(["--repo", str(repo), "verify"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "1 commits, 0 attested, 0 errors, 0 warnings"


def test_verify_error_finding_exits_1_with_text_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sha = commit(repo, "src/a.py", "x\n", CLAUDE)
    write_policy(repo)

    code = main(["--repo", str(repo), "verify"])

    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert lines[0].startswith(f"{sha[:7]} agent-src-needs-attestation error ")
    assert lines[-1] == "1 commits, 0 attested, 1 errors, 0 warnings"


def test_verify_warnings_alone_exit_0(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    attest(repo, commit(repo, "a.txt", "x\n"))
    write_policy(repo, "")

    code = main(["--repo", str(repo), "verify"])

    out = capsys.readouterr().out
    assert code == 0
    assert "attestation_signature warning" in out
    assert out.strip().splitlines()[-1] == "1 commits, 1 attested, 0 errors, 1 warnings"


def test_verify_missing_policy_exits_3_suggesting_policy_init(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--repo", str(repo), "verify"])

    assert code == 3
    assert "policy-init" in capsys.readouterr().err


def test_verify_invalid_policy_exits_3(repo: Path) -> None:
    write_policy(repo, "[policy]\nbogus = 1\n")

    assert main(["--repo", str(repo), "verify"]) == 3


def test_verify_bad_range_exits_3(repo: Path) -> None:
    write_policy(repo, "")

    assert main(["--repo", str(repo), "verify", "no-such-ref..HEAD"]) == 3


def test_verify_non_repo_exits_3(tmp_path: Path) -> None:
    (tmp_path / "pedigree.toml").write_text("", encoding="utf-8")

    assert main(["--repo", str(tmp_path), "verify"]) == 3


def test_verify_explicit_policy_path(repo: Path, tmp_path: Path) -> None:
    commit(repo, "src/a.py", "x\n", CLAUDE)
    elsewhere = tmp_path / "other.toml"
    elsewhere.write_text(POLICY_TOML, encoding="utf-8")

    assert main(["--repo", str(repo), "verify", "--policy", str(elsewhere)]) == 1


def test_verify_json_shape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sha = commit(repo, "src/a.py", "x\n", CLAUDE)
    write_policy(repo)

    code = main(["--repo", str(repo), "verify", "--format", "json"])

    data = json.loads(capsys.readouterr().out)
    assert code == 1
    assert data["version"] == 1
    assert data["summary"] == {"commits": 1, "attested": 0, "errors": 1, "warnings": 0}
    (entry,) = data["commits"]
    assert entry["sha"] == sha
    assert entry["author_type"] == "agent"
    assert entry["attested"] is False
    assert entry["signature"] == "none"
    assert entry["findings"][0]["rule"] == "agent-src-needs-attestation"
    assert set(entry["findings"][0]) == {"rule", "severity", "message"}


def test_verify_uses_key_from_environment(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attest(repo, commit(repo, "a.txt", "x\n"), key=SECRET)
    write_policy(repo, "")

    monkeypatch.setenv(KEY_ENV, "wrong")
    assert main(["--repo", str(repo), "verify"]) == 1

    monkeypatch.setenv(KEY_ENV, SECRET)
    assert main(["--repo", str(repo), "verify"]) == 0
