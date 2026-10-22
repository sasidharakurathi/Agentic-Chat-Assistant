"""Email (Phase 7b.5).

Messages only notify and link into the app; names in them can't forge a
header or carry a link; plain text. They are recorded (never the body),
queued on their own queue, and retried while a failure may pass. Invites,
versions waiting for review and reviews done are emailed to the right
people. The SMTP settings come from the environment; an admin can only
send themselves a test. The preflight refuses a production setup that
would log invite links, and warns about a personal Gmail account.
"""

from __future__ import annotations

import smtplib
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from app import preflight
from app.config import settings
from app.db.session import get_sessionmaker
from app.mail import backends, templates
from app.mail.message import Mail, clean_field
from app.models.mail import MailMessage, MailStatus
from app.models.user import User
from app.services import mail as mail_service
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_deploy import _production
from tests.versions import submit

from test_rbac_escalation import Team, _team  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


@pytest.fixture
async def team(client: AsyncClient) -> Team:
    return await _team(client)


def _to(kind: str) -> list[str]:
    return sorted(m.to for m in backends.outbox if m.kind == kind)


async def _rows() -> list[MailMessage]:
    async with get_sessionmaker()() as s:
        return list(await s.scalars(select(MailMessage)))


# ── what a message may contain ───────────────────────────────


def test_a_name_cannot_forge_a_header_or_carry_a_link() -> None:
    # An address is a way to reach someone, as a link is: it goes whole.
    assert clean_field("Refunds\r\nBcc: everyone@example.com") == "Refunds Bcc: [link removed]"
    assert clean_field("Approved by CTO, see https://evil.example/login") == (
        "Approved by CTO, see [link removed]"
    )
    assert clean_field("x" * 500) == "x" * 120
    with pytest.raises(ValueError):
        Mail(to="a@example.com", subject="one\ntwo", text="", kind="test")


def test_a_message_links_only_into_the_app_and_says_so() -> None:
    m = templates.version_submitted(
        to="admin@example.com",
        assistant_name="Desk https://evil.example",
        number=3,
        submitter="Pat",
        assistant_id="a1",
    )
    assert m.subject == "Desk [link removed]: version 3 waits for your review"
    assert f"{settings.app_base_url.rstrip('/')}/assistants/a1/build?tab=history" in m.text
    assert "evil.example" not in m.text
    assert "never asks you to approve anything" in m.text
    email = m.to_email(sender="notifications@example.com", sender_name="Studio\nBcc: x")
    assert "\n" not in str(email["From"])
    assert email.get_content_type() == "text/plain"


# ── who is told what ─────────────────────────────────────────


async def test_an_invite_is_emailed_with_its_link_and_recorded_without_it(
    client: AsyncClient, team: Team
) -> None:
    r = await client.post(
        f"{API}/orgs/{team.org_id}/invites",
        json={"email": "pat@example.com", "role": "member"},
        headers=team.h(team.admin),
    )
    assert r.status_code == 201, r.text
    assert r.json()["emailed"] is True
    # The team's own four invites are in the outbox too.
    (sent,) = [m for m in backends.outbox if m.kind == "invite" and m.to == "pat@example.com"]
    assert sent.to == "pat@example.com" and r.json()["accept_url"] in sent.text
    (row,) = [x for x in await _rows() if x.kind == "invite" and x.to_address == "pat@example.com"]
    assert (row.status, row.to_address) == (MailStatus.sent, "pat@example.com")
    assert not hasattr(row, "text"), "the body, and its link, are never stored"


async def test_reviewers_hear_of_a_submission_and_the_builder_of_the_outcome(
    client: AsyncClient, team: Team
) -> None:
    builder = team.h(team.member)
    aid = (await client.post(f"{API}/assistants", json={"name": "Desk"}, headers=builder)).json()[
        "id"
    ]
    async with get_sessionmaker()() as s:
        gone = await s.get(User, uuid.UUID(team.admin2.user_id))
        assert gone is not None
        gone.is_active = False
        await s.commit()
    n = await submit(client, aid, builder)
    assert _to("version_submitted") == ["admin@example.com", "owner@example.com"]

    r = await client.post(
        f"{API}/assistants/{aid}/versions/{n}/reject",
        json={"note": "Not yet"},
        headers=team.h(team.admin),
    )
    assert r.status_code == 200, r.text
    (outcome,) = [m for m in backends.outbox if m.kind == "version_reviewed"]
    assert outcome.to == "member@example.com" and "rejected" in outcome.subject
    assert "Not yet" not in outcome.text, "the reviewer's words stay in the app"


async def test_nobody_is_emailed_about_their_own_self_approval(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = (
        await client.post(f"{API}/assistants", json={"name": "Solo"}, headers=org_headers)
    ).json()["id"]
    n = await submit(client, aid, org_headers)
    assert _to("version_submitted") == [], "the only reviewer is the submitter"
    await client.post(
        f"{API}/assistants/{aid}/versions/{n}/approve",
        json={"note": "Alone"},
        headers=org_headers,
    )
    assert _to("version_reviewed") == []


# ── sending, queueing, retrying ──────────────────────────────


async def _queued(kind: str = "test") -> uuid.UUID:
    async with get_sessionmaker()() as s:
        row = MailMessage(to_address="a@example.com", kind=kind, subject="s")
        s.add(row)
        await s.commit()
        return row.id


async def _row(message_id: uuid.UUID) -> MailMessage:
    async with get_sessionmaker()() as s:
        row = await s.get(MailMessage, message_id)
        assert row is not None
        return row


async def test_a_passing_failure_is_retried_and_a_lasting_one_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = templates.test(to="a@example.com").as_dict()

    async def busy(_m: Mail) -> None:
        raise backends.MailError("could not send: TimeoutError")

    monkeypatch.setattr(backends, "send", busy)
    mid = await _queued()
    assert await mail_service.deliver(mid, data, attempt=1) is False, "retry it"
    row = await _row(mid)
    assert (row.status, row.attempts) == (MailStatus.queued, 1)
    assert await mail_service.deliver(mid, data, attempt=settings.mail_max_tries) is True
    assert (await _row(mid)).status is MailStatus.failed, "out of attempts"

    async def refused(_m: Mail) -> None:
        raise backends.MailError("the SMTP server refused the sign-in", permanent=True)

    monkeypatch.setattr(backends, "send", refused)
    mid = await _queued()
    assert await mail_service.deliver(mid, data, attempt=1) is True
    assert (await _row(mid)).last_error == "the SMTP server refused the sign-in"
    assert mail_service.retry_delay_s(1) == 30
    assert mail_service.retry_delay_s(4) == 240
    assert mail_service.retry_delay_s(20) == mail_service.RETRY_MAX_S


async def test_the_job_asks_arq_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import worker
    from arq import Retry

    async def later(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(mail_service, "deliver", later)
    with pytest.raises(Retry):
        await worker.send_mail_job({"job_try": 2}, str(uuid.uuid4()), {})


async def test_smtp_goes_on_its_own_queue_and_a_dead_queue_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import queue

    monkeypatch.setattr(settings, "mail_backend", "smtp")
    seen: list[Any] = []

    async def down(message_id: uuid.UUID, data: dict) -> bool:
        seen.append(message_id)
        return False

    monkeypatch.setattr(queue, "enqueue_mail", down)
    row = await mail_service.send(templates.test(to="a@example.com"), org_id=None)
    assert row is not None and seen == [row.id]
    stored = await _row(row.id)
    assert (stored.status, stored.last_error) == (
        MailStatus.failed,
        "the mail queue was unavailable",
    )


async def test_mail_off_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_backend", "off")
    assert await mail_service.send(templates.test(to="a@example.com"), org_id=None) is None
    assert backends.outbox == [] and await _rows() == []


class _FakeSMTP:
    calls: ClassVar[list[str]] = []
    refuse_login = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.calls.append(f"connect {host}:{port}")

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_: Any) -> None:
        self.calls.append("quit")

    def starttls(self, context: Any) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        if self.refuse_login:
            raise smtplib.SMTPAuthenticationError(535, b"no")
        self.calls.append(f"login {user}")

    def send_message(self, msg: Any) -> None:
        self.calls.append(f"send {msg['To']}")


def test_smtp_starts_tls_signs_in_and_never_says_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    for k, v in {
        "smtp_host": "smtp.example.com",
        "smtp_username": "notify@example.com",
        "smtp_password": "app-password-value",
        "mail_from": "notify@example.com",
        "smtp_security": "starttls",
    }.items():
        monkeypatch.setattr(settings, k, v)
    _FakeSMTP.calls = []
    backends._send_smtp(templates.test(to="a@example.com"))
    assert _FakeSMTP.calls == [
        "connect smtp.example.com:587",
        "starttls",
        "login notify@example.com",
        "send a@example.com",
        "quit",
    ]
    _FakeSMTP.refuse_login = True
    try:
        with pytest.raises(backends.MailError) as refused:
            backends._send_smtp(templates.test(to="a@example.com"))
    finally:
        _FakeSMTP.refuse_login = False
    assert refused.value.permanent and "app-password-value" not in str(refused.value)


# ── the admin's view ─────────────────────────────────────────


async def test_an_admin_sees_how_mail_goes_and_tests_it_on_themselves(
    client: AsyncClient, team: Team
) -> None:
    status = await client.get(f"{API}/orgs/{team.org_id}/mail", headers=team.h(team.admin))
    assert status.status_code == 200
    assert status.json()["transport"] == "memory"
    assert "password" not in str(status.json()).lower()
    r = await client.post(f"{API}/orgs/{team.org_id}/mail:test", headers=team.h(team.admin))
    assert r.json() == {"queued": True, "to": "admin@example.com"}
    assert _to("test") == ["admin@example.com"]
    member = await client.post(f"{API}/orgs/{team.org_id}/mail:test", headers=team.h(team.member))
    assert member.status_code == 403


# ── the preflight ────────────────────────────────────────────


def _findings(**over: Any) -> dict[str, str]:
    report = preflight.Report()
    # The tests' MAIL_BACKEND=memory (conftest) is in the environment, which
    # Settings reads: unset it here unless a case names one.
    preflight.review_mail(_production(**{"mail_backend": None, **over}), report)
    return {f.name: f.level for f in report.findings}


def test_the_preflight_checks_mail_in_production() -> None:
    assert _findings(mail_backend="console") == {"MAIL_BACKEND": "fail"}
    assert _findings() == {"SMTP_HOST": "warn"}, "off: links are copied by hand"
    assert _findings(smtp_host="smtp.example.com", smtp_username="n@example.com") == {
        "SMTP_PASSWORD": "fail"
    }
    assert _findings(smtp_host="smtp.example.com") == {"MAIL_FROM": "fail"}
    gmail = _findings(
        smtp_host="smtp.gmail.com",
        smtp_username="someone@gmail.com",
        smtp_password="x",
        mail_from="someone@gmail.com",
    )
    assert gmail == {"MAIL_FROM / SMTP_USERNAME": "warn"}
    assert (
        _findings(
            smtp_host="smtp.company.example",
            smtp_username="n@company.example",
            smtp_password="x",
            mail_from="n@company.example",
        )
        == {}
    )


def test_a_dated_invite_reads_well() -> None:
    m = templates.invite(
        to="a@example.com",
        org_name="Acme",
        role="member",
        inviter="Pat",
        link="https://studio.example/invites/t",
        expires=datetime(2026, 10, 16, tzinfo=UTC),
    )
    assert "until 16 Oct 2026" in m.text and "https://studio.example/invites/t" in m.text
