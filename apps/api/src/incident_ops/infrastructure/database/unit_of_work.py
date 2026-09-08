"""Explicit SQLAlchemy transaction boundary."""

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, AsyncSessionTransaction

from incident_ops.application.auth import Principal
from incident_ops.infrastructure.database.repositories import (
    SqlAlchemyActionRequestRepository,
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
    def __init__(self, engine: AsyncEngine, *, principal: Principal | None = None) -> None:
        self._session_factory = create_session_factory(engine)
        self._principal = principal
        self.session: AsyncSession = self._session_factory()
        self._transaction: AsyncSessionTransaction | None = None
        self._finalized = False

        self.incidents = SqlAlchemyIncidentRepository(self.session)
        self.actions = SqlAlchemyActionRequestRepository(self.session)
        self.idempotency = SqlAlchemyIdempotencyRepository(self.session)
        self.events = SqlAlchemyEventRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._transaction = await self.session.begin()
        if self._principal is not None:
            await apply_principal_context(self.session, self._principal)
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
