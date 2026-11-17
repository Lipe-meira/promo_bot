"""Application factory. Everything external (database engine, signing keys) is injected, so tests
exercise the real verification and authorization code with synthetic infrastructure."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from promo_api import workspaces
from promo_api.auth.tokens import (
    ClerkTokenVerifier,
    JwksKeySource,
    Principal,
    SigningKeySource,
    invalid_token,
)
from promo_api.capabilities import workspace_capabilities, workspace_status
from promo_api.db.engine import create_engine
from promo_api.db.models import User
from promo_api.errors import ApiError, ErrorResponse, install_error_handlers
from promo_api.identity import resolve_user
from promo_api.readiness import DEFAULT_MIGRATIONS_DIR, database_is_ready, shipped_heads
from promo_api.schemas import (
    CapabilitiesOut,
    HealthOut,
    MeOut,
    ReadinessOut,
    UserOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceStatusOut,
    WorkspaceUpdate,
)
from promo_api.settings import ApiSettings

_bearer = HTTPBearer(auto_error=False, description="Clerk session token")

_AUTH_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"},
    403: {"model": ErrorResponse, "description": "Authenticated but not admitted"},
}
_WORKSPACE_ERRORS: dict[int | str, dict[str, object]] = {
    **_AUTH_ERRORS,
    404: {"model": ErrorResponse, "description": "Unknown workspace or not a member"},
}


def create_app(
    settings: ApiSettings,
    *,
    engine: AsyncEngine | None = None,
    key_source: SigningKeySource | None = None,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> FastAPI:
    # Read once, at startup: a build without its migrations must not boot into a permanent 503.
    expected_heads = shipped_heads(migrations_dir)
    owned_engine = engine is None
    db_engine = engine if engine is not None else create_engine(settings)
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    verifier = ClerkTokenVerifier(
        issuer=settings.clerk_issuer,
        authorized_parties=settings.clerk_authorized_parties,
        key_source=key_source if key_source is not None else JwksKeySource(settings.jwks_url),
        leeway_seconds=settings.clock_skew_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned_engine:
            await db_engine.dispose()

    app = FastAPI(
        title="Promo Bot API",
        version="0.1.0",
        summary="Control plane: identity, workspaces and capabilities.",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def never_cache(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # Responses depend on the caller's token: no browser, proxy or CDN may store or share them.
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Authorization, Origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Added last so that it wraps everything, including error responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    async def session() -> AsyncIterator[AsyncSession]:
        async with sessions() as db:
            yield db

    Db = Annotated[AsyncSession, Depends(session)]

    async def principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise invalid_token()
        return await verifier.verify(credentials.credentials)

    async def current_user(db: Db, who: Annotated[Principal, Depends(principal)]) -> User:
        return await resolve_user(db, who)

    CurrentUser = Annotated[User, Depends(current_user)]

    @app.get("/healthz", response_model=HealthOut, tags=["health"])
    async def healthz() -> HealthOut:
        return HealthOut(status="ok")

    @app.get(
        "/readyz",
        response_model=ReadinessOut,
        tags=["health"],
        responses={503: {"model": ErrorResponse}},
    )
    async def readyz(db: Db) -> ReadinessOut:
        if not await database_is_ready(db, expected_heads):
            raise ApiError(503, "NOT_READY", "The database is not ready.")
        return ReadinessOut(status="ready", database="ok")

    @app.get("/v1/me", response_model=MeOut, tags=["identity"], responses=_AUTH_ERRORS)
    async def me(db: Db, user: CurrentUser) -> MeOut:
        return MeOut(
            user=UserOut(id=user.id, email=user.email),
            workspaces=await workspaces.list_for_user(db, user.id),
        )

    @app.post(
        "/v1/workspaces",
        response_model=WorkspaceOut,
        status_code=201,
        tags=["workspaces"],
        responses={**_AUTH_ERRORS, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    async def create_workspace(db: Db, user: CurrentUser, body: WorkspaceCreate) -> WorkspaceOut:
        return await workspaces.create(db, user.id, body)

    @app.get(
        "/v1/workspaces/{workspace_id}",
        response_model=WorkspaceOut,
        tags=["workspaces"],
        responses=_WORKSPACE_ERRORS,
    )
    async def get_workspace(db: Db, user: CurrentUser, workspace_id: UUID) -> WorkspaceOut:
        return await workspaces.get_for_user(db, user.id, workspace_id)

    @app.patch(
        "/v1/workspaces/{workspace_id}",
        response_model=WorkspaceOut,
        tags=["workspaces"],
        responses={
            **_WORKSPACE_ERRORS,
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    async def update_workspace(
        db: Db, user: CurrentUser, workspace_id: UUID, body: WorkspaceUpdate
    ) -> WorkspaceOut:
        return await workspaces.update_for_user(db, user.id, workspace_id, body)

    @app.get(
        "/v1/workspaces/{workspace_id}/status",
        response_model=WorkspaceStatusOut,
        tags=["workspaces"],
        responses=_WORKSPACE_ERRORS,
    )
    async def get_status(db: Db, user: CurrentUser, workspace_id: UUID) -> WorkspaceStatusOut:
        workspace = await workspaces.get_for_user(db, user.id, workspace_id)
        return workspace_status(workspace.id)

    @app.get(
        "/v1/workspaces/{workspace_id}/capabilities",
        response_model=CapabilitiesOut,
        tags=["workspaces"],
        responses=_WORKSPACE_ERRORS,
    )
    async def get_capabilities(db: Db, user: CurrentUser, workspace_id: UUID) -> CapabilitiesOut:
        workspace = await workspaces.get_for_user(db, user.id, workspace_id)
        return workspace_capabilities(workspace.id)

    return app
