"""Operator CLI for the private preview: `promo-api-admin invite|revoke|list`.

It talks to the database named by `PROMO_API_DATABASE_URL` and nothing else. There is no HTTP
endpoint for admission on purpose: nobody can invite themselves.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from promo_api import identity
from promo_api.db.models import Invitation


async def _run(args: argparse.Namespace, database_url: str) -> int:
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if args.command == "invite":
                await identity.invite(session, args.email)
                print("invited")
            elif args.command == "revoke":
                found = await identity.revoke(session, args.email)
                print("revoked" if found else "no such invitation")
                return 0 if found else 1
            else:
                rows = await session.execute(select(Invitation).order_by(Invitation.created_at))
                for row in rows.scalars():
                    state = (
                        "revoked"
                        if row.revoked_at
                        else "accepted"
                        if row.accepted_at
                        else "pending"
                    )
                    print(f"{state}\t{row.email}")
        return 0
    finally:
        await engine.dispose()


def entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promo-api-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("invite", "revoke"):
        commands.add_parser(name).add_argument("email")
    commands.add_parser("list")
    args = parser.parse_args(argv)

    database_url = os.environ.get("PROMO_API_DATABASE_URL", "")
    if not database_url.startswith("postgresql+asyncpg://"):
        print("PROMO_API_DATABASE_URL must be a postgresql+asyncpg:// URL", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(args, database_url))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(entrypoint())
