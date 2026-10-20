"""Real agent credentials retain command policy, replay, audit and isolation."""

from uuid import uuid4

import jwt
import pytest
from httpx2 import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from test_action_api import ActionTokenVerifier, headers
from test_alert_ingestion import alert_settings as alert_settings
from test_monitoring_remediation import monitored_job
from test_rls import _ensure_runtime_role

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    DemoJobModel,
    IdempotencyRecordModel,
)
from incident_ops.infrastructure.database.session import apply_principal_context
from incident_ops.infrastructure.rate_limit import FixedWindowRateLimiter
from incident_ops.main import create_app

pytestmark = pytest.mark.integration


class Humans(ActionTokenVerifier):
    async def verify(self, token):
        if token == "other-human":
            return Principal(
                ActorType.HUMAN, "other-operator", frozenset({"incidents:read", "actions:request"})
            )
        return await super().verify(token)


@pytest.fixture
def delegated_settings(alert_settings):
    return alert_settings.model_copy(
        update={
            "agent_tokens_enabled": True,
            "agent_token_issuer": "https://agents.example.test",
            "agent_token_audience": "incident-ops-agent-api",
            "agent_token_signing_key": SecretStr("phase10-test-only-" * 4),
        }
    )


def client_for(settings, engine, **kwargs):
    return AsyncClient(
        transport=ASGITransport(
            app=create_app(settings, database_engine=engine, token_verifier=Humans(), **kwargs)
        ),
        base_url="https://testserver",
    )


async def credential(client, human="human-token"):
    response = await client.post("/api/v1/agent-sessions", headers=headers(human), json={})
    assert response.status_code == 200
    return response.json()["access_token"]


def retry_body(job_id, version=2):
    return {
        "expected_incident_version": version,
        "action_type": "retry_failed_job",
        "parameters": {"job_id": str(job_id)},
        "rationale": "Synthetic deterministic tool request.",
    }


async def test_refreshed_agent_can_replay_and_recover_only_its_own_audited_request(
    delegated_settings, database_engine, clean_database
):
    async with client_for(delegated_settings, database_engine) as client:
        _, incident_id, job_id = await monitored_job(client)
        token = await credential(client)
        key = uuid4()
        path = f"/api/v1/incidents/{incident_id}/actions"
        first = await client.post(path, headers=headers(token, key=key), json=retry_body(job_id))
        assert first.status_code == 201
        assert first.json()["status"] == "applied"
        refreshed = await credential(client)
        replay = await client.post(
            path, headers=headers(refreshed, key=key), json=retry_body(job_id)
        )
        assert replay.json() == first.json()
        recovered = await client.get(
            f"/api/v1/actions/by-request/{key}", headers=headers(refreshed)
        )
        assert recovered.status_code == 200
        assert recovered.json() == first.json()
        other = await credential(client, "other-human")
        denied = await client.get(f"/api/v1/actions/by-request/{key}", headers=headers(other))
        assert denied.status_code == 404
        mismatch = await client.post(
            path,
            headers=headers(refreshed, key=key),
            json={**retry_body(job_id), "rationale": "Changed request"},
        )
        assert mismatch.status_code == 422
        claims = jwt.decode(token, options={"verify_signature": False})
        timeline = await client.get(
            f"/api/v1/incidents/{incident_id}/timeline", headers=headers(refreshed)
        )
        requested = next(
            item for item in timeline.json()["items"] if item["event_type"] == "action.requested"
        )
        assert requested["actor_type"] == "agent"
        assert requested["payload"]["delegation"] == {
            "human_subject": "operator-1",
            "credential_id": claims["jti"],
        }
        async with database_engine.connect() as connection:
            assert await connection.scalar(select(DemoJobModel.retry_count)) == 1
            assert (
                await connection.scalar(select(func.count()).select_from(ActionRequestModel)) == 1
            )


async def test_agent_cannot_propose_close_or_spoof_identity_even_without_tool_wrapper(
    delegated_settings, database_engine, clean_database
):
    async with client_for(delegated_settings, database_engine) as client:
        _, incident_id, job_id = await monitored_job(client)
        resolved = await client.patch(
            f"/api/v1/incidents/{incident_id}",
            headers=headers("human-token", key=uuid4()),
            json={"expected_version": 2, "status": "resolved"},
        )
        assert resolved.status_code == 200
        token = await credential(client)
        close = await client.post(
            f"/api/v1/incidents/{incident_id}/actions",
            headers=headers(token, key=uuid4()),
            json={
                "expected_incident_version": 3,
                "action_type": "request_status_update",
                "parameters": {"target_status": "closed"},
                "rationale": "Attempted close proposal",
            },
        )
        assert close.status_code == 403
        spoofed = await client.post(
            f"/api/v1/incidents/{incident_id}/actions",
            headers=headers(token, key=uuid4()),
            json={**retry_body(job_id, 3), "actor_type": "human"},
        )
        assert spoofed.status_code == 422
        for target in [
            f"/api/v1/actions/{uuid4()}/approve",
            f"/api/v1/actions/{uuid4()}/reject",
            f"/api/v1/demo/jobs/{job_id}/simulate-failure",
            f"/api/v1/demo/incidents/{incident_id}/job",
        ]:
            assert (
                await client.post(target, headers=headers(token, key=uuid4()), json={})
            ).status_code == 403
        assert (
            await client.patch(
                f"/api/v1/incidents/{incident_id}",
                headers=headers(token, key=uuid4()),
                json={"expected_version": 3, "severity": "critical"},
            )
        ).status_code == 403
        async with database_engine.connect() as connection:
            assert (
                await connection.scalar(select(func.count()).select_from(ActionRequestModel)) == 0
            )
            assert await connection.scalar(select(DemoJobModel.retry_count)) == 0


async def test_rotating_agent_credentials_does_not_reset_command_rate_limit(
    delegated_settings, database_engine, clean_database
):
    async with client_for(
        delegated_settings,
        database_engine,
        rate_limiter=FixedWindowRateLimiter(human_limit=3, agent_limit=2),
    ) as client:
        _, incident_id, job_id = await monitored_job(client)
        for expected in [201, 422, 429]:
            token = await credential(client)
            response = await client.post(
                f"/api/v1/incidents/{incident_id}/actions",
                headers=headers(token, key=uuid4()),
                json=retry_body(job_id),
            )
            assert response.status_code == expected


async def test_delegated_idempotency_rows_remain_isolated_by_postgres_rls(
    delegated_settings, database_engine, clean_database
):
    await _ensure_runtime_role(database_engine)
    async with client_for(delegated_settings, database_engine) as client:
        _, incident_id, job_id = await monitored_job(client)
        token = await credential(client)
        response = await client.post(
            f"/api/v1/incidents/{incident_id}/actions",
            headers=headers(token, key=uuid4()),
            json=retry_body(job_id),
        )
        assert response.status_code == 201
        claims = jwt.decode(token, options={"verify_signature": False})
        for subject, count in [(claims["sub"], 1), ("unrelated-agent", 0)]:
            async with database_engine.connect() as connection, connection.begin():
                await connection.execute(text("SET LOCAL ROLE incident_ops_phase2_runtime"))
                await apply_principal_context(
                    connection,
                    Principal(
                        ActorType.AGENT, subject, frozenset({"incidents:read", "actions:request"})
                    ),
                )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(IdempotencyRecordModel)
                    )
                    == count
                )
