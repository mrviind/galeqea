"""SQLAlchemy models. Importing this package registers every mapper."""

from .ai import AgentRole, AgentTrace, ChatMessage, ChatSession, MemoryItem
from .appmodel import (
    AppElement,
    AppScreen,
    AppTransition,
    HealEvent,
    StepCache,
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
from .integrations import (
    IntegrationConnection,
    JiraIssueMap,
    Notification,
    PluginRecord,
    ReportPage,
    RunExport,
)
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
from .journey import STAGE_ORDER, Journey, JourneyStage, JourneyStatus
from .release import (
    Cycle,
    CycleStatus,
    DefectLink,
    DefectMap,
    Environment,
    Milestone,
    MilestoneStatus,
    SharedStep,
    TestPlan,
)
from .testing import (
    TERMINAL_RUN_STATES,
    Artifact,
    DocKind,
    RequirementDoc,
    RequirementItem,
    RequirementRule,
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
from .webhooks import WEBHOOK_EVENTS, WebhookDelivery, WebhookEndpoint

__all__ = [
    "RANK", "TERMINAL_RUN_STATES",
    "AgentRole", "AgentTrace", "AnomalyRecord", "ApiToken", "AppElement", "AppScreen", "StepCache",
    "AppTransition", "ApprovalBatch", "ApprovalRequest", "ApprovalStatus", "Artifact",
    "AuditEvent", "ChatMessage", "ChatSession", "CoverageSnapshot", "DocKind",
    "ExplorationFinding", "ExplorationSession", "RecordingSession",
    "FailureSignature", "HealEvent", "IdMixin", "IntegrationConnection", "JiraIssueMap", "JSONish", "ReportPage", "RunExport",
    "JudgeVerdict", "MemoryItem", "Notification", "PluginRecord", "PolicyRule", "Project",
    "ProjectMember", "RCAReport", "RequirementDoc", "RequirementItem", "RequirementRule",
    "RiskTier", "Role",
    "Run", "RunStatus", "RunStepRecord", "RunTest", "Schedule", "StepAction", "SuiteMember",
    "TestCase", "TestCategory", "TestStat", "TestStatus", "TestStep", "TestSuite",
    "TestVersion", "TimestampMixin", "UsageLedger", "User", "VaultSecret", "VisualBaseline", "VisualComparison",
    "WEBHOOK_EVENTS", "WebhookDelivery", "WebhookEndpoint",
    "STAGE_ORDER", "Journey", "JourneyStage", "JourneyStatus",
    "Milestone", "MilestoneStatus", "Environment", "TestPlan", "Cycle",
    "CycleStatus", "DefectLink", "DefectMap", "SharedStep",
    "new_id", "utcnow",
]
