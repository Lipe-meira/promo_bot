"""Explicit local history commands. No .env, API, Telegram or implicit database selection."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promo_bot.affiliate.history_context import validate_history_storage
from promo_bot.affiliate.history_query import GENERATION_STATES, SEND_STATES, read_history
from promo_bot.affiliate.shadow_listener_lock import ShadowListenerLock
from promo_bot.database.history_repository import (
    AffiliateHistoryError,
    AffiliateLinkHistoryRepository,
)
from promo_bot.database.history_storage import durable_sqlite_path
from promo_bot.database.session import AffiliateShadowDatabase, Database


def add_history_parser(actions: Any) -> None:
    parser = actions.add_parser("link-history", help="explicit local durable audit history")
    commands = parser.add_subparsers(dest="history_command", required=True)
    for action in ("list", "show", "legacy-blocks", "request-legacy-generation"):
        command = commands.add_parser(action)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--scope", choices=("runtime", "shadow"), required=True)
        if action in {"list", "show"}:
            command.add_argument("--include-urls", action="store_true")
        if action == "show":
            command.add_argument("--generation-id", required=True)
            command.add_argument("--include-legacy", action="store_true")
        elif action == "request-legacy-generation":
            command.add_argument(
                "--legacy-kind", choices=("canonical-proof", "coin-evidence"), required=True
            )
            command.add_argument("--legacy-id", type=int, required=True)
            command.add_argument("--confirm-new-generation", action="store_true")
        else:
            command.add_argument("--platform", default=None)
            command.add_argument("--limit", type=int, default=20)
            if action == "list":
                command.add_argument("--generation-result", choices=GENERATION_STATES)
                command.add_argument("--send-result", choices=SEND_STATES)
                for field in ("generated-after", "generated-before", "used-after", "used-before"):
                    command.add_argument(f"--{field}")


async def _legacy(args: argparse.Namespace) -> dict[str, Any]:
    path, scope = args.database, args.scope
    if not path.is_absolute() or not path.is_file():
        raise AffiliateHistoryError("AFFILIATE_HISTORY_DATABASE_REQUIRED")
    readonly = args.history_command == "legacy-blocks"
    if readonly:
        # SQLite URI mode=ro enforces read-only even if a future repository accidentally writes.
        url = f"sqlite+aiosqlite:///file:{path.as_posix()}?mode=ro&uri=true"
    else:
        path = durable_sqlite_path(f"sqlite+aiosqlite:///{path.as_posix()}")
        url = f"sqlite+aiosqlite:///{path.as_posix()}"
    database = AffiliateShadowDatabase(url) if scope == "shadow" else Database(url)
    try:
        if readonly:
            async with database.session() as session:
                rows = await AffiliateLinkHistoryRepository(session).legacy_blocks(scope=scope)
            if args.platform not in {None, "aliexpress"}:
                rows = []
            return {"legacy_blocks": rows[: args.limit], "read_only": True}
        await validate_history_storage(database, real=True)
        if not args.confirm_new_generation:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_OPERATOR_CONFIRMATION_REQUIRED")
        async with database.session() as session:
            request = await AffiliateLinkHistoryRepository(session).request_legacy_generation(
                scope=scope,
                legacy_kind=args.legacy_kind,
                legacy_id=args.legacy_id,
                now=datetime.now(UTC),
            )
            report = {
                "generation_request": request.id,
                "state": request.state,
                "scope": scope,
                "legacy_kind": request.legacy_kind,
                "legacy_id": request.legacy_id,
                "tracking_confirmed": False,
                "attribution_unverified": True,
                "api_calls": 0,
            }
        return report
    finally:
        await database.dispose()


def command_history(args: argparse.Namespace) -> int:
    try:
        if args.history_command in {"list", "show"}:
            kwargs = {
                key: getattr(args, key)
                for key in (
                    "scope",
                    "generation_id",
                    "platform",
                    "generation_result",
                    "send_result",
                    "generated_after",
                    "generated_before",
                    "used_after",
                    "used_before",
                    "limit",
                    "include_urls",
                    "include_legacy",
                )
                if hasattr(args, key)
            }
            report = read_history(args.database, **kwargs)
        elif args.history_command == "legacy-blocks":
            report = asyncio.run(_legacy(args))
        else:
            if not args.database.is_absolute() or not args.database.is_file():
                raise AffiliateHistoryError("AFFILIATE_HISTORY_DATABASE_REQUIRED")
            with ShadowListenerLock(args.database):
                report = asyncio.run(_legacy(args))
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, AffiliateHistoryError)
            else "AFFILIATE_HISTORY_COMMAND_FAILED"
        )
        print(json.dumps({"status": "failed_safe", "error_code": code}, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0
