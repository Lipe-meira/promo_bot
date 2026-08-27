# AGENTS.md

## Scope

Implement only the phase explicitly authorized by the user. Do not anticipate later integrations.

## Architecture

- `src/promo_bot/domain`: dependency-free business types and invariants.
- `src/promo_bot/config`: environment and YAML validation.
- `src/promo_bot/database`: SQLAlchemy models, sessions, repositories, and migrations.
- `src/promo_bot/observability`: structured, sanitized logging.
- `tests/unit`: offline tests; real network, Telegram, stores, and browsers are forbidden here.

## Commands

Use Python 3.12 through uv:

- `uv sync --locked`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src`
- `uv run pytest`

## Git and security

- Work on `feat/promo-affiliate-bot-mvp`, never directly on `main`.
- Preserve user work and review status, diff, and staged content before every commit.
- Never use force push, destructive reset, or `git add .` without reviewing status.
- Never commit `.env`, credentials, tokens, cookies, sessions, browser profiles, databases, or logs.
- Never log secrets or credential-bearing URLs. Keep external access disabled by default.
- Use Conventional Commits and add only phase-related files.

## Definition of done

The authorized phase is documented, passes Ruff, mypy, pytest, and its controlled smoke test; the
diff and staged content are reviewed for secrets; explicit stubs never simulate success.
