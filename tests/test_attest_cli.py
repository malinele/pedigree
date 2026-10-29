import os
import subprocess
from pathlib import Path

import pytest

from pedigree.attestation import from_json, verify_signature
from pedigree.cli import main
from pedigree.gitio import notes_show

KEY_ENV = "PEDIGREE_HMAC_KEY"


def git(repo: Path, *args: str) -> str:
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": os.environ["PATH"],
    }
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(KEY_ENV, raising=False)
    # git notes needs a committer identity; the CLI inherits the process environment.
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Ada Tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "ada@example.com")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Ada Tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "ada@example.com")
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "commit.gpgsign", "false")
    (path / "a.txt").write_text("one\n")
    git(path, "add", "-A")
    git(path, "-c", "user.name=Ada", "-c", "user.email=ada@example.com", "commit", "-q", "-m", "first")
    return path


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def attest(repo: Path, *extra: str) -> int:
    return main(["--repo", str(repo), "attest", "--author-type", "agent", *extra])


def test_attest_head_writes_readable_note(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = attest(repo, "--tool", "claude-code", "--model", "m1", "--contract-id", "pg-003")

    out = capsys.readouterr().out.strip()
    note = notes_show(repo, head(repo))
    assert code == 0
    assert note == out
    assert note is not None
    att = from_json(note)
    assert att.commit == head(repo)
    assert (att.author_type, att.tool, att.model, att.contract_id) == ("agent", "claude-code", "m1", "pg-003")


def test_attest_explicit_commit(repo: Path) -> None:
    sha = head(repo)

    assert main(["--repo", str(repo), "attest", "--commit", sha, "--author-type", "human"]) == 0
    note = notes_show(repo, sha)
    assert note is not None
    assert from_json(note).author_type == "human"


def test_second_attest_exits_1_and_keeps_note(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert attest(repo, "--tool", "first") == 0
    capsys.readouterr()

    code = attest(repo, "--tool", "second")

    assert code == 1
    assert "already exists" in capsys.readouterr().err
    note = notes_show(repo, head(repo))
    assert note is not None
    assert from_json(note).tool == "first"


def test_force_overwrites(repo: Path) -> None:
    assert attest(repo, "--tool", "first") == 0

    assert attest(repo, "--tool", "second", "--force") == 0

    note = notes_show(repo, head(repo))
    assert note is not None
    assert from_json(note).tool == "second"


def test_unsigned_notice_when_key_absent(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = attest(repo)

    captured = capsys.readouterr()
    assert code == 0
    assert KEY_ENV in captured.err
    assert "unsigned" in captured.err
    assert from_json(captured.out.strip()).signature == ""


def test_signed_when_key_set(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(KEY_ENV, "top-secret")

    code = attest(repo)

    captured = capsys.readouterr()
    att = from_json(captured.out.strip())
    assert code == 0
    assert captured.err == ""
    assert att.signature != ""
    assert verify_signature(att, b"top-secret")
    assert not verify_signature(att, b"wrong")


def test_evidence_file_is_hashed(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_bytes(b"abc")

    code = attest(repo, "--evidence", str(evidence))

    assert code == 0
    att = from_json(capsys.readouterr().out.strip())
    assert att.evidence_sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_missing_evidence_file_exits_3(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = attest(repo, "--evidence", str(tmp_path / "nope.txt"))

    assert code == 3
    assert "evidence" in capsys.readouterr().err
    assert notes_show(repo, head(repo)) is None


def test_unknown_commit_exits_3(repo: Path) -> None:
    assert attest(repo, "--commit", "deadbeef" * 5) == 3


def test_non_repo_exits_3(tmp_path: Path) -> None:
    assert main(["--repo", str(tmp_path), "attest", "--author-type", "agent"]) == 3


def test_author_type_is_required(repo: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--repo", str(repo), "attest"])

    assert excinfo.value.code == 2
