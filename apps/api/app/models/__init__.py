"""SQLAlchemy models.

Importing this package imports every model so that ``Base.metadata`` is complete
for Alembic autogenerate and ``create_all`` in tests.
"""

from app.models.api_token import ApiToken
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion
from app.models.audit_log import AuditLog
from app.models.conversation import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    Run,
    RunStatus,
)
from app.models.enums import MemberRole
from app.models.invite import Invite
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.usage import UsageEvent, UsageKind
from app.models.user import User

__all__ = [
    "ApiToken",
    "Assistant",
    "AssistantStatus",
    "AssistantVersion",
    "AuditLog",
    "Conversation",
    "ConversationStatus",
    "Invite",
    "MemberRole",
    "Membership",
    "Message",
    "MessageRole",
    "Organization",
    "RefreshToken",
    "Run",
    "RunStatus",
    "UsageEvent",
    "UsageKind",
    "User",
]
