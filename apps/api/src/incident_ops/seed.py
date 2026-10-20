"""Deterministic additive development fixtures."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.retrieval import HistoricalRecord
from incident_ops.application.services.retrieval import LOADER, HistoricalIngestionService
from incident_ops.config import Settings, get_settings
from incident_ops.domain.enums import ActionType, ActorType, IncidentSeverity
from incident_ops.domain.policies import ActionPolicyInput, evaluate_action_policy
from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    DemoJobModel,
    IncidentModel,
)
from incident_ops.infrastructure.database.session import create_database_engine
from incident_ops.infrastructure.embeddings import build_embedder

INCIDENT_IDS = (
    UUID("10000000-0000-4000-8000-000000000001"),
    UUID("10000000-0000-4000-8000-000000000002"),
    UUID("10000000-0000-4000-8000-000000000003"),
)
JOB_IDS = (
    UUID("20000000-0000-4000-8000-000000000001"),
    UUID("20000000-0000-4000-8000-000000000002"),
)
ACTION_IDS = (
    UUID("30000000-0000-4000-8000-000000000001"),
    UUID("30000000-0000-4000-8000-000000000002"),
    UUID("30000000-0000-4000-8000-000000000003"),
)


async def seed_database(engine: AsyncEngine, *, reset: bool) -> None:
    """Add stable synthetic fixtures; optionally remove only fixture-owned rows first."""

    decided_at = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    pending_policy = evaluate_action_policy(
        ActionPolicyInput(
            action_type=ActionType.REQUEST_STATUS_UPDATE,
            incident_severity=IncidentSeverity.MEDIUM,
            requested_by=ActorType.AGENT,
            parameters_valid=True,
            retry_count=0,
        )
    )
    async with engine.begin() as connection:
        if reset:
            await connection.execute(
                delete(ActionRequestModel).where(ActionRequestModel.id.in_(ACTION_IDS))
            )
            await connection.execute(delete(DemoJobModel).where(DemoJobModel.id.in_(JOB_IDS)))
            await connection.execute(
                delete(IncidentModel).where(IncidentModel.id.in_(INCIDENT_IDS))
            )

        await connection.execute(
            insert(IncidentModel)
            .values(
                [
                    {
                        "id": INCIDENT_IDS[0],
                        "title": "Synthetic checkout latency",
                        "description": "Demo-only latency incident for the automatic retry path.",
                        "service": "checkout",
                        "severity": "low",
                        "status": "investigating",
                        "version": 1,
                        "created_by_type": "system",
                        "created_by_id": "fixture-loader",
                    },
                    {
                        "id": INCIDENT_IDS[1],
                        "title": "Synthetic notification failures",
                        "description": "Demo-only incident with a pending human decision.",
                        "service": "notifications",
                        "severity": "medium",
                        "status": "open",
                        "version": 1,
                        "created_by_type": "system",
                        "created_by_id": "fixture-loader",
                    },
                    {
                        "id": INCIDENT_IDS[2],
                        "title": "Synthetic rejected retry",
                        "description": "Demo-only incident showing the hard retry cap.",
                        "service": "billing",
                        "severity": "high",
                        "status": "investigating",
                        "version": 1,
                        "created_by_type": "system",
                        "created_by_id": "fixture-loader",
                    },
                ]
            )
            .on_conflict_do_nothing(index_elements=[IncidentModel.id])
        )
        await connection.execute(
            insert(DemoJobModel)
            .values(
                [
                    {
                        "id": JOB_IDS[0],
                        "incident_id": INCIDENT_IDS[0],
                        "stable_key": "checkout-reindex",
                        "name": "Rebuild checkout search index",
                        "status": "failed",
                        "retry_count": 0,
                        "version": 1,
                        "last_error": "Synthetic timeout",
                    },
                    {
                        "id": JOB_IDS[1],
                        "incident_id": INCIDENT_IDS[2],
                        "stable_key": "billing-reconciliation",
                        "name": "Run billing reconciliation",
                        "status": "failed",
                        "retry_count": 2,
                        "version": 3,
                        "last_error": "Synthetic retry cap reached",
                    },
                ]
            )
            .on_conflict_do_nothing(index_elements=[DemoJobModel.id])
        )
        action_records = (
            {
                "id": ACTION_IDS[0],
                "incident_id": INCIDENT_IDS[1],
                "action_type": "request_status_update",
                "status": "pending",
                "execution_mode": "human",
                "requested_by_type": "agent",
                "requested_by_id": "fixture-agent",
                "idempotency_key": UUID("40000000-0000-4000-8000-000000000001"),
                "request_fingerprint": "a" * 64,
                "base_incident_version": 1,
                "parameters": {"target_status": "investigating"},
                "policy_version": pending_policy.policy_version,
                "policy_snapshot": pending_policy.snapshot,
            },
            {
                "id": ACTION_IDS[1],
                "incident_id": INCIDENT_IDS[0],
                "action_type": "request_status_update",
                "status": "applied",
                "execution_mode": "human_approved",
                "requested_by_type": "human",
                "requested_by_id": "fixture-operator",
                "idempotency_key": UUID("40000000-0000-4000-8000-000000000002"),
                "request_fingerprint": "b" * 64,
                "base_incident_version": 1,
                "parameters": {"target_status": "investigating"},
                "policy_version": "2026-09-05.v1",
                "policy_snapshot": {"decision": "require_approval"},
                "outcome_code": "status_updated",
                "outcome_payload": {},
                "decided_by_type": "human",
                "decided_by_id": "fixture-reviewer",
                "decided_at": decided_at,
            },
            {
                "id": ACTION_IDS[2],
                "incident_id": INCIDENT_IDS[2],
                "action_type": "retry_failed_job",
                "status": "rejected",
                "execution_mode": "human",
                "requested_by_type": "agent",
                "requested_by_id": "fixture-agent",
                "idempotency_key": UUID("40000000-0000-4000-8000-000000000003"),
                "request_fingerprint": "c" * 64,
                "base_incident_version": 1,
                "parameters": {"job_id": str(JOB_IDS[1])},
                "policy_version": "2026-09-05.v1",
                "policy_snapshot": {"decision": "reject"},
                "outcome_code": "retry_limit_reached",
                "outcome_payload": {},
                "decided_by_type": "system",
                "decided_by_id": "policy-engine",
                "decided_at": decided_at,
            },
        )
        for action_record in action_records:
            await connection.execute(
                insert(ActionRequestModel)
                .values(**action_record)
                .on_conflict_do_nothing(index_elements=[ActionRequestModel.id])
            )


def load_historical_fixtures() -> list[HistoricalRecord]:
    path = Path(__file__).resolve().parents[4] / "fixtures/historical-incidents/incidents.json"
    records = [HistoricalRecord.model_validate(item) for item in json.loads(path.read_text())]
    if not 10 <= len(records) <= 20 or len({record.id for record in records}) != len(records):
        raise ValueError("curated history must contain ten to twenty distinct sources")
    return records


async def seed_historical(settings: Settings) -> dict[str, object]:
    engine = create_database_engine(settings.database_url)
    try:
        return await HistoricalIngestionService(engine, build_embedder(settings)).ingest(
            principal=LOADER, records=load_historical_fixtures()
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load deterministic Incident Ops fixtures")
    parser.add_argument("corpus", nargs="?", choices=["historical"])
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete fixture-owned development data before loading",
    )
    return parser


def ensure_reset_allowed(settings: Settings, *, reset: bool) -> None:
    if reset and (settings.app_env != "development" or not settings.allow_development_reset):
        raise SystemExit(
            "Fixture reset refused: require APP_ENV=development and ALLOW_DEVELOPMENT_RESET=true"
        )


async def _run(reset: bool) -> None:
    settings = get_settings()
    ensure_reset_allowed(settings, reset=reset)
    engine = create_database_engine(settings.database_url)
    try:
        await seed_database(engine, reset=reset)
    finally:
        await engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    if args.corpus == "historical":
        if args.reset:
            raise SystemExit("Historical ingestion is versioned and does not support reset")
        result = asyncio.run(seed_historical(get_settings()))
        print(json.dumps(result, sort_keys=True))
        return
    asyncio.run(_run(args.reset))
    mode = "reset then additive" if args.reset else "additive"
    print(f"Fixture mode: {mode}. Phase 2 synthetic records loaded.")


if __name__ == "__main__":
    main()
