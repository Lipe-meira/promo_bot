"""Local identity and private (invite-only) admission.

A verified Clerk token proves *who* the caller is. Whether they may use the product is decided
here, from PostgreSQL: an existing enabled local user, or — on first contact — an active
invitation for the token's verified e-mail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_api.auth.clerk_backend import PrimaryEmailVerifier
from promo_api.auth.tokens import Principal
from promo_api.db.models import Invitation, User
from promo_api.errors import ApiError


def not_admitted(reason: str) -> ApiError:
    return ApiError(
        403,
        "NOT_ADMITTED",
        "This account has not been admitted to the private preview.",
        details={"reason": reason},
    )


async def resolve_user(
    session: AsyncSession, principal: Principal, primary_email: PrimaryEmailVerifier
) -> User:
    """Return the enabled local user for a verified principal, admitting it if invited."""

    user = await session.scalar(select(User).where(User.clerk_user_id == principal.subject))
    if user is None:
        user = await _admit(session, principal, primary_email)
    if user.disabled_at is not None:
        raise not_admitted("ACCESS_REVOKED")
    return user


async def _admit(
    session: AsyncSession, principal: Principal, primary_email: PrimaryEmailVerifier
) -> User:
    if principal.email is None:
        raise not_admitted("EMAIL_CLAIM_MISSING")
    if not principal.email_verified:
        raise not_admitted("EMAIL_NOT_VERIFIED")

    # `email_verified` only says that *some* address of the user is verified. Before admitting,
    # Clerk must confirm that this address is the verified primary one. Ask only when an active
    # invitation exists (strangers cannot drive Backend API traffic), and not while holding a
    # transaction open across the network call.
    invited = await session.scalar(
        select(Invitation.id).where(
            Invitation.email == principal.email, Invitation.revoked_at.is_(None)
        )
    )
    await session.rollback()
    if invited is None:
        raise not_admitted("NO_INVITATION")
    if not await primary_email.is_verified_primary(principal.subject, principal.email):
        raise not_admitted("PRIMARY_EMAIL_NOT_VERIFIED")

    # The row lock serializes concurrent first requests of the same account.
    invitation = await session.scalar(
        select(Invitation).where(Invitation.email == principal.email).with_for_update()
    )
    if invitation is None or invitation.revoked_at is not None:
        await session.rollback()
        raise not_admitted("NO_INVITATION")

    await session.execute(
        insert(User)
        .values(clerk_user_id=principal.subject, email=principal.email)
        .on_conflict_do_nothing()
    )
    user = await session.scalar(select(User).where(User.clerk_user_id == principal.subject))
    if user is None or (
        invitation.accepted_by_user_id is not None and invitation.accepted_by_user_id != user.id
    ):
        # The e-mail already belongs to another Clerk identity: never rebind it silently.
        await session.rollback()
        raise not_admitted("INVITATION_ALREADY_USED")
    if invitation.accepted_by_user_id is None:
        await session.execute(
            update(Invitation)
            .where(Invitation.id == invitation.id)
            .values(accepted_at=datetime.now(UTC), accepted_by_user_id=user.id)
        )
    await session.commit()
    return user


async def invite(session: AsyncSession, email: str) -> UUID:
    """Create (or re-activate) an invitation. Used by the operator CLI only."""

    normalized = normalize_email(email)
    row = await session.scalar(
        insert(Invitation)
        .values(email=normalized)
        .on_conflict_do_update(index_elements=[Invitation.email], set_={"revoked_at": None})
        .returning(Invitation.id)
    )
    user_id = await session.scalar(
        select(Invitation.accepted_by_user_id).where(Invitation.id == row)
    )
    if user_id is not None:
        await session.execute(update(User).where(User.id == user_id).values(disabled_at=None))
    await session.commit()
    assert row is not None
    return row


async def revoke(session: AsyncSession, email: str) -> bool:
    """Revoke an invitation and disable the user admitted through it, if any."""

    now = datetime.now(UTC)
    invitation = await session.scalar(
        select(Invitation).where(Invitation.email == normalize_email(email)).with_for_update()
    )
    if invitation is None:
        await session.rollback()
        return False
    await session.execute(
        update(Invitation).where(Invitation.id == invitation.id).values(revoked_at=now)
    )
    if invitation.accepted_by_user_id is not None:
        await session.execute(
            update(User).where(User.id == invitation.accepted_by_user_id).values(disabled_at=now)
        )
    await session.commit()
    return True


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    local, _, domain = normalized.partition("@")
    if (
        not local
        or "." not in domain
        or len(normalized) > 320
        or any(char.isspace() or ord(char) < 32 for char in normalized)
        or "@" in domain
    ):
        raise ValueError("invalid e-mail address")
    return normalized
