"""Fixed-destination, bounded embedding adapters. No generation or execution."""

import asyncio
import hashlib
import json
import re

import httpx2 as httpx
from pydantic import SecretStr

from incident_ops.application.retrieval import EmbeddingPort
from incident_ops.config import Settings
from incident_ops.domain.errors import RetrievalUnavailableError
from incident_ops.domain.retrieval import EMBEDDING_DIMENSIONS, validate_vector


class DeterministicEmbedder:
    provider = "deterministic"
    model = "hash-bow-v1"

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"\w+", text.casefold()):
            index = (
                int.from_bytes(hashlib.sha256(word.encode()).digest()[:4]) % EMBEDDING_DIMENSIONS
            )
            vector[index] += 1
        return validate_vector(vector)


class OpenAIEmbedder:
    provider = "openai"
    model = "text-embedding-3-small"

    def __init__(
        self, api_key: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = api_key
        self._transport = transport

    async def embed(self, text: str) -> list[float]:
        if not text.strip() or len(text.encode()) > 8000:
            raise RetrievalUnavailableError()
        try:
            async with (
                asyncio.timeout(6),
                httpx.AsyncClient(
                    timeout=5,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                    json={
                        "model": self.model,
                        "input": text,
                        "dimensions": EMBEDDING_DIMENSIONS,
                        "encoding_format": "float",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise RetrievalUnavailableError()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 262144:
                            raise RetrievalUnavailableError()
            payload = json.loads(body)
            if not isinstance(payload, dict) or payload.get("model") != self.model:
                raise RetrievalUnavailableError()
            rows = payload.get("data")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise RetrievalUnavailableError()
            if type(rows[0].get("index")) is not int or rows[0]["index"] != 0:
                raise RetrievalUnavailableError()
            return validate_vector(rows[0].get("embedding"))
        except (httpx.HTTPError, TimeoutError, ValueError, OverflowError) as error:
            raise RetrievalUnavailableError() from error


def build_embedder(settings: Settings) -> EmbeddingPort | None:
    if settings.embedding_provider == "disabled":
        return None
    if settings.embedding_provider == "deterministic":
        return DeterministicEmbedder()
    assert settings.embedding_api_key is not None
    return OpenAIEmbedder(settings.embedding_api_key)
