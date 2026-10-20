"""Authorized advisory retrieval and transactional synthetic-corpus ingestion."""

import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal, require_scope
from incident_ops.application.ports import CommandObserver
from incident_ops.application.retrieval import (
    EmbeddingPort,
    HistoricalRecord,
    HistoricalSource,
    RemediationResponse,
    RetrievalRequest,
    embedding_text,
    query_text,
)
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import (
    AuthorizationDeniedError,
    ResourceNotFoundError,
    RetrievalUnavailableError,
    VersionConflictError,
)
from incident_ops.domain.retrieval import EMBEDDING_TEMPLATE, validate_vector
from incident_ops.infrastructure.database.retrieval import HistoricalRepository, StoredHistory
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

LOADER = Principal(
    ActorType.SYSTEM, "historical-fixture-loader", frozenset({"retrieval:read", "retrieval:write"})
)
INGEST_NAMESPACE = UUID("a1100000-0000-4000-8000-000000000000")


def _authorize_read(principal: Principal) -> None:
    if principal.actor_type not in (ActorType.HUMAN, ActorType.AGENT):
        raise AuthorizationDeniedError()
    require_scope(principal, "incidents:read")


def _compatible(row: StoredHistory, embedder: EmbeddingPort) -> bool:
    return (row.provider, row.model, row.template, row.provenance) == (
        embedder.provider,
        embedder.model,
        EMBEDDING_TEMPLATE,
        "seeded_demo_data",
    )


def _check_corpus(corpus: dict[UUID, StoredHistory], embedder: EmbeddingPort) -> bool:
    seeded = [row for row in corpus.values() if row.provenance == "seeded_demo_data"]
    if any(not _compatible(row, embedder) for row in seeded):
        raise RetrievalUnavailableError()
    return bool(seeded)


class RetrievalService:
    def __init__(self, engine: AsyncEngine, embedder: EmbeddingPort | None) -> None:
        self._engine = engine
        self._embedder = embedder

    async def source(self, *, principal: Principal, source_id: UUID) -> HistoricalSource:
        _authorize_read(principal)
        async with SqlAlchemyUnitOfWork(
            self._engine, principal=principal, trusted_scopes=frozenset({"retrieval:read"})
        ) as uow:
            source = await HistoricalRepository(uow.session).source(source_id)
            if source is None:
                raise ResourceNotFoundError()
            return source

    async def suggest(
        self, *, principal: Principal, incident_id: UUID, request: RetrievalRequest
    ) -> RemediationResponse:
        _authorize_read(principal)
        request = RetrievalRequest.model_validate(request.model_dump())
        async with SqlAlchemyUnitOfWork(
            self._engine, principal=principal, trusted_scopes=frozenset({"retrieval:read"})
        ) as uow:
            incident = await uow.incidents.get(incident_id)
            if incident is None:
                raise ResourceNotFoundError()
            if self._embedder is None:
                raise RetrievalUnavailableError()
            corpus = await HistoricalRepository(uow.session).corpus()
            has_corpus = _check_corpus(corpus, self._embedder)
        mode: Literal["deterministic_demo", "semantic"] = (
            "deterministic_demo" if self._embedder.provider == "deterministic" else "semantic"
        )
        if not has_corpus:
            return RemediationResponse(
                incident_id=incident.id,
                incident_version=incident.version,
                retrieval_mode=mode,
                status="empty_corpus",
                suggestions=[],
            )
        text = query_text(
            title=incident.title,
            description=request.query or incident.description,
            service=incident.service,
            severity=incident.severity,
        )
        try:
            vector = validate_vector(await self._embedder.embed(text))
        except (ValueError, OverflowError) as error:
            raise RetrievalUnavailableError() from error
        async with SqlAlchemyUnitOfWork(
            self._engine, principal=principal, trusted_scopes=frozenset({"retrieval:read"})
        ) as uow:
            current = await uow.incidents.get(incident_id)
            if current is None or current.version != incident.version:
                raise VersionConflictError()
            repository = HistoricalRepository(uow.session)
            _check_corpus(await repository.corpus(), self._embedder)
            suggestions = await repository.search(
                vector=vector,
                provider=self._embedder.provider,
                model=self._embedder.model,
                request=request,
            )
        return RemediationResponse(
            incident_id=incident.id,
            incident_version=incident.version,
            retrieval_mode=mode,
            status="matched" if suggestions else "no_matches",
            suggestions=suggestions,
        )


class HistoricalIngestionService:
    def __init__(
        self,
        engine: AsyncEngine,
        embedder: EmbeddingPort | None,
        *,
        observer: CommandObserver | None = None,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._observer = observer

    async def _observe(self, point: str) -> None:
        if self._observer is not None:
            await self._observer.reached(point)

    async def ingest(
        self,
        *,
        principal: Principal,
        records: list[HistoricalRecord],
        idempotency_key: UUID | None = None,
    ) -> dict[str, object]:
        if principal.actor_type is not ActorType.SYSTEM or principal.subject != LOADER.subject:
            raise AuthorizationDeniedError()
        require_scope(principal, "retrieval:write")
        require_scope(principal, "retrieval:read")
        records = [HistoricalRecord.model_validate(record.model_dump()) for record in records]
        ids = {record.id for record in records}
        if not 1 <= len(records) <= 20 or len(ids) != len(records):
            raise ValueError("historical manifest must contain one to twenty distinct records")
        if self._embedder is None:
            raise RetrievalUnavailableError()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "sources": sorted((str(record.id), record.content_hash) for record in records),
                    "provider": self._embedder.provider,
                    "model": self._embedder.model,
                    "template": EMBEDDING_TEMPLATE,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        key = idempotency_key or uuid5(INGEST_NAMESPACE, fingerprint)
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            repository = HistoricalRepository(uow.session)
            before = await repository.corpus()
            self._require_complete_switch(before, ids)
            replay = await repository.replay(principal, key, fingerprint)
            if replay is not None:
                return replay
        changed = [
            record
            for record in records
            if record.id not in before
            or before[record.id].content_hash != record.content_hash
            or not _compatible(before[record.id], self._embedder)
        ]
        vectors = {}
        for record in changed:
            try:
                vectors[record.id] = validate_vector(
                    await self._embedder.embed(embedding_text(record))
                )
            except (ValueError, OverflowError) as error:
                raise RetrievalUnavailableError() from error
        await self._observe("historical.ingest.after_embed")
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            repository = HistoricalRepository(uow.session)
            await repository.lock()
            reservation = await uow.idempotency.reserve(
                principal=principal, operation="historical.ingest", key=key, fingerprint=fingerprint
            )
            if not reservation.created:
                assert reservation.response_body is not None
                return dict(reservation.response_body)
            current = await repository.corpus()
            self._require_complete_switch(current, ids)
            # Compare the whole small corpus snapshot, including unchanged rows and new inserts.
            if current != before:
                raise VersionConflictError()
            versions = {
                str(record.id): current[record.id].version
                for record in records
                if record.id in current
            }
            for record in changed:
                previous = before.get(record.id)
                versions[str(record.id)] = await repository.put(
                    record,
                    expected_version=None if previous is None else previous.version,
                    provider=self._embedder.provider,
                    model=self._embedder.model,
                    vector=vectors[record.id],
                    key=key,
                    principal=principal,
                )
            result: dict[str, object] = {
                "changed": len(changed),
                "sources": versions,
                "provenance": "seeded_demo_data",
            }
            await uow.idempotency.complete(
                record_id=reservation.record_id, response_status=200, response_body=result
            )
            await self._observe("historical.ingest.before_commit")
            await uow.commit()
            return result

    def _require_complete_switch(self, corpus: dict[UUID, StoredHistory], ids: set[UUID]) -> None:
        assert self._embedder is not None
        if len(set(corpus) | ids) > 20:
            raise RetrievalUnavailableError()
        if any(
            row.provenance == "seeded_demo_data"
            and not _compatible(row, self._embedder)
            and row.id not in ids
            for row in corpus.values()
        ):
            raise RetrievalUnavailableError()
