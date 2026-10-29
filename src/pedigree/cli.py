"""Command-line interface for Pedigree.

Exit codes:
    0  success
    1  policy violations or verification failure
    2  usage error or command not implemented yet
    3  repository or environment error
"""

import argparse
import hashlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from pedigree import __version__
from pedigree.attestation import SCHEMA, Attestation, sign, to_json, utc_now_iso
from pedigree.gitio import GitError, notes_add, notes_show, read_commits
from pedigree.policy import default_policy_toml

HMAC_KEY_ENV = "PEDIGREE_HMAC_KEY"
POLICY_FILE = "pedigree.toml"

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

COMMANDS: dict[str, str] = {
    "attest": "record a signed attestation for a commit",
    "verify": "verify a commit range against the policy",
    "report": "report provenance for a commit range",
    "policy-init": "create a starter policy file",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pedigree",
        description="Git-native provenance and policy for AI-written code.",
    )
    parser.add_argument("--version", action="version", version=f"pedigree {__version__}")
    parser.add_argument("--repo", metavar="PATH", default=".", help="path to the git repository (default: .)")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for name, help_text in COMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text)
        if name == "attest":
            _configure_attest(sub)
        elif name == "policy-init":
            sub.add_argument("--force", action="store_true", help="overwrite an existing policy file")
    return parser


def _configure_attest(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--commit", metavar="SHA", default=None, help="commit to attest (default: HEAD)")
    sub.add_argument("--author-type", choices=["agent", "human"], required=True)
    sub.add_argument("--tool", default="")
    sub.add_argument("--model", default="")
    sub.add_argument("--contract-id", default="")
    sub.add_argument("--evidence", metavar="FILE", default=None, help="file whose sha256 is recorded")
    sub.add_argument("--force", action="store_true", help="overwrite an existing attestation")


def _run_attest(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    evidence_sha = ""
    if args.evidence is not None:
        try:
            evidence_sha = hashlib.sha256(Path(args.evidence).read_bytes()).hexdigest()
        except OSError as exc:
            print(f"pedigree attest: cannot read evidence file {args.evidence}: {exc}", file=sys.stderr)
            return EXIT_ENVIRONMENT
    try:
        rev = None if args.commit is None else f"{args.commit}^!"
        commits = read_commits(repo, rev)
        if not commits:
            print(f"pedigree attest: commit not found: {args.commit}", file=sys.stderr)
            return EXIT_ENVIRONMENT
        sha = commits[0].sha
        if not args.force and notes_show(repo, sha) is not None:
            print(
                f"pedigree attest: an attestation for {sha} already exists (use --force to overwrite)",
                file=sys.stderr,
            )
            return EXIT_VIOLATIONS
        att = Attestation(
            schema=SCHEMA,
            commit=sha,
            author_type=args.author_type,
            tool=args.tool,
            model=args.model,
            contract_id=args.contract_id,
            evidence_sha256=evidence_sha,
            created_at=utc_now_iso(),
        )
        key = os.environ.get(HMAC_KEY_ENV, "")
        if key:
            att = sign(att, key.encode("utf-8"))
        else:
            print(f"pedigree attest: {HMAC_KEY_ENV} not set; storing unsigned attestation", file=sys.stderr)
        text = to_json(att)
        notes_add(repo, sha, text, args.force)
    except GitError as exc:
        print(f"pedigree attest: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    print(text)
    return EXIT_OK


def _run_policy_init(args: argparse.Namespace) -> int:
    target = Path(args.repo) / POLICY_FILE
    if target.exists() and not args.force:
        print(f"pedigree policy-init: {target} already exists (use --force to overwrite)", file=sys.stderr)
        return EXIT_VIOLATIONS
    try:
        target.write_text(default_policy_toml(), encoding="utf-8")
    except OSError as exc:
        print(f"pedigree policy-init: cannot write {target}: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    print(target)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    command: str = args.command
    if command == "attest":
        return _run_attest(args)
    if command == "policy-init":
        return _run_policy_init(args)
    print(f"pedigree {command}: not implemented yet")
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
