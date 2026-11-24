"""Manual registry of Telegram channels. Every read and write goes through the caller's membership.

Registering an id proves nothing about access: no Telegram call is made here. Mutations lock the
workspace row first, so the limit and the source/destination cross-check cannot be raced.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from promo_api.db.models import (
    TelegramDestination,
    TelegramSourceChannel,
    Workspace,
    WorkspaceMembership,
)
from promo_api.errors import ApiError
from promo_api.schemas import (
    MAX_SOURCE_CHANNELS,
    DestinationOut,
    SourceChannelOut,
    TelegramChannelsOut,
)


def _not_found() -> ApiError:
    # Also returned for another user's workspace: existence is never revealed.
    return ApiError(404, "WORKSPACE_NOT_FOUND", "Workspace not found.")


def _duplicate() -> ApiError:
    return ApiError(409, "SOURCE_CHANNEL_DUPLICATE", "This source channel is already registered.")


async def _authorize(
    session: AsyncSession, user_id: UUID, workspace_id: UUID, *, lock: bool
) -> None:
    query = (
        select(Workspace.id)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, WorkspaceMembership.user_id == user_id)
    )
    if lock:
        query = query.with_for_update(of=Workspace)
    if await session.scalar(query) is None:
        await session.rollback()
        raise _not_found()


async def _config(session: AsyncSession, workspace_id: UUID) -> TelegramChannelsOut:
    # One statement, so sources and destination come from the same snapshot: two separate
    # queries could straddle a concurrent commit and return a mix that never existed.
    sources = (
        select(
            literal("source").label("kind"),
            TelegramSourceChannel.chat_id.label("chat_id"),
            TelegramSourceChannel.created_at.label("at"),
        )
        .where(TelegramSourceChannel.workspace_id == workspace_id)
        .subquery()
    )
    destination = (
        select(
            literal("destination").label("kind"),
            TelegramDestination.chat_id.label("chat_id"),
            TelegramDestination.updated_at.label("at"),
        )
        .where(TelegramDestination.workspace_id == workspace_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(sources).union_all(select(destination)).order_by("at", "chat_id")
        )
    ).all()
    destination_row = next((row for row in rows if row.kind == "destination"), None)
    return TelegramChannelsOut(
        workspace_id=workspace_id,
        sources=[
            SourceChannelOut(chat_id=row.chat_id, created_at=row.at)
            for row in rows
            if row.kind == "source"
        ],
        destination=(
            DestinationOut(chat_id=destination_row.chat_id, updated_at=destination_row.at)
            if destination_row is not None
            else None
        ),
        max_sources=MAX_SOURCE_CHANNELS,
        access_status="not_verified",
    )


async def get_for_user(
    session: AsyncSession, user_id: UUID, workspace_id: UUID
) -> TelegramChannelsOut:
    await _authorize(session, user_id, workspace_id, lock=False)
    return await _config(session, workspace_id)


async def add_source(
    session: AsyncSession, user_id: UUID, workspace_id: UUID, chat_id: str
) -> TelegramChannelsOut:
    await _authorize(session, user_id, workspace_id, lock=True)
    destination = await session.scalar(
        select(TelegramDestination.chat_id).where(TelegramDestination.workspace_id == workspace_id)
    )
    if destination == chat_id:
        await session.rollback()
        raise ApiError(
            409,
            "CHANNEL_IS_DESTINATION",
            "This chat is already the destination; a chat cannot be both.",
        )
    exists = await session.scalar(
        select(TelegramSourceChannel.chat_id).where(
            TelegramSourceChannel.workspace_id == workspace_id,
            TelegramSourceChannel.chat_id == chat_id,
        )
    )
    if exists is not None:
        await session.rollback()
        raise _duplicate()
    registered = await session.scalar(
        select(func.count())
        .select_from(TelegramSourceChannel)
        .where(TelegramSourceChannel.workspace_id == workspace_id)
    )
    if (registered or 0) >= MAX_SOURCE_CHANNELS:
        await session.rollback()
        raise ApiError(
            409,
            "SOURCE_CHANNEL_LIMIT",
            "The limit of source channels was reached.",
            details={"max_sources": MAX_SOURCE_CHANNELS},
        )
    session.add(TelegramSourceChannel(workspace_id=workspace_id, chat_id=chat_id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _duplicate() from None
    return await _config(session, workspace_id)


async def remove_source(
    session: AsyncSession, user_id: UUID, workspace_id: UUID, chat_id: str
) -> TelegramChannelsOut:
    await _authorize(session, user_id, workspace_id, lock=True)
    removed = await session.scalar(
        delete(TelegramSourceChannel)
        .where(
            TelegramSourceChannel.workspace_id == workspace_id,
            TelegramSourceChannel.chat_id == chat_id,
        )
        .returning(TelegramSourceChannel.chat_id)
    )
    if removed is None:
        await session.rollback()
        raise ApiError(404, "SOURCE_CHANNEL_NOT_FOUND", "Source channel not found.")
    await session.commit()
    return await _config(session, workspace_id)


async def set_destination(
    session: AsyncSession, user_id: UUID, workspace_id: UUID, chat_id: str
) -> TelegramChannelsOut:
    await _authorize(session, user_id, workspace_id, lock=True)
    is_source = await session.scalar(
        select(TelegramSourceChannel.chat_id).where(
            TelegramSourceChannel.workspace_id == workspace_id,
            TelegramSourceChannel.chat_id == chat_id,
        )
    )
    if is_source is not None:
        await session.rollback()
        raise ApiError(
            409,
            "DESTINATION_IS_SOURCE",
            "This chat is a registered source; a chat cannot be both.",
        )
    current = await session.scalar(
        select(TelegramDestination.chat_id).where(TelegramDestination.workspace_id == workspace_id)
    )
    if current != chat_id:  # saving the same id again changes nothing, not even `updated_at`
        statement = pg_insert(TelegramDestination).values(
            workspace_id=workspace_id, chat_id=chat_id
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[TelegramDestination.workspace_id],
                set_={"chat_id": chat_id, "updated_at": func.now()},
            )
        )
    await session.commit()
    return await _config(session, workspace_id)
