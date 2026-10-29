import hashlib
import json
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


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "run-7"
    path.mkdir()
    outcome = {"state": "accepted", "contract_id": "pg-007", "run_id": "run-7"}
    envelope = {"executor": {"name": "claude-code", "model": "sonnet-5", "cost_usd": 0.5}}
    (path / "task_outcome.json").write_text(json.dumps(outcome))
    (path / "execution_envelope.json").write_text(json.dumps(envelope))
    return path


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def test_from_cco_run_writes_note_with_run_fields(repo: Path, run_dir: Path) -> None:
    code = main(["--repo", str(repo), "attest", "--from-cco-run", str(run_dir)])

    note = notes_show(repo, head(repo))
    assert code == 0
    assert note is not None
    att = from_json(note)
    assert (att.author_type, att.tool, att.model, att.contract_id) == ("agent", "claude-code", "sonnet-5", "pg-007")
    expected = hashlib.sha256((run_dir / "task_outcome.json").read_bytes()).hexdigest()
    assert att.evidence_sha256 == expected


def test_from_cco_run_signs_when_key_set(repo: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "top-secret")

    code = main(["--repo", str(repo), "attest", "--from-cco-run", str(run_dir)])

    note = notes_show(repo, head(repo))
    assert code == 0
    assert note is not None
    assert verify_signature(from_json(note), b"top-secret")


def test_from_cco_run_respects_force(repo: Path, run_dir: Path) -> None:
    argv = ["--repo", str(repo), "attest", "--from-cco-run", str(run_dir)]
    assert main(argv) == 0

    assert main(argv) == 1
    assert main([*argv, "--force"]) == 0


def test_combining_with_tool_exits_2(repo: Path, run_dir: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--repo", str(repo), "attest", "--from-cco-run", str(run_dir), "--tool", "x"])

    assert excinfo.value.code == 2
    assert notes_show(repo, head(repo)) is None


@pytest.mark.parametrize(
    "extra",
    [["--model", "m"], ["--contract-id", "c"], ["--evidence", "f"], ["--author-type", "human"]],
)
def test_other_conflicts_exit_2(repo: Path, run_dir: Path, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--repo", str(repo), "attest", "--from-cco-run", str(run_dir), *extra])

    assert excinfo.value.code == 2


def test_bad_run_dir_exits_3(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--repo", str(repo), "attest", "--from-cco-run", str(tmp_path / "nope")])

    assert code == 3
    assert "task_outcome.json" in capsys.readouterr().err
    assert notes_show(repo, head(repo)) is None
