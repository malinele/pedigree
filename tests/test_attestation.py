import json
from dataclasses import replace

import pytest

from pedigree.attestation import (
    Attestation,
    AttestationError,
    bound_to,
    canonical_payload,
    from_json,
    sign,
    to_json,
    verify_signature,
)

SHA = "a" * 40
KEY = b"secret-key"


def make() -> Attestation:
    return Attestation(
        schema="pedigree/1",
        commit=SHA,
        author_type="agent",
        tool="claude-code",
        model="m1",
        contract_id="pg-003",
        evidence_sha256="e" * 64,
        created_at="2026-01-02T03:04:05Z",
    )


def test_canonical_payload_is_sorted_compact_and_excludes_signature() -> None:
    payload = canonical_payload(sign(make(), KEY))

    assert payload == (
        b'{"author_type":"agent","commit":"' + SHA.encode() + b'","contract_id":"pg-003",'
        b'"created_at":"2026-01-02T03:04:05Z","evidence_sha256":"' + b"e" * 64 + b'",'
        b'"model":"m1","schema":"pedigree/1","tool":"claude-code"}'
    )


def test_canonical_payload_is_stable_across_signing() -> None:
    att = make()

    assert canonical_payload(att) == canonical_payload(sign(att, KEY))


def test_sign_verify_round_trip() -> None:
    signed = sign(make(), KEY)

    assert len(signed.signature) == 64
    assert verify_signature(signed, KEY)


def test_wrong_key_fails() -> None:
    assert not verify_signature(sign(make(), KEY), b"other-key")


def test_unsigned_attestation_does_not_verify() -> None:
    assert not verify_signature(make(), KEY)


@pytest.mark.parametrize(
    "change",
    [
        {"commit": "b" * 40},
        {"author_type": "human"},
        {"tool": "other"},
        {"model": "m2"},
        {"contract_id": "pg-999"},
        {"evidence_sha256": ""},
        {"created_at": "2027-01-01T00:00:00Z"},
    ],
)
def test_tampered_field_fails(change: dict[str, str]) -> None:
    signed = sign(make(), KEY)

    assert not verify_signature(replace(signed, **change), KEY)


def test_json_round_trip() -> None:
    signed = sign(make(), KEY)

    assert from_json(to_json(signed)) == signed


def _doc(**overrides: object) -> str:
    data: dict[str, object] = {
        "schema": "pedigree/1",
        "commit": SHA,
        "author_type": "human",
        "tool": "",
        "model": "",
        "contract_id": "",
        "evidence_sha256": "",
        "created_at": "2026-01-02T03:04:05Z",
        "signature": "",
    }
    data.update(overrides)
    return json.dumps(data)


def test_from_json_accepts_valid_document() -> None:
    assert from_json(_doc()).author_type == "human"


def test_from_json_rejects_invalid_json() -> None:
    with pytest.raises(AttestationError, match="not valid JSON"):
        from_json("{nope")


def test_from_json_rejects_non_object() -> None:
    with pytest.raises(AttestationError, match="JSON object"):
        from_json("[]")


def test_from_json_rejects_unknown_schema() -> None:
    with pytest.raises(AttestationError, match="schema"):
        from_json(_doc(schema="pedigree/2"))


def test_from_json_rejects_bad_author_type() -> None:
    with pytest.raises(AttestationError, match="author_type"):
        from_json(_doc(author_type="robot"))


def test_from_json_rejects_missing_field() -> None:
    data = json.loads(_doc())
    del data["commit"]

    with pytest.raises(AttestationError, match="missing fields: commit"):
        from_json(json.dumps(data))


def test_from_json_rejects_unknown_field() -> None:
    with pytest.raises(AttestationError, match="unknown fields: extra"):
        from_json(_doc(extra="x"))


def test_from_json_rejects_non_string_field() -> None:
    with pytest.raises(AttestationError, match="must be a string"):
        from_json(_doc(tool=5))


def test_bound_to_matches_only_own_commit() -> None:
    att = make()

    assert bound_to(att, SHA)
    assert not bound_to(att, "b" * 40)


def test_moved_attestation_is_flagged_as_bound_to_other_commit() -> None:
    signed = sign(make(), KEY)
    other = "c" * 40

    assert verify_signature(signed, KEY)
    assert not bound_to(signed, other)
    assert not verify_signature(replace(signed, commit=other), KEY)
