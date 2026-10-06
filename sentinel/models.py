"""
Shared Pydantic models used across Sentinel modules.
"""
from __future__ import annotations
import time
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class IncidentSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    ROOT_CAUSE_IDENTIFIED = "ROOT_CAUSE_IDENTIFIED"
    REMEDIATION_CREATED = "REMEDIATION_CREATED"
    PATCH_VALIDATED = "PATCH_VALIDATED"
    DEPLOYED = "DEPLOYED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"


class MetricsPayload(BaseModel):
    service: str
    version: str
    metrics: dict[str, Any]
    pool: dict[str, Any]
    failure_mode: str = "none"
    timestamp: float = Field(default_factory=time.time)


class Incident(BaseModel):
    incident_id: str
    service: str
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.DETECTED
    symptoms: list[str] = Field(default_factory=list)
    triggering_metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    # Populated by later pipeline stages
    rca: str | None = None
    rca_confidence: float | None = None
    candidate_causes: list[dict] | None = None
    fix_branch: str | None = None
    fix_pr_url: str | None = None
    fix_pr_number: int | None = None
    validation_result: dict | None = None
    verification_result: dict | None = None
    resolved_at: float | None = None

    def transition(self, new_status: IncidentStatus) -> None:
        self.status = new_status
        self.updated_at = time.time()


class RCAResult(BaseModel):
    incident_id: str
    summary: str
    root_cause: str
    confidence: float          # 0-1
    candidate_causes: list[dict]
    evidence: list[str]
    affected_file: str | None = None
    affected_function: str | None = None
    recommended_fix: str | None = None


class ValidationResult(BaseModel):
    incident_id: str
    passed: bool
    unit_tests: dict      # {"passed": int, "failed": int}
    integration_tests: dict
    lint: dict
    connection_leak_test: dict
    notes: str = ""
