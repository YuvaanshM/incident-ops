"""Owned embedding adapter contract, with no paid or external requests."""

import json

import httpx2 as httpx
import pytest
from pydantic import SecretStr, ValidationError

from incident_ops.config import Settings
from incident_ops.domain.errors import RetrievalUnavailableError
from incident_ops.infrastructure.embeddings import OpenAIEmbedder


@pytest.mark.parametrize(
    "changes",
    [
        {"embedding_provider": "unknown"},
        {"embedding_provider": "openai", "embedding_model": "wrong"},
        {"embedding_provider": "openai", "embedding_model": "text-embedding-3-small"},
        {"embedding_provider": "deterministic", "embedding_model": "text-embedding-3-small"},
    ],
)
def test_embedding_configuration_fails_closed(changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=SecretStr("postgresql://local/test"), **changes)


async def test_openai_contract_and_exact_model_dimension_validation():
    async def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/embeddings"
        assert json.loads(request.content) == {
            "model": "text-embedding-3-small",
            "input": "synthetic checkout timeout",
            "dimensions": 1536,
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "data": [{"index": 0, "embedding": [1.0] + [0.0] * 1535}],
            },
        )

    provider = OpenAIEmbedder(
        SecretStr("synthetic-placeholder"), transport=httpx.MockTransport(respond)
    )
    assert (await provider.embed("synthetic checkout timeout"))[0] == 1.0


@pytest.mark.parametrize(
    "body",
    [
        {"model": "another-model", "data": [{"index": 0, "embedding": [1.0] * 1536}]},
        {"model": "text-embedding-3-small", "data": [{"index": 0, "embedding": [1.0]}]},
        {"model": "text-embedding-3-small", "data": [{"index": 1, "embedding": [1.0] * 1536}]},
        {"model": "text-embedding-3-small", "data": []},
    ],
)
async def test_untrusted_provider_response_is_rejected_without_exposing_payload(body):
    provider = OpenAIEmbedder(
        SecretStr("synthetic-placeholder"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    with pytest.raises(RetrievalUnavailableError) as error:
        await provider.embed("synthetic text")
    assert "another-model" not in str(error.value)


@pytest.mark.parametrize("status", [401, 429, 500, 302])
async def test_provider_errors_are_generic_and_not_retried(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="private provider details")

    provider = OpenAIEmbedder(
        SecretStr("synthetic-placeholder"), transport=httpx.MockTransport(respond)
    )
    with pytest.raises(RetrievalUnavailableError) as error:
        await provider.embed("synthetic text")
    assert len(calls) == 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "key", ["", "\n", "x\ny", "x" * 4097], ids=["empty", "blank", "newline", "oversized"]
)
def test_external_provider_requires_bounded_printable_nonempty_secret(key):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=SecretStr("postgresql://local/test"),
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            embedding_allow_external_processing=True,
            embedding_api_key=SecretStr(key),
        )


async def test_provider_timeout_and_oversized_output_are_generic():
    async def timeout(request):
        raise httpx.ReadTimeout("synthetic-private-timeout", request=request)

    for transport in (
        httpx.MockTransport(timeout),
        httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 262145)),
    ):
        with pytest.raises(RetrievalUnavailableError) as error:
            await OpenAIEmbedder(SecretStr("synthetic-placeholder"), transport=transport).embed(
                "synthetic text"
            )
        assert "synthetic-private" not in str(error.value)


async def test_embedding_cancellation_is_not_converted_to_a_retry_or_success():
    import asyncio

    started = asyncio.Event()
    never = asyncio.Event()

    async def wait(request):
        started.set()
        await never.wait()
        return httpx.Response(500)

    provider = OpenAIEmbedder(
        SecretStr("synthetic-placeholder"), transport=httpx.MockTransport(wait)
    )
    task = asyncio.create_task(provider.embed("synthetic text"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
