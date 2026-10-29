from pathlib import Path

import pytest

from pedigree.cli import main
from pedigree.policy import default_policy_toml, load_policy


def test_policy_init_writes_loadable_file_and_prints_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--repo", str(tmp_path), "policy-init"])

    target = tmp_path / "pedigree.toml"
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == str(target)
    assert target.read_text(encoding="utf-8") == default_policy_toml()
    assert len(load_policy(target).rules) == 3


def test_policy_init_refuses_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "pedigree.toml"
    target.write_text("# mine\n", encoding="utf-8")

    exit_code = main(["--repo", str(tmp_path), "policy-init"])

    assert exit_code == 1
    assert target.read_text(encoding="utf-8") == "# mine\n"
    assert "--force" in capsys.readouterr().err


def test_policy_init_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "pedigree.toml"
    target.write_text("# mine\n", encoding="utf-8")

    exit_code = main(["--repo", str(tmp_path), "policy-init", "--force"])

    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == default_policy_toml()
