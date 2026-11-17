"""Operator CLI for the private preview: `promo-api-admin invite|revoke|list`.

It talks to the database named by `PROMO_API_DATABASE_URL` and nothing else. There is no HTTP
endpoint for admission on purpose: nobody can invite themselves.

E-mail addresses are personal data. `invite` and `revoke` therefore read the address from standard
input (one line) and never from an argument, which every local user can read in the process list
and which ends up in shell history and CI logs. Nothing here prints an address back unless
`list --reveal` is used, and error messages never echo input.

    printf '%s\\n' "$ADDRESS" | promo-api-admin invite      # or type it when prompted
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from typing import NoReturn, TextIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from promo_api import identity
from promo_api.db.engine import create_operator_engine
from promo_api.db.models import Invitation


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # `message` quotes the offending arguments: it may be an address typed by mistake.
        self.print_usage(sys.stderr)
        self.exit(
            2,
            "promo-api-admin: invalid arguments. invite and revoke read the e-mail address from "
            "standard input, never from an argument.\n",
        )


def mask_email(email: str) -> str:
    """`someone@example.test` -> `s***@example.test`."""

    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def _read_email(stdin: TextIO) -> str:
    line = getpass.getpass("E-mail (not echoed): ") if stdin.isatty() else stdin.readline()
    return line.strip()


async def _run(args: argparse.Namespace, engine: AsyncEngine, email: str | None) -> int:
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if args.command == "invite":
                assert email is not None
                await identity.invite(session, email)
                print("invited")
            elif args.command == "revoke":
                assert email is not None
                found = await identity.revoke(session, email)
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
                    shown = row.email if args.reveal else mask_email(row.email)
                    print(f"{state}\t{shown}")
        return 0
    finally:
        await engine.dispose()


def entrypoint(argv: list[str] | None = None, *, stdin: TextIO | None = None) -> int:
    parser = _Parser(prog="promo-api-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("invite", help="admit the address read from standard input")
    commands.add_parser("revoke", help="revoke the address read from standard input")
    listing = commands.add_parser("list", help="list invitations (addresses are masked)")
    listing.add_argument("--reveal", action="store_true", help="print full addresses")
    args = parser.parse_args(argv)

    email: str | None = None
    if args.command in {"invite", "revoke"}:
        raw = _read_email(stdin if stdin is not None else sys.stdin)
        if not raw:
            print("An e-mail address is required on standard input.", file=sys.stderr)
            return 2
        try:
            email = identity.normalize_email(raw)
        except ValueError:
            print("The e-mail address on standard input is not valid.", file=sys.stderr)
            return 2

    database_url = os.environ.get("PROMO_API_DATABASE_URL", "")
    if not database_url.startswith("postgresql+asyncpg://"):
        print("PROMO_API_DATABASE_URL must be a postgresql+asyncpg:// URL", file=sys.stderr)
        return 2
    try:
        engine = create_operator_engine(database_url)
    except ValueError as exc:  # the message names parameters only, never the URL
        print(str(exc), file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(args, engine, email))
    except Exception as exc:
        # A traceback would print the host (TLS failures) and, with some drivers, the bound e-mail
        # address. The type is enough to tell an outage from a refused credential.
        print(f"The database operation failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
