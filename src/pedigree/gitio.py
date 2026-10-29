"""Thin, safe git plumbing layer: commit reading, trailer parsing and a notes store.

All git access goes through ``_git``, which uses an argv list (never a shell).
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

NOTES_REF = "refs/notes/pedigree"

_RECORD_START = "\x01"
_FIELD_SEP = "\x00"
_LOG_FORMAT = "%x01%H%x00%an%x00%ae%x00%aI%x00%s%x00%B%x00"
_LOG_FIELD_COUNT = 7  # sha, name, email, date, subject, body, numstat remainder

_TRAILER_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)[ \t]*:[ \t]*(.*)$")
_BRACE_RENAME_RE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


class GitError(Exception):
    """A git invocation failed (maps to CLI exit code 3)."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclass(frozen=True)
class Commit:
    sha: str
    author_name: str
    author_email: str
    author_date: str
    subject: str
    trailers: dict[str, list[str]]
    insertions: int
    deletions: int
    files: tuple[str, ...]


def _git(repo: Path, *args: str) -> str:
    """Run ``git -C repo args...`` and return stdout; raise GitError on failure."""
    argv = ["git", "-C", str(repo), "-c", "core.quotepath=off", *args]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise GitError(f"cannot run git in {repo}: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise GitError(f"git {' '.join(args)} failed (rc={proc.returncode}): {stderr}", stderr)
    return proc.stdout


def parse_trailers(body: str) -> dict[str, list[str]]:
    """Parse git-style trailers from the last paragraph of a commit body.

    Keys are lower-cased (matching is case-insensitive); repeated keys collect
    their values in order. The subject-only paragraph never counts as trailers,
    and a last paragraph containing any non-trailer line yields no trailers.
    """
    paragraphs = re.split(r"\n[ \t]*\n", body.strip())
    if len(paragraphs) < 2:
        return {}
    trailers: dict[str, list[str]] = {}
    current: str | None = None
    for line in paragraphs[-1].splitlines():
        if line[:1] in (" ", "\t") and current is not None:
            values = trailers[current]
            values[-1] = f"{values[-1]} {line.strip()}".strip()
            continue
        match = _TRAILER_RE.match(line)
        if match is None:
            return {}
        current = match.group(1).lower()
        trailers.setdefault(current, []).append(match.group(2).strip())
    return trailers


def _numstat_path(raw: str) -> str:
    """Resolve a numstat path, keeping the new side of renames."""
    brace = _BRACE_RENAME_RE.match(raw)
    if brace is not None:
        prefix, _old, new, suffix = brace.groups()
        return (prefix + new + suffix).replace("//", "/")
    if " => " in raw:
        return raw.split(" => ", 1)[1]
    return raw


def _parse_numstat(text: str) -> tuple[int, int, tuple[str, ...]]:
    insertions = deletions = 0
    files: list[str] = []
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        insertions += int(added) if added.isdigit() else 0  # '-' for binary
        deletions += int(removed) if removed.isdigit() else 0
        files.append(_numstat_path(path))
    return insertions, deletions, tuple(files)


def _parse_record(record: str) -> Commit:
    fields = record.split(_FIELD_SEP, _LOG_FIELD_COUNT - 1)
    if len(fields) != _LOG_FIELD_COUNT:
        raise GitError(f"unparseable git log record: {record[:80]!r}")
    sha, name, email, date, subject, body, numstat = fields
    insertions, deletions, files = _parse_numstat(numstat)
    return Commit(
        sha=sha,
        author_name=name,
        author_email=email,
        author_date=date,
        subject=subject,
        trailers=parse_trailers(body),
        insertions=insertions,
        deletions=deletions,
        files=files,
    )


def read_commits(repo: Path, rev_range: str | None) -> list[Commit]:
    """Read commits (newest first). ``None`` means HEAD only."""
    if rev_range is not None and rev_range.startswith("-"):
        raise GitError(f"invalid revision range: {rev_range!r}")
    selection = ["-1", "HEAD"] if rev_range is None else [rev_range]
    out = _git(repo, "log", f"--pretty=format:{_LOG_FORMAT}", "--numstat", "-M", *selection, "--")
    return [_parse_record(rec) for rec in out.split(_RECORD_START)[1:]]


def notes_show(repo: Path, sha: str) -> str | None:
    try:
        out = _git(repo, "notes", f"--ref={NOTES_REF}", "show", sha)
    except GitError as exc:
        if "no note found" in exc.stderr.lower():
            return None
        raise
    return out.rstrip("\n")


def notes_add(repo: Path, sha: str, text: str, force: bool) -> None:
    if not force and notes_show(repo, sha) is not None:
        raise GitError(f"note for {sha} already exists (use force to overwrite)")
    args = ["notes", f"--ref={NOTES_REF}", "add"]
    if force:
        args.append("-f")
    _git(repo, *args, "-m", text, sha)


def notes_list(repo: Path) -> dict[str, str]:
    """Map annotated commit sha -> note text."""
    out = _git(repo, "notes", f"--ref={NOTES_REF}", "list")
    notes: dict[str, str] = {}
    for line in out.splitlines():
        note_obj, _, target = line.partition(" ")
        if target:
            notes[target] = _git(repo, "cat-file", "blob", note_obj).rstrip("\n")
    return notes


def current_head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def is_git_repo(repo: Path) -> bool:
    try:
        return _git(repo, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


def branch_name(repo: Path) -> str | None:
    """Current branch name, or None when HEAD is detached."""
    try:
        return _git(repo, "symbolic-ref", "-q", "--short", "HEAD").strip() or None
    except GitError:
        return None
