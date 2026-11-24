"use client";

import { useId, useRef, useState, type FormEvent } from "react";

import { describeError } from "@/lib/api/messages";
import type { TelegramChannels } from "@/lib/api/types";
import { useSession } from "@/lib/session";
import { useAsync } from "@/lib/use-async";

import { ErrorView, LoadingView } from "./states";

/** Same shape the API and the bot accept; the API stays the authority, this only saves a trip. */
export const CHAT_ID_PATTERN = /^-100[1-9][0-9]{0,14}$/;
const ID_PROBLEM = "Informe o ID completo, começando com -100 (por exemplo, -1001234567890).";

type Notice = { kind: "ok" | "error"; text: string } | null;

function ChatIdForm({
  label,
  hint,
  submitLabel,
  initial = "",
  clearOnSuccess = false,
  unchangedDisables = false,
  busy = false,
  onSubmit,
}: {
  label: string;
  hint: string;
  submitLabel: string;
  initial?: string;
  clearOnSuccess?: boolean;
  unchangedDisables?: boolean;
  /** Another change of the card is in flight: nothing else may be sent meanwhile. */
  busy?: boolean;
  /** Resolves true only when the server accepted the change; the field is cleared only then. */
  onSubmit: (chatId: string) => Promise<boolean>;
}) {
  const id = useId();
  const [value, setValue] = useState(initial);
  const [pending, setPending] = useState(false);
  const [touched, setTouched] = useState(false);
  const cleaned = value.trim();
  const problem = !CHAT_ID_PATTERN.test(cleaned) ? ID_PROBLEM : null;
  const unchanged = unchangedDisables && cleaned === initial;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (problem || pending || busy || unchanged) return;
    setPending(true);
    try {
      const saved = await onSubmit(cleaned);
      if (saved && clearOnSuccess) {
        setValue("");
        setTouched(false);
      }
    } finally {
      setPending(false);
    }
  }

  const showProblem = touched && problem !== null;
  return (
    <form className="form" onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor={id}>{label}</label>
        <input
          id={id}
          value={value}
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          maxLength={40}
          aria-invalid={showProblem}
          aria-describedby={`${id}-hint`}
          disabled={pending || busy}
          onChange={(event) => setValue(event.target.value)}
        />
        <p id={`${id}-hint`} className={showProblem ? "hint hint-error" : "hint"}>
          {showProblem ? problem : hint}
        </p>
      </div>
      <button type="submit" className="button" disabled={pending || busy || unchanged}>
        {pending ? "Salvando…" : submitLabel}
      </button>
    </form>
  );
}

/**
 * Manual registry of Telegram channels: source channels and one test destination. What is shown
 * is what the API answered; saving an id never claims that the bot can reach the chat.
 */
export function TelegramChannelsCard({ workspaceId }: { workspaceId: string }) {
  const { api } = useSession();
  const loaded = useAsync(() => api.getTelegramChannels(workspaceId));
  const [latest, setLatest] = useState<TelegramChannels | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const config = latest ?? (loaded.status === "ready" ? loaded.data : null);

  /** One change at a time: a late answer can never overwrite a newer one. True when saved. */
  async function run(action: () => Promise<TelegramChannels>, success: string): Promise<boolean> {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setNotice(null);
    try {
      setLatest(await action());
      setNotice({ kind: "ok", text: success });
      return true;
    } catch (error) {
      setNotice({ kind: "error", text: describeError(error) });
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="telegram-channels-title">
      <h2 id="telegram-channels-title">Canais do Telegram</h2>
      <p className="muted">
        Cadastro manual de IDs, salvo no servidor. Salvar o cadastro não comprova que o bot tem
        acesso ao canal: nenhuma conexão com o Telegram é feita nesta etapa.
      </p>
      {loaded.status === "loading" && config === null ? (
        <LoadingView label="Carregando canais…" />
      ) : null}
      {loaded.status === "error" && config === null ? (
        <ErrorView message={describeError(loaded.error)} onRetry={loaded.reload} />
      ) : null}
      {config !== null ? (
        <>
          <h3>Canais de origem</h3>
          {config.sources.length === 0 ? (
            <p className="muted">Nenhum canal de origem cadastrado.</p>
          ) : (
            <ul className="channel-list" aria-label="Canais de origem cadastrados">
              {config.sources.map((source) => (
                <li key={source.chat_id} className="channel-row">
                  <code>{source.chat_id}</code>
                  <button
                    type="button"
                    className="button button-secondary"
                    aria-label={`Remover canal ${source.chat_id}`}
                    disabled={busy}
                    onClick={() =>
                      void run(
                        () => api.removeSourceChannel(workspaceId, source.chat_id),
                        "Canal removido.",
                      )
                    }
                  >
                    Remover
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="muted meta">
            {config.sources.length} de {config.max_sources} canais de origem.
          </p>
          <ChatIdForm
            label="ID do canal de origem"
            hint="ID numérico do canal ou supergrupo (começa com -100)."
            submitLabel="Adicionar canal"
            clearOnSuccess
            busy={busy}
            onSubmit={(chatId) =>
              run(() => api.addSourceChannel(workspaceId, chatId), "Canal adicionado.")
            }
          />

          <h3>Canal privado de teste</h3>
          <p className="muted">
            {config.destination
              ? "Destino salvo. Para trocar, informe outro ID e salve."
              : "Nenhum canal privado de teste cadastrado."}
          </p>
          <ChatIdForm
            // A saved destination remounts the form, so the field shows what the server holds.
            key={config.destination?.chat_id ?? "none"}
            label="ID do canal privado de teste"
            hint="Use o ID numérico de um canal ou supergrupo privado de teste, como no bot local (começa com -100). Não é uma conversa direta com um usuário."
            submitLabel={config.destination ? "Trocar destino" : "Salvar destino"}
            initial={config.destination?.chat_id ?? ""}
            unchangedDisables
            busy={busy}
            onSubmit={(chatId) =>
              run(() => api.setDestinationChannel(workspaceId, chatId), "Destino salvo.")
            }
          />

          {notice ? (
            <p
              className={notice.kind === "ok" ? "form-success" : "form-error"}
              role={notice.kind === "ok" ? "status" : "alert"}
            >
              {notice.text}
            </p>
          ) : null}
          <p className="muted meta">
            Status do acesso do bot: <strong>não verificado</strong>.
          </p>
        </>
      ) : null}
    </section>
  );
}
