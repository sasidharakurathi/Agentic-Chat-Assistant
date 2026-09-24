"""SQLAlchemy models.

Importing this package imports every model so that ``Base.metadata`` is complete
for Alembic autogenerate and ``create_all`` in tests.
"""

from app.models.api_token import ApiToken
from app.models.approval import Approval, ApprovalRisk, ApprovalStatus
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion
from app.models.audit_log import AuditLog
from app.models.conversation import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    Run,
    RunStatus,
    ToolCall,
)
from app.models.enums import MemberRole
from app.models.integration import (
    DbConnection,
    DbConnectionStatus,
    DbEngine,
    DbSchemaCache,
)
from app.models.invite import Invite
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from app.models.refresh_token import RefreshToken
from app.models.secret import Secret, SecretKind
from app.models.usage import UsageEvent, UsageKind
from app.models.user import User

__all__ = [
    "ApiToken",
    "Approval",
    "ApprovalRisk",
    "ApprovalStatus",
    "Assistant",
    "AssistantStatus",
    "AssistantVersion",
    "AuditLog",
    "Chunk",
    "Conversation",
    "ConversationStatus",
    "DataSource",
    "DataSourceStatus",
    "DataSourceType",
    "DbConnection",
    "DbConnectionStatus",
    "DbEngine",
    "DbSchemaCache",
    "Document",
    "Invite",
    "MemberRole",
    "Membership",
    "Message",
    "MessageRole",
    "Organization",
    "RefreshToken",
    "Run",
    "RunStatus",
    "Secret",
    "SecretKind",
    "ToolCall",
    "UsageEvent",
    "UsageKind",
    "User",
]
