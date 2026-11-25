"""Application factory. Everything external (database engine, signing keys) is injected, so tests
exercise the real verification and authorization code with synthetic infrastructure."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from promo_api import local_agent, telegram_channels, workspaces
from promo_api.auth.clerk_backend import ClerkBackendClient, PrimaryEmailVerifier
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
from promo_api.limits import BodySizeLimit
from promo_api.readiness import (
    DEFAULT_MIGRATIONS_DIR,
    CachedReadiness,
    database_is_ready,
    shipped_heads,
)
from promo_api.schemas import (
    CHAT_ID_PATTERN,
    AgentCommandBody,
    AgentPairBody,
    AgentPairedOut,
    AgentStatusOut,
    AgentSync,
    AgentSyncOut,
    CapabilitiesOut,
    ChatIdBody,
    HealthOut,
    MeOut,
    PairingCodeOut,
    ReadinessOut,
    TelegramChannelsOut,
    UserOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceStatusOut,
    WorkspaceUpdate,
)
from promo_api.settings import ApiSettings

_bearer = HTTPBearer(auto_error=False, description="Clerk session token")
_agent_bearer = HTTPBearer(
    auto_error=False, scheme_name="AgentToken", description="Token of a paired local agent"
)

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
    primary_email: PrimaryEmailVerifier | None = None,
    readiness: CachedReadiness | None = None,
) -> FastAPI:
    # Read once, at startup: a build without its migrations must not boot into a permanent 503.
    expected_heads = shipped_heads(migrations_dir)
    owned_engine = engine is None
    db_engine = engine if engine is not None else create_engine(settings)
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    readiness = readiness if readiness is not None else CachedReadiness()
    email_verifier = (
        primary_email
        if primary_email is not None
        else ClerkBackendClient(settings.clerk_secret_key)
    )
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
        # A public URL publishes neither the interactive docs nor the machine-readable contract.
        docs_url="/docs" if settings.environment != "production" else None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
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

    # Bounds the body before FastAPI parses it, which happens before authentication.
    app.add_middleware(BodySizeLimit)

    # Added last so that it wraps these, including their refusals. (Starlette's outermost layer
    # answers an *unhandled* exception, outside CORS: those 500s carry no CORS headers.)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
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
        return await resolve_user(db, who, email_verifier)

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
        # Cached on purpose: this public endpoint would otherwise keep a scale-to-zero database
        # awake for anyone who knows the URL.
        if not await readiness.check(lambda: database_is_ready(db, expected_heads)):
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

    channel_errors: dict[int | str, dict[str, object]] = {
        **_WORKSPACE_ERRORS,
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    }
    ChannelId = Annotated[str, PathParam(min_length=5, max_length=19, pattern=CHAT_ID_PATTERN)]

    @app.get(
        "/v1/workspaces/{workspace_id}/telegram-channels",
        response_model=TelegramChannelsOut,
        tags=["telegram"],
        responses=_WORKSPACE_ERRORS,
    )
    async def get_telegram_channels(
        db: Db, user: CurrentUser, workspace_id: UUID
    ) -> TelegramChannelsOut:
        return await telegram_channels.get_for_user(db, user.id, workspace_id)

    @app.post(
        "/v1/workspaces/{workspace_id}/telegram-channels/sources",
        response_model=TelegramChannelsOut,
        status_code=201,
        tags=["telegram"],
        responses=channel_errors,
    )
    async def add_source_channel(
        db: Db, user: CurrentUser, workspace_id: UUID, body: ChatIdBody
    ) -> TelegramChannelsOut:
        return await telegram_channels.add_source(db, user.id, workspace_id, body.chat_id)

    @app.delete(
        "/v1/workspaces/{workspace_id}/telegram-channels/sources/{chat_id}",
        response_model=TelegramChannelsOut,
        tags=["telegram"],
        responses=channel_errors,
    )
    async def remove_source_channel(
        db: Db, user: CurrentUser, workspace_id: UUID, chat_id: ChannelId
    ) -> TelegramChannelsOut:
        return await telegram_channels.remove_source(db, user.id, workspace_id, chat_id)

    @app.put(
        "/v1/workspaces/{workspace_id}/telegram-channels/destination",
        response_model=TelegramChannelsOut,
        tags=["telegram"],
        responses=channel_errors,
    )
    async def set_destination_channel(
        db: Db, user: CurrentUser, workspace_id: UUID, body: ChatIdBody
    ) -> TelegramChannelsOut:
        return await telegram_channels.set_destination(db, user.id, workspace_id, body.chat_id)

    @app.get(
        "/v1/workspaces/{workspace_id}/agent",
        response_model=AgentStatusOut,
        tags=["agent"],
        responses=_WORKSPACE_ERRORS,
    )
    async def get_agent(db: Db, user: CurrentUser, workspace_id: UUID) -> AgentStatusOut:
        return await local_agent.get_for_user(db, user.id, workspace_id)

    @app.post(
        "/v1/workspaces/{workspace_id}/agent/pairing-code",
        response_model=PairingCodeOut,
        status_code=201,
        tags=["agent"],
        responses=_WORKSPACE_ERRORS,
    )
    async def create_pairing_code(db: Db, user: CurrentUser, workspace_id: UUID) -> PairingCodeOut:
        return await local_agent.create_pairing_code(db, user.id, workspace_id)

    @app.delete(
        "/v1/workspaces/{workspace_id}/agent",
        response_model=AgentStatusOut,
        tags=["agent"],
        responses=_WORKSPACE_ERRORS,
    )
    async def remove_agent(db: Db, user: CurrentUser, workspace_id: UUID) -> AgentStatusOut:
        return await local_agent.remove(db, user.id, workspace_id)

    @app.post(
        "/v1/workspaces/{workspace_id}/agent/commands",
        response_model=AgentStatusOut,
        tags=["agent"],
        responses={
            **_WORKSPACE_ERRORS,
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    async def send_agent_command(
        db: Db, user: CurrentUser, workspace_id: UUID, body: AgentCommandBody
    ) -> AgentStatusOut:
        return await local_agent.send_command(db, user.id, workspace_id, body.action)

    # --- called by the local agent, not by a browser: its own token, never a Clerk session ----

    @app.post(
        "/v1/agent/pair",
        response_model=AgentPairedOut,
        status_code=201,
        tags=["agent"],
        responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    async def pair_agent(db: Db, body: AgentPairBody) -> AgentPairedOut:
        return await local_agent.pair(db, body.code)

    @app.post(
        "/v1/agent/sync",
        response_model=AgentSyncOut,
        tags=["agent"],
        responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    async def sync_agent(
        db: Db,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_agent_bearer)],
        body: AgentSync,
    ) -> AgentSyncOut:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise local_agent.invalid_agent_token()
        return await local_agent.sync(db, credentials.credentials, body)

    return app
