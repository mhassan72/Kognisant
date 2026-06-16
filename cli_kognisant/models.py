"""Data models for the Goal Generation and World Model subsystem.

This module defines all dataclasses used across the world model, observer,
and goal engine modules. Each dataclass provides to_dict() and from_dict()
methods for JSON serialization/deserialization.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ───────────────────────────────────────────────────────────
# Helper Functions
# ───────────────────────────────────────────────────────────


def clamp_confidence(value: float) -> float:
    """Clamp a confidence value to [0.0, 1.0] bounds."""
    return max(0.0, min(1.0, value))


def generate_uuid() -> str:
    """Generate a UUID v4 string."""
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ───────────────────────────────────────────────────────────
# Data Models
# ───────────────────────────────────────────────────────────


@dataclass
class Node:
    """Represents a code entity (module, class, or function) in the dependency graph."""

    id: str
    node_type: str
    file_path: str
    line_start: int
    line_end: int
    last_modified: str
    tags: list[str] = field(default_factory=list)
    module: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "last_modified": self.last_modified,
            "tags": self.tags,
            "module": self.module,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=d["id"],
            node_type=d["node_type"],
            file_path=d["file_path"],
            line_start=d["line_start"],
            line_end=d["line_end"],
            last_modified=d["last_modified"],
            tags=d.get("tags", []),
            module=d.get("module", ""),
        )


@dataclass
class Edge:
    """Represents a relationship between two nodes in the dependency graph."""

    id: str
    source: str
    target: str
    edge_type: str
    confidence: float
    provenance: str
    version: int = 1
    conditional: bool = False
    stable: bool = False
    reinforcement_count: int = 0
    last_reinforced: str = ""
    first_seen: str = ""
    falsification_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "version": self.version,
            "conditional": self.conditional,
            "stable": self.stable,
            "reinforcement_count": self.reinforcement_count,
            "last_reinforced": self.last_reinforced,
            "first_seen": self.first_seen,
            "falsification_count": self.falsification_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(
            id=d["id"],
            source=d["source"],
            target=d["target"],
            edge_type=d["edge_type"],
            confidence=d["confidence"],
            provenance=d["provenance"],
            version=d.get("version", 1),
            conditional=d.get("conditional", False),
            stable=d.get("stable", False),
            reinforcement_count=d.get("reinforcement_count", 0),
            last_reinforced=d.get("last_reinforced", ""),
            first_seen=d.get("first_seen", ""),
            falsification_count=d.get("falsification_count", 0),
        )


@dataclass
class Belief:
    """Represents a belief about the codebase held by the world model."""

    id: str
    statement: str
    node_id: str
    edge_id: str | None
    provenance: str
    confidence: float
    created_at: str
    last_reinforced: str
    falsification_count: int = 0
    archived: bool = False
    archive_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "statement": self.statement,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_reinforced": self.last_reinforced,
            "falsification_count": self.falsification_count,
            "archived": self.archived,
            "archive_reason": self.archive_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Belief":
        return cls(
            id=d["id"],
            statement=d["statement"],
            node_id=d["node_id"],
            edge_id=d.get("edge_id"),
            provenance=d["provenance"],
            confidence=d["confidence"],
            created_at=d["created_at"],
            last_reinforced=d["last_reinforced"],
            falsification_count=d.get("falsification_count", 0),
            archived=d.get("archived", False),
            archive_reason=d.get("archive_reason"),
        )


@dataclass
class Contract:
    """Represents an expected calling contract between two nodes."""

    id: str
    source_node: str
    target_node: str
    expected_args: list[str] = field(default_factory=list)
    expected_return: str | None = None
    expected_errors: list[str] = field(default_factory=list)
    version: int = 1
    confidence: float = 0.7
    provenance: str = "static"
    violated: bool = False
    last_verified: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_node": self.source_node,
            "target_node": self.target_node,
            "expected_args": self.expected_args,
            "expected_return": self.expected_return,
            "expected_errors": self.expected_errors,
            "version": self.version,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "violated": self.violated,
            "last_verified": self.last_verified,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Contract":
        return cls(
            id=d["id"],
            source_node=d["source_node"],
            target_node=d["target_node"],
            expected_args=d.get("expected_args", []),
            expected_return=d.get("expected_return"),
            expected_errors=d.get("expected_errors", []),
            version=d.get("version", 1),
            confidence=d.get("confidence", 0.7),
            provenance=d.get("provenance", "static"),
            violated=d.get("violated", False),
            last_verified=d.get("last_verified", ""),
        )


@dataclass
class EpistemicGap:
    """Represents a known unknown in the world model's knowledge."""

    id: str
    gap_type: str
    node_id: str
    description: str
    discovered_at: str
    resolution_status: str = "open"
    resolved_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "gap_type": self.gap_type,
            "node_id": self.node_id,
            "description": self.description,
            "discovered_at": self.discovered_at,
            "resolution_status": self.resolution_status,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EpistemicGap":
        return cls(
            id=d["id"],
            gap_type=d["gap_type"],
            node_id=d["node_id"],
            description=d["description"],
            discovered_at=d["discovered_at"],
            resolution_status=d.get("resolution_status", "open"),
            resolved_at=d.get("resolved_at"),
        )


@dataclass
class Goal:
    """Represents a generated goal for code improvement."""

    id: str
    goal_type: str
    title: str
    target_node: str | None = None
    target_file: str | None = None
    context: dict = field(default_factory=dict)
    priority_score: float = 0.0
    validation_status: str = "requires_user_review"
    status: str = "active"
    created_at: str = ""
    resolved_at: str | None = None
    causal_chain: list[str] = field(default_factory=list)
    snapshot_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_type": self.goal_type,
            "title": self.title,
            "target_node": self.target_node,
            "target_file": self.target_file,
            "context": self.context,
            "priority_score": self.priority_score,
            "validation_status": self.validation_status,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "causal_chain": self.causal_chain,
            "snapshot_path": self.snapshot_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        return cls(
            id=d["id"],
            goal_type=d["goal_type"],
            title=d["title"],
            target_node=d.get("target_node"),
            target_file=d.get("target_file"),
            context=d.get("context", {}),
            priority_score=d.get("priority_score", 0.0),
            validation_status=d.get("validation_status", "requires_user_review"),
            status=d.get("status", "active"),
            created_at=d.get("created_at", ""),
            resolved_at=d.get("resolved_at"),
            causal_chain=d.get("causal_chain", []),
            snapshot_path=d.get("snapshot_path"),
        )


@dataclass
class ToolCallTrace:
    """Records a single tool invocation within a trace session."""

    timestamp: str
    tool_name: str
    arguments: str
    result_summary: str
    success: bool
    duration_ms: int

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result_summary": self.result_summary,
            "success": self.success,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCallTrace":
        return cls(
            timestamp=d["timestamp"],
            tool_name=d["tool_name"],
            arguments=d["arguments"],
            result_summary=d["result_summary"],
            success=d["success"],
            duration_ms=d["duration_ms"],
        )


@dataclass
class FileOpTrace:
    """Records a file read/write operation within a trace session."""

    timestamp: str
    file_path: str
    operation: str
    byte_count: int

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "file_path": self.file_path,
            "operation": self.operation,
            "byte_count": self.byte_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FileOpTrace":
        return cls(
            timestamp=d["timestamp"],
            file_path=d["file_path"],
            operation=d["operation"],
            byte_count=d["byte_count"],
        )


@dataclass
class LLMCallTrace:
    """Records an LLM API call within a trace session."""

    timestamp: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "model_id": self.model_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LLMCallTrace":
        return cls(
            timestamp=d["timestamp"],
            model_id=d["model_id"],
            prompt_tokens=d["prompt_tokens"],
            completion_tokens=d["completion_tokens"],
            latency_ms=d["latency_ms"],
        )


@dataclass
class TraceRecord:
    """Records an entire PERP execution session with all sub-traces."""

    session_id: str
    start_time: str
    end_time: str | None = None
    task_description: str = ""
    status: str = "running"
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    file_operations: list[FileOpTrace] = field(default_factory=list)
    llm_calls: list[LLMCallTrace] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "task_description": self.task_description,
            "status": self.status,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "file_operations": [fo.to_dict() for fo in self.file_operations],
            "llm_calls": [lc.to_dict() for lc in self.llm_calls],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TraceRecord":
        return cls(
            session_id=d["session_id"],
            start_time=d["start_time"],
            end_time=d.get("end_time"),
            task_description=d.get("task_description", ""),
            status=d.get("status", "running"),
            tool_calls=[
                ToolCallTrace.from_dict(tc) for tc in d.get("tool_calls", [])
            ],
            file_operations=[
                FileOpTrace.from_dict(fo) for fo in d.get("file_operations", [])
            ],
            llm_calls=[
                LLMCallTrace.from_dict(lc) for lc in d.get("llm_calls", [])
            ],
        )


@dataclass
class FeedbackSignal:
    """Records user feedback on a goal proposal for the learning loop."""

    goal_type: str
    module: str
    polarity: str
    strength: float
    timestamp: str
    source: str

    def to_dict(self) -> dict:
        return {
            "goal_type": self.goal_type,
            "module": self.module,
            "polarity": self.polarity,
            "strength": self.strength,
            "timestamp": self.timestamp,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FeedbackSignal":
        return cls(
            goal_type=d["goal_type"],
            module=d["module"],
            polarity=d["polarity"],
            strength=d["strength"],
            timestamp=d["timestamp"],
            source=d["source"],
        )
