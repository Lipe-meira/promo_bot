"use client";

import { useId, useMemo, useState, type FormEvent } from "react";

import { describeError, invalidFields } from "@/lib/api/messages";
import { timezoneOptions } from "@/lib/timezones";

export interface WorkspaceFormValues {
  name: string;
  timezone: string;
}

const FIELD_LABELS: Record<string, string> = { name: "Nome", timezone: "Fuso horário" };

/**
 * Name + time zone form shared by onboarding and settings. It validates what it cheaply can
 * (length), but the API is the authority: server-side field errors are shown next to the fields.
 */
export function WorkspaceForm({
  initial,
  submitLabel,
  disabled = false,
  onSubmit,
}: {
  initial: WorkspaceFormValues;
  submitLabel: string;
  disabled?: boolean;
  onSubmit: (values: WorkspaceFormValues) => Promise<void>;
}) {
  const nameId = useId();
  const zoneId = useId();
  const [values, setValues] = useState(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const zones = useMemo(() => {
    const options = timezoneOptions();
    return options.includes(initial.timezone) ? options : [initial.timezone, ...options];
  }, [initial.timezone]);

  const cleaned = values.name.trim().replace(/\s+/g, " ");
  const nameProblem =
    cleaned.length < 2 || cleaned.length > 60 ? "O nome deve ter de 2 a 60 caracteres." : null;
  const serverFields = invalidFields(error);
  const dirty = cleaned !== initial.name || values.timezone !== initial.timezone;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (nameProblem || pending || disabled) return;
    setPending(true);
    setError(null);
    try {
      await onSubmit({ name: cleaned, timezone: values.timezone });
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="form" onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor={nameId}>Nome do workspace</label>
        <input
          id={nameId}
          value={values.name}
          maxLength={200}
          autoComplete="off"
          aria-invalid={nameProblem !== null}
          aria-describedby={`${nameId}-hint`}
          disabled={disabled || pending}
          onChange={(event) => setValues({ ...values, name: event.target.value })}
        />
        <p id={`${nameId}-hint`} className={nameProblem ? "hint hint-error" : "hint"}>
          {nameProblem ?? "Como você quer chamar este espaço."}
        </p>
      </div>
      <div className="field">
        <label htmlFor={zoneId}>Fuso horário</label>
        <select
          id={zoneId}
          value={values.timezone}
          disabled={disabled || pending}
          onChange={(event) => setValues({ ...values, timezone: event.target.value })}
        >
          {zones.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
      </div>
      {error ? (
        <div className="form-error" role="alert">
          <p>{describeError(error)}</p>
          {serverFields.length > 0 ? (
            <p className="muted">
              Campos: {serverFields.map((field) => FIELD_LABELS[field] ?? field).join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}
      <button
        type="submit"
        className="button"
        disabled={disabled || pending || nameProblem !== null || !dirty}
      >
        {pending ? "Salvando…" : submitLabel}
      </button>
    </form>
  );
}
