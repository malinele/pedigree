"""Load evidence from a guarded-runner run directory."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUTCOME_FILE = "task_outcome.json"
ENVELOPE_FILE = "execution_envelope.json"


class IngestError(Exception):
    """A run directory is missing files or contains unusable JSON."""


@dataclass(frozen=True)
class RunEvidence:
    tool: str
    model: str
    contract_id: str
    outcome_state: str
    evidence_sha256: str
    source: str


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise IngestError(f"cannot read {path}: {exc}") from exc


def _parse_object(path: Path, raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise IngestError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise IngestError(f"{path} must contain a JSON object")
    return data


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def load_run_evidence(run_dir: Path) -> RunEvidence:
    """Extract attestation fields from ``task_outcome.json`` and ``execution_envelope.json``."""
    outcome_path = run_dir / OUTCOME_FILE
    envelope_path = run_dir / ENVELOPE_FILE
    outcome_raw = _read_bytes(outcome_path)
    envelope_raw = _read_bytes(envelope_path)
    outcome = _parse_object(outcome_path, outcome_raw)
    envelope = _parse_object(envelope_path, envelope_raw)
    executor = envelope.get("executor")
    if not isinstance(executor, dict):
        executor = {}
    return RunEvidence(
        tool=_text(executor.get("name")),
        model=_text(executor.get("model")),
        contract_id=_text(outcome.get("contract_id")),
        outcome_state=_text(outcome.get("state")),
        evidence_sha256=hashlib.sha256(outcome_raw).hexdigest(),
        source=run_dir.resolve().name,
    )
