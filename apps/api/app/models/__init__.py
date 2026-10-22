"""SQLAlchemy models.

Importing this package imports every model so that ``Base.metadata`` is complete
for Alembic autogenerate and ``create_all`` in tests.
"""

from app.models.alert import AdminAlert
from app.models.api_token import ApiToken
from app.models.approval import (
    Approval,
    ApprovalDelegation,
    ApprovalRisk,
    ApprovalStatus,
    ApprovalTier,
    ApproverNotice,
)
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion
from app.models.attachment import Attachment
from app.models.audit_log import AuditLog
from app.models.budget import Budget, BudgetPeriod
from app.models.conversation import (
    Conversation,
    ConversationShare,
    ConversationStatus,
    Message,
    MessageRole,
    Run,
    RunStatus,
    ToolCall,
)
from app.models.enums import MemberRole
from app.models.evals import EvalCase, EvalCaseResult, EvalRun, EvalRunStatus, EvalSuite
from app.models.feedback import ConversationOutcome, MessageFeedback
from app.models.group import Group, GroupMember
from app.models.handoff import Handoff, HandoffChannel, HandoffStatus
from app.models.integration import (
    DbConnection,
    DbConnectionStatus,
    DbEngine,
    DbSchemaCache,
    HttpConnection,
    McpServer,
    McpServerStatus,
    McpTransport,
)
from app.models.invite import Invite
from app.models.mail import MailMessage, MailStatus
from app.models.membership import Membership
from app.models.memory import MemoryFile
from app.models.organization import Organization
from app.models.privacy import NoticeAcceptance, PrivacyNotice
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from app.models.refresh_token import RefreshToken
from app.models.retention import Erasure, LegalHold
from app.models.review import ReviewItem, ReviewState
from app.models.second_factor import UserRecoveryCode, UserSecondFactor
from app.models.secret import Secret, SecretKind
from app.models.usage import UsageEvent, UsageKind
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.models.user_token import TokenPurpose, UserToken

__all__ = [
    "AdminAlert",
    "ApiToken",
    "Approval",
    "ApprovalDelegation",
    "ApprovalRisk",
    "ApprovalStatus",
    "ApprovalTier",
    "ApproverNotice",
    "Assistant",
    "AssistantStatus",
    "AssistantVersion",
    "Attachment",
    "AuditLog",
    "Budget",
    "BudgetPeriod",
    "Chunk",
    "Conversation",
    "ConversationOutcome",
    "ConversationShare",
    "ConversationStatus",
    "DataSource",
    "DataSourceStatus",
    "DataSourceType",
    "DbConnection",
    "DbConnectionStatus",
    "DbEngine",
    "DbSchemaCache",
    "Document",
    "Erasure",
    "EvalCase",
    "EvalCaseResult",
    "EvalRun",
    "EvalRunStatus",
    "EvalSuite",
    "Group",
    "GroupMember",
    "Handoff",
    "HandoffChannel",
    "HandoffStatus",
    "HttpConnection",
    "Invite",
    "LegalHold",
    "MailMessage",
    "MailStatus",
    "McpServer",
    "McpServerStatus",
    "McpTransport",
    "MemberRole",
    "Membership",
    "MemoryFile",
    "Message",
    "MessageFeedback",
    "MessageRole",
    "NoticeAcceptance",
    "Organization",
    "PrivacyNotice",
    "RefreshToken",
    "ReviewItem",
    "ReviewState",
    "Run",
    "RunStatus",
    "Secret",
    "SecretKind",
    "TokenPurpose",
    "ToolCall",
    "UsageEvent",
    "UsageKind",
    "User",
    "UserIdentity",
    "UserRecoveryCode",
    "UserSecondFactor",
    "UserToken",
]
