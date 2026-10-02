"""Read-only audit views. No settings, transport, recovery, migration or purge."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from promo_bot.database.history_repository import AffiliateHistoryError

GENERATION_STATES = (
    "REQUESTED",
    "PREPARED",
    "CALL_STARTED",
    "CONFIRMED",
    "REJECTED",
    "FAILED",
    "UNCERTAIN",
)
SEND_STATES = ("SEND_RESERVED", "SEND_IN_FLIGHT", "SEND_CONFIRMED", "SEND_FAILED", "SEND_UNCERTAIN")


@contextmanager
def readonly_history(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_absolute() or not path.is_file():
        raise AffiliateHistoryError("AFFILIATE_HISTORY_DATABASE_REQUIRED")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if (
            not {"affiliate_link_generations", "affiliate_link_uses", "affiliate_link_use_links"}
            <= tables
        ):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_SCHEMA_REQUIRED")
        yield connection
    finally:
        connection.close()


def _date(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_PERIOD_INVALID")
    from datetime import UTC

    return parsed.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _generation(row: sqlite3.Row, *, include_urls: bool, include_legacy: bool) -> dict[str, Any]:
    fields = (
        "id",
        "scope",
        "platform",
        "provider",
        "operation",
        "state",
        "operator_requested_at",
        "prepared_at",
        "call_started_at",
        "generated_at",
        "finished_at",
        "expires_at",
        "contract_version",
        "correlation_mode",
        "origin_missing_reason",
        "error_code",
        "request_reason",
        "legacy_kind",
        "legacy_id",
    )
    result = {field: row[field] for field in fields}
    result.update(
        tracking_confirmed=bool(row["tracking_confirmed"]),
        attribution_unverified=True,
        origin=json.loads(row["origin"]) if row["origin"] else None,
        validation_facts=json.loads(row["validation_facts"]) if row["validation_facts"] else None,
        url_included=include_urls and row["state"] == "CONFIRMED",
        commission_confirmed=False,
        history_authorizes_reuse=False,
    )
    if include_urls and row["state"] == "CONFIRMED":
        result["generated_url"] = row["generated_url"]
    if include_legacy and row["legacy_record_snapshot"]:
        snapshot = json.loads(row["legacy_record_snapshot"])
        if not include_urls:
            for field in ("short_link", "promotion_link", "canonical_url"):
                if field in snapshot.get("record", {}):
                    snapshot["record"][field] = "<redacted>"
            if "canonical_url" in snapshot.get("candidate_identity", {}):
                snapshot["candidate_identity"]["canonical_url"] = "<redacted>"
        result["legacy_snapshot"] = snapshot
    return result


def _uses(connection: sqlite3.Connection, generation_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT u.*, l.cache_hit FROM affiliate_link_uses u JOIN affiliate_link_use_links l "
        "ON u.id=l.use_id WHERE l.generation_id=? ORDER BY u.occurred_at,u.id",
        (generation_id,),
    )
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "state": row["state"],
            "effective_send_result": "SEND_UNCERTAIN"
            if row["state"] == "SEND_IN_FLIGHT"
            else row["state"]
            if row["kind"] == "SEND"
            else None,
            "occurred_at": row["occurred_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "cache_hit": bool(row["cache_hit"]),
            "operational_kind": row["operational_kind"],
            "operational_id": row["operational_id"],
            "destination_key": row["destination_key"],
            "telegram_message_id": row["telegram_message_id"],
            "error_code": row["error_code"],
            "origin": json.loads(row["origin"]) if row["origin"] else None,
            "origin_missing_reason": row["origin_missing_reason"],
        }
        for row in rows
    ]


def read_history(
    path: Path,
    *,
    scope: str,
    generation_id: str | None = None,
    platform: str | None = None,
    generation_result: str | None = None,
    send_result: str | None = None,
    generated_after: str | None = None,
    generated_before: str | None = None,
    used_after: str | None = None,
    used_before: str | None = None,
    limit: int = 20,
    include_urls: bool = False,
    include_legacy: bool = False,
) -> dict[str, Any]:
    if scope not in {"shadow", "runtime"} or not 1 <= limit <= 200:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_QUERY_INVALID")
    if platform is not None and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", platform, re.ASCII) is None:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_QUERY_INVALID")
    if generation_result is not None and generation_result not in GENERATION_STATES:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_QUERY_INVALID")
    if send_result is not None and send_result not in SEND_STATES:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_QUERY_INVALID")
    conditions = ["g.scope=?"]
    parameters: list[str | None] = [scope]
    for field, value in (
        ("id", generation_id),
        ("platform", platform),
        ("state", generation_result),
    ):
        if value is not None:
            conditions.append(f"g.{field}=?")
            parameters.append(value)
    for field, sign, value in (
        ("generated_at", ">=", generated_after),
        ("generated_at", "<", generated_before),
    ):
        if value is not None:
            conditions.append(f"g.{field}{sign}?")
            parameters.append(_date(value))
    if send_result or used_after or used_before:
        uses_where = ["l.generation_id=g.id"]
        if send_result:
            if send_result == "SEND_UNCERTAIN":
                uses_where.append("u.state IN ('SEND_UNCERTAIN','SEND_IN_FLIGHT')")
            else:
                uses_where.append("u.state=?")
                parameters.append(send_result)
            uses_where.append("u.kind='SEND'")
        for sign, value in ((">=", used_after), ("<", used_before)):
            if value:
                uses_where.append(f"u.occurred_at{sign}?")
                parameters.append(_date(value))
        conditions.append(
            "EXISTS (SELECT 1 FROM affiliate_link_use_links l "
            "JOIN affiliate_link_uses u ON u.id=l.use_id WHERE " + " AND ".join(uses_where) + ")"
        )
    with readonly_history(path) as connection:
        rows = connection.execute(
            "SELECT g.* FROM affiliate_link_generations g WHERE "
            + " AND ".join(conditions)
            + " ORDER BY g.created_at DESC,g.id LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        generations = [
            _generation(row, include_urls=include_urls, include_legacy=include_legacy)
            for row in rows
        ]
        for generation in generations:
            generation["uses"] = _uses(connection, generation["id"])
        if generation_id is not None:
            if not generations:
                raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_NOT_FOUND")
            return {"generation": generations[0], "read_only": True}
        return {"generations": generations, "count": len(generations), "read_only": True}
