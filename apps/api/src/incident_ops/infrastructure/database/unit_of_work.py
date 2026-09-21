"""Explicit SQLAlchemy transaction boundary."""

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, AsyncSessionTransaction

from incident_ops.application.auth import Principal
from incident_ops.infrastructure.database.alerts import SqlAlchemyAlertRepository
from incident_ops.infrastructure.database.repositories import (
    SqlAlchemyActionRequestRepository,
    SqlAlchemyDemoJobRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyIdempotencyRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyOutboxRepository,
)
from incident_ops.infrastructure.database.session import (
    apply_principal_context,
    create_session_factory,
)


class SqlAlchemyUnitOfWork:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        principal: Principal | None = None,
        trusted_scopes: frozenset[str] = frozenset(),
    ) -> None:
        self._session_factory = create_session_factory(engine)
        self._principal = principal
        self._trusted_scopes = trusted_scopes
        self.session: AsyncSession = self._session_factory()
        self._transaction: AsyncSessionTransaction | None = None
        self._finalized = False

        self.incidents = SqlAlchemyIncidentRepository(self.session)
        self.alerts = SqlAlchemyAlertRepository(self.session)
        self.actions = SqlAlchemyActionRequestRepository(self.session)
        self.jobs = SqlAlchemyDemoJobRepository(self.session)
        self.idempotency = SqlAlchemyIdempotencyRepository(self.session)
        self.events = SqlAlchemyEventRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._transaction = await self.session.begin()
        if self._principal is not None:
            database_principal = Principal(
                actor_type=self._principal.actor_type,
                subject=self._principal.subject,
                scopes=self._principal.scopes | self._trusted_scopes,
            )
            await apply_principal_context(self.session, database_principal)
        return self

    async def commit(self) -> None:
        await self.session.commit()
        self._finalized = True

    async def rollback(self) -> None:
        await self.session.rollback()
        self._finalized = True

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception, traceback
        if exception_type is not None or not self._finalized:
            await self.session.rollback()
        await self.session.close()
