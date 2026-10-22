"""Google sign-in, the accounts (Phase 8.3): who a checked Google token is,
and what it may do.

The rules, in order:

- **Signing in** finds the account by Google's id for the person (`sub`),
  never by address. A switched-off account is refused.
- **An address that already has an account** (a password one) is not
  joined to the Google account by matching it: that is how a renamed or
  recycled mailbox, or an account someone registered first, would be
  taken over. The person signs in with their password and connects Google
  from Your account (`link`), which proves the account is theirs.
- **A new account** is made only:
  - through an invite to that very address (the invite's organisation
    and role, and no personal org); or
  - by the organisation's opt-in auto-join (`google_auto_join`): an
    account of the allowed Google Workspace domain, as an Employee.
  Either way it has no password: Google is its only way in.

Each outcome is in the audit log.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import get_logger
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.services import audit, org_settings
from app.services.google import GoogleIdentity, GoogleRefused

log = get_logger(__name__)

PROVIDER = "google"
#: A link ticket lasts long enough to reach Google and choose an account.
TICKET_TTL = timedelta(minutes=10)


async def _identity(session: AsyncSession, subject: str) -> UserIdentity | None:
    return await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == PROVIDER, UserIdentity.subject == subject
        )
    )


async def _first_org(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(
        select(Membership.org_id)
        .join(Organization, Organization.id == Membership.org_id)
        .where(
            Membership.user_id == user_id,
            Membership.deactivated_at.is_(None),
            Organization.archived_at.is_(None),
        )
        .order_by(Organization.is_personal, Membership.created_at)
        .limit(1)
        .execution_options(all_orgs=True)
    )


async def sign_in(
    session: AsyncSession, who: GoogleIdentity, *, invite_token: str | None, ip: str | None
) -> User:
    """The account a Google sign-in opens, made if the rules allow."""
    linked = await _identity(session, who.subject)
    if linked is not None:
        user = await session.get(User, linked.user_id)
        if user is None or not user.is_active:
            raise GoogleRefused("account_disabled", "the account is switched off")
        user.last_login_at = datetime.now(UTC)
        await audit.record(
            session,
            action="user.login",
            org_id=await _first_org(session, user.id),
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            meta={"via": PROVIDER},
            ip=ip,
        )
        await session.commit()
        return user

    existing = await session.scalar(select(User).where(User.email == who.email))
    if existing is not None:
        # Never joined by address (see the module's docstring).
        raise GoogleRefused(
            "password_account", "an account with this address signs in with a password"
        )

    if invite_token:
        return await _join_by_invite(session, who, invite_token, ip=ip)
    return await _auto_join(session, who, ip=ip)


async def _new_user(session: AsyncSession, who: GoogleIdentity) -> User:
    now = datetime.now(UTC)
    user = User(
        email=who.email,
        password_hash=None,
        name=who.name.strip()[:200],
        # Google verified the address (the token says so, and it is checked).
        email_verified_at=now,
        last_login_at=now,
    )
    session.add(user)
    await session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=PROVIDER,
            subject=who.subject,
            domain=who.domain,
            email=who.email,
        )
    )
    await session.flush()
    return user


async def _join_by_invite(
    session: AsyncSession, who: GoogleIdentity, invite_token: str, *, ip: str | None
) -> User:
    from app.services import auth, orgs

    invite = await auth._usable_invite(session, who.email, invite_token)
    if invite is None:
        # Wrong address, used, withdrawn, expired, or its sender can no
        # longer invite: one answer, as for a password sign-up.
        raise GoogleRefused("invite", "the invite isn't usable with this Google account")
    user = await _new_user(session, who)
    await audit.record(
        session,
        action="user.register",
        org_id=invite.org_id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={"invite": str(invite.id), "via": PROVIDER},
        ip=ip,
    )
    await orgs.join(session, invite, user, ip=ip)
    log.info("user_registered_google", user_id=str(user.id), org_id=str(invite.org_id))
    return user


async def auto_join_org(session: AsyncSession) -> Organization | None:
    """The one open organisation whose owner switched auto-join on, if the
    server allows it (a Google Workspace domain is set). Two would be a
    choice the server can't make: none, then."""
    if not settings.google_allowed_domain:
        return None
    candidates = [
        org
        for org in (
            await session.scalars(select(Organization).where(Organization.archived_at.is_(None)))
        ).all()
        if org_settings.switches_of(org).google_auto_join
    ]
    return candidates[0] if len(candidates) == 1 else None


async def _auto_join(session: AsyncSession, who: GoogleIdentity, *, ip: str | None) -> User:
    org = await auto_join_org(session)
    allowed = settings.google_allowed_domain.strip().lower()
    if org is None or not allowed or who.domain != allowed:
        raise GoogleRefused("no_invite", "sign-up is by invitation")
    user = await _new_user(session, who)
    # An Employee, whatever else: auto-join never makes a builder.
    session.add(Membership(org_id=org.id, user_id=user.id, role=MemberRole.employee))
    await audit.record(
        session,
        action="user.jit_provision",
        org_id=org.id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={"via": PROVIDER, "domain": who.domain, "role": MemberRole.employee.value},
        ip=ip,
    )
    await session.commit()
    log.info("user_joined_by_domain", user_id=str(user.id), org_id=str(org.id))
    return user


# ── linking, while signed in ─────────────────────────────────


def link_ticket(user: User) -> str:
    """A short-lived ticket naming the signed-in account, carried into the
    Google flow (a browser navigation can't carry the bearer token)."""
    payload = {
        "typ": "google_link",
        "sub": str(user.id),
        "exp": datetime.now(UTC) + TICKET_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def ticket_user_id(ticket: str) -> str:
    try:
        data = jwt.decode(ticket, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise GoogleRefused("ticket", "the link ticket expired") from exc
    if data.get("typ") != "google_link" or not data.get("sub"):
        raise GoogleRefused("ticket", "not a link ticket")
    return str(data["sub"])


async def link(session: AsyncSession, who: GoogleIdentity, *, user_id: str, ip: str | None) -> User:
    user = await session.get(User, uuid.UUID(user_id))
    if user is None or not user.is_active:
        raise GoogleRefused("account_disabled", "the account is switched off")
    taken = await _identity(session, who.subject)
    if taken is not None and taken.user_id != user.id:
        raise GoogleRefused("linked_elsewhere", "that Google account opens another account")
    # Only the account's own address (Phase 9.9): a stolen session could
    # otherwise attach the attacker's Google account, a lasting way in.
    if (who.email or "").strip().lower() != user.email.strip().lower():
        raise GoogleRefused("email_mismatch", "that Google account has another email address")
    mine = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == PROVIDER
        )
    )
    if mine is not None and mine.subject != who.subject:
        raise GoogleRefused("already_linked", "this account is linked to another Google account")
    if mine is None:
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=PROVIDER,
                subject=who.subject,
                domain=who.domain,
                email=who.email,
            )
        )
        await audit.record(
            session,
            action="user.identity.link",
            org_id=await _first_org(session, user.id),
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            meta={"provider": PROVIDER, "email": who.email, "domain": who.domain},
            ip=ip,
        )
    await session.commit()
    return user


async def unlink(session: AsyncSession, user: User, *, ip: str | None) -> None:
    from app.api.errors import BadRequest

    mine = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == PROVIDER
        )
    )
    if mine is None:
        raise BadRequest("Google isn't connected to this account", code="not_linked")
    if user.password_hash is None:
        raise BadRequest(
            "Google is this account's only way in: it can't be disconnected", code="only_way_in"
        )
    await session.delete(mine)
    await audit.record(
        session,
        action="user.identity.unlink",
        org_id=await _first_org(session, user.id),
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={"provider": PROVIDER},
        ip=ip,
    )
    await session.commit()


async def linked_email(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(UserIdentity.email).where(
            UserIdentity.user_id == user_id, UserIdentity.provider == PROVIDER
        )
    )


__all__ = [
    "PROVIDER",
    "auto_join_org",
    "link",
    "link_ticket",
    "linked_email",
    "sign_in",
    "ticket_user_id",
    "unlink",
]
