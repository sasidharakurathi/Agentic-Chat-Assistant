"""Idempotent dev seed: one org + one owner user from SEED_* settings.

Run:  just seed   (or)   python -m scripts.seed
Assumes migrations are already applied (`just migrate`).
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.db.session import get_sessionmaker
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.security.passwords import hash_password
from app.services.slug import slugify
from sqlalchemy import select


async def _seed() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        email = settings.seed_admin_email.strip().lower()
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                password_hash=hash_password(settings.seed_admin_password),
                name="Admin",
            )
            session.add(user)
            await session.flush()
            print(f"created user {email}")
        else:
            print(f"user {email} already exists")

        org = await session.scalar(
            select(Organization).where(Organization.slug == slugify(settings.seed_org_name))
        )
        if org is None:
            org = Organization(name=settings.seed_org_name, slug=slugify(settings.seed_org_name))
            session.add(org)
            await session.flush()
            print(f"created org {org.slug}")
        else:
            print(f"org {org.slug} already exists")

        membership = await session.scalar(
            select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id)
        )
        if membership is None:
            session.add(Membership(org_id=org.id, user_id=user.id, role=MemberRole.owner))
            print("added owner membership")

        await session.commit()
    print("seed complete")


if __name__ == "__main__":
    asyncio.run(_seed())
