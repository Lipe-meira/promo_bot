"""Workspace persistence. Every read and write is scoped by the caller's membership."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from promo_api.db.models import Workspace, WorkspaceMembership
from promo_api.errors import ApiError
from promo_api.schemas import (
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceSummary,
    WorkspaceUpdate,
)


def _not_found() -> ApiError:
    # Also returned for another user's workspace: existence is never revealed.
    return ApiError(404, "WORKSPACE_NOT_FOUND", "Workspace not found.")


def _out(workspace: Workspace, role: str) -> WorkspaceOut:
    return WorkspaceOut.model_validate(
        {
            "id": workspace.id,
            "name": workspace.name,
            "timezone": workspace.timezone,
            "version": workspace.version,
            "role": role,
            "created_at": workspace.created_at,
            "updated_at": workspace.updated_at,
        }
    )


async def list_for_user(session: AsyncSession, user_id: UUID) -> list[WorkspaceSummary]:
    rows = await session.execute(
        select(Workspace.id, Workspace.name, WorkspaceMembership.role)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user_id)
        .order_by(Workspace.created_at, Workspace.id)
    )
    return [
        WorkspaceSummary.model_validate({"id": row.id, "name": row.name, "role": row.role})
        for row in rows
    ]


async def get_for_user(session: AsyncSession, user_id: UUID, workspace_id: UUID) -> WorkspaceOut:
    row = (
        await session.execute(
            select(Workspace, WorkspaceMembership.role)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMembership.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise _not_found()
    return _out(row[0], row[1])


async def create(session: AsyncSession, user_id: UUID, data: WorkspaceCreate) -> WorkspaceOut:
    workspace = Workspace(owner_user_id=user_id, name=data.name, timezone=data.timezone)
    session.add(workspace)
    try:
        await session.flush()
        session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user_id, role="owner"))
        await session.commit()
    except IntegrityError:
        # Repeated or concurrent creation: the unique owner constraint decides, not a prior read.
        await session.rollback()
        existing = await session.scalar(
            select(Workspace.id).where(Workspace.owner_user_id == user_id)
        )
        if existing is None:
            raise
        raise ApiError(
            409,
            "WORKSPACE_ALREADY_EXISTS",
            "This account already has a workspace.",
            details={"workspace_id": str(existing)},
        ) from None
    await session.refresh(workspace)
    return _out(workspace, "owner")


async def update_for_user(
    session: AsyncSession, user_id: UUID, workspace_id: UUID, data: WorkspaceUpdate
) -> WorkspaceOut:
    current = await get_for_user(session, user_id, workspace_id)  # authorization first
    changes = data.model_dump(exclude_unset=True, exclude={"version"}, exclude_none=True)
    if not changes:
        raise ApiError(422, "VALIDATION_ERROR", "The request is invalid.", details={"fields": []})
    updated = (
        await session.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id, Workspace.version == data.version)
            .values(**changes, version=Workspace.version + 1, updated_at=func.now())
            .returning(Workspace.id)
        )
    ).scalar_one_or_none()
    if updated is None:
        await session.rollback()
        latest = await session.scalar(select(Workspace.version).where(Workspace.id == workspace_id))
        raise ApiError(
            409,
            "WORKSPACE_VERSION_CONFLICT",
            "The workspace was changed elsewhere. Reload and try again.",
            details={"current_version": latest if latest is not None else current.version},
        )
    await session.commit()
    session.expire_all()
    return await get_for_user(session, user_id, workspace_id)
