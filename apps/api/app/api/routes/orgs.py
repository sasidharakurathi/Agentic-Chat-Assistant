from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import (
    ClientIP,
    CurrentUser,
    OrgMembership,
    PageQuery,
    SessionDep,
    per_user_limit,
    require_role,
)
from app.config import settings
from app.logging import get_logger
from app.mail import templates as mail_templates
from app.models.audit_log import AuditLog
from app.models.enums import MemberRole
from app.models.mail import MailMessage, MailStatus
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.schemas.common import Message, Page
from app.schemas.org import (
    AuditEntryOut,
    AuditIntegrityOut,
    DeactivateRequest,
    InviteAccepted,
    InviteCreate,
    InviteOut,
    InvitePreview,
    LeavingOut,
    MailStatusOut,
    MailTestOut,
    MemberOut,
    OrgCreate,
    OrgOut,
    OrgSettingsOut,
    OrgSettingsUpdate,
    RoleUpdate,
)
from app.schemas.privacy import NoticeAdminOut, NoticeRevisionOut, NoticeUpdate, ProcessorOut
from app.schemas.usage import GroupBy, UsageRollupResponse
from app.services import audit, audit_chain, notify, org_settings, people, privacy
from app.services import mail as mail_service
from app.services import orgs as orgs_service
from app.services import usage as usage_service

router = APIRouter(tags=["orgs"])

RequireAdmin = Depends(require_role(MemberRole.admin))
#: The audit export and its integrity check (Phase 7b.7): the owner, the one
#: role no admin can grant themselves.
RequireOwner = Depends(require_role(MemberRole.owner))
log = get_logger(__name__)
# Role changes don't use RequireOwner: "owner required" is only true when a
# change *touches* owner, which a route dependency cannot see — see
# `orgs_service._authorise_role_change`.


@router.get("/orgs", response_model=Page[OrgOut])
async def list_orgs(user: CurrentUser, session: SessionDep, page: PageQuery) -> Page[OrgOut]:
    orgs = await orgs_service.list_for_user(session, user.id, limit=page.limit, cursor=page.cursor)
    return Page(items=[OrgOut.model_validate(o) for o in orgs.items], next_cursor=orgs.next_cursor)


@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> OrgOut:
    org = await orgs_service.create(session, user_id=user.id, name=body.name, ip=ip)
    return OrgOut.model_validate(org)


def _settings_out(org: Organization, membership: Membership) -> OrgSettingsOut:
    switches = org_settings.switches_of(org)
    return OrgSettingsOut(
        name=org.name,
        **switches.model_dump(),
        org_mode=settings.org_mode,
        can_edit=membership.role is MemberRole.owner,
    )


@router.get("/orgs/{org_id}/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    org_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
) -> OrgSettingsOut:
    """The organisation's name and switches (Phase 7b.8), for its admins."""
    org = await session.get(Organization, org_id)
    assert org is not None  # the membership above is in it
    return _settings_out(org, actor)


@router.patch("/orgs/{org_id}/settings", response_model=OrgSettingsOut)
async def update_org_settings(
    org_id: Annotated[uuid.UUID, Path()],
    body: OrgSettingsUpdate,
    actor: Annotated[Membership, RequireOwner],
    session: SessionDep,
    ip: ClientIP,
) -> OrgSettingsOut:
    """Rename the organisation, or change a switch: the owner's. Each change
    is in the audit log, before and after."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    # Null is a value here (Phase 9.7): no cap on builder testing.
    if "builder_monthly_usd" in body.model_fields_set:
        changes["builder_monthly_usd"] = body.builder_monthly_usd
    name = changes.pop("name", None)
    org = await org_settings.update(
        session, org_id=org_id, actor_id=actor.user_id, name=name, changes=changes, ip=ip
    )
    return _settings_out(org, actor)


async def _notice_admin_out(
    session: SessionDep, org_id: uuid.UUID, membership: Membership
) -> NoticeAdminOut:
    notice = await privacy.current(session, org_id)
    return NoticeAdminOut(
        version=notice.version,
        revision=notice.revision,
        body=notice.body,
        custom=notice.custom,
        updated_at=notice.updated_at,
        contact=notice.contact,
        processors=[ProcessorOut(name=p.name, what=p.what) for p in privacy.processors()],
        accepted=await privacy.acceptances(session, org_id, notice.version),
        default_body=privacy.DEFAULT_BODY,
        can_edit=membership.role is MemberRole.owner,
    )


@router.get("/orgs/{org_id}/notice", response_model=NoticeAdminOut)
async def get_notice(
    org_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
) -> NoticeAdminOut:
    """The privacy notice (Phase 8.7), for its admins: the text, its version,
    and how many have accepted it."""
    return await _notice_admin_out(session, org_id, actor)


@router.put("/orgs/{org_id}/notice", response_model=NoticeAdminOut)
async def update_notice(
    org_id: Annotated[uuid.UUID, Path()],
    body: NoticeUpdate,
    actor: Annotated[Membership, RequireOwner],
    session: SessionDep,
    ip: ClientIP,
) -> NoticeAdminOut:
    """The owner's text for the notice. `ask_again`: a real change, which
    everyone accepts again before their next message; off for a typo."""
    await privacy.update(
        session,
        org_id=org_id,
        actor_id=actor.user_id,
        body=body.body,
        ask_again=body.ask_again,
        ip=ip,
    )
    return await _notice_admin_out(session, org_id, actor)


@router.get("/orgs/{org_id}/notice/revisions", response_model=list[NoticeRevisionOut])
async def notice_revisions(
    org_id: Annotated[uuid.UUID, Path()],
    _actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
) -> list[NoticeRevisionOut]:
    """Every saved text, newest first: what each version said when people
    accepted it."""
    rows = await privacy.revisions(session, org_id)
    authors = {
        u.id: (u.name or u.email)
        for u in (
            await session.scalars(
                select(User).where(User.id.in_({r.created_by for r in rows if r.created_by}))
            )
        ).all()
    }
    return [
        NoticeRevisionOut(
            revision=r.revision,
            version=r.version,
            body=r.body,
            created_at=r.created_at,
            created_by=authors.get(r.created_by) if r.created_by else None,
        )
        for r in rows
    ]


@router.get("/orgs/{org_id}/members", response_model=Page[MemberOut])
async def list_members(
    org_id: Annotated[uuid.UUID, Path()],
    _membership: OrgMembership,
    session: SessionDep,
    page: PageQuery,
) -> Page[MemberOut]:
    rows = await orgs_service.list_members(session, org_id, limit=page.limit, cursor=page.cursor)
    return Page(
        items=[_member_out(u, m) for u, m in rows.items],
        next_cursor=rows.next_cursor,
    )


@router.patch(
    "/orgs/{org_id}/members/{user_id}",
    response_model=MemberOut,
    dependencies=[RequireAdmin],
)
async def update_member_role(
    org_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    body: RoleUpdate,
    actor: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> MemberOut:
    membership = await orgs_service.change_role(
        session,
        org_id=org_id,
        actor_user_id=actor.id,
        target_user_id=user_id,
        new_role=body.role,
        ip=ip,
    )
    target = await session.get(User, user_id)
    assert target is not None  # change_role already verified membership
    return _member_out(target, membership)


def _member_out(user: User, m: Membership) -> MemberOut:
    return MemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=m.role,
        joined_at=m.created_at,
        is_active=user.is_active and m.deactivated_at is None,
        deactivated_at=m.deactivated_at,
    )


# ── people leaving (Phase 7b.4) ──────────────────────────────


@router.get(
    "/orgs/{org_id}/members/{user_id}/leaving",
    response_model=LeavingOut,
    dependencies=[RequireAdmin],
)
async def member_leaving(
    org_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    session: SessionDep,
) -> LeavingOut:
    """What deactivating them would hand on, close or revoke: shown before
    an admin confirms."""
    found = await people.leaving(session, org_id, user_id)
    return LeavingOut.model_validate(asdict(found))


@router.post(
    "/orgs/{org_id}/members/{user_id}:deactivate",
    response_model=MemberOut,
)
async def deactivate_member(
    org_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    body: DeactivateRequest,
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> MemberOut:
    """Remove someone from the organisation: their access and sessions end,
    their assistants pass to `transfer_to` (the admin by default), their
    pending approvals close and their unused invites are withdrawn."""
    membership = await people.deactivate(
        session,
        org_id=org_id,
        actor_user_id=actor.user_id,
        target_user_id=user_id,
        transfer_to=body.transfer_to,
        ip=ip,
    )
    target = await session.get(User, user_id)
    assert target is not None
    return _member_out(target, membership)


@router.post(
    "/orgs/{org_id}/members/{user_id}:reactivate",
    response_model=MemberOut,
)
async def reactivate_member(
    org_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> MemberOut:
    membership = await people.reactivate(
        session, org_id=org_id, actor_user_id=actor.user_id, target_user_id=user_id, ip=ip
    )
    target = await session.get(User, user_id)
    assert target is not None
    return _member_out(target, membership)


@router.post(
    "/orgs/{org_id}/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireAdmin],
)
async def create_invite(
    org_id: Annotated[uuid.UUID, Path()],
    body: InviteCreate,
    actor: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> InviteOut:
    invite, raw = await orgs_service.create_invite(
        session,
        org_id=org_id,
        actor_user_id=actor.id,
        email=body.email,
        role=body.role,
        ip=ip,
    )
    link = orgs_service.invite_accept_url(raw)
    org = await session.get(Organization, org_id)
    try:
        emailed = await notify.invite(
            org_id=org_id,
            to=invite.email,
            org_name=org.name if org else "your team",
            role=invite.role.value,
            inviter=actor.name or actor.email,
            link=link,
            expires=invite.expires_at,
        )
    except Exception as exc:  # the link is still shown to send by hand
        log.warning("invite_mail_failed", error=type(exc).__name__)
        emailed = False
    return InviteOut(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        accept_url=link,
        invited_by=invite.invited_by,
        created_at=invite.created_at,
        emailed=emailed,
    )


# ── email (Phase 7b.5) ───────────────────────────────────────


@router.get("/orgs/{org_id}/mail", response_model=MailStatusOut, dependencies=[RequireAdmin])
async def mail_status(org_id: Annotated[uuid.UUID, Path()], session: SessionDep) -> MailStatusOut:
    """How email is sent, and how it has gone lately. The settings are the
    server's environment, never editable here."""
    since = datetime.now(UTC) - timedelta(days=7)
    counts = dict(
        (
            await session.execute(
                select(MailMessage.status, func.count())
                .where(MailMessage.org_id == org_id, MailMessage.created_at >= since)
                .group_by(MailMessage.status)
            )
        )
        .tuples()
        .all()
    )
    return MailStatusOut(
        transport=settings.mail_transport,
        from_address=(settings.mail_from or settings.smtp_username) or None,
        host=settings.smtp_host or None,
        sent_7d=int(counts.get(MailStatus.sent, 0)),
        failed_7d=int(counts.get(MailStatus.failed, 0)),
    )


@router.post(
    "/orgs/{org_id}/mail:test",
    response_model=MailTestOut,
    dependencies=[RequireAdmin, per_user_limit("mail_test", "rate_limit_mail_test")],
)
async def mail_test(
    org_id: Annotated[uuid.UUID, Path()], actor: CurrentUser, session: SessionDep, ip: ClientIP
) -> MailTestOut:
    """Email a test message to the admin asking, and nobody else: it can't
    be pointed at another address or server."""
    row = await mail_service.send(mail_templates.test(to=actor.email), org_id=org_id)
    await audit.record(
        session,
        action="mail.test",
        org_id=org_id,
        actor_user_id=actor.id,
        target_type="user",
        target_id=actor.id,
        meta={"queued": row is not None},
        ip=ip,
    )
    await session.commit()
    return MailTestOut(queued=row is not None, to=actor.email)


@router.get(
    "/orgs/{org_id}/invites",
    response_model=list[InviteOut],
    dependencies=[RequireAdmin],
)
async def list_invites(
    org_id: Annotated[uuid.UUID, Path()], session: SessionDep
) -> list[InviteOut]:
    """Invites nobody has used or withdrawn (Phase 7b.4). The link itself
    was shown once, at creation; a lost one is replaced by inviting again."""
    return [
        InviteOut(
            id=i.id,
            email=i.email,
            role=i.role,
            expires_at=i.expires_at,
            invited_by=i.invited_by,
            created_at=i.created_at,
        )
        for i in await orgs_service.list_invites(session, org_id)
    ]


@router.delete("/orgs/{org_id}/invites/{invite_id}", response_model=Message)
async def revoke_invite(
    org_id: Annotated[uuid.UUID, Path()],
    invite_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> Message:
    """Withdraw an unused invite: its link opens nothing afterwards."""
    await orgs_service.revoke_invite(
        session, org_id=org_id, invite_id=invite_id, actor=actor, ip=ip
    )
    return Message(message="revoked")


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite(token: Annotated[str, Path()], session: SessionDep) -> InvitePreview:
    """Unauthenticated on purpose: the invitee may not have an account yet,
    and needs to know which email to sign in with. The token is 32 random
    bytes and single-use, so holding it is the authorisation."""
    invite, org = await orgs_service.preview_invite(session, raw_token=token)
    state: Literal["pending", "accepted", "expired", "revoked"] = (
        "accepted"
        if invite.accepted_at is not None
        else "revoked"
        if invite.revoked_at is not None
        or not await orgs_service.inviter_still_may(session, invite)
        else "expired"
        if invite.expires_at <= datetime.now(UTC)
        else "pending"
    )
    return InvitePreview(
        org_name=org.name,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        status=state,
    )


@router.post("/invites/{token}/accept", response_model=InviteAccepted)
async def accept_invite(
    token: Annotated[str, Path()], user: CurrentUser, session: SessionDep, ip: ClientIP
) -> InviteAccepted:
    membership = await orgs_service.accept_invite(session, raw_token=token, user=user, ip=ip)
    _, org = await orgs_service.preview_invite(session, raw_token=token)
    return InviteAccepted(org_id=org.id, org_name=org.name, role=membership.role)


@router.get("/orgs/{org_id}/usage", response_model=UsageRollupResponse)
async def get_usage(
    org_id: Annotated[uuid.UUID, Path()],
    membership: OrgMembership,
    session: SessionDep,
    group_by: Annotated[GroupBy, Query()] = "assistant",
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> UsageRollupResponse:
    rows = await usage_service.rollup(
        session,
        org_id=org_id,
        group_by=group_by,
        date_from=from_,
        date_to=to,
        limit=limit,
        viewer_id=membership.user_id,
        viewer_is_admin=membership.role.satisfies(MemberRole.admin),
    )
    return UsageRollupResponse(group_by=group_by, rows=rows)


def _audit_filter(
    action: Annotated[str | None, Query(max_length=120)] = None,
    actor: Annotated[uuid.UUID | None, Query()] = None,
    target: Annotated[str | None, Query(max_length=80)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> orgs_service.AuditFilter:
    return orgs_service.AuditFilter(
        action=action, actor_id=actor, target_id=target, since=since, until=until
    )


#: Rows one export may hold: a year of a busy org, and a bounded response.
EXPORT_LIMIT = 50_000


@router.get("/orgs/{org_id}/audit-log.csv", response_class=Response)
async def export_audit_log(
    org_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireOwner],
    session: SessionDep,
    ip: ClientIP,
    filters: Annotated[orgs_service.AuditFilter, Depends(_audit_filter)],
) -> Response:
    """The owner's copy of the audit log, as CSV (Phase 7b.7). The export is
    itself recorded."""
    rows = (
        await session.scalars(
            orgs_service.audit_query(org_id, filters)
            .order_by(AuditLog.created_at, AuditLog.id)
            .limit(EXPORT_LIMIT)
        )
    ).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["when", "seq", "action", "actor", "actor_id", "target_type", "target_id", "ip", "details"]
    )
    for r in rows:
        writer.writerow(
            [
                r.created_at.isoformat(),
                r.seq if r.seq is not None else "",
                r.action,
                r.actor_label or "",
                str(r.actor_user_id or ""),
                r.target_type or "",
                r.target_id or "",
                r.ip or "",
                json.dumps(r.meta or {}, sort_keys=True),
            ]
        )
    await audit.record(
        session,
        action="audit.export",
        org_id=org_id,
        actor_user_id=actor.user_id,
        meta={"rows": len(rows), "action": filters.action},
        ip=ip,
    )
    await session.commit()
    return Response(
        content=out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )


@router.get(
    "/orgs/{org_id}/audit-log/integrity",
    response_model=AuditIntegrityOut,
    dependencies=[RequireOwner],
)
async def audit_integrity(
    org_id: Annotated[uuid.UUID, Path()], session: SessionDep
) -> AuditIntegrityOut:
    """Seal what is waiting, then check the hash chain from its start."""
    await audit_chain.seal(session)
    found = await audit_chain.verify(session)
    return AuditIntegrityOut(**asdict(found))


@router.get(
    "/orgs/{org_id}/audit-log",
    response_model=Page[AuditEntryOut],
    dependencies=[RequireAdmin],
)
async def list_audit_log(
    org_id: Annotated[uuid.UUID, Path()],
    session: SessionDep,
    page: PageQuery,
    filters: Annotated[orgs_service.AuditFilter, Depends(_audit_filter)],
) -> Page[AuditEntryOut]:
    """Newest first, narrowed by kind of action (a prefix), person, target
    and dates."""
    entries = await orgs_service.list_audit(
        session, org_id=org_id, limit=page.limit, cursor=page.cursor, filters=filters
    )
    return Page(
        items=[AuditEntryOut.model_validate(e) for e in entries.items],
        next_cursor=entries.next_cursor,
    )
