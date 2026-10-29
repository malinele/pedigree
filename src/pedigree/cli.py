"""Command-line interface for Pedigree.

Exit codes:
    0  success
    1  policy violations or verification failure
    2  usage error or command not implemented yet
    3  repository or environment error
"""

import argparse
from collections.abc import Sequence

from pedigree import __version__

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
        subparsers.add_parser(name, help=help_text)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    command: str = args.command
    print(f"pedigree {command}: not implemented yet")
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
