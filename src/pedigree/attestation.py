"""Attestation model: a small, optionally HMAC-signed statement about one commit.

The commit sha is part of the signed payload, so an attestation copied onto a
different commit can be detected with ``bound_to``.
"""

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime

SCHEMA = "pedigree/1"
AUTHOR_TYPES = ("human", "agent")


class AttestationError(ValueError):
    """An attestation document is malformed."""


@dataclass(frozen=True)
class Attestation:
    schema: str
    commit: str
    author_type: str
    tool: str
    model: str
    contract_id: str
    evidence_sha256: str
    created_at: str
    signature: str = ""


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_payload(att: Attestation) -> bytes:
    """Deterministic JSON of every field except the signature."""
    data = {key: value for key, value in asdict(att).items() if key != "signature"}
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _mac(att: Attestation, key: bytes) -> str:
    return hmac.new(key, canonical_payload(att), hashlib.sha256).hexdigest()


def sign(att: Attestation, key: bytes) -> Attestation:
    return replace(att, signature=_mac(att, key))


def verify_signature(att: Attestation, key: bytes) -> bool:
    if not att.signature:
        return False
    return hmac.compare_digest(att.signature, _mac(att, key))


def bound_to(att: Attestation, sha: str) -> bool:
    """True when the attestation's (signed) commit equals ``sha``."""
    return att.commit == sha


def to_json(att: Attestation) -> str:
    return json.dumps(asdict(att), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def from_json(text: str) -> Attestation:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AttestationError(f"attestation is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise AttestationError("attestation must be a JSON object")
    names = [f.name for f in fields(Attestation)]
    missing = [name for name in names if name not in raw]
    if missing:
        raise AttestationError(f"attestation is missing fields: {', '.join(missing)}")
    unknown = sorted(str(key) for key in raw if key not in names)
    if unknown:
        raise AttestationError(f"attestation has unknown fields: {', '.join(unknown)}")
    for name in names:
        if not isinstance(raw[name], str):
            raise AttestationError(f"attestation field {name!r} must be a string")
    if raw["schema"] != SCHEMA:
        raise AttestationError(f"unknown attestation schema: {raw['schema']!r}")
    if raw["author_type"] not in AUTHOR_TYPES:
        raise AttestationError(f"invalid author_type: {raw['author_type']!r}")
    if not raw["commit"]:
        raise AttestationError("attestation commit must not be empty")
    return Attestation(**{name: raw[name] for name in names})
