import { ApiClientError, NETWORK_ERROR, SESSION_CHANGED } from "./client";

/** User-facing Portuguese text for every stable API error code. Never shows raw server text. */
const MESSAGES: Record<string, string> = {
  AUTH_INVALID_TOKEN: "Sua sessão não é mais válida. Entre novamente.",
  AUTH_TOKEN_EXPIRED: "Sua sessão expirou. Entre novamente.",
  AUTH_KEYS_UNAVAILABLE: "A verificação de identidade está indisponível. Tente em instantes.",
  NOT_ADMITTED: "Esta conta ainda não foi admitida na prévia privada.",
  WORKSPACE_NOT_FOUND: "Workspace não encontrado.",
  WORKSPACE_ALREADY_EXISTS: "Esta conta já possui um workspace.",
  WORKSPACE_VERSION_CONFLICT:
    "Este workspace foi alterado em outro lugar. Recarregue para ver a versão atual.",
  VALIDATION_ERROR: "Alguns campos são inválidos. Revise e tente novamente.",
  AGENT_NOT_PAIRED: "Nenhum agente está pareado neste workspace.",
  AGENT_OFFLINE: "O agente não está conectado. Execute-o no seu PC e tente de novo.",
  AGENT_STOPPING: "Há uma parada em andamento. Aguarde o agente confirmar a saída do processo.",
  DATABASE_UNAVAILABLE: "O servidor está temporariamente indisponível. Tente em instantes.",
  SOURCE_CHANNEL_DUPLICATE: "Este canal de origem já está cadastrado.",
  SOURCE_CHANNEL_LIMIT: "O limite de canais de origem foi atingido. Remova algum para adicionar outro.",
  SOURCE_CHANNEL_NOT_FOUND: "Este canal de origem não está mais cadastrado.",
  CHANNEL_IS_DESTINATION: "Este chat já é o canal privado de teste. Um chat não pode ser origem e destino.",
  DESTINATION_IS_SOURCE: "Este chat já é um canal de origem. Um chat não pode ser origem e destino.",
  NOT_READY: "O servidor ainda não está pronto. Tente em instantes.",
  [NETWORK_ERROR]: "Não foi possível falar com o servidor. Verifique sua conexão.",
  [SESSION_CHANGED]: "Sua sessão mudou. Repita a ação.",
};

const ADMISSION_REASONS: Record<string, string> = {
  NO_INVITATION: "Não há convite ativo para o e-mail desta conta.",
  EMAIL_NOT_VERIFIED: "O e-mail desta conta ainda não foi verificado.",
  EMAIL_CLAIM_MISSING: "Não foi possível identificar o e-mail desta conta.",
  INVITATION_ALREADY_USED: "O convite deste e-mail já foi usado por outra identidade.",
  ACCESS_REVOKED: "O acesso desta conta foi revogado.",
};

export function describeError(error: unknown): string {
  if (error instanceof ApiClientError) {
    return MESSAGES[error.code] ?? "Algo deu errado. Tente novamente.";
  }
  return "Algo deu errado. Tente novamente.";
}

export function admissionReason(error: ApiClientError): string | null {
  const reason = error.details?.reason;
  return typeof reason === "string" ? (ADMISSION_REASONS[reason] ?? null) : null;
}

/** Field-level problems of a 422, by field name. Only paths are sent by the API, never values. */
export function invalidFields(error: unknown): string[] {
  if (!(error instanceof ApiClientError) || error.code !== "VALIDATION_ERROR") return [];
  const fields = error.details?.fields;
  if (!Array.isArray(fields)) return [];
  return fields.flatMap((item) =>
    typeof item === "object" && item !== null && typeof (item as { field?: unknown }).field === "string"
      ? [(item as { field: string }).field]
      : [],
  );
}
