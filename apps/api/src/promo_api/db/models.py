"""Control-plane tables (PostgreSQL only). The bot's SQLite runtime schema is unrelated."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _timestamp(*, nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else func.now(),
    )


class User(Base):
    """Local identity: one row per admitted Clerk user."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    clerk_user_id: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = _timestamp()
    disabled_at: Mapped[datetime | None] = _timestamp(nullable=True)


class Invitation(Base):
    """Private admission: only an invited, verified e-mail may become a local user."""

    __tablename__ = "invitations"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = _timestamp()
    revoked_at: Mapped[datetime | None] = _timestamp(nullable=True)
    accepted_at: Mapped[datetime | None] = _timestamp(nullable=True)
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("char_length(name) BETWEEN 2 AND 60", name="name_length"),
        # Private MVP: one workspace per owner. Also what makes repeated creation safe.
        UniqueConstraint("owner_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(60))
    timezone: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()


class WorkspaceMembership(Base):
    """The authorization record: a user may touch a workspace only through a membership."""

    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner')", name="role_known"),
        Index("ix_workspace_memberships_user_id", "user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _timestamp()


# Telegram chat ids are stored as registered by hand; saving one does not prove that the bot can
# reach the chat. Same shape the bot itself accepts (src/promo_bot/config/schema.py), bounded.
CHAT_ID_SQL_PATTERN = "^-100[1-9][0-9]{0,14}$"


class TelegramSourceChannel(Base):
    """A source channel registered by a workspace. Unique per workspace, never across them."""

    __tablename__ = "telegram_source_channels"
    __table_args__ = (CheckConstraint(f"chat_id ~ '{CHAT_ID_SQL_PATTERN}'", name="chat_id_format"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[str] = mapped_column(String(19), primary_key=True)
    created_at: Mapped[datetime] = _timestamp()


class TelegramDestination(Base):
    """The one test destination of a workspace; replaced in place when it is changed."""

    __tablename__ = "telegram_destinations"
    __table_args__ = (CheckConstraint(f"chat_id ~ '{CHAT_ID_SQL_PATTERN}'", name="chat_id_format"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[str] = mapped_column(String(19))
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()
