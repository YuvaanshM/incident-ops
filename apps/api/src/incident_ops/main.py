"""FastAPI application factory."""

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from incident_ops import __version__
from incident_ops.api.errors import incident_ops_error_handler
from incident_ops.api.routers.health import router as health_router
from incident_ops.application.ports import TokenVerifier
from incident_ops.config import Settings, get_settings
from incident_ops.domain.errors import IncidentOpsError
from incident_ops.infrastructure.database.session import create_database_engine, probe_database

logger = logging.getLogger("incident_ops.http")
ReadinessProbe = Callable[[], Awaitable[bool]]


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    """Build the HTTP adapter with validated configuration and secure defaults."""

    resolved_settings = settings or get_settings()
    is_production = resolved_settings.app_env == "production"
    database_engine = None
    resolved_probe = readiness_probe
    if resolved_probe is None:
        database_engine = create_database_engine(resolved_settings.database_url)

        async def default_probe() -> bool:
            assert database_engine is not None
            return await probe_database(database_engine)

        resolved_probe = default_probe

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if database_engine is not None:
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
    application.add_exception_handler(IncidentOpsError, incident_ops_error_handler)

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
    return application
