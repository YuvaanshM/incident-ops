"""Fixed-endpoint, stateless generation adapter. No provider-hosted tools."""

import asyncio
import json

import httpx2 as httpx
from pydantic import SecretStr

from incident_ops.application.investigation import (
    MODEL,
    PROMPT,
    Decision,
    InvestigationModel,
    Limits,
    ModelTurn,
    input_token_reservation,
)
from incident_ops.config import Settings
from incident_ops.domain.errors import InvestigationUnavailableError


class OpenAIInvestigator:
    provider = "openai"
    model = MODEL

    def __init__(
        self, api_key: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = api_key
        self._transport = transport

    async def next(self, context: str) -> ModelTurn:
        if not context or len(context.encode()) > Limits().max_context_bytes:
            raise InvestigationUnavailableError()
        try:
            async with (
                asyncio.timeout(8),
                httpx.AsyncClient(
                    timeout=7, follow_redirects=False, trust_env=False, transport=self._transport
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                    json={
                        "model": MODEL,
                        "instructions": PROMPT,
                        "input": context,
                        "store": False,
                        "reasoning": {"effort": "none"},
                        "max_output_tokens": Limits().output_tokens,
                        "text": {
                            "format": {
                                "type": "json_schema",
                                "name": "investigation_decision",
                                "strict": True,
                                "schema": Decision.model_json_schema(),
                            }
                        },
                    },
                ) as response:
                    if response.status_code != 200:
                        raise InvestigationUnavailableError()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise InvestigationUnavailableError()
            payload = json.loads(body)
            if (
                not isinstance(payload, dict)
                or payload.get("model") != MODEL
                or payload.get("status") != "completed"
            ):
                raise InvestigationUnavailableError()
            output = payload.get("output")
            if not isinstance(output, list) or len(output) != 1:
                raise InvestigationUnavailableError()
            message = output[0]
            if (
                not isinstance(message, dict)
                or message.get("type") != "message"
                or message.get("role") != "assistant"
                or message.get("status") != "completed"
            ):
                raise InvestigationUnavailableError()
            content = message.get("content")
            if (
                not isinstance(content, list)
                or len(content) != 1
                or not isinstance(content[0], dict)
                or content[0].get("type") != "output_text"
                or not isinstance(content[0].get("text"), str)
            ):
                raise InvestigationUnavailableError()
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                raise InvestigationUnavailableError()
            input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
            if (
                type(input_tokens) is not int
                or type(output_tokens) is not int
                or not 0 <= input_tokens <= input_token_reservation(context)
                or not 0 <= output_tokens <= Limits().output_tokens
            ):
                raise InvestigationUnavailableError()
            return ModelTurn(
                Decision.model_validate_json(content[0]["text"]), input_tokens, output_tokens
            )
        except (httpx.HTTPError, TimeoutError, ValueError, OverflowError) as error:
            raise InvestigationUnavailableError() from error


def build_investigator(settings: Settings) -> InvestigationModel | None:
    if not settings.investigation_enabled:
        return None
    assert settings.investigation_api_key is not None
    return OpenAIInvestigator(settings.investigation_api_key)
