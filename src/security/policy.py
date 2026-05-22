"""Approval policy engine scaffold."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .client_identity import ClientIdentity, ClientKind


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    DESTRUCTIVE = "destructive"


class ApprovalMode(str, Enum):
    MANUAL = "manual"
    APPROVE_LOW_RISK = "approve_low_risk"
    ASK_HIGH_RISK = "ask_high_risk"
    VIEW_ONLY = "view_only"
    DENY_ALL = "deny_all"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    REQUIRE_DESKTOP_CONFIRM = "require_desktop_confirm"
    REQUIRE_STRONG_CONFIRM = "require_strong_confirm"


@dataclass(frozen=True)
class ApprovalPolicy:
    policy_id: str
    mode: ApprovalMode = ApprovalMode.MANUAL


@dataclass(frozen=True)
class ApprovalResult:
    decision: PolicyDecision
    reason: str


class ApprovalPolicyEngine:
    """Conservative first-pass policy evaluator.

    It implements the safety defaults from the architecture docs: keyboards may
    approve low risk only; high and above requires desktop confirmation.
    """

    def evaluate(
        self,
        policy: ApprovalPolicy,
        risk_level: RiskLevel,
        client: Optional[ClientIdentity] = None,
    ) -> ApprovalResult:
        if policy.mode == ApprovalMode.DENY_ALL:
            return ApprovalResult(PolicyDecision.DENY, "policy denies all requests")
        if policy.mode == ApprovalMode.VIEW_ONLY:
            return ApprovalResult(PolicyDecision.DENY, "view-only policy blocks mutating actions")

        if client and client.kind == ClientKind.DEVICE_TRANSPORT and risk_level != RiskLevel.LOW:
            return ApprovalResult(
                PolicyDecision.REQUIRE_DESKTOP_CONFIRM,
                "keyboard approval is limited to low-risk requests",
            )

        if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL, RiskLevel.DESTRUCTIVE}:
            return ApprovalResult(
                PolicyDecision.REQUIRE_DESKTOP_CONFIRM,
                f"{risk_level.value} risk requires desktop confirmation",
            )

        if policy.mode == ApprovalMode.APPROVE_LOW_RISK and risk_level == RiskLevel.LOW:
            return ApprovalResult(PolicyDecision.ALLOW, "low-risk request allowed by policy")

        if policy.mode == ApprovalMode.ASK_HIGH_RISK and risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}:
            return ApprovalResult(PolicyDecision.ALLOW, "request allowed by ask-high-risk policy")

        return ApprovalResult(PolicyDecision.ASK, "manual approval required")
