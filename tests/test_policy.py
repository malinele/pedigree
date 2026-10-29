from pathlib import Path

import pytest

from pedigree.policy import Policy, PolicyError, Rule, default_policy_toml, glob_match, load_policy


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "pedigree.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults() -> None:
    policy = Policy()
    rule = Rule(name="r", paths=("a",))

    assert policy.require_attestation_branches == ()
    assert policy.signed_required is False
    assert policy.rules == ()
    assert rule.applies_to == "any"
    assert rule.require == ()
    assert rule.max_diff_lines is None
    assert rule.forbid is False


def test_empty_file_loads_defaults(tmp_path: Path) -> None:
    assert load_policy(_write(tmp_path, "")) == Policy()


def test_round_trip_of_every_field(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[policy]
require_attestation_branches = ["main", "release/*"]
signed_required = true

[[rule]]
name = "one"
paths = ["src/**", "lib/*.py"]
applies_to = "agent"
require = ["attestation", "contract_id"]
max_diff_lines = 50
forbid = true

[[rule]]
name = "two"
paths = ["docs/**"]
""",
    )

    policy = load_policy(path)

    assert policy.require_attestation_branches == ("main", "release/*")
    assert policy.signed_required is True
    assert policy.rules == (
        Rule(
            name="one",
            paths=("src/**", "lib/*.py"),
            applies_to="agent",
            require=("attestation", "contract_id"),
            max_diff_lines=50,
            forbid=True,
        ),
        Rule(name="two", paths=("docs/**",)),
    )


def test_default_policy_toml_loads(tmp_path: Path) -> None:
    policy = load_policy(_write(tmp_path, default_policy_toml()))

    assert [r.name for r in policy.rules] == [
        "agent-src-needs-attestation",
        "agent-no-ci-changes",
        "agent-large-diff-needs-contract",
    ]
    assert policy.rules[1].forbid is True
    assert policy.rules[2].max_diff_lines == 800
    assert policy.rules[2].require == ("contract_id",)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("bogus = 1\n", "bogus"),
        ("[policy]\nbogus_key = 1\n", "bogus_key"),
        ('[[rule]]\nname = "a"\npaths = ["x"]\nbogus_rule_key = 1\n', "bogus_rule_key"),
    ],
)
def test_unknown_key_error_names_the_key(tmp_path: Path, text: str, key: str) -> None:
    with pytest.raises(PolicyError, match=key):
        load_policy(_write(tmp_path, text))


def test_duplicate_rule_names_rejected(tmp_path: Path) -> None:
    text = '[[rule]]\nname = "dup"\npaths = ["a"]\n[[rule]]\nname = "dup"\npaths = ["b"]\n'

    with pytest.raises(PolicyError, match="dup"):
        load_policy(_write(tmp_path, text))


@pytest.mark.parametrize(
    "extra",
    ['applies_to = "robot"', 'require = ["telepathy"]'],
)
def test_bad_enum_values_rejected(tmp_path: Path, extra: str) -> None:
    text = f'[[rule]]\nname = "a"\npaths = ["x"]\n{extra}\n'

    with pytest.raises(PolicyError, match=r"robot|telepathy"):
        load_policy(_write(tmp_path, text))


def test_wrong_types_rejected(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="signed_required"):
        load_policy(_write(tmp_path, '[policy]\nsigned_required = "yes"\n'))


def test_missing_file_and_bad_toml_raise_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "nope.toml")
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, "[policy\n"))


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/**/x.py", "src/x.py", True),
        ("src/**/x.py", "src/a/b/c/x.py", True),
        ("src/**/x.py", "lib/a/x.py", False),
        ("src/**/x.py", "src/a/y.py", False),
        ("src/**", "src/a/b.py", True),
        ("src/**", "other/a.py", False),
        ("**", "a", True),
        ("**", "a/b/c.txt", True),
        ("*.py", "x.py", True),
        ("*.py", "src/x.py", False),
        ("x.py", "x.py", True),
        ("x.py", "a/x.py", False),
        (".github/**", ".github/workflows/ci.yml", True),
        ("**/*.md", "README.md", True),
        ("**/*.md", "docs/a/b.md", True),
        ("src/*/x.py", "src/a/b/x.py", False),
    ],
)
def test_glob_match(pattern: str, path: str, expected: bool) -> None:
    assert glob_match(pattern, path) is expected
