"""FastAPI application factory."""

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from incident_ops import __version__
from incident_ops.api.errors import (
    incident_ops_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from incident_ops.api.routers.actions import demo_router
from incident_ops.api.routers.actions import router as actions_router
from incident_ops.api.routers.agent_sessions import router as agent_sessions_router
from incident_ops.api.routers.alerts import router as alerts_router
from incident_ops.api.routers.health import router as health_router
from incident_ops.api.routers.incidents import router as incidents_router
from incident_ops.api.routers.investigations import router as investigations_router
from incident_ops.api.routers.retrieval import router as retrieval_router
from incident_ops.api.routers.slack import router as slack_router
from incident_ops.application.investigation import InvestigationModel
from incident_ops.application.ports import CommandObserver, RateLimiter, TokenVerifier
from incident_ops.application.retrieval import EmbeddingPort
from incident_ops.application.services.actions import ActionCommandService, ActionQueryService
from incident_ops.application.services.agent_sessions import AgentSessionService
from incident_ops.application.services.alert_ingestion import (
    AlertIngestionService,
    AlertQueryService,
)
from incident_ops.application.services.incidents import IncidentCommandService, IncidentQueryService
from incident_ops.application.services.investigations import InvestigationService
from incident_ops.application.services.retrieval import RetrievalService
from incident_ops.application.services.slack_intake import SlackIntakeService
from incident_ops.config import Settings, get_settings
from incident_ops.domain.errors import IncidentOpsError
from incident_ops.infrastructure.auth.agent import AgentJwtCodec, HumanOrAgentTokenVerifier
from incident_ops.infrastructure.auth.alert_webhook import AlertWebhookVerifier
from incident_ops.infrastructure.auth.development import DevelopmentTokenVerifier
from incident_ops.infrastructure.auth.oidc import OidcTokenVerifier
from incident_ops.infrastructure.auth.slack import SlackSignatureVerifier
from incident_ops.infrastructure.database.session import create_database_engine, probe_database
from incident_ops.infrastructure.embeddings import build_embedder
from incident_ops.infrastructure.investigation_model import build_investigator
from incident_ops.infrastructure.rate_limit import FixedWindowRateLimiter

logger = logging.getLogger("incident_ops.http")
ReadinessProbe = Callable[[], Awaitable[bool]]


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    readiness_probe: ReadinessProbe | None = None,
    database_engine: AsyncEngine | None = None,
    command_observer: CommandObserver | None = None,
    rate_limiter: RateLimiter | None = None,
    embedding_provider: EmbeddingPort | None = None,
    investigation_model: InvestigationModel | None = None,
) -> FastAPI:
    """Build the HTTP adapter with validated configuration and secure defaults."""

    resolved_settings = settings or get_settings()
    is_production = resolved_settings.app_env == "production"
    if (
        token_verifier is None
        and resolved_settings.app_env == "development"
        and resolved_settings.development_auth_enabled
    ):
        token_verifier = DevelopmentTokenVerifier()
    elif token_verifier is None and resolved_settings.app_env == "production":
        assert resolved_settings.oidc_issuer_url is not None
        assert resolved_settings.oidc_audience is not None
        assert resolved_settings.oidc_jwks_url is not None
        token_verifier = OidcTokenVerifier(
            issuer_url=str(resolved_settings.oidc_issuer_url),
            audience=resolved_settings.oidc_audience,
            jwks_url=str(resolved_settings.oidc_jwks_url),
            actor_type_claim=resolved_settings.oidc_actor_type_claim,
        )
    owns_database_engine = database_engine is None
    agent_codec = None
    if resolved_settings.agent_tokens_enabled:
        assert resolved_settings.agent_token_issuer is not None
        assert resolved_settings.agent_token_audience is not None
        assert resolved_settings.agent_token_signing_key is not None
        agent_codec = AgentJwtCodec(
            issuer=resolved_settings.agent_token_issuer,
            audience=resolved_settings.agent_token_audience,
            signing_key=resolved_settings.agent_token_signing_key,
        )
    token_verifier = HumanOrAgentTokenVerifier(token_verifier, agent_codec)
    resolved_probe = readiness_probe
    if database_engine is None:
        database_engine = create_database_engine(resolved_settings.database_url)
    if resolved_probe is None:

        async def default_probe() -> bool:
            assert database_engine is not None
            return await probe_database(database_engine)

        resolved_probe = default_probe

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if database_engine is not None and owns_database_engine:
            await database_engine.dispose()

    application = FastAPI(
        title="Incident Ops API",
        version=__version__,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.token_verifier = token_verifier
    application.state.readiness_probe = resolved_probe
    application.state.database_engine = database_engine
    application.state.rate_limiter = rate_limiter or FixedWindowRateLimiter()
    application.state.agent_exchange_limiter = FixedWindowRateLimiter(human_limit=30)
    application.state.agent_session_service = AgentSessionService(agent_codec)
    application.state.slack_ingress_limiter = FixedWindowRateLimiter(human_limit=120)
    application.state.slack_source_limiter = FixedWindowRateLimiter(human_limit=30)
    application.state.slack_signature_verifier = SlackSignatureVerifier(
        resolved_settings.slack_intake.signing_secret
    )
    application.state.alert_ingress_limiter = FixedWindowRateLimiter(human_limit=120)
    application.state.alert_source_limiter = FixedWindowRateLimiter(human_limit=30)
    application.state.alert_signature_verifier = AlertWebhookVerifier(
        resolved_settings.alert_intake
    )
    application.state.alert_ingestion_service = AlertIngestionService(
        database_engine, resolved_settings.alert_intake, observer=command_observer
    )
    application.state.alert_query_service = AlertQueryService(database_engine)
    application.state.slack_intake_service = SlackIntakeService(
        database_engine, resolved_settings.slack_intake, observer=command_observer
    )
    application.state.retrieval_limiter = FixedWindowRateLimiter(human_limit=20, agent_limit=10)
    application.state.retrieval_service = RetrievalService(
        database_engine, embedding_provider or build_embedder(resolved_settings)
    )
    application.state.incident_query_service = IncidentQueryService(database_engine)
    application.state.incident_command_service = IncidentCommandService(
        database_engine,
        observer=command_observer,
    )
    application.state.action_query_service = ActionQueryService(database_engine)
    application.state.action_command_service = ActionCommandService(
        database_engine,
        observer=command_observer,
        demo_enabled=not is_production,
    )
    application.state.investigation_limiter = FixedWindowRateLimiter(human_limit=5, agent_limit=5)
    application.state.investigation_service = InvestigationService(
        database_engine,
        investigation_model or build_investigator(resolved_settings),
        retrieval=application.state.retrieval_service,
        actions=application.state.action_command_service,
        observer=command_observer,
    )
    application.add_exception_handler(IncidentOpsError, incident_ops_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(Exception, internal_error_handler)

    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=resolved_settings.allowed_hosts,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_strings,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-CSRF-Token",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    @application.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )
        return response

    application.include_router(health_router)
    application.include_router(incidents_router)
    application.include_router(retrieval_router)
    application.include_router(investigations_router)
    application.include_router(actions_router)
    application.include_router(agent_sessions_router)
    application.include_router(slack_router)
    application.include_router(alerts_router)
    if not is_production:
        application.include_router(demo_router)
    return application
