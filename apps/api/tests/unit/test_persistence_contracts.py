"""Application persistence ports remain explicit and intention revealing."""

from typing import get_type_hints

from incident_ops.application.ports import (
    ActionRequestRepository,
    EventRepository,
    IdempotencyRepository,
    IncidentRepository,
    OutboxRepository,
    TokenVerifier,
    UnitOfWork,
)


def test_repository_ports_expose_intentions_not_generic_crud() -> None:
    expected_methods = {
        IncidentRepository: {"transition_status"},
        ActionRequestRepository: {"lock_pending"},
        IdempotencyRepository: {"reserve"},
        EventRepository: {"append"},
        OutboxRepository: {"enqueue"},
    }

    for repository, expected in expected_methods.items():
        public_methods = {
            name
            for name in repository.__dict__
            if not name.startswith("_") and callable(getattr(repository, name))
        }
        assert expected <= public_methods
        assert {"create", "update", "delete", "save"}.isdisjoint(public_methods)


def test_unit_of_work_owns_repository_ports_and_transaction_methods() -> None:
    hints = get_type_hints(UnitOfWork)

    assert hints["incidents"] is IncidentRepository
    assert hints["actions"] is ActionRequestRepository
    assert hints["idempotency"] is IdempotencyRepository
    assert hints["events"] is EventRepository
    assert hints["outbox"] is OutboxRepository
    assert {"commit", "rollback"} <= UnitOfWork.__dict__.keys()


def test_token_verifier_is_provider_neutral() -> None:
    assert "verify" in TokenVerifier.__dict__
    assert list(get_type_hints(TokenVerifier.verify)) == ["token", "return"]
