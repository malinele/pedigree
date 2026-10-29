from pathlib import Path

import pytest

from pedigree.cli import main

STUB_COMMANDS = ["verify", "report"]


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


@pytest.mark.parametrize("command", STUB_COMMANDS)
def test_stub_subcommand_returns_2_and_prints_its_name(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([command])

    assert exit_code == 2
    assert f"pedigree {command}: not implemented yet" in capsys.readouterr().out


@pytest.mark.parametrize("command", STUB_COMMANDS)
def test_repo_option_is_accepted(command: str, tmp_path: Path) -> None:
    assert main(["--repo", str(tmp_path), command]) == 2
