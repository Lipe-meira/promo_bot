"""Configuration of the API process. Deliberately separate from the bot's `EnvironmentSettings`.

There is no switch that disables authentication: a deployment without a valid Clerk issuer and
authorized parties does not start.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from promo_api.db.engine import prepare_database


def _split(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _is_local(host: str | None) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"}


def _origin(value: str) -> str:
    """Accept only `scheme://host[:port]`; http is tolerated for localhost alone."""

    parts = urlsplit(value)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
        or (parts.scheme == "http" and not _is_local(parts.hostname))
    ):
        raise ValueError("origins must be https://host[:port] (http only for localhost)")
    return f"{parts.scheme}://{parts.netloc}"


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROMO_API_", env_file=None, extra="ignore", frozen=True
    )

    environment: Literal["development", "test", "production"] = "development"
    #: `postgresql+asyncpg://user:password@host:port/database`
    database_url: SecretStr
    #: The Clerk Frontend API URL of the instance, e.g. `https://<slug>.clerk.accounts.dev`.
    clerk_issuer: str
    #: Defaults to `<issuer>/.well-known/jwks.json`.
    clerk_jwks_url: str | None = None
    #: Origins allowed in the token's `azp` claim (the web app). Never empty.
    clerk_authorized_parties: Annotated[tuple[str, ...], NoDecode] = Field(min_length=1)
    #: Browser origins allowed by CORS. Defaults to the authorized parties.
    cors_allowed_origins: Annotated[tuple[str, ...], NoDecode] = ()
    clock_skew_seconds: int = Field(default=5, ge=0, le=60)
    # Pool sized for a free instance (one small process, a database that suspends when idle).
    db_pool_size: int = Field(default=3, ge=1, le=10)
    db_max_overflow: int = Field(default=2, ge=0, le=10)
    #: How long a request waits for a free connection before it gets a retryable 503.
    db_pool_timeout_seconds: float = Field(default=10, gt=0, le=60)
    #: How long opening one connection may take (a suspended Neon compute needs a moment to wake).
    db_connect_timeout_seconds: float = Field(default=15, ge=1, le=60)
    #: Connections older than this are replaced, well before an idle compute would drop them.
    db_pool_recycle_seconds: int = Field(default=240, ge=30, le=3600)

    @field_validator("clerk_authorized_parties", "cors_allowed_origins", mode="before")
    @classmethod
    def split_lists(cls, value: object) -> object:
        return _split(value)

    @field_validator("clerk_authorized_parties", "cors_allowed_origins")
    @classmethod
    def validate_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_origin(value) for value in values)

    @field_validator("clerk_issuer")
    @classmethod
    def validate_issuer(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("clerk_issuer must be an https URL")
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the postgresql+asyncpg driver")
        # Refuse an unsafe or unsupported URL at startup, not on the first request. The error
        # carries parameter names only: never the host or the password.
        prepare_database(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def validate_jwks_url(self) -> ApiSettings:
        if self.clerk_jwks_url is not None:
            parts = urlsplit(self.clerk_jwks_url)
            if parts.scheme != "https" or not parts.hostname:
                raise ValueError("clerk_jwks_url must be an https URL")
        return self

    @property
    def jwks_url(self) -> str:
        return self.clerk_jwks_url or f"{self.clerk_issuer}/.well-known/jwks.json"

    @property
    def cors_origins(self) -> tuple[str, ...]:
        return self.cors_allowed_origins or self.clerk_authorized_parties
