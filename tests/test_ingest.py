import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from pedigree.ingest import IngestError, RunEvidence, load_run_evidence

OUTCOME = {"state": "accepted", "contract_id": "pg-007", "run_id": "run-42"}
ENVELOPE = {
    "executor": {"name": "claude-code", "model": "sonnet-5", "cost_usd": 0.31},
    "routing": {"task_class": "code", "attempts": 2, "stop_reason": "done"},
}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "run-42"
    path.mkdir()
    (path / "task_outcome.json").write_text(json.dumps(OUTCOME, indent=2))
    (path / "execution_envelope.json").write_text(json.dumps(ENVELOPE, indent=2))
    return path


def test_fields_are_extracted_exactly(run_dir: Path) -> None:
    evidence = load_run_evidence(run_dir)

    assert evidence.tool == "claude-code"
    assert evidence.model == "sonnet-5"
    assert evidence.contract_id == "pg-007"
    assert evidence.outcome_state == "accepted"
    assert evidence.source == "run-42"


def test_sha256_covers_outcome_file_bytes(run_dir: Path) -> None:
    expected = hashlib.sha256((run_dir / "task_outcome.json").read_bytes()).hexdigest()

    assert load_run_evidence(run_dir).evidence_sha256 == expected


def test_evidence_is_frozen(run_dir: Path) -> None:
    evidence = load_run_evidence(run_dir)

    assert isinstance(evidence, RunEvidence)
    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence.tool = "other"  # type: ignore[misc]


def test_missing_optional_keys_default_to_empty(run_dir: Path) -> None:
    (run_dir / "task_outcome.json").write_text("{}")
    (run_dir / "execution_envelope.json").write_text(json.dumps({"executor": {"name": "x"}}))

    evidence = load_run_evidence(run_dir)

    assert (evidence.tool, evidence.model, evidence.contract_id, evidence.outcome_state) == ("x", "", "", "")


def test_missing_executor_object_is_tolerated(run_dir: Path) -> None:
    (run_dir / "execution_envelope.json").write_text("{}")

    evidence = load_run_evidence(run_dir)

    assert (evidence.tool, evidence.model) == ("", "")


@pytest.mark.parametrize("name", ["task_outcome.json", "execution_envelope.json"])
def test_missing_file_raises(run_dir: Path, name: str) -> None:
    (run_dir / name).unlink()

    with pytest.raises(IngestError, match=name):
        load_run_evidence(run_dir)


@pytest.mark.parametrize("name", ["task_outcome.json", "execution_envelope.json"])
def test_malformed_json_raises(run_dir: Path, name: str) -> None:
    (run_dir / name).write_text("{not json")

    with pytest.raises(IngestError, match="invalid JSON"):
        load_run_evidence(run_dir)


def test_non_object_json_raises(run_dir: Path) -> None:
    (run_dir / "task_outcome.json").write_text("[1, 2]")

    with pytest.raises(IngestError, match="JSON object"):
        load_run_evidence(run_dir)
