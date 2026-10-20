"""Loopback-only real API for deterministic browser acceptance; disposable DB only."""

import asyncio
import json
import os
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from incident_ops.application.investigation import Decision, ModelTurn
from incident_ops.config import Settings
from incident_ops.infrastructure.monitoring_config import AlertIntakeSettings
from incident_ops.main import create_app
from incident_ops.seed import seed_historical


class BrowserInvestigator:
    """Deterministic owned model port; never enabled by application settings."""

    provider = "test"
    model = "browser-scripted"

    async def next(self, context):
        data = json.loads(context)
        observed = data["untrusted_observations"]
        for name in ("timeline", "alerts", "precedent"):
            if name not in observed:
                return ModelTurn(
                    Decision(tool=name, findings=[], job_id=None, target_status=None), 100, 100
                )
        incident = observed["incident"]
        evidence = incident["evidence"][0]
        action = data["allow_actions"] and bool(incident["job_ids"])
        return ModelTurn(
            Decision.model_validate(
                {
                    "tool": "request_retry_failed_job" if action else "finish",
                    "findings": [
                        {"source_id": evidence["source_id"], "quote": evidence["text"][:100]}
                    ],
                    "job_id": incident["job_ids"][0] if action else None,
                    "target_status": None,
                }
            ),
            100,
            100,
        )


def main() -> None:
    os.chdir(Path(__file__).resolve().parents[3])
    configured = os.getenv("TEST_DATABASE_URL") or Settings().database_url.get_secret_value()  # type: ignore[call-arg]
    url = make_url(configured)
    database = url.database or ""
    if not database.endswith("_test"):
        url = url.set(database=f"{database}_test")
    if not url.database or not url.database.endswith("_test"):
        raise RuntimeError("Browser acceptance requires a disposable test database")
    database_url = url.render_as_string(hide_password=False)
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url=SecretStr(database_url),
        development_auth_enabled=True,
        embedding_provider="deterministic",
        embedding_model="hash-bow-v1",
        agent_tokens_enabled=True,
        agent_token_issuer="https://agents.example.test",
        agent_token_audience="incident-ops-agent-api",
        agent_token_signing_key=SecretStr("phase10-test-only-" * 4),
        alert_intake=AlertIntakeSettings.model_validate(
            {
                "enabled": True,
                "sources": [
                    {
                        "source": "browser-monitor",
                        "services": ["checkout"],
                        "keys": [
                            {
                                "key_id": "browser-key",
                                "signing_secret": "phase10-monitor-test-only-" * 2,
                            }
                        ],
                    }
                ],
            }
        ),
    )
    asyncio.run(seed_historical(settings))
    uvicorn.run(
        create_app(settings, investigation_model=BrowserInvestigator()),
        host="127.0.0.1",
        port=8108,
        access_log=False,
    )


if __name__ == "__main__":
    main()
