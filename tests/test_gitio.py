import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from pedigree.gitio import (
    GitError,
    branch_name,
    current_head,
    is_git_repo,
    notes_add,
    notes_list,
    notes_show,
    parse_trailers,
    read_commits,
)
from pedigree.gitio import _numstat_path as numstat_path

DEFAULT_DATE = "2024-01-02T03:04:05+00:00"


def git(repo: Path, *args: str, date: str = DEFAULT_DATE) -> str:
    env = {
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": os.environ["PATH"],
    }
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return proc.stdout.strip()


def commit(repo: Path, subject: str, *paragraphs: str, date: str = DEFAULT_DATE) -> str:
    args = ["commit", "-q", "-m", subject]
    for para in paragraphs:
        args += ["-m", para]
    git(repo, *args, date=date)
    return git(repo, "rev-parse", "HEAD")


@dataclass(frozen=True)
class Repo:
    path: Path
    shas: list[str]  # oldest first


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Ada Tester")
    git(path, "config", "user.email", "ada@example.com")
    git(path, "config", "commit.gpgsign", "false")

    (path / "a.txt").write_text("l1\nl2\nl3\n")
    git(path, "add", "-A")
    c1 = commit(path, "first commit")

    (path / "b.txt").write_text("x\n")
    git(path, "add", "-A")
    c2 = commit(
        path,
        "second commit",
        "Some explanation.",
        "Co-Authored-By: Bob <bob@example.com>\nco-authored-by: Cy <cy@example.com>\nAI-Assisted: yes",
    )

    (path / "a.txt").write_text("l1\nl3\nl4\nl5\n")
    (path / "blob.bin").write_bytes(b"\x00\x01\x02\xff\x00binary")
    git(path, "add", "-A")
    c3 = commit(path, "third commit", date="2024-02-03T04:05:06+00:00")

    git(path, "mv", "a.txt", "renamed.txt")
    c4 = commit(path, "fourth: rename")
    return Repo(path, [c1, c2, c3, c4])


def test_read_commits_head_only(repo: Repo) -> None:
    commits = read_commits(repo.path, None)

    assert len(commits) == 1
    assert commits[0].sha == repo.shas[3]
    assert commits[0].subject == "fourth: rename"


def test_read_commits_exact_fields(repo: Repo) -> None:
    commits = read_commits(repo.path, "main")

    assert [c.sha for c in commits] == list(reversed(repo.shas))
    first = commits[3]
    assert first.author_name == "Ada Tester"
    assert first.author_email == "ada@example.com"
    assert first.author_date == "2024-01-02T03:04:05+00:00"
    assert first.subject == "first commit"
    assert first.trailers == {}
    assert (first.insertions, first.deletions, first.files) == (3, 0, ("a.txt",))
    assert commits[1].author_date == "2024-02-03T04:05:06+00:00"


def test_multi_value_trailers_case_insensitive(repo: Repo) -> None:
    second = read_commits(repo.path, f"{repo.shas[0]}..{repo.shas[1]}")[0]

    assert second.trailers == {
        "co-authored-by": ["Bob <bob@example.com>", "Cy <cy@example.com>"],
        "ai-assisted": ["yes"],
    }


def test_numstat_binary_counts_zero(repo: Repo) -> None:
    third = read_commits(repo.path, f"{repo.shas[1]}..{repo.shas[2]}")[0]

    assert third.files == ("a.txt", "blob.bin")
    assert (third.insertions, third.deletions) == (2, 1)


def test_numstat_rename_keeps_new_path(repo: Repo) -> None:
    fourth = read_commits(repo.path, None)[0]

    assert fourth.files == ("renamed.txt",)
    assert (fourth.insertions, fourth.deletions) == (0, 0)


def test_rev_range_set_and_order(repo: Repo) -> None:
    commits = read_commits(repo.path, f"{repo.shas[0]}..{repo.shas[3]}")

    assert [c.sha for c in commits] == [repo.shas[3], repo.shas[2], repo.shas[1]]


def test_bogus_range_raises(repo: Repo) -> None:
    with pytest.raises(GitError):
        read_commits(repo.path, "nope..alsonope")


def test_option_like_range_rejected(repo: Repo) -> None:
    with pytest.raises(GitError):
        read_commits(repo.path, "--all")


def test_non_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        read_commits(tmp_path, None)
    assert is_git_repo(tmp_path) is False


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        current_head(tmp_path / "missing")


def test_notes_round_trip(repo: Repo) -> None:
    notes_add(repo.path, repo.shas[0], "hello", False)
    notes_add(repo.path, repo.shas[1], "multi\nline", False)

    assert notes_show(repo.path, repo.shas[0]) == "hello"
    assert notes_list(repo.path) == {repo.shas[0]: "hello", repo.shas[1]: "multi\nline"}
    assert git(repo.path, "rev-parse", "--verify", "refs/notes/pedigree")


def test_notes_existing_requires_force(repo: Repo) -> None:
    notes_add(repo.path, repo.shas[0], "one", False)

    with pytest.raises(GitError, match="exists"):
        notes_add(repo.path, repo.shas[0], "two", False)
    assert notes_show(repo.path, repo.shas[0]) == "one"

    notes_add(repo.path, repo.shas[0], "two", True)
    assert notes_show(repo.path, repo.shas[0]) == "two"


def test_notes_missing_is_none(repo: Repo) -> None:
    assert notes_show(repo.path, repo.shas[0]) is None
    assert notes_list(repo.path) == {}


def test_head_and_branch(repo: Repo) -> None:
    assert is_git_repo(repo.path) is True
    assert current_head(repo.path) == repo.shas[3]
    assert branch_name(repo.path) == "main"

    git(repo.path, "checkout", "-q", "--detach")
    assert branch_name(repo.path) is None


def test_parse_trailers_edge_cases() -> None:
    assert parse_trailers("subject only") == {}
    assert parse_trailers("subject\n\nplain prose, no trailers") == {}
    assert parse_trailers("subject\n\nKey: a\nnot a trailer") == {}
    assert parse_trailers("subject\n\nKey: long\n  continued\nKEY: b\n") == {"key": ["long continued", "b"]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a.txt", "a.txt"),
        ("a.txt => b.txt", "b.txt"),
        ("src/{old => new}/f.py", "src/new/f.py"),
        ("src/{ => sub}/f.py", "src/sub/f.py"),
        ("src/{sub => }/f.py", "src/f.py"),
    ],
)
def test_numstat_path(raw: str, expected: str) -> None:
    assert numstat_path(raw) == expected
