"""HTTP contracts. These models are the single source of the OpenAPI document and, through it,
of the TypeScript types used by the web app."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Role = Literal["owner"]


@lru_cache(maxsize=1)
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def _workspace_name(value: str) -> str:
    cleaned = " ".join(unicodedata.normalize("NFC", value).split())
    if not 2 <= len(cleaned) <= 60:
        raise ValueError("name must have 2 to 60 characters")
    if any(unicodedata.category(char).startswith("C") for char in cleaned):
        raise ValueError("name cannot contain control characters")
    return cleaned


def _timezone(value: str) -> str:
    if value not in _timezones():
        raise ValueError("timezone must be a valid IANA time zone")
    return value


WorkspaceName = Annotated[str, Field(max_length=200), AfterValidator(_workspace_name)]
Timezone = Annotated[str, Field(max_length=64), AfterValidator(_timezone)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WorkspaceCreate(_Request):
    name: WorkspaceName
    timezone: Timezone = "America/Sao_Paulo"


class WorkspaceUpdate(_Request):
    """Partial update guarded by optimistic concurrency: `version` is the one the client read."""

    version: int = Field(ge=1, strict=True)
    name: WorkspaceName | None = None
    timezone: Timezone | None = None


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    timezone: str
    version: int
    role: Role
    created_at: datetime
    updated_at: datetime


class WorkspaceSummary(BaseModel):
    id: UUID
    name: str
    role: Role


class UserOut(BaseModel):
    id: UUID
    email: str


class MeOut(BaseModel):
    user: UserOut
    workspaces: list[WorkspaceSummary]


ComponentState = Literal["not_provisioned", "not_connected", "not_configured"]


class ComponentStatus(BaseModel):
    """Real state only. `last_heartbeat_at` stays null until a worker actually reports one."""

    state: ComponentState
    reason: str
    last_heartbeat_at: datetime | None = None


class WorkspaceStatusOut(BaseModel):
    workspace_id: UUID
    operational: bool
    worker: ComponentStatus
    telegram: ComponentStatus
    aliexpress: ComponentStatus
    checked_at: datetime


CapabilityKey = Literal[
    "workspace_settings",
    "telegram_connection",
    "channel_selection",
    "manual_channel_registry",
    "local_agent_test",
    "aliexpress_credentials",
    "relay_automation",
    "promotion_history",
    "automatic_publishing",
]
CapabilityReason = Literal[
    "NOT_IMPLEMENTED",
    "REQUIRES_SECRET_VAULT",
    "REQUIRES_WORKER_SUPERVISOR",
    "REQUIRES_TELEGRAM_CONNECTION",
    "REQUIRES_EXPLICIT_AUTHORIZATION",
]


class Capability(BaseModel):
    key: CapabilityKey
    title: str
    available: bool
    reason_code: CapabilityReason | None = None
    reason: str | None = None


class CapabilitiesOut(BaseModel):
    workspace_id: UUID
    capabilities: list[Capability]


# Same shape the bot accepts for a private channel or supergroup (src/promo_bot/config/schema.py),
# bounded in length. Strict on purpose: no trimming, no usernames, no links, no positive user ids.
CHAT_ID_PATTERN = r"^-100[1-9][0-9]{0,14}$"
ChatId = Annotated[str, Field(min_length=5, max_length=19, pattern=CHAT_ID_PATTERN)]
MAX_SOURCE_CHANNELS = 50


class ChatIdBody(_Request):
    chat_id: ChatId


class SourceChannelOut(BaseModel):
    chat_id: str
    created_at: datetime


class DestinationOut(BaseModel):
    chat_id: str
    updated_at: datetime


class TelegramChannelsOut(BaseModel):
    """What was registered by hand. `access_status` is always `not_verified`: saving an id does
    not prove that the bot can read the source or write to the destination."""

    workspace_id: UUID
    sources: list[SourceChannelOut]
    destination: DestinationOut | None
    max_sources: int
    access_status: Literal["not_verified"]


ProcessState = Literal["running", "stopped"]
AgentAction = Literal["start", "stop"]
AgentEvent = Literal["start_failed", "exited"]


class PairingCodeOut(BaseModel):
    """Shown once to the signed-in owner, to be typed into the agent on their PC."""

    code: str
    expires_at: datetime


class AgentStatusOut(BaseModel):
    """What is known about the paired agent. While it is not connected the process state is
    `unknown`: an old report is never presented as the current state."""

    workspace_id: UUID
    paired: bool
    connected: bool
    process_state: Literal["running", "stopped", "unknown"]
    pending_action: AgentAction | None
    last_seen_at: datetime | None
    last_event: AgentEvent | None


class AgentCommandBody(BaseModel):
    """The whole vocabulary of the panel: start or stop. Never a program or its arguments."""

    model_config = ConfigDict(extra="forbid")

    action: AgentAction


class AgentPairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=12, max_length=20)


class AgentPairedOut(BaseModel):
    agent_token: str
    workspace_id: UUID


class AgentSync(BaseModel):
    """The agent's report. Extra fields are refused, so nothing else (a credential, for
    instance) can be sent here by mistake."""

    model_config = ConfigDict(extra="forbid")

    process_state: ProcessState
    completed_command_id: int | None = Field(default=None, ge=1, strict=True)
    last_event: AgentEvent | None = None
    closing: bool = Field(default=False, strict=True)


class AgentCommandOut(BaseModel):
    id: int
    action: AgentAction


class AgentSyncOut(BaseModel):
    command: AgentCommandOut | None
    poll_interval_seconds: int


class HealthOut(BaseModel):
    status: Literal["ok"]


class ReadinessOut(BaseModel):
    status: Literal["ready"]
    database: Literal["ok"]
