"""SQLAlchemy models. Importing this package registers every mapper."""

from .ai import AgentRole, AgentTrace, ChatMessage, ChatSession, MemoryItem
from .appmodel import (
    AppElement,
    AppScreen,
    AppTransition,
    HealEvent,
    VisualBaseline,
    VisualComparison,
)
from .base import IdMixin, JSONish, TimestampMixin, new_id, utcnow
from .governance import (
    ApprovalBatch,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    PolicyRule,
    RiskTier,
    VaultSecret,
)
from .identity import RANK, ApiToken, Project, ProjectMember, Role, User
from .integrations import IntegrationConnection, Notification, PluginRecord
from .intel import (
    AnomalyRecord,
    CoverageSnapshot,
    ExplorationFinding,
    ExplorationSession,
    FailureSignature,
    JudgeVerdict,
    RCAReport,
    RecordingSession,
    TestStat,
    UsageLedger,
)
from .testing import (
    TERMINAL_RUN_STATES,
    Artifact,
    DocKind,
    RequirementDoc,
    RequirementItem,
    Run,
    RunStatus,
    RunStepRecord,
    RunTest,
    Schedule,
    StepAction,
    SuiteMember,
    TestCase,
    TestCategory,
    TestStatus,
    TestStep,
    TestSuite,
    TestVersion,
)

__all__ = [
    "RANK", "TERMINAL_RUN_STATES",
    "AgentRole", "AgentTrace", "AnomalyRecord", "ApiToken", "AppElement", "AppScreen",
    "AppTransition", "ApprovalBatch", "ApprovalRequest", "ApprovalStatus", "Artifact",
    "AuditEvent", "ChatMessage", "ChatSession", "CoverageSnapshot", "DocKind",
    "ExplorationFinding", "ExplorationSession", "RecordingSession",
    "FailureSignature", "HealEvent", "IdMixin", "IntegrationConnection", "JSONish",
    "JudgeVerdict", "MemoryItem", "Notification", "PluginRecord", "PolicyRule", "Project",
    "ProjectMember", "RCAReport", "RequirementDoc", "RequirementItem", "RiskTier", "Role",
    "Run", "RunStatus", "RunStepRecord", "RunTest", "Schedule", "StepAction", "SuiteMember",
    "TestCase", "TestCategory", "TestStat", "TestStatus", "TestStep", "TestSuite",
    "TestVersion", "TimestampMixin", "UsageLedger", "User", "VaultSecret", "VisualBaseline", "VisualComparison",
    "new_id", "utcnow",
]
