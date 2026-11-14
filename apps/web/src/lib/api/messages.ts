import { ApiClientError, NETWORK_ERROR } from "./client";

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
  NOT_READY: "O servidor ainda não está pronto. Tente em instantes.",
  [NETWORK_ERROR]: "Não foi possível falar com o servidor. Verifique sua conexão.",
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
