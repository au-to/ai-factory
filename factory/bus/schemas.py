from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Requirement(BaseModel):
    id: str
    description: str
    priority: str = "medium"  # high, medium, low
    acceptance_criteria: list[str] = Field(default_factory=list)


class NonFunctionalRequirement(BaseModel):
    type: str  # performance, security, scalability, etc.
    description: str
    threshold: Optional[str] = None


class ClarifyingQuestion(BaseModel):
    question: str
    answer: Optional[str] = None


class PRDArtifact(BaseModel):
    version: int = 1
    stage: str = "requirements"
    run_id: str
    title: str
    problem_statement: str
    target_users: list[str] = Field(default_factory=list)
    functional_requirements: list[Requirement] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    clarifying_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class Component(BaseModel):
    name: str
    description: str
    responsibilities: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class APIContract(BaseModel):
    method: str
    path: str
    description: str
    request_body: Optional[str] = None
    response_body: Optional[str] = None


class DataModel(BaseModel):
    name: str
    fields: dict[str, str] = Field(default_factory=dict)  # field_name -> type


class TechSpecArtifact(BaseModel):
    version: int = 1
    stage: str = "design"
    run_id: str
    overview: str
    components: list[Component] = Field(default_factory=list)
    data_models: list[DataModel] = Field(default_factory=list)
    api_contracts: list[APIContract] = Field(default_factory=list)
    tech_stack: dict[str, str] = Field(default_factory=dict)
    architecture_notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class FileEntry(BaseModel):
    path: str
    purpose: str


class BuildLogArtifact(BaseModel):
    version: int = 1
    stage: str = "development"
    run_id: str
    summary: str
    files_created: list[FileEntry] = Field(default_factory=list)
    components_completed: list[str] = Field(default_factory=list)
    components_pending: list[str] = Field(default_factory=list)
    issues_encountered: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class TestResult(BaseModel):
    name: str
    status: str  # passed, failed, skipped
    details: Optional[str] = None


class QaReportArtifact(BaseModel):
    version: int = 1
    stage: str = "testing"
    run_id: str
    summary: str
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    coverage_percent: Optional[float] = None
    test_results: list[TestResult] = Field(default_factory=list)
    issues_found: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ServiceConfig(BaseModel):
    name: str
    image: Optional[str] = None
    build_context: Optional[str] = None
    ports: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)


class DeployConfigArtifact(BaseModel):
    version: int = 1
    stage: str = "deployment"
    run_id: str
    summary: str
    services: list[ServiceConfig] = Field(default_factory=list)
    dockerfile_path: Optional[str] = None
    docker_compose_path: Optional[str] = None
    environment_variables: dict[str, str] = Field(default_factory=dict)
    deployment_notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# Union of all artifact types
Artifact = PRDArtifact | TechSpecArtifact | BuildLogArtifact | QaReportArtifact | DeployConfigArtifact

# Map stage names to artifact classes
STAGE_ARTIFACT_MAP: dict[str, type] = {
    "requirements": PRDArtifact,
    "design": TechSpecArtifact,
    "development": BuildLogArtifact,
    "testing": QaReportArtifact,
    "deployment": DeployConfigArtifact,
}


class StageStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class GateStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
