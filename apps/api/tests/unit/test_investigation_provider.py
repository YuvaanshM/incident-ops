"""Protocol-accurate external-provider tests, no live model calls."""

import json

import httpx2 as httpx
import pytest
from pydantic import SecretStr, ValidationError

from incident_ops.application.investigation import MODEL
from incident_ops.config import Settings
from incident_ops.domain.errors import InvestigationUnavailableError
from incident_ops.infrastructure.investigation_model import OpenAIInvestigator


def response_body():
    return {
        "model": MODEL,
        "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 100},
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "tool": "alerts",
                                "findings": [],
                                "job_id": None,
                                "target_status": None,
                            }
                        ),
                    }
                ],
            }
        ],
    }


async def test_provider_uses_pinned_model_stateless_strict_schema_and_no_tools():
    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["store"] is False
        assert body["reasoning"] == {"effort": "none"}
        assert body["max_output_tokens"] == 1000
        assert body["text"]["format"]["strict"] is True
        assert "tools" not in body and "previous_response_id" not in body
        return httpx.Response(200, json=response_body())

    provider = OpenAIInvestigator(
        SecretStr("synthetic-placeholder"), transport=httpx.MockTransport(respond)
    )
    result = await provider.next('{"untrusted_observations":{}}')
    assert result.decision.tool == "alerts"
    assert result.input_tokens == 100


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "wrong-model"},
        {"status": "incomplete"},
        {"usage": {}},
        {"output": [{"type": "function_call", "name": "execute"}]},
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": "private"}],
                }
            ]
        },
        {"usage": {"input_tokens": True, "output_tokens": 100}},
        {"usage": {"input_tokens": 100, "output_tokens": 1001}},
    ],
)
async def test_invalid_or_refused_output_fails_closed(changes):
    provider = OpenAIInvestigator(
        SecretStr("synthetic-placeholder"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={**response_body(), **changes})
        ),
    )
    with pytest.raises(InvestigationUnavailableError):
        await provider.next("{}")


@pytest.mark.parametrize("status", [302, 401, 429, 500])
async def test_failures_are_generic_and_not_retried(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive provider error")

    provider = OpenAIInvestigator(
        SecretStr("synthetic-placeholder"), transport=httpx.MockTransport(respond)
    )
    with pytest.raises(InvestigationUnavailableError) as error:
        await provider.next("{}")
    assert len(calls) == 1
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"investigation_enabled": True},
        {
            "investigation_enabled": True,
            "investigation_api_key": "synthetic-placeholder",
            "investigation_synthetic_data_confirmed": True,
        },
        {"investigation_model": "arbitrary"},
        {"investigation_api_key": "x\ny"},
    ],
)
def test_investigator_configuration_requires_explicit_safe_enablement(changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=SecretStr("postgresql://local/test"), **changes)
