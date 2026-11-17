# Promo Bot API (control plane)

FastAPI service for identity admission, workspaces and capabilities. Independent from the legacy
bot CLI (it imports nothing of the bot and has its own `pyproject.toml` and `uv.lock`). See
[docs/SAAS_WEB_FOUNDATION.md](../../docs/SAAS_WEB_FOUNDATION.md) for architecture, configuration,
local execution and acceptance criteria, and
[docs/SAAS_BACKEND_RENDER_NEON.md](../../docs/SAAS_BACKEND_RENDER_NEON.md) for the Render Free +
Neon Free preparation (Blueprint, TLS, migrations runbook, Clerk admission).

Operator notes

- The server never migrates: `alembic upgrade head` is a separate, deliberate step, run on the
  **direct** database endpoint with `-x confirm=<host>` for any non-local target.
- `promo-api-admin invite|revoke` read the e-mail address from standard input, never from an argument.
- Deploy from the source tree (`uv sync --locked --no-dev`): the wheel does not ship `migrations/`.
