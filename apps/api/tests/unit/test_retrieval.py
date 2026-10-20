"""Retrieval contracts reject unsafe vectors and preserve source provenance."""

import math
from uuid import UUID

import pytest
from pydantic import ValidationError

from incident_ops.application.retrieval import HistoricalRecord, RetrievalRequest, embedding_text
from incident_ops.domain.retrieval import validate_vector
from incident_ops.infrastructure.embeddings import DeterministicEmbedder


def record(**changes):
    return HistoricalRecord.model_validate(
        {
            "id": UUID("a1100000-0000-4000-8000-000000000001"),
            "title": "Checkout retry failed",
            "description": "A synthetic checkout job failed.",
            "service": "checkout",
            "severity": "low",
            "status": "resolved",
            "resolution_summary": "A reviewed retry restored the fixture.",
            "resolution_steps": ["Review the failure", "Request the allowlisted retry"],
            "provenance": "seeded_demo_data",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "vector",
    [
        [1.0],
        [0.0] * 1536,
        [math.nan] + [1.0] * 1535,
        [math.inf] + [1.0] * 1535,
        [True] + [1.0] * 1535,
        ["1"] + [1.0] * 1535,
    ],
)
def test_embedding_rejects_wrong_dimensions_nonfinite_zero_and_coercion(vector):
    with pytest.raises(ValueError, match="embedding"):
        validate_vector(vector)


def test_embedding_template_is_stable_bounded_and_excludes_resolution_commands():
    first = record()
    text = embedding_text(first)
    assert text == embedding_text(first)
    assert first.title in text and first.resolution_summary in text
    assert first.resolution_steps[0] not in text
    assert len(text.encode()) <= 8000
    assert first.content_hash != record(resolution_steps=["Different source step"]).content_hash


@pytest.mark.parametrize(
    "changes",
    [
        {"provenance": "production"},
        {"status": "open"},
        {"resolution_steps": []},
        {"resolution_steps": ["x"] * 21},
        {"actor_type": "human"},
        {"title": " "},
        {"description": "x\u200by"},
        {"description": "<script>execute()</script>"},
    ],
)
def test_manifest_only_accepts_bounded_plain_synthetic_records(changes):
    with pytest.raises(ValidationError):
        record(**changes)


@pytest.mark.parametrize(
    "payload",
    [
        {"query": " "},
        {"query": "x" * 2001},
        {"limit": 2},
        {"limit": 6},
        {"limit": "3"},
        {"execute": True},
        {"service": " "},
        {"severity": "urgent"},
    ],
)
def test_query_contract_is_strict(payload):
    with pytest.raises(ValidationError):
        RetrievalRequest.model_validate(payload)


async def test_deterministic_provider_is_stable_and_discriminates_words():
    provider = DeterministicEmbedder()
    first = await provider.embed("checkout queue timeout")
    assert first == await provider.embed("checkout queue timeout")
    assert first != await provider.embed("database disk full")
    assert len(validate_vector(first)) == 1536
    assert provider.provider == "deterministic"
    assert provider.model == "hash-bow-v1"


def test_curated_manifest_has_stable_unique_synthetic_resolved_sources():
    from incident_ops.seed import load_historical_fixtures

    records = load_historical_fixtures()
    assert 10 <= len(records) <= 20
    assert len({record.id for record in records}) == len(records)
    assert len({record.service for record in records}) >= 4
    assert {record.severity for record in records} == {"low", "medium", "high", "critical"}
    assert all(
        record.provenance == "seeded_demo_data" and record.status == "resolved"
        for record in records
    )
    assert records == load_historical_fixtures()


def test_embedding_template_keeps_json_valid_and_utf8_bytes_bounded():
    import json

    from incident_ops.application.retrieval import query_text

    text = query_text(
        title='"' * 200,
        description='"' * 10000,
        service='"' * 100,
        severity="low",
        summary='"' * 2000,
    )
    assert len(text.encode()) <= 8000
    assert json.loads(text)["severity"] == "low"
    unicode_text = query_text(
        title="界" * 200,
        description="界" * 10000,
        service="界" * 100,
        severity="low",
        summary="界" * 2000,
    )
    assert len(unicode_text.encode()) <= 8000
    assert json.loads(unicode_text)["severity"] == "low"
