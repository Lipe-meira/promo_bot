import type { components } from "./schema";

// Every contract type comes from the generated OpenAPI schema; nothing is declared by hand.
type Schemas = components["schemas"];

export type Me = Schemas["MeOut"];
export type Workspace = Schemas["WorkspaceOut"];
export type WorkspaceSummary = Schemas["WorkspaceSummary"];
export type WorkspaceCreate = Schemas["WorkspaceCreate"];
export type WorkspaceUpdate = Schemas["WorkspaceUpdate"];
export type WorkspaceStatus = Schemas["WorkspaceStatusOut"];
export type ComponentStatus = Schemas["ComponentStatus"];
export type Capabilities = Schemas["CapabilitiesOut"];
export type Capability = Schemas["Capability"];
export type TelegramChannels = Schemas["TelegramChannelsOut"];
export type AgentStatus = Schemas["AgentStatusOut"];
export type AgentAction = Schemas["AgentCommandBody"]["action"];
export type PairingCode = Schemas["PairingCodeOut"];
export type ErrorEnvelope = Schemas["ErrorResponse"];
