"""SQLAlchemy adapter for the small, exact-search historical corpus."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from incident_ops.application.auth import Principal
from incident_ops.application.retrieval import (
    HistoricalRecord,
    HistoricalSource,
    RemediationSuggestion,
    RetrievalRequest,
)
from incident_ops.domain.errors import IdempotencyKeyMismatchError, VersionConflictError
from incident_ops.domain.retrieval import EMBEDDING_TEMPLATE
from incident_ops.infrastructure.database.models import HistoricalIncidentModel as History
from incident_ops.infrastructure.database.models import (
    HistoricalIngestionEventModel,
    IdempotencyRecordModel,
)


@dataclass(frozen=True)
class StoredHistory:
    id: UUID
    version: int
    content_hash: str | None
    provider: str
    model: str
    template: str
    provenance: str


class HistoricalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def corpus(self) -> dict[UUID, StoredHistory]:
        rows = (await self._session.scalars(select(History))).all()
        return {
            row.id: StoredHistory(
                row.id,
                row.version,
                row.content_hash,
                row.embedding_provider,
                row.embedding_model,
                row.embedding_template,
                row.provenance,
            )
            for row in rows
        }

    async def lock(self) -> None:
        # One server-owned lock: fixture corpus is at most twenty rows, replaced atomically.
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": 11001536}
        )

    async def replay(
        self, principal: Principal, key: UUID, fingerprint: str
    ) -> dict[str, object] | None:
        row = await self._session.scalar(
            select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.principal_type == principal.actor_type.value,
                IdempotencyRecordModel.principal_id == principal.subject,
                IdempotencyRecordModel.operation == "historical.ingest",
                IdempotencyRecordModel.idempotency_key == key,
            )
        )
        if row is None:
            return None
        if row.request_fingerprint != fingerprint:
            raise IdempotencyKeyMismatchError()
        if row.response_body is None:
            raise VersionConflictError()
        return dict(row.response_body)

    async def put(
        self,
        record: HistoricalRecord,
        *,
        expected_version: int | None,
        provider: str,
        model: str,
        vector: list[float],
        key: UUID,
        principal: Principal,
    ) -> int:
        version = 1 if expected_version is None else expected_version + 1
        values = record.model_dump(exclude={"id", "status"})
        values.update(
            source_id=str(record.id),
            content_hash=record.content_hash,
            version=version,
            embedding_provider=provider,
            embedding_model=model,
            embedding=vector,
            embedding_template=EMBEDDING_TEMPLATE,
        )
        if expected_version is None:
            self._session.add(History(id=record.id, **values))
            await self._session.flush()
        else:
            result = await self._session.execute(
                update(History)
                .where(
                    History.id == record.id,
                    History.version == expected_version,
                )
                .values(**values, updated_at=func.clock_timestamp())
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise VersionConflictError()
        self._session.add(
            HistoricalIngestionEventModel(
                historical_id=record.id,
                idempotency_key=key,
                actor_type=principal.actor_type.value,
                actor_id=principal.subject,
                version=version,
                content_hash=record.content_hash,
                embedding_provider=provider,
                embedding_model=model,
                embedding_template=EMBEDDING_TEMPLATE,
            )
        )
        await self._session.flush()
        return version

    async def source(self, source_id: UUID) -> HistoricalSource | None:
        row = await self._session.scalar(
            select(History).where(
                History.id == source_id,
                History.provenance == "seeded_demo_data",
                History.embedding_template == EMBEDDING_TEMPLATE,
            )
        )
        return None if row is None else self._source(row)

    async def search(
        self, *, vector: list[float], provider: str, model: str, request: RetrievalRequest
    ) -> list[RemediationSuggestion]:
        distance = History.embedding.cosine_distance(vector)
        statement = select(History, distance.label("distance")).where(
            History.provenance == "seeded_demo_data",
            History.embedding_template == EMBEDDING_TEMPLATE,
            History.embedding_provider == provider,
            History.embedding_model == model,
            distance <= 0.75,
        )
        if request.service is not None:
            statement = statement.where(History.service == request.service)
        if request.severity is not None:
            statement = statement.where(History.severity == request.severity)
        rows = (
            await self._session.execute(
                statement.order_by(distance, History.id).limit(request.limit)
            )
        ).all()
        return [
            RemediationSuggestion(
                **self._source(row).model_dump(),
                similarity_score=max(-1.0, min(1.0, 1.0 - float(score))),
            )
            for row, score in rows
        ]

    @staticmethod
    def _source(row: History) -> HistoricalSource:
        return HistoricalSource.model_validate(
            {
                "source_incident_id": row.id,
                "source_version": row.version,
                "title": row.title,
                "description": row.description,
                "service": row.service,
                "severity": row.severity,
                "resolution_summary": row.resolution_summary,
                "resolution_steps": row.resolution_steps,
                "provenance": row.provenance,
            }
        )
