"""Policy model and loader for ``pedigree.toml`` (stdlib only)."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

APPLIES_TO_VALUES = ("agent", "human", "any")
REQUIREMENTS = ("attestation", "signed_attestation", "human_review_trailer", "contract_id")

_POLICY_KEYS = frozenset({"require_attestation_branches", "signed_required"})
_RULE_KEYS = frozenset({"name", "paths", "applies_to", "require", "max_diff_lines", "forbid"})
_TOP_LEVEL_KEYS = frozenset({"policy", "rule"})


class PolicyError(Exception):
    """Raised when a policy file is missing, malformed or invalid."""


@dataclass(frozen=True)
class Rule:
    name: str
    paths: tuple[str, ...]
    applies_to: str = "any"
    require: tuple[str, ...] = ()
    max_diff_lines: int | None = None
    forbid: bool = False


@dataclass(frozen=True)
class Policy:
    require_attestation_branches: tuple[str, ...] = ()
    signed_required: bool = False
    rules: tuple[Rule, ...] = ()


def _match_segments(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match_segments(rest, path[i:]) for i in range(len(path) + 1))
    if not path:
        return False
    return fnmatchcase(path[0], head) and _match_segments(rest, path[1:])


def glob_match(pattern: str, path: str) -> bool:
    """Match ``path`` against ``pattern``; ``**`` spans any number of segments."""
    return _match_segments(tuple(pattern.split("/")), tuple(path.split("/")))


def _check_keys(table: Mapping[str, object], allowed: frozenset[str], where: str) -> None:
    for key in table:
        if key not in allowed:
            raise PolicyError(f"unknown key '{key}' in {where}")


def _str_tuple(value: object, key: str, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PolicyError(f"'{key}' in {where} must be an array of strings")
    return tuple(str(item) for item in value)


def _bool(value: object, key: str, where: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyError(f"'{key}' in {where} must be a boolean")
    return value


def _table(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise PolicyError(f"{where} must be a table")
    return {str(k): v for k, v in value.items()}


def _parse_rule(raw: object, index: int) -> Rule:
    where = f"[[rule]] #{index}"
    table = _table(raw, where)
    _check_keys(table, _RULE_KEYS, where)
    name = table.get("name")
    if not isinstance(name, str) or not name:
        raise PolicyError(f"'name' in {where} must be a non-empty string")
    where = f"rule '{name}'"
    if "paths" not in table:
        raise PolicyError(f"missing key 'paths' in {where}")
    paths = _str_tuple(table["paths"], "paths", where)
    applies_to = table.get("applies_to", "any")
    if applies_to not in APPLIES_TO_VALUES:
        raise PolicyError(f"bad value {applies_to!r} for 'applies_to' in {where}; expected one of {APPLIES_TO_VALUES}")
    require = _str_tuple(table.get("require", []), "require", where)
    for item in require:
        if item not in REQUIREMENTS:
            raise PolicyError(f"bad value {item!r} for 'require' in {where}; expected one of {REQUIREMENTS}")
    max_diff_lines = table.get("max_diff_lines")
    if max_diff_lines is not None and (
        not isinstance(max_diff_lines, int) or isinstance(max_diff_lines, bool) or max_diff_lines < 0
    ):
        raise PolicyError(f"'max_diff_lines' in {where} must be a non-negative integer")
    forbid = _bool(table.get("forbid", False), "forbid", where)
    return Rule(
        name=name,
        paths=paths,
        applies_to=str(applies_to),
        require=require,
        max_diff_lines=max_diff_lines,
        forbid=forbid,
    )


def _parse_rules(raw: object) -> tuple[Rule, ...]:
    if not isinstance(raw, list):
        raise PolicyError("'rule' must be an array of tables ([[rule]])")
    rules = tuple(_parse_rule(item, i + 1) for i, item in enumerate(raw))
    seen: set[str] = set()
    for rule in rules:
        if rule.name in seen:
            raise PolicyError(f"duplicate rule name '{rule.name}'")
        seen.add(rule.name)
    return rules


def load_policy(path: Path) -> Policy:
    """Load and validate a policy from a ``pedigree.toml`` file."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"invalid TOML in {path}: {exc}") from exc
    _check_keys(data, _TOP_LEVEL_KEYS, "top level")
    section = _table(data.get("policy", {}), "[policy]")
    _check_keys(section, _POLICY_KEYS, "[policy]")
    return Policy(
        require_attestation_branches=_str_tuple(
            section.get("require_attestation_branches", []), "require_attestation_branches", "[policy]"
        ),
        signed_required=_bool(section.get("signed_required", False), "signed_required", "[policy]"),
        rules=_parse_rules(data.get("rule", [])),
    )


def default_policy_toml() -> str:
    """Return a commented starter policy."""
    return """\
# Pedigree policy. Evaluated by `pedigree verify`.

[policy]
# Branch glob patterns on which every commit must carry an attestation.
# Empty (the default) switches branch enforcement off.
require_attestation_branches = []

# When true, an unsigned or unverifiable attestation counts as missing.
signed_required = false

# Rules: `paths` are globs matched against changed file paths ('**' spans
# any number of directories). `applies_to` is "agent", "human" or "any".
# `require` may list: attestation, signed_attestation,
# human_review_trailer, contract_id.

# Agent commits touching source code must be attested.
[[rule]]
name = "agent-src-needs-attestation"
paths = ["src/**"]
applies_to = "agent"
require = ["attestation"]

# Agents must never change CI configuration.
[[rule]]
name = "agent-no-ci-changes"
paths = [".github/**"]
applies_to = "agent"
forbid = true

# Agent commits changing more than 800 lines must reference a contract.
[[rule]]
name = "agent-large-diff-needs-contract"
paths = ["**"]
applies_to = "agent"
max_diff_lines = 800
require = ["contract_id"]
"""
