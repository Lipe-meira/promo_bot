"""The control channel between a workspace and the one agent paired on the owner's PC.

The agent connects outwards and asks; nothing here ever connects to the PC. The only commands are
`start` and `stop`: what is started is decided by the agent's own code, never by this server. No
credential of the bot (Telegram, AliExpress) has a field anywhere in this protocol.

Locks are always taken in the order workspace → pairing code → agent.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_api.db.models import Agent, AgentPairingCode, Workspace, WorkspaceMembership
from promo_api.errors import ApiError
from promo_api.schemas import (
    AgentAction,
    AgentCommandOut,
    AgentPairedOut,
    AgentStatusOut,
    AgentSync,
    AgentSyncOut,
    PairingCodeOut,
)

#: How often the agent is told to call, in seconds.
POLL_INTERVAL_SECONDS = 3
#: An agent silent for longer than this is shown as disconnected (four missed calls).
OFFLINE_AFTER = timedelta(seconds=12)
#: A command the agent did not complete within this time is dropped, never executed late.
COMMAND_TTL = timedelta(seconds=30)
PAIRING_CODE_TTL = timedelta(minutes=10)

TOKEN_PREFIX = "pbagent_"
# No 0/O or 1/I/L: the code is read from the screen and typed. 12 characters are ~59 bits,
# single use and valid for minutes, so guessing it is not practical.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 12


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _bad_code() -> ApiError:
    # One answer for unknown, used, replaced and expired codes.
    return ApiError(401, "PAIRING_CODE_INVALID", "This pairing code is not valid.")


def invalid_agent_token() -> ApiError:
    return ApiError(
        401,
        "AGENT_TOKEN_INVALID",
        "This agent is not paired.",
        headers={"WWW-Authenticate": "Bearer"},
    )


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
        # Also returned for another user's workspace: existence is never revealed.
        raise ApiError(404, "WORKSPACE_NOT_FOUND", "Workspace not found.")


async def _load(
    session: AsyncSession, where: ColumnElement[bool], *, lock: bool
) -> tuple[Agent, bool, bool] | None:
    """The agent plus two facts judged by the database clock: connected, command still fresh."""

    query = select(
        Agent,
        func.coalesce(Agent.last_seen_at > func.now() - OFFLINE_AFTER, False),
        func.coalesce(Agent.command_issued_at > func.now() - COMMAND_TTL, False),
    ).where(where)
    if lock:
        query = query.with_for_update(of=Agent)
    row = (await session.execute(query)).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


def _status(workspace_id: UUID, loaded: tuple[Agent, bool, bool] | None) -> AgentStatusOut:
    agent, connected, fresh = loaded if loaded is not None else (None, False, False)
    # A report from a silent agent says nothing about now.
    live = agent if connected else None
    return AgentStatusOut.model_validate(
        {
            "workspace_id": workspace_id,
            "paired": agent is not None,
            "connected": connected,
            "process_state": live.process_state if live else "unknown",
            "pending_action": live.command_action if live and fresh else None,
            "last_seen_at": agent.last_seen_at if agent else None,
            "last_event": live.last_event if live else None,
        }
    )


async def _current(session: AsyncSession, workspace_id: UUID) -> AgentStatusOut:
    return _status(
        workspace_id, await _load(session, Agent.workspace_id == workspace_id, lock=False)
    )


async def get_for_user(session: AsyncSession, user_id: UUID, workspace_id: UUID) -> AgentStatusOut:
    await _authorize(session, user_id, workspace_id, lock=False)
    return await _current(session, workspace_id)


async def create_pairing_code(
    session: AsyncSession, user_id: UUID, workspace_id: UUID
) -> PairingCodeOut:
    await _authorize(session, user_id, workspace_id, lock=True)
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    values = {"code_hash": _hash(code), "expires_at": func.now() + PAIRING_CODE_TTL}
    statement = pg_insert(AgentPairingCode).values(workspace_id=workspace_id, **values)
    expires_at = await session.scalar(
        statement.on_conflict_do_update(
            index_elements=[AgentPairingCode.workspace_id],
            set_={**values, "created_at": func.now()},
        ).returning(AgentPairingCode.expires_at)
    )
    await session.commit()
    grouped = "-".join(code[start : start + 4] for start in range(0, _CODE_LENGTH, 4))
    return PairingCodeOut.model_validate({"code": grouped, "expires_at": expires_at})


async def pair(session: AsyncSession, code: str) -> AgentPairedOut:
    normalized = code.replace("-", "").upper()
    if len(normalized) != _CODE_LENGTH or not set(normalized) <= set(_CODE_ALPHABET):
        raise _bad_code()
    code_hash = _hash(normalized)
    workspace_id = await session.scalar(
        select(AgentPairingCode.workspace_id).where(AgentPairingCode.code_hash == code_hash)
    )
    if workspace_id is None:
        await session.rollback()
        raise _bad_code()
    await session.execute(
        select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
    )
    # Consumed atomically: of two callers with the same code, only one gets a row back.
    consumed = await session.scalar(
        delete(AgentPairingCode)
        .where(AgentPairingCode.code_hash == code_hash, AgentPairingCode.expires_at > func.now())
        .returning(AgentPairingCode.workspace_id)
    )
    if consumed is None:
        await session.rollback()
        raise _bad_code()
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    # Pairing again replaces the previous agent: its token stops working at once.
    row = {
        "token_hash": _hash(token),
        "paired_at": func.now(),
        "last_seen_at": None,
        "process_state": "stopped",
        "last_event": None,
        "command_action": None,
        "command_issued_at": None,
    }
    statement = pg_insert(Agent).values(workspace_id=workspace_id, **row)
    await session.execute(
        statement.on_conflict_do_update(index_elements=[Agent.workspace_id], set_=row)
    )
    await session.commit()
    return AgentPairedOut(agent_token=token, workspace_id=workspace_id)


async def remove(session: AsyncSession, user_id: UUID, workspace_id: UUID) -> AgentStatusOut:
    await _authorize(session, user_id, workspace_id, lock=True)
    await session.execute(
        delete(AgentPairingCode).where(AgentPairingCode.workspace_id == workspace_id)
    )
    await session.execute(delete(Agent).where(Agent.workspace_id == workspace_id))
    await session.commit()
    return _status(workspace_id, None)


async def send_command(
    session: AsyncSession, user_id: UUID, workspace_id: UUID, action: AgentAction
) -> AgentStatusOut:
    await _authorize(session, user_id, workspace_id, lock=True)
    loaded = await _load(session, Agent.workspace_id == workspace_id, lock=True)
    if loaded is None:
        await session.rollback()
        raise ApiError(409, "AGENT_NOT_PAIRED", "No agent is paired to this workspace.")
    agent, connected, fresh = loaded
    if not connected:
        await session.rollback()
        raise ApiError(409, "AGENT_OFFLINE", "The agent is not connected.")
    pending = agent.command_action if fresh else None
    target = "running" if action == "start" else "stopped"
    # Already asked, or already so with nothing else on the way: a repeated click adds nothing.
    if not (pending == action or (pending is None and agent.process_state == target)):
        agent.command_id += 1
        agent.command_action = action
        agent.command_issued_at = func.now()
    await session.commit()
    return await _current(session, workspace_id)


async def sync(session: AsyncSession, token: str, report: AgentSync) -> AgentSyncOut:
    if not token.startswith(TOKEN_PREFIX):
        raise invalid_agent_token()
    loaded = await _load(session, Agent.token_hash == _hash(token), lock=True)
    if loaded is None:
        await session.rollback()
        raise invalid_agent_token()
    agent, _, fresh = loaded
    agent.process_state = report.process_state
    agent.last_event = report.last_event
    agent.last_seen_at = None if report.closing else func.now()
    # Completing an older command never clears a newer one.
    if report.closing or not fresh or report.completed_command_id == agent.command_id:
        agent.command_action = None
        agent.command_issued_at = None
    command = None
    if agent.command_action is not None:
        command = AgentCommandOut.model_validate(
            {"id": agent.command_id, "action": agent.command_action}
        )
    await session.commit()
    return AgentSyncOut(command=command, poll_interval_seconds=POLL_INTERVAL_SECONDS)
