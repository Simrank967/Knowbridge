"""Typed API contracts. These models drive the OpenAPI schema shown on /docs, so every field carries a
description and the key models carry realistic examples (taken from the synthetic demo dataset)."""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------------------------- enums
VerificationStatus = Literal["VERIFIED", "INFERRED", "CONFLICTING", "MISSING", "STALE"]
EntityType = Literal["person", "task", "decision", "risk", "milestone", "resource", "question"]
RelationType = Literal["ASSIGNED_TO", "DEPENDS_ON", "BLOCKS", "DECIDED_BY", "SUPPORTED_BY", "RELATED_TO", "SUPERSEDES"]
Severity = Literal["High", "Medium", "Low"]
ExtractorChoice = Literal["auto", "rules"]
ExportFormat = Literal["json", "graphml"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------------------------- system
class HealthResponse(_Model):
    status: Literal["ok"] = Field(description="Always `ok` when the service is up.")
    service: str
    version: str
    extractor_mode: str = Field(description="`rules` (offline, deterministic) or the configured LLM provider.")
    documents_loaded: int
    claims_extracted: int
    uptime_seconds: float

    model_config = ConfigDict(json_schema_extra={"example": {
        "status": "ok", "service": "knowbridge-reconstruction-engine", "version": "1.0.0",
        "extractor_mode": "rules", "documents_loaded": 4, "claims_extracted": 52, "uptime_seconds": 118.4}})


class ResetResponse(_Model):
    message: str
    documents_removed: int


# ---------------------------------------------------------------------------------------------- documents
class DocumentOut(_Model):
    id: str = Field(description="Stable document ID, derived from the file content.")
    filename: str
    source_type: str = Field(description="pdf | docx | txt | csv | markdown")
    upload_date: str
    doc_date: Optional[str] = Field(None, description="Document date parsed from a `Date:` line or CSV `updated` column.")
    n_chunks: int = Field(description="Number of provenance-tagged chunks the document was split into.")

    model_config = ConfigDict(json_schema_extra={"example": {
        "id": "doc-43311644", "filename": "meeting_notes.md", "source_type": "markdown",
        "upload_date": "2026-11-10T08:12:44+00:00", "doc_date": "2026-10-05", "n_chunks": 4}})


class UploadResponse(DocumentOut):
    message: str = Field(description="Hint for the next pipeline step.")


class DocumentList(_Model):
    count: int
    documents: list[DocumentOut]


# ---------------------------------------------------------------------------------------------- extraction
class ExtractResponse(_Model):
    claims_extracted: int = Field(description="Total structured claims produced across all chunks.")
    by_kind: dict[str, int] = Field(description="Claim counts per kind (task, decision, risk, ...).")
    extractor: Optional[str] = Field(None, description="Extractor that produced the claims.")
    documents_processed: int
    chunks_processed: int

    model_config = ConfigDict(json_schema_extra={"example": {
        "claims_extracted": 52, "by_kind": {"person": 5, "task": 14, "decision": 5, "risk": 4, "question": 2},
        "extractor": "rules", "documents_processed": 4, "chunks_processed": 11}})


# ---------------------------------------------------------------------------------------------- evidence
class Evidence(_Model):
    doc_id: str
    doc_name: str = Field(description="Original file the supporting passage came from.")
    chunk_id: str = Field(description="Chunk the passage was found in (use it to locate the exact section/page).")
    text: str = Field(description="Verbatim supporting passage from the source document.")
    timestamp: Optional[str] = None
    certainty: Literal["explicit", "hedged"] = Field(
        description="`explicit` = stated directly; `hedged` = source uses uncertain wording (probably, might, ...).")


class ConflictObservation(_Model):
    value: Any
    document: str
    chunk_id: str
    timestamp: Optional[str] = None
    text: str


class Conflict(_Model):
    attribute: str = Field(description="Attribute in dispute, e.g. `status` or `owner`.")
    note: str
    observations: list[ConflictObservation] = Field(description="Every competing observation. None is silently chosen.")


class EntityOut(_Model):
    id: str
    type: str
    name: str
    description: str
    status: VerificationStatus = Field(description="Overall verification label for this entity.")
    attr_status: dict[str, str] = Field(description="Per-attribute label, e.g. `{\"owner\": \"MISSING\"}`.")
    attrs: dict[str, Any]
    source_documents: list[str]
    timestamp: Optional[str] = None
    conflicts: list[Conflict]
    stale_reasons: list[str]
    evidence_count: int

    model_config = ConfigDict(json_schema_extra={"example": {
        "id": "task:t-105", "type": "task", "name": "Configure the cloud deployment pipeline",
        "description": "task: Configure the cloud deployment pipeline", "status": "VERIFIED",
        "attr_status": {"owner": "MISSING", "status": "MISSING"}, "attrs": {"ref": "T-105", "title": "Configure the cloud deployment pipeline"},
        "source_documents": ["meeting_notes.md"], "timestamp": "2026-10-05", "conflicts": [], "stale_reasons": [], "evidence_count": 3}})


class EntityList(_Model):
    count: int
    entities: list[EntityOut]


class EvidenceFact(_Model):
    id: str
    type: str
    name: str
    status: VerificationStatus
    evidence: list[Evidence]


class EvidenceFactList(_Model):
    count: int
    facts: list[EvidenceFact]


class EvidenceEntityRef(_Model):
    id: str
    type: str
    name: str
    status: VerificationStatus
    attr_status: dict[str, str]


class EvidenceRelationship(_Model):
    type: str
    target: str
    status: VerificationStatus
    evidence: list[Evidence]


class EntityEvidence(_Model):
    entity: EvidenceEntityRef
    evidence: list[Evidence]
    relationships: list[EvidenceRelationship]


class SearchMatch(_Model):
    chunk_id: str
    doc_id: str
    section: Optional[str] = None
    page: Optional[int] = None
    text: str


class EvidenceSearch(_Model):
    query: str
    count: int
    matches: list[SearchMatch]


EvidenceResponse = Union[EntityEvidence, EvidenceSearch, EvidenceFactList]


# ---------------------------------------------------------------------------------------------- graph
class GraphNode(_Model):
    id: str
    type: str
    name: str
    description: str = ""
    status: VerificationStatus
    attr_status: dict[str, str] = {}
    attrs: dict[str, Any] = {}
    source_documents: list[str] = []
    evidence: list[Evidence] = []
    conflicts: list[Conflict] = []
    stale_reasons: list[str] = []
    timestamp: Optional[str] = None


class GraphEdge(_Model):
    source: str
    target: str
    id: str
    type: RelationType
    description: str
    status: VerificationStatus
    source_documents: list[str]
    evidence: list[Evidence]
    timestamp: Optional[str] = None


class GraphStats(_Model):
    knowledge_nodes: int
    evidence_nodes: int
    edges: int
    nodes_by_type: dict[str, int]
    edges_by_type: dict[str, int]
    nodes_by_status: dict[str, int]


class GraphResponse(_Model):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    stats: GraphStats
    exported: Optional[dict[str, str]] = Field(None, description="Paths of the JSON/GraphML files when `export_files=true`.")


# ---------------------------------------------------------------------------------------------- knowledge debt
class RelatedEntity(_Model):
    id: str
    type: str
    name: str


class DebtEvidence(_Model):
    document: str
    chunk_id: str
    text: str
    timestamp: Optional[str] = None


class DebtIssue(_Model):
    id: str = Field(description="Report-local identifier, e.g. `KD-001`.")
    title: str
    category: str
    description: str
    severity: Severity
    related_entity: RelatedEntity
    supporting_evidence: list[DebtEvidence]
    recommended_verification: str = Field(description="What a human should confirm before the handover.")

    model_config = ConfigDict(json_schema_extra={"example": {
        "id": "KD-001", "title": "No owner for T-105 Configure the cloud deployment pipeline", "category": "Missing owner",
        "description": "No source names an owner for this task; it is currently blocked.", "severity": "High",
        "related_entity": {"id": "task:t-105", "type": "task", "name": "Configure the cloud deployment pipeline"},
        "supporting_evidence": [{"document": "meeting_notes.md", "chunk_id": "doc-43311644-c002",
                                 "text": "Action T-105: Configure the cloud deployment pipeline due 2026-11-05", "timestamp": "2026-10-05"}],
        "recommended_verification": "Confirm who owns this task and record it in the task board."}})


class DebtSummary(_Model):
    total: int
    by_severity: dict[str, int]
    by_category: dict[str, int]


class DebtReport(_Model):
    summary: DebtSummary
    issues: list[DebtIssue]
    filters_applied: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------------------------- dashboard summary / demo
class KnowledgeSummary(_Model):
    documents: int
    chunks: int
    claims: int
    entities_by_type: dict[str, int]
    entities_by_verification: dict[str, int]
    relationships_by_type: dict[str, int]
    knowledge_debt: DebtSummary
    note: str = Field(description="Counts only. KNOWBRIDGE deliberately reports no confidence percentages.")


class DemoLoadResponse(_Model):
    message: str
    dataset: str
    documents: list[DocumentOut]
    errors: list[dict[str, str]]
    extraction: ExtractResponse
    summary: KnowledgeSummary


class ErrorResponse(BaseModel):
    """Error body returned for 4xx/5xx responses."""
    model_config = ConfigDict(json_schema_extra={"example": {"detail": "corrupted or unreadable PDF"}})
    detail: str = Field(description="Human-readable reason the request was rejected.")
