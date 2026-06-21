"""Goal engine module for the Goal Generation and World Model subsystem.

This module contains the LearningLoop class for tracking user feedback signals
and computing acceptance rates for goal types, the GoalGenerator class
for creating improvement goals from multiple detection strategies, the
PriorityRanker class for scoring and ranking goals, the
GraduatedAutonomyController for managing autonomy levels based on acceptance rates,
the ExecutionEngine for translating accepted goals into PERP tasks with
snapshot and rollback support, and the ProposalInterface for surfacing goals
to users via session-start display, /goals command, and inline suggestions.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Callable

from cli_kognisant.colors import Colors
from cli_kognisant.config import load_autonomy_config, save_autonomy_config
from cli_kognisant.models import FeedbackSignal, Goal, utc_now_iso

if TYPE_CHECKING:
    from cli_kognisant.world_model import DependencyGraph, GraphMaintenanceEngine
    from cli_kognisant.world_model_store import WorldModelStore

logger = logging.getLogger(__name__)


class LearningLoop:
    """Tracks goal acceptance rates and implicit feedback signals.

    Records feedback signals (accept, dismiss, ignore, manual_fix) and
    computes weighted acceptance rates per goal type per module. Negative
    signals carry 1.5x the weight of positive signals of the same strength
    (asymmetric weighting).

    Persists signals to <project_root>/.kognisant/goals/learning.json using
    atomic writes (write to .tmp then os.rename). On write failure, signals
    are retained in memory for the next attempt.
    """

    NEGATIVE_WEIGHT_MULTIPLIER = 1.5
    WINDOW_SIZE = 20

    def __init__(self, project_root: str):
        """Initialize with project root for persistence."""
        self._project_root = project_root
        self._signals: list[FeedbackSignal] = []
        self._session_counts: dict[str, int] = defaultdict(int)
        self._persistence_path = os.path.join(
            project_root, ".kognisant", "goals", "learning.json"
        )
        self._load_signals()

    def _ensure_directory(self) -> None:
        """Create .kognisant/goals/ directory if it doesn't exist."""
        directory = os.path.dirname(self._persistence_path)
        os.makedirs(directory, exist_ok=True)

    def _load_signals(self) -> None:
        """Load persisted signals from disk."""
        if not os.path.exists(self._persistence_path):
            return
        try:
            with open(self._persistence_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            signals_data = data.get("signals", [])
            self._signals = [FeedbackSignal.from_dict(s) for s in signals_data]
            self._session_counts = defaultdict(
                int, data.get("session_counts", {})
            )
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            # Corrupted file — start fresh but retain anything in memory
            pass

    def _persist_signals(self) -> None:
        """Persist signals to disk using atomic write.

        On write failure, logs to stderr and retains signals in memory
        for the next attempt (R14.7).
        """
        self._ensure_directory()
        data = {
            "signals": [s.to_dict() for s in self._signals],
            "session_counts": dict(self._session_counts),
        }
        tmp_path = self._persistence_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, self._persistence_path)
        except OSError as e:
            logger.error("Failed to persist signals: %s", e)
            # Remove tmp file if it exists
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            # Signals remain in memory for next attempt (R14.7)

    def record_signal(self, signal: FeedbackSignal) -> None:
        """Record a feedback signal and persist.

        Appends the signal to the in-memory buffer and attempts to
        persist to .kognisant/goals/learning.json (R14.1, R14.2).
        """
        self._signals.append(signal)
        self._persist_signals()

    def record_accept(self, goal_type: str, module: str, timestamp: str) -> None:
        """Record an accept signal: positive, strength=1.0 (R14.1)."""
        signal = FeedbackSignal(
            goal_type=goal_type,
            module=module,
            polarity="positive",
            strength=1.0,
            timestamp=timestamp,
            source="accept",
        )
        self.record_signal(signal)

    def record_dismiss(self, goal_type: str, module: str, timestamp: str) -> None:
        """Record a dismiss signal: negative, strength=1.0 (R14.2)."""
        signal = FeedbackSignal(
            goal_type=goal_type,
            module=module,
            polarity="negative",
            strength=1.0,
            timestamp=timestamp,
            source="dismiss",
        )
        self.record_signal(signal)

    def record_session_for_goal(self, goal_id: str) -> None:
        """Track session count for a goal for ignore detection (R14.3).

        After 3 sessions without accept/dismiss, a negative signal
        is recorded with strength=0.5.
        """
        self._session_counts[goal_id] = self._session_counts.get(goal_id, 0) + 1

    def check_ignore(
        self, goal_id: str, goal_type: str, module: str, timestamp: str
    ) -> bool:
        """Check if a goal has been ignored for 3 sessions and record signal.

        Returns True if an ignore signal was recorded (R14.3).
        """
        count = self._session_counts.get(goal_id, 0)
        if count >= 3:
            signal = FeedbackSignal(
                goal_type=goal_type,
                module=module,
                polarity="negative",
                strength=0.5,
                timestamp=timestamp,
                source="ignore",
            )
            self.record_signal(signal)
            # Reset counter after recording
            self._session_counts[goal_id] = 0
            self._persist_signals()
            return True
        return False

    def detect_manual_fix(
        self,
        edit_file: str,
        edit_line_start: int,
        edit_line_end: int,
        goal_type: str,
        module: str,
        target_line_start: int,
        target_line_end: int,
        timestamp: str,
    ) -> bool:
        """Detect manual fix: user edit overlaps with goal's target node range.

        Records a positive signal with strength=0.5 if the edit line range
        overlaps with the goal's target node line range or call site line
        range (R14.4).

        Returns True if a manual fix signal was recorded.
        """
        # Check for line range overlap (with 1-line adjacency tolerance)
        if edit_line_start <= target_line_end and edit_line_end >= target_line_start - 1:
            signal = FeedbackSignal(
                goal_type=goal_type,
                module=module,
                polarity="positive",
                strength=0.5,
                timestamp=timestamp,
                source="manual_fix",
            )
            self.record_signal(signal)
            return True
        return False

    def get_acceptance_rate(self, goal_type: str) -> float:
        """Compute weighted acceptance rate for a goal type over last 20 proposals.

        Rate = weighted_positive / (weighted_positive + weighted_negative)

        Asymmetric weighting (R14.5): negative signals carry 1.5x weight.
        Window (R14.6): last 20 proposals per type per module.

        Returns 0.5 if no signals exist for the type.
        """
        # Filter signals for this goal type
        type_signals = [s for s in self._signals if s.goal_type == goal_type]

        if not type_signals:
            return 0.5

        # Group by module and take last WINDOW_SIZE per module
        by_module: dict[str, list[FeedbackSignal]] = defaultdict(list)
        for s in type_signals:
            by_module[s.module].append(s)

        weighted_positive = 0.0
        weighted_negative = 0.0

        for module_signals in by_module.values():
            # Take only last WINDOW_SIZE signals per module
            recent = module_signals[-self.WINDOW_SIZE:]
            for s in recent:
                if s.polarity == "positive":
                    weighted_positive += s.strength
                elif s.polarity == "negative":
                    weighted_negative += s.strength * self.NEGATIVE_WEIGHT_MULTIPLIER

        total = weighted_positive + weighted_negative
        if total == 0.0:
            return 0.5

        return weighted_positive / total

    def get_all_rates(self) -> dict[str, float]:
        """Return acceptance rates for all known goal types."""
        goal_types = set(s.goal_type for s in self._signals)
        return {gt: self.get_acceptance_rate(gt) for gt in goal_types}

    def get_total_proposal_count(self) -> int:
        """Return total number of recorded signals across all types."""
        return len(self._signals)


# ───────────────────────────────────────────────────────────
# GoalGenerator
# ───────────────────────────────────────────────────────────


class GoalGenerator:
    """Generates improvement goals from multiple detection strategies (R10).

    Runs six strategies to detect issues in the codebase and creates
    Goal objects for each detected issue. Includes deduplication to
    prevent duplicate active goals for the same target.

    Goal id format: type prefix + counter, e.g. "cv-001", "cg-002".
    """

    _TYPE_PREFIXES: dict[str, str] = {
        "contract_violation": "cv",
        "coverage_gap": "cg",
        "decay_alert": "da",
        "complexity": "cx",
        "stale_artifact": "sa",
        "pattern_detection": "pd",
    }

    def __init__(self, graph, contracts, gaps, beliefs, store) -> None:
        """Initialize with world model components.

        Args:
            graph: DependencyGraph instance for node/edge queries.
            contracts: ContractRegistry instance for violation detection.
            gaps: EpistemicGapTracker instance for coverage gap queries.
            beliefs: BeliefSystem instance for belief-related queries.
            store: WorldModelStore instance for persistence.
        """
        self._graph = graph
        self._contracts = contracts
        self._gaps = gaps
        self._beliefs = beliefs
        self._store = store
        self._active_goals: list[Goal] = []
        self._goal_counter: int = 0
        self._learning_loop: "LearningLoop | None" = None

    @property
    def learning_loop(self) -> "LearningLoop | None":
        """Get the optional LearningLoop instance."""
        return self._learning_loop

    @learning_loop.setter
    def learning_loop(self, loop: "LearningLoop | None") -> None:
        """Set the LearningLoop instance for R19.5 integration."""
        self._learning_loop = loop

    def generate_goals(self, decay_summary: dict | None = None) -> list[Goal]:
        """Run all six generation strategies and return new goals created.

        Each strategy produces zero or more goals. Deduplication prevents
        creating goals with the same (goal_type, target_node/target_file)
        as an existing active goal.

        Args:
            decay_summary: Optional summary from GraphMaintenanceEngine.decay_tick()
                containing 'beliefs_pruned' list and other decay info.

        Returns:
            List of newly created Goal objects.
        """
        new_goals: list[Goal] = []

        new_goals.extend(self._check_contract_violations())
        new_goals.extend(self._check_coverage_gaps())
        new_goals.extend(self._check_decay_alerts(decay_summary))
        new_goals.extend(self._check_complexity())
        new_goals.extend(self._check_stale_artifacts())
        new_goals.extend(self._check_pattern_detection())

        return new_goals

    def self_validate(self, goal: Goal) -> str:
        """Attempt self-validation of a goal by cross-referencing evidence sources (R19).

        Cross-references static analysis, dynamic traces, and test outcomes
        for the affected nodes. Updates the goal's priority_score and
        validation_status based on evidence agreement.

        Evidence sources:
        - Static: edges with provenance="static" from/to target node
        - Dynamic: edges with provenance="dynamic" from/to target node
        - Test: node has "unstable" tag OR has connected test coverage edges

        Args:
            goal: The Goal to validate.

        Returns:
            Validation status string: "high_confidence", "partially_validated",
            or "requires_user_review".
        """
        target_node_id = goal.target_node
        if not target_node_id:
            # No target node — cannot validate, reduce priority by 15%
            goal.priority_score *= 0.85
            goal.validation_status = "partially_validated"
            return "partially_validated"

        # Gather evidence from each source
        has_static = self._has_static_evidence(target_node_id)
        has_dynamic = self._has_dynamic_evidence(target_node_id)
        has_test = self._has_test_evidence(target_node_id)

        sources_available = sum([has_static is not None,
                                 has_dynamic is not None,
                                 has_test is not None])
        sources_agreeing = sum([
            v for v in [has_static, has_dynamic, has_test] if v is True
        ])

        # R19.6: fewer than two sources available
        if sources_available < 2:
            goal.priority_score *= 0.85
            goal.validation_status = "partially_validated"
            return "partially_validated"

        # R19.2: all three sources agree (provide supporting evidence)
        if sources_agreeing == 3:
            goal.validation_status = "high_confidence"
            return "high_confidence"

        # R19.3: exactly two sources agree
        if sources_agreeing == 2:
            goal.priority_score *= 0.85
            goal.validation_status = "partially_validated"
            return "partially_validated"

        # R19.4: sources disagree (one or fewer agree with the diagnosis)
        goal.priority_score *= 0.70
        goal.validation_status = "requires_user_review"
        return "requires_user_review"

    def record_validation_success(self, goal: Goal) -> None:
        """Record strong positive signal when self-validated goal's test passes (R19.5).

        Integrates with LearningLoop to record a positive signal with
        strength 1.0 and source "self_validate" when a self-validated
        goal executes successfully and the subsequent test run passes.

        Args:
            goal: The Goal that was successfully validated and executed.
        """
        if self._learning_loop is None:
            return

        module = ""
        if goal.target_node:
            # Extract module from node id (e.g. "agents.MyClass.my_func" -> "agents")
            parts = goal.target_node.split(".")
            module = parts[0] if parts else ""

        signal = FeedbackSignal(
            goal_type=goal.goal_type,
            module=module,
            polarity="positive",
            strength=1.0,
            timestamp=utc_now_iso(),
            source="self_validate",
        )
        self._learning_loop.record_signal(signal)

    def _has_static_evidence(self, node_id: str) -> bool | None:
        """Check if static analysis provides evidence for the node.

        Returns True if static edges exist from/to the node,
        False if the node exists but has no static edges,
        None if the node doesn't exist in the graph.
        """
        node = self._graph.get_node(node_id)
        if node is None:
            return None

        edges_from = self._graph.get_edges_from(node_id)
        edges_to = self._graph.get_edges_to(node_id)

        static_edges = [
            e for e in edges_from + edges_to
            if e.provenance == "static"
        ]
        return len(static_edges) > 0

    def _has_dynamic_evidence(self, node_id: str) -> bool | None:
        """Check if dynamic traces provide evidence for the node.

        Returns True if dynamic edges exist from/to the node,
        False if the node exists but has no dynamic edges,
        None if the node doesn't exist in the graph.
        """
        node = self._graph.get_node(node_id)
        if node is None:
            return None

        edges_from = self._graph.get_edges_from(node_id)
        edges_to = self._graph.get_edges_to(node_id)

        dynamic_edges = [
            e for e in edges_from + edges_to
            if e.provenance == "dynamic"
        ]
        return len(dynamic_edges) > 0

    def _has_test_evidence(self, node_id: str) -> bool | None:
        """Check if test outcomes provide evidence for the node.

        Returns True if the node has "unstable" tag OR has connected
        test coverage (edges to/from test nodes),
        False if node exists but has no test evidence,
        None if the node doesn't exist in the graph.
        """
        node = self._graph.get_node(node_id)
        if node is None:
            return None

        # Check for "unstable" tag indicating test failures
        if "unstable" in node.tags:
            return True

        # Check for test coverage edges (edges connecting to test nodes)
        edges_from = self._graph.get_edges_from(node_id)
        edges_to = self._graph.get_edges_to(node_id)

        for edge in edges_from + edges_to:
            # Check if the other end of the edge is a test node
            other_id = edge.target if edge.source == node_id else edge.source
            other_node = self._graph.get_node(other_id)
            if other_node and "test" in other_node.file_path:
                return True

        return False

    # ─── Private Strategy Methods ─────────────────────────────

    def _check_contract_violations(self) -> list[Goal]:
        """Poll ContractRegistry for pending violations and create goals (R10.1).

        Creates a goal of type "contract_violation" for each violation event
        with source node, target node, and contract id as context.
        """
        goals: list[Goal] = []
        violations = self._contracts.get_pending_violations()

        for source, target, contract_id in violations:
            if self._is_duplicate("contract_violation", target_node=target):
                continue

            goal = self._create_goal(
                goal_type="contract_violation",
                title=f"Contract violation detected: {source} -> {target}",
                target_node=target,
                context={
                    "source_node": source,
                    "target_node": target,
                    "contract_id": contract_id,
                },
            )
            goals.append(goal)

        return goals

    def _check_coverage_gaps(self) -> list[Goal]:
        """Query EpistemicGapTracker for modules with >3 untested_branch gaps (R10.2).

        Creates a goal of type "coverage_gap" when a module has more than
        3 open untested_branch gaps.
        """
        goals: list[Goal] = []

        # Group open gaps by module
        open_gaps = self._gaps.get_open_gaps()
        module_gaps: dict[str, list[str]] = defaultdict(list)

        for gap in open_gaps:
            if gap.gap_type != "untested_branch":
                continue
            # Extract module from node_id (convention: module.class.function)
            module = gap.node_id.split(".")[0] if "." in gap.node_id else gap.node_id
            module_gaps[module].append(gap.node_id)

        for module, affected_nodes in module_gaps.items():
            if len(affected_nodes) <= 3:
                continue

            if self._is_duplicate("coverage_gap", target_node=module):
                continue

            goal = self._create_goal(
                goal_type="coverage_gap",
                title=f"Coverage gap in module '{module}': {len(affected_nodes)} untested branches",
                target_node=module,
                context={
                    "module": module,
                    "affected_functions": affected_nodes,
                    "gap_count": len(affected_nodes),
                },
            )
            goals.append(goal)

        return goals

    def _check_decay_alerts(self, decay_summary: dict | None) -> list[Goal]:
        """Check if decay pruned >5 beliefs from same module (R10.3).

        Groups pruned belief ids by module (using node_id prefix convention)
        and creates a goal when more than 5 are pruned from the same module.

        Args:
            decay_summary: Dict with 'beliefs_pruned' list of belief node_ids.
        """
        goals: list[Goal] = []

        if decay_summary is None:
            return goals

        beliefs_pruned = decay_summary.get("beliefs_pruned", [])
        if not beliefs_pruned:
            return goals

        # Group pruned beliefs by module (from node_id prefix)
        module_counts: dict[str, int] = defaultdict(int)
        for node_id in beliefs_pruned:
            module = node_id.split(".")[0] if "." in node_id else node_id
            module_counts[module] += 1

        for module, count in module_counts.items():
            if count <= 5:
                continue

            if self._is_duplicate("decay_alert", target_node=module):
                continue

            goal = self._create_goal(
                goal_type="decay_alert",
                title=f"Decay alert for module '{module}': {count} beliefs pruned",
                target_node=module,
                context={
                    "module": module,
                    "pruned_count": count,
                },
            )
            goals.append(goal)

        return goals

    def _check_complexity(self) -> list[Goal]:
        """Check graph nodes for high complexity with churn or no coverage (R10.4).

        Creates a goal of type "complexity" when a function has cyclomatic
        complexity > 15 AND either high churn (3+ modifications in 30 days)
        or no test coverage.

        Optimized: batches git log calls per-file rather than per-node.
        """
        goals: list[Goal] = []

        # Pre-compute churn data for all unique file paths in one batch
        function_nodes = [n for n in self._graph._nodes.values() if n.node_type == "function"]
        if not function_nodes:
            return goals

        unique_files = set(n.file_path for n in function_nodes if n.file_path)
        churn_cache = self._batch_check_churn(unique_files)

        for node in function_nodes:
            # Compute cyclomatic complexity via AST (in-process, no subprocess)
            complexity = self._compute_complexity_for_node(node)
            if complexity is None or complexity <= 15:
                continue

            # Check churn from batch cache
            has_high_churn = churn_cache.get(node.file_path, False)
            # Check coverage: no dynamic edges = no coverage
            has_no_coverage = not any(
                e.provenance == "dynamic"
                for e in self._graph.get_edges_from(node.id)
            )

            if not has_high_churn and not has_no_coverage:
                continue

            if self._is_duplicate("complexity", target_node=node.id):
                continue

            goal = self._create_goal(
                goal_type="complexity",
                title=f"High complexity in '{node.id}': complexity={complexity}",
                target_node=node.id,
                target_file=node.file_path,
                context={
                    "complexity": complexity,
                    "has_high_churn": has_high_churn,
                    "has_no_coverage": has_no_coverage,
                    "file_path": node.file_path,
                },
            )
            goals.append(goal)

        return goals

    def _check_stale_artifacts(self) -> list[Goal]:
        """Check graph nodes for stale files with low confidence (R10.5).

        Creates a goal of type "stale_artifact" when a file has not been
        modified in 90 days and contains nodes with confidence < 0.4.

        Optimized: batches staleness check into a single git log call.
        """
        goals: list[Goal] = []

        # Group nodes by file_path
        file_nodes: dict[str, list] = defaultdict(list)
        for node in self._graph._nodes.values():
            if node.file_path:
                file_nodes[node.file_path].append(node)

        if not file_nodes:
            return goals

        # Batch staleness check: single git log call for all files
        stale_files = self._batch_check_staleness(set(file_nodes.keys()), days=90)

        for file_path, nodes in file_nodes.items():
            if file_path not in stale_files:
                continue

            # Count nodes with confidence < 0.4 (check edges from each node)
            low_confidence_count = 0
            for node in nodes:
                edges = self._graph.get_edges_from(node.id)
                if any(e.confidence < 0.4 for e in edges):
                    low_confidence_count += 1

            if low_confidence_count == 0:
                continue

            if self._is_duplicate("stale_artifact", target_file=file_path):
                continue

            goal = self._create_goal(
                goal_type="stale_artifact",
                title=f"Stale artifact: '{file_path}' ({low_confidence_count} low-confidence nodes)",
                target_file=file_path,
                context={
                    "file_path": file_path,
                    "low_confidence_nodes": low_confidence_count,
                    "total_nodes": len(nodes),
                },
            )
            goals.append(goal)

        return goals

    def _check_pattern_detection(self) -> list[Goal]:
        """Scan trace records for repeated error patterns (R10.6).

        Detects when the same exception class name occurs from the same
        source function 3 times within the last 5 PERP executions.
        Exception class names are normalized by stripping module prefix
        (keeping only the class name).

        Reads trace files from .kognisant/traces/ directory.

        Returns:
            List of new pattern_detection goals.
        """
        goals: list[Goal] = []

        # Load recent trace records from disk
        traces_dir = os.path.join(
            self._store._project_root if hasattr(self._store, '_project_root') else "",
            ".kognisant", "traces"
        )
        if not os.path.isdir(traces_dir):
            return goals

        # Load the 5 most recent trace files (by modification time)
        try:
            trace_files = sorted(
                [
                    os.path.join(traces_dir, f)
                    for f in os.listdir(traces_dir)
                    if f.endswith(".json")
                ],
                key=lambda p: os.path.getmtime(p),
                reverse=True,
            )[:5]
        except OSError:
            return goals

        if len(trace_files) < 1:
            return goals

        # Collect error patterns: (normalized_exception, source_function) -> [session_ids]
        error_occurrences: dict[tuple[str, str], list[str]] = defaultdict(list)

        for trace_path in trace_files:
            try:
                with open(trace_path, "r", encoding="utf-8") as f:
                    trace_data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue

            session_id = trace_data.get("session_id", "")
            tool_calls = trace_data.get("tool_calls", [])

            for tc in tool_calls:
                if not tc.get("success", True):
                    # Extract error info from result_summary
                    result = tc.get("result_summary", "")
                    tool_name = tc.get("tool_name", "unknown")

                    # Normalize exception class name: strip module prefix
                    exception_class = self._extract_exception_class(result)
                    if exception_class:
                        key = (exception_class, tool_name)
                        if session_id not in error_occurrences[key]:
                            error_occurrences[key].append(session_id)

        # Detect patterns: 3+ occurrences across the 5 sessions
        for (exception_class, source), session_ids in error_occurrences.items():
            if len(session_ids) < 3:
                continue

            target_node = source
            if self._is_duplicate("pattern_detection", target_node=target_node):
                continue

            goal = self._create_goal(
                goal_type="pattern_detection",
                title=f"Repeated error '{exception_class}' from '{source}' ({len(session_ids)} occurrences)",
                target_node=target_node,
                context={
                    "exception_class": exception_class,
                    "source_function": source,
                    "occurrence_count": len(session_ids),
                    "session_ids": session_ids[:5],
                },
            )
            goals.append(goal)

        return goals

    def _extract_exception_class(self, error_text: str) -> str | None:
        """Extract and normalize exception class name from error text.

        Strips module prefix, keeping only the class name.
        E.g. "builtins.ValueError: invalid" -> "ValueError"
             "[Error] FileNotFoundError: ..." -> "FileNotFoundError"

        Returns None if no recognizable exception pattern found.
        """
        if not error_text:
            return None

        # Common patterns: "ExceptionClass: message" or "[Error] ExceptionClass: message"
        # Match patterns like "SomeError:", "some.module.SomeError:", "[Error] SomeError:"
        match = re.search(r'(?:\[Error\]\s*)?(?:[\w.]+\.)?(\w*(?:Error|Exception|Warning|Failure))\b', error_text)
        if match:
            return match.group(1)

        # Fallback: if result starts with [Error], use a generic marker
        if error_text.startswith("[Error"):
            return "UnknownError"

        return None

    # ─── Helper Methods ───────────────────────────────────────

    def _create_goal(
        self,
        goal_type: str,
        title: str,
        target_node: str | None = None,
        target_file: str | None = None,
        context: dict | None = None,
    ) -> Goal:
        """Create a new Goal with unique id and register it as active.

        Goal id format: type prefix + counter, e.g. "cv-001", "cg-002".
        """
        self._goal_counter += 1
        prefix = self._TYPE_PREFIXES.get(goal_type, "xx")
        goal_id = f"{prefix}-{self._goal_counter:03d}"

        goal = Goal(
            id=goal_id,
            goal_type=goal_type,
            title=title[:120],  # Truncate to 120 chars
            target_node=target_node,
            target_file=target_file,
            context=context or {},
            status="active",
            created_at=utc_now_iso(),
            validation_status=self.self_validate(
                Goal(id="", goal_type=goal_type, title="")
            ),
        )

        self._active_goals.append(goal)
        return goal

    def _is_duplicate(
        self,
        goal_type: str,
        target_node: str | None = None,
        target_file: str | None = None,
    ) -> bool:
        """Check if a goal of the same type targeting the same node/file exists active (R10.8).

        Returns True if a duplicate active goal exists.
        """
        for goal in self._active_goals:
            if goal.status != "active":
                continue
            if goal.goal_type != goal_type:
                continue
            # Match on target_node if provided
            if target_node is not None and goal.target_node == target_node:
                return True
            # Match on target_file if provided (and no target_node match)
            if target_file is not None and goal.target_file == target_file:
                return True
        return False

    def _compute_complexity_for_node(self, node) -> int | None:
        """Compute cyclomatic complexity for a function node using StaticAnalyzer.

        Uses the in-process AST-based complexity computation from observer.py
        instead of spawning a subprocess per node.

        Returns the complexity value, or None if computation fails.
        """
        try:
            from .observer import StaticAnalyzer

            # Get the project root from the store
            project_root = ""
            if hasattr(self._store, '_project_root'):
                project_root = self._store._project_root

            # Build absolute file path
            file_path = node.file_path
            if project_root and not os.path.isabs(file_path):
                file_path = os.path.join(project_root, file_path)

            if not os.path.isfile(file_path):
                return None

            # Extract function name from node_id (module.class.func or module.func)
            func_name = node.id.split(".")[-1]

            analyzer = StaticAnalyzer(project_root or ".", {"max_files": 1})
            return analyzer.compute_complexity(file_path, func_name)
        except Exception:
            return None

    def _batch_check_churn(self, file_paths: set[str]) -> dict[str, bool]:
        """Batch check churn for multiple files in a single git log call.

        Returns dict mapping file_path -> True if 3+ commits in last 30 days.
        Replaces N individual subprocess calls with 1.
        """
        result: dict[str, bool] = {fp: False for fp in file_paths}
        if not file_paths:
            return result

        try:
            # Single git log call with --name-only to get all files modified in last 30 days
            git_result = subprocess.run(
                ["git", "log", "--oneline", "--name-only", "--since=30 days ago"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if git_result.returncode != 0:
                return result

            # Count how many commits touch each file
            file_commit_counts: dict[str, int] = defaultdict(int)
            current_commit = False
            for line in git_result.stdout.strip().split("\n"):
                if not line:
                    current_commit = False
                    continue
                # Lines starting with a hash are commit headers
                if not current_commit and line and not line.startswith(" "):
                    current_commit = True
                    continue
                # File name lines (after commit header)
                if line.strip():
                    file_commit_counts[line.strip()] += 1

            for fp in file_paths:
                result[fp] = file_commit_counts.get(fp, 0) >= 3
        except (subprocess.TimeoutExpired, OSError):
            pass
        return result

    def _batch_check_staleness(self, file_paths: set[str], days: int = 90) -> set[str]:
        """Batch check which files are stale (>N days since last modification).

        Returns set of file paths that are stale.
        Single git log call replaces N individual subprocess calls.
        """
        stale_files: set[str] = set()
        if not file_paths:
            return stale_files

        import time as _time
        cutoff_ts = int(_time.time()) - (days * 86400)

        try:
            # Get last commit timestamp for ALL tracked files in one call
            git_result = subprocess.run(
                ["git", "log", "--format=%ct", "--name-only", "--diff-filter=AM",
                 f"--since={days * 2} days ago"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if git_result.returncode != 0:
                # Fallback: assume nothing is stale
                return stale_files

            # Parse: lines alternate between timestamp and filenames
            recently_modified: set[str] = set()
            for line in git_result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Skip timestamps (pure digits)
                if line.isdigit():
                    continue
                recently_modified.add(line)

            # Files NOT in recently_modified set are stale
            for fp in file_paths:
                if fp not in recently_modified:
                    stale_files.add(fp)

        except (subprocess.TimeoutExpired, OSError):
            pass
        return stale_files

    def _check_churn(self, file_path: str, node_id: str) -> bool:
        """Check if a file has 3+ modifications in the last 30 days via git log.

        Returns True if high churn detected.
        """
        try:
            result = subprocess.run(
                [
                    "git", "log", "--oneline", "--since=30 days ago",
                    "--follow", "--", file_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                commit_count = len(
                    [line for line in result.stdout.strip().split("\n") if line]
                )
                return commit_count >= 3
        except (subprocess.TimeoutExpired, OSError):
            pass
        return False

    def _is_file_stale(self, file_path: str, days: int = 90) -> bool:
        """Check if a file has not been modified in the given number of days.

        Uses git log to determine last modification date.
        Returns True if the file is stale (>days since last commit touching it).
        """
        try:
            result = subprocess.run(
                [
                    "git", "log", "-1", "--format=%ct", "--", file_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                import time

                last_modified_ts = int(result.stdout.strip())
                now_ts = int(time.time())
                days_since = (now_ts - last_modified_ts) / 86400
                return days_since > days
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return False


# ───────────────────────────────────────────────────────────
# PriorityRanker
# ───────────────────────────────────────────────────────────


class PriorityRanker:
    """Scores and ranks goals by priority (R11).

    Uses the formula: score = (impact_radius × severity_weight × likelihood) / effort_estimate

    - impact_radius: count of nodes reachable within 2 hops (confidence >= 0.3)
      from the goal's target node. Returns 1 for disconnected nodes.
    - severity_weight: fixed per goal type.
    - likelihood: max confidence from edges connected to target_node, or 0.5 default.
    - effort_estimate: mapped from files + functions count in goal context to 1-10 scale.
    """

    SEVERITY_WEIGHTS: dict[str, float] = {
        "contract_violation": 3.0,
        "coverage_gap": 2.0,
        "decay_alert": 1.5,
        "complexity": 2.5,
        "stale_artifact": 1.0,
        "pattern_detection": 2.8,
    }

    # Effort mapping brackets: (max_items, effort_value)
    # sum of files + functions → effort scale 1-10
    _EFFORT_BRACKETS: list[tuple[int, int]] = [
        (2, 1),
        (4, 2),
        (6, 3),
        (9, 4),
        (12, 5),
        (16, 6),
        (21, 7),
        (28, 8),
        (36, 9),
    ]

    def __init__(self, graph) -> None:
        """Initialize with graph for impact radius calculation.

        Args:
            graph: DependencyGraph instance.
        """
        self._graph = graph

    def rank_goals(self, goals: list[Goal]) -> list[Goal]:
        """Recompute priority scores for all goals, sort descending.

        Uses creation timestamp (oldest first) as tiebreaker for equal scores (R11.5).

        Args:
            goals: List of Goal objects to rank.

        Returns:
            New list sorted by priority_score descending, then created_at ascending.
        """
        for goal in goals:
            goal.priority_score = self.compute_score(goal)

        # Sort: descending by score, then ascending by created_at (oldest first as tiebreaker)
        return sorted(goals, key=lambda g: (-g.priority_score, g.created_at))

    def compute_score(self, goal: Goal) -> float:
        """Compute priority score for a single goal.

        Formula: score = (impact_radius × severity_weight × likelihood) / effort_estimate

        Args:
            goal: The Goal to score.

        Returns:
            Computed priority score as a float.
        """
        impact_radius = self._compute_impact_radius(goal)
        severity_weight = self.SEVERITY_WEIGHTS.get(goal.goal_type, 1.0)
        likelihood = self._compute_likelihood(goal)
        effort_estimate = self._compute_effort(goal)

        return (impact_radius * severity_weight * likelihood) / effort_estimate

    def _compute_impact_radius(self, goal: Goal) -> int:
        """Count nodes reachable within 2 hops with confidence >= 0.3.

        Returns 1 for disconnected nodes (zero reachable neighbors) or when
        the target_node is not in the graph (R11.2).
        """
        target = goal.target_node
        if target is None:
            return 1

        reachable = self._graph.query_reachable(
            node_id=target,
            max_hops=2,
            min_confidence=0.3,
        )

        # Return 1 if disconnected (no reachable neighbors)
        if not reachable:
            return 1

        return len(reachable)

    def _compute_likelihood(self, goal: Goal) -> float:
        """Compute likelihood from triggering evidence confidence.

        Uses max confidence from edges connected to target_node.
        Returns 0.5 as default when no edges are found.
        """
        target = goal.target_node
        if target is None:
            return 0.5

        # Collect edges connected to target node (both directions)
        edges_from = self._graph.get_edges_from(target)
        edges_to = self._graph.get_edges_to(target)

        all_edges = edges_from + edges_to
        if not all_edges:
            return 0.5

        return max(e.confidence for e in all_edges)

    def _compute_effort(self, goal: Goal) -> int:
        """Estimate effort from files + functions in goal context.

        Sums the number of files and functions referenced in the context,
        then maps to a 1-10 effort scale using bracket mapping.

        Returns 1 as default to prevent division by zero when context is empty (R11.6).
        """
        context = goal.context
        if not context:
            return 1

        # Count items: look for common context keys that reference files/functions
        item_count = 0

        # Count files
        if "file_path" in context:
            item_count += 1
        if "files" in context:
            files = context["files"]
            item_count += len(files) if isinstance(files, list) else 1

        # Count functions
        if "affected_functions" in context:
            funcs = context["affected_functions"]
            item_count += len(funcs) if isinstance(funcs, list) else 1
        if "source_node" in context:
            item_count += 1
        if "target_node" in context:
            item_count += 1

        # Zero items → default effort 1
        if item_count == 0:
            return 1

        return self._map_effort(item_count)

    def _map_effort(self, item_count: int) -> int:
        """Map item count to effort scale 1-10 using bracket mapping.

        Brackets: 1-2→1, 3-4→2, 5-6→3, 7-9→4, 10-12→5,
                  13-16→6, 17-21→7, 22-28→8, 29-36→9, 37+→10
        """
        for max_items, effort in self._EFFORT_BRACKETS:
            if item_count <= max_items:
                return effort
        return 10


# ───────────────────────────────────────────────────────────
# GraduatedAutonomyController
# ───────────────────────────────────────────────────────────


class GraduatedAutonomyController:
    """Manages graduated autonomy levels based on historical acceptance rates (R15).

    Determines whether goals of a given type should be auto-executed,
    presented for confirmation, or suppressed based on the effective
    acceptance rate computed from the LearningLoop.

    Thresholds:
    - rate > 85%: auto_execute (R15.1)
    - rate < 20%: suppress (R15.2)
    - 20% <= rate <= 85%: ask (R15.3)

    Cold start mode (R15.5): when fewer than 20 total proposals exist,
    all goals require confirmation and llm_inference beliefs are capped
    at 0.7 confidence.

    Unsuppression (R15.2): after every 10 proposals across other types,
    one suppressed goal is presented for re-evaluation.

    Per-project rates with global fallback (R15.4):
    effective_rate = (global_count * global_rate + local_count * local_rate)
                   / (global_count + local_count)
    When local_count >= 20, use local_rate exclusively.
    When both counts are 0, default to 0.5.

    Configuration persisted to ~/.kognisant_core/autonomy_config.json (R15.6).
    """

    AUTO_EXECUTE_THRESHOLD = 0.85
    SUPPRESS_THRESHOLD = 0.20
    COLD_START_PROPOSAL_LIMIT = 20
    UNSUPPRESS_INTERVAL = 10
    CONFIDENCE_CEILING_COLD_START = 0.7
    LOCAL_EXCLUSIVE_THRESHOLD = 20

    def __init__(self, learning_loop: LearningLoop, project_root: str) -> None:
        """Initialize with learning loop and project root.

        Args:
            learning_loop: LearningLoop instance for rate queries.
            project_root: Project root path for local rate computation.
        """
        self._learning_loop = learning_loop
        self._project_root = project_root
        self._proposal_count_since_last_unsuppress = 0
        self._config = load_autonomy_config()

    def get_autonomy_level(self, goal_type: str) -> str:
        """Determine autonomy level for a goal type based on effective rate.

        Returns:
            "auto_execute" if effective rate > 85% (strictly) (R15.1)
            "suppress" if effective rate < 20% (strictly) (R15.2)
            "ask" otherwise (20% <= rate <= 85%) (R15.3)

        Cold start mode overrides: always returns "ask" when active (R15.5).
        """
        if self.is_cold_start():
            return "ask"

        effective_rate = self._compute_effective_rate(goal_type)

        if effective_rate > self.AUTO_EXECUTE_THRESHOLD:
            return "auto_execute"
        elif effective_rate < self.SUPPRESS_THRESHOLD:
            return "suppress"
        else:
            return "ask"

    def is_cold_start(self) -> bool:
        """Check if cold start mode is active.

        Cold start is active when fewer than 20 total proposals have been
        recorded across all types for the current project (R15.5).

        Returns:
            True if in cold start mode.
        """
        return self._learning_loop.get_total_proposal_count() < self.COLD_START_PROPOSAL_LIMIT

    def should_unsuppress(self, goal_type: str) -> bool:
        """Check if a suppressed goal type should be re-evaluated.

        After every 10 proposals across other (non-suppressed) types,
        one suppressed goal is presented for re-evaluation (R15.2).

        Args:
            goal_type: The suppressed goal type to check.

        Returns:
            True if it's time to present a suppressed goal for re-evaluation.
        """
        # Only relevant for suppressed types
        if self.get_autonomy_level(goal_type) != "suppress":
            return False

        return self._proposal_count_since_last_unsuppress >= self.UNSUPPRESS_INTERVAL

    def record_proposal(self, goal_type: str) -> None:
        """Track a new proposal for unsuppression counting.

        Increments the counter used for unsuppression logic. When the
        counter reaches the interval threshold, the next call to
        should_unsuppress() for a suppressed type will return True.

        Args:
            goal_type: The goal type being proposed.
        """
        # Only count proposals for non-suppressed types toward unsuppression
        if self.get_autonomy_level(goal_type) != "suppress":
            self._proposal_count_since_last_unsuppress += 1

    def reset_unsuppress_counter(self) -> None:
        """Reset the unsuppression counter after presenting a suppressed goal."""
        self._proposal_count_since_last_unsuppress = 0

    def recalculate_on_signal(self, goal_type: str) -> str:
        """Recalculate autonomy level after a new feedback signal (R15.7).

        Called by the LearningLoop when a new signal is recorded to check
        if the rate crosses a threshold boundary. Persists updated config.

        Args:
            goal_type: The goal type whose signal was updated.

        Returns:
            The new autonomy level for the goal type.
        """
        new_level = self.get_autonomy_level(goal_type)

        # Persist updated state
        self._update_config(goal_type, new_level)

        return new_level

    def get_confidence_ceiling(self) -> float | None:
        """Return the confidence ceiling for llm_inference beliefs in cold start.

        Returns:
            0.7 if in cold start mode, None otherwise.
        """
        if self.is_cold_start():
            return self.CONFIDENCE_CEILING_COLD_START
        return None

    def _compute_effective_rate(self, goal_type: str) -> float:
        """Compute effective acceptance rate using weighted blend (R15.4).

        effective_rate = (global_count * global_rate + local_count * local_rate)
                       / (global_count + local_count)

        When local_count >= 20, use local_rate exclusively.
        When both counts are 0, default to 0.5.

        Args:
            goal_type: The goal type to compute the rate for.

        Returns:
            Effective acceptance rate as a float between 0.0 and 1.0.
        """
        local_rate = self._learning_loop.get_acceptance_rate(goal_type)
        local_count = self._get_local_count(goal_type)

        # When local_count >= 20, use local_rate exclusively
        if local_count >= self.LOCAL_EXCLUSIVE_THRESHOLD:
            return local_rate

        global_rate = self._get_global_rate(goal_type)
        global_count = self._get_global_count(goal_type)

        total_count = global_count + local_count

        # When both counts are 0, default to 0.5
        if total_count == 0:
            return 0.5

        # Weighted blend
        return (global_count * global_rate + local_count * local_rate) / total_count

    def _get_local_count(self, goal_type: str) -> int:
        """Get count of local signals for a given goal type."""
        return len([
            s for s in self._learning_loop._signals
            if s.goal_type == goal_type
        ])

    def _get_global_rate(self, goal_type: str) -> float:
        """Get global acceptance rate for a goal type from persisted config.

        Returns 0.5 if no global data exists.
        """
        global_rates = self._config.get("global_rates", {})
        return global_rates.get(goal_type, 0.5)

    def _get_global_count(self, goal_type: str) -> int:
        """Get global signal count for a goal type from persisted config.

        Returns 0 if no global data exists.
        """
        global_counts = self._config.get("global_counts", {})
        return global_counts.get(goal_type, 0)

    def _update_config(self, goal_type: str, level: str) -> None:
        """Update and persist autonomy configuration (R15.6).

        Saves per-type autonomy levels and rates to
        ~/.kognisant_core/autonomy_config.json.
        """
        if "levels" not in self._config:
            self._config["levels"] = {}
        self._config["levels"][goal_type] = level

        # Update the local rate in global stats for cross-project use
        if "global_rates" not in self._config:
            self._config["global_rates"] = {}
        if "global_counts" not in self._config:
            self._config["global_counts"] = {}

        local_rate = self._learning_loop.get_acceptance_rate(goal_type)
        local_count = self._get_local_count(goal_type)

        # Blend into global stats: simple running update
        old_global_count = self._config["global_counts"].get(goal_type, 0)
        old_global_rate = self._config["global_rates"].get(goal_type, 0.5)

        new_global_count = old_global_count + local_count
        if new_global_count > 0:
            new_global_rate = (
                old_global_count * old_global_rate + local_count * local_rate
            ) / new_global_count
        else:
            new_global_rate = 0.5

        self._config["global_rates"][goal_type] = new_global_rate
        self._config["global_counts"][goal_type] = new_global_count

        try:
            save_autonomy_config(self._config)
        except OSError as e:
            logger.error("Failed to persist autonomy config: %s", e)


class ExecutionEngine:
    """Translates accepted goals into PERP tasks with World Model context.

    Manages goal execution lifecycle:
    1. Build enriched task description with causal chain from the graph
    2. Create pre-execution snapshot of affected subgraph
    3. Execute goal via a pluggable PERP callback (stubbed for now)
    4. On success: reinforce traversed edges
    5. On failure/timeout: restore snapshot
    6. Update goal status in active.json / completed.json

    Requirements: R13.1, R13.2, R13.3, R13.4, R13.5, R13.6
    """

    EXECUTION_TIMEOUT_SECONDS = 600  # 10 minutes

    # Remediation templates by goal type
    _REMEDIATION_STEPS = {
        "contract_violation": [
            "Review the contract between source and target nodes",
            "Update function signatures to match expected types",
            "Add appropriate type guards or validation",
            "Update callers if the contract has legitimately changed",
        ],
        "coverage_gap": [
            "Identify untested branches in the target module",
            "Write unit tests covering the identified branches",
            "Verify tests exercise the specified code paths",
        ],
        "decay_alert": [
            "Review the module for stale assumptions",
            "Re-run static analysis to refresh confidence scores",
            "Update or remove outdated beliefs about this module",
        ],
        "complexity": [
            "Extract helper functions from the complex function",
            "Simplify conditional logic where possible",
            "Consider applying design patterns to reduce branching",
        ],
        "stale_artifact": [
            "Review the file for relevance to current project goals",
            "Update or remove stale code and documentation",
            "Re-run static analysis to rebuild confidence",
        ],
        "pattern_detection": [
            "Investigate the repeated error pattern",
            "Add error handling or defensive checks at the source",
            "Consider adding retry logic or circuit breakers",
        ],
    }

    def __init__(
        self,
        store: "WorldModelStore",
        graph: "DependencyGraph",
        perp_callback: Callable[[str, dict, list], bool] | None = None,
        maintenance_engine: "GraphMaintenanceEngine | None" = None,
        project_root: str | None = None,
    ):
        """Initialize with store for snapshots and graph for context.

        Args:
            store: WorldModelStore instance for snapshot operations.
            graph: DependencyGraph instance for querying causal chains.
            perp_callback: Optional callable(task_description, project_info,
                compiled_models) -> bool. If None, goals are marked as
                accepted without actual PERP execution (stub for Task 20).
            maintenance_engine: Optional GraphMaintenanceEngine for edge
                reinforcement on success.
            project_root: Project root for goal status persistence.
        """
        self._store = store
        self._graph = graph
        self._perp_callback = perp_callback
        self._maintenance_engine = maintenance_engine
        self._project_root = project_root

    def build_task_description(self, goal: Goal) -> str:
        """Generate enriched PERP task description with causal chain (R13.1).

        Includes:
        - Goal type and title
        - Affected nodes
        - Causal chain (edges within 2 hops with confidence > 0.3)
        - Suggested remediation steps based on goal type

        Args:
            goal: The goal to build a task description for.

        Returns:
            A rich text string suitable for PERP task execution.
        """
        lines: list[str] = []

        # Header with goal type and title
        lines.append(f"## Goal: {goal.title}")
        lines.append(f"Type: {goal.goal_type}")
        lines.append("")

        # Affected nodes
        lines.append("### Affected Nodes")
        if goal.target_node:
            lines.append(f"- Primary target: {goal.target_node}")
        if goal.target_file:
            lines.append(f"- Target file: {goal.target_file}")

        # Include context-specific affected items
        context = goal.context or {}
        if "source_node" in context:
            lines.append(f"- Source node: {context['source_node']}")
        if "affected_functions" in context:
            for func in context["affected_functions"]:
                lines.append(f"- Affected function: {func}")
        lines.append("")

        # Causal chain - edges within 2 hops with confidence > 0.3
        lines.append("### Causal Chain")
        causal_edges = self._get_causal_chain(goal)
        if causal_edges:
            for edge in causal_edges:
                lines.append(
                    f"- {edge.source} --[{edge.edge_type}]--> {edge.target} "
                    f"(confidence: {edge.confidence:.2f})"
                )
        else:
            lines.append("- No causal chain edges found above confidence threshold")
        lines.append("")

        # Suggested remediation steps
        lines.append("### Suggested Remediation Steps")
        steps = self._REMEDIATION_STEPS.get(goal.goal_type, [])
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

        # Additional context
        if context:
            lines.append("### Additional Context")
            for key, value in context.items():
                if key not in ("source_node", "affected_functions"):
                    lines.append(f"- {key}: {value}")

        return "\n".join(lines)

    def execute_goal(
        self,
        goal: Goal,
        project_info: dict,
        compiled_models: list,
    ) -> bool:
        """Execute a goal via PERP. Returns success status (R13.2-R13.6).

        Steps:
        1. Build task description
        2. Create pre-execution snapshot (abort on failure)
        3. Call PERP orchestrator with enriched task description
        4. On success: reinforce World Model edges
        5. On failure/timeout: restore snapshot
        6. Update goal status

        Args:
            goal: The accepted goal to execute.
            project_info: Project info dict for PERP context.
            compiled_models: Compiled model list for PERP.

        Returns:
            True if goal execution succeeded, False otherwise.
        """
        # 1. Build task description
        task_description = self.build_task_description(goal)

        # 2. Create pre-execution snapshot (R13.2, R13.3)
        affected_node_ids = self._get_affected_node_ids(goal)
        try:
            snapshot_path = self._store.create_snapshot(affected_node_ids)
            goal.snapshot_path = snapshot_path
        except (OSError, IOError, Exception) as e:
            # R13.3: Abort on snapshot failure
            logger.error(
                "Snapshot creation failed for goal %s: %s", goal.id, e
            )
            goal.status = "failed"
            goal.resolved_at = utc_now_iso()
            goal.context["failure_reason"] = "snapshot creation failed"
            self._update_goal_status(goal)
            return False

        # 3. Execute via PERP callback with 10-minute timeout (R13.5)
        success = False
        failure_reason = ""

        if self._perp_callback is not None:
            # Use threading for timeout enforcement
            result_holder: dict = {"success": False, "error": None}

            def _run_perp():
                try:
                    result_holder["success"] = self._perp_callback(
                        task_description, project_info, compiled_models
                    )
                except Exception as exc:
                    result_holder["error"] = str(exc)

            thread = threading.Thread(target=_run_perp, daemon=True)
            thread.start()
            thread.join(timeout=self.EXECUTION_TIMEOUT_SECONDS)

            if thread.is_alive():
                # Timeout occurred (R13.5)
                failure_reason = (
                    f"execution timed out after {self.EXECUTION_TIMEOUT_SECONDS}s"
                )
                logger.warning(
                    "Goal %s execution timed out after %ds",
                    goal.id,
                    self.EXECUTION_TIMEOUT_SECONDS,
                )
                success = False
            elif result_holder["error"] is not None:
                failure_reason = f"execution error: {result_holder['error']}"
                success = False
            else:
                success = result_holder["success"]
                if not success:
                    failure_reason = "PERP execution returned failure"
        else:
            # Stub mode: no actual PERP execution, mark as accepted/completed
            # This allows the system to work before full PERP integration (Task 20)
            logger.info(
                "Goal %s accepted (stub mode, no PERP callback configured)",
                goal.id,
            )
            success = True

        # 4/5. Handle success or failure
        if success:
            # R13.4: Reinforce edges traversed during execution
            self._reinforce_traversed_edges(goal)
            goal.status = "completed"
            goal.resolved_at = utc_now_iso()
            # Clean up snapshot on success
            try:
                self._store.delete_snapshot(snapshot_path)
            except (OSError, Exception) as e:
                logger.warning(
                    "Failed to clean up snapshot for goal %s: %s", goal.id, e
                )
        else:
            # R13.5: Restore snapshot on failure/timeout
            try:
                self._store.restore_snapshot(snapshot_path)
            except (OSError, Exception) as e:
                logger.error(
                    "Failed to restore snapshot for goal %s: %s", goal.id, e
                )
            goal.status = "failed"
            goal.resolved_at = utc_now_iso()
            goal.context["failure_reason"] = failure_reason
            logger.warning(
                "Goal %s execution failed: %s", goal.id, failure_reason
            )

        # 6. Update goal status (R13.6)
        self._update_goal_status(goal)

        return success

    def _get_causal_chain(self, goal: Goal) -> list:
        """Get edges within 2 hops of affected nodes with confidence > 0.3.

        Used for building the causal chain context in task descriptions.
        """
        affected_node_ids = self._get_affected_node_ids(goal)
        causal_edges = []
        seen_edge_ids: set[str] = set()

        for node_id in affected_node_ids:
            # Get reachable nodes within 2 hops with min confidence 0.3
            reachable = self._graph.query_reachable(
                node_id, max_hops=2, min_confidence=0.3
            )

            # Collect edges from the affected node and its reachable neighbors
            for edge in self._graph.get_edges_from(node_id):
                if (
                    edge.confidence > 0.3
                    and edge.id not in seen_edge_ids
                ):
                    causal_edges.append(edge)
                    seen_edge_ids.add(edge.id)

            # Also get incoming edges to the affected node
            for edge in self._graph.get_edges_to(node_id):
                if (
                    edge.confidence > 0.3
                    and edge.id not in seen_edge_ids
                ):
                    causal_edges.append(edge)
                    seen_edge_ids.add(edge.id)

            # Get edges between reachable nodes (2nd hop)
            for node in reachable:
                for edge in self._graph.get_edges_from(node.id):
                    if (
                        edge.confidence > 0.3
                        and edge.id not in seen_edge_ids
                    ):
                        causal_edges.append(edge)
                        seen_edge_ids.add(edge.id)

        return causal_edges

    def _get_affected_node_ids(self, goal: Goal) -> list[str]:
        """Extract the list of affected node IDs from a goal."""
        node_ids: list[str] = []

        if goal.target_node:
            node_ids.append(goal.target_node)

        context = goal.context or {}
        if "source_node" in context:
            node_ids.append(context["source_node"])
        if "affected_functions" in context:
            node_ids.extend(context["affected_functions"])

        # Deduplicate while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for nid in node_ids:
            if nid not in seen:
                seen.add(nid)
                result.append(nid)

        return result if result else ["unknown"]

    def _reinforce_traversed_edges(self, goal: Goal) -> None:
        """Reinforce World Model edges traversed during execution (R13.4).

        Uses the causal chain edge IDs stored in the goal record to
        reinforce edges that were consistent with observed behavior.
        """
        if self._maintenance_engine is None:
            return

        # Get edge IDs from the causal chain
        causal_edges = self._get_causal_chain(goal)
        edge_ids = [edge.id for edge in causal_edges]

        if edge_ids:
            self._maintenance_engine.reinforce_edges(edge_ids)
            logger.info(
                "Reinforced %d edges for goal %s", len(edge_ids), goal.id
            )

    def _update_goal_status(self, goal: Goal) -> None:
        """Update goal status in active.json, move completed/failed to completed.json (R13.6).

        Uses atomic writes for safety.
        """
        if self._project_root is None:
            return

        goals_dir = os.path.join(self._project_root, ".kognisant", "goals")
        os.makedirs(goals_dir, exist_ok=True)

        active_path = os.path.join(goals_dir, "active.json")
        completed_path = os.path.join(goals_dir, "completed.json")

        # Load current active goals
        active_goals = self._load_json(active_path, default=[])

        # Update or add goal in active list
        goal_dict = goal.to_dict()
        updated = False
        for i, existing in enumerate(active_goals):
            if existing.get("id") == goal.id:
                active_goals[i] = goal_dict
                updated = True
                break
        if not updated:
            active_goals.append(goal_dict)

        # If goal is completed or failed, move to completed.json
        if goal.status in ("completed", "failed"):
            # Remove from active
            active_goals = [
                g for g in active_goals if g.get("id") != goal.id
            ]
            # Add to completed
            completed_goals = self._load_json(completed_path, default=[])
            completed_goals.append(goal_dict)
            self._atomic_write(completed_path, completed_goals)

        # Write updated active list
        self._atomic_write(active_path, active_goals)

    def _load_json(self, path: str, default=None):
        """Load a JSON file, returning default if it doesn't exist or is invalid."""
        if default is None:
            default = []
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default

    def _atomic_write(self, path: str, data: object) -> None:
        """Write JSON data atomically (write to .tmp then os.rename)."""
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, path)
        except OSError as e:
            logger.error("Atomic write failed for %s: %s", path, e)
            # Clean up tmp file if rename failed
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class ProposalInterface:
    """Surfaces goals to users via session-start display, /goals command,
    inline contextual suggestions, and critical priority notifications.

    Methods return formatted strings — the caller in chat.py prints them.

    Requirements: R12.1, R12.2, R12.3, R12.4, R12.5, R12.6, R12.7
    """

    def __init__(
        self,
        project_root: str,
        goals: list[Goal] | None = None,
        learning_loop: LearningLoop | None = None,
        ranker: PriorityRanker | None = None,
    ):
        """Initialize ProposalInterface.

        Args:
            project_root: Project root for loading/persisting goals.
            goals: Optional list of active goals. If None, loads from
                .kognisant/goals/active.json.
            learning_loop: Optional LearningLoop for recording feedback.
            ranker: Optional PriorityRanker for ranking goals.
        """
        self._project_root = project_root
        self._learning_loop = learning_loop
        self._ranker = ranker
        self._notified_critical: set[str] = set()

        if goals is not None:
            self._goals = goals
        else:
            self._goals = self._load_active_goals()

    def display_session_start_goals(self) -> str:
        """Show top 3 active goals by priority (R12.1).

        Each goal displays: type, score, and description (≤120 chars).

        Returns:
            Formatted string with up to 3 goals, or empty string if none.
        """
        active = [g for g in self._goals if g.status == "active"]
        if not active:
            return ""

        # Sort by priority_score descending
        ranked = sorted(active, key=lambda g: g.priority_score, reverse=True)
        top_goals = ranked[:3]

        lines: list[str] = []
        lines.append(
            f"{Colors.BOLD}{Colors.CYAN}━━━ Active Goals ━━━{Colors.RESET}"
        )
        for goal in top_goals:
            desc = goal.title[:120]
            score_color = Colors.RED if goal.priority_score > 8.0 else Colors.YELLOW
            lines.append(
                f"  {Colors.BOLD}{goal.id}{Colors.RESET} "
                f"[{Colors.MAGENTA}{goal.goal_type}{Colors.RESET}] "
                f"score: {score_color}{goal.priority_score:.1f}{Colors.RESET} "
                f"- {desc}"
            )
        lines.append(
            f"{Colors.CYAN}Use /goals to see all, "
            f"/goals accept <id> or /goals dismiss <id>{Colors.RESET}"
        )
        return "\n".join(lines)

    def handle_command(self, args: list[str]) -> str:
        """Dispatch /goals command (R12.2, R12.3, R12.4, R12.5).

        Args:
            args: Command arguments. Empty for list, or
                ["accept", id] or ["dismiss", id].

        Returns:
            Formatted string with command output.
        """
        if not args:
            return self._display_all_goals()

        subcommand = args[0].lower()

        if subcommand == "accept" and len(args) >= 2:
            return self._accept_goal(args[1])
        elif subcommand == "dismiss" and len(args) >= 2:
            return self._dismiss_goal(args[1])
        else:
            return self._display_all_goals()

    def get_inline_suggestion(self, file_path: str) -> str | None:
        """Show highest-priority goal associated with a file (R12.7).

        Args:
            file_path: The file path being read/worked on.

        Returns:
            Formatted suggestion string, or None if no goals match.
        """
        active = [g for g in self._goals if g.status == "active"]
        if not active:
            return None

        # Find goals associated with this file
        matching = []
        for goal in active:
            if goal.target_file and self._paths_match(goal.target_file, file_path):
                matching.append(goal)
            elif goal.target_node and self._node_in_file(
                goal.target_node, file_path
            ):
                matching.append(goal)

        if not matching:
            return None

        # Select highest priority
        best = max(matching, key=lambda g: g.priority_score)
        desc = best.title[:120]
        return (
            f"{Colors.YELLOW}💡 Goal suggestion:{Colors.RESET} "
            f"{Colors.BOLD}{best.id}{Colors.RESET} "
            f"[{Colors.MAGENTA}{best.goal_type}{Colors.RESET}] "
            f"- {desc}"
        )

    def check_critical_notifications(self) -> str | None:
        """Check for critical priority goals (score > 8.0) and emit log (R12.6).

        Emits exactly one log entry per goal that crosses the threshold.

        Returns:
            Notification string if a new critical goal is found, else None.
        """
        active = [g for g in self._goals if g.status == "active"]
        notifications: list[str] = []

        for goal in active:
            if goal.priority_score > 8.0 and goal.id not in self._notified_critical:
                self._notified_critical.add(goal.id)
                logger.warning(
                    "CRITICAL PRIORITY: Goal %s (%s) has score %.1f - %s",
                    goal.id,
                    goal.goal_type,
                    goal.priority_score,
                    goal.title[:120],
                )
                notifications.append(
                    f"{Colors.RED}{Colors.BOLD}⚠ CRITICAL:{Colors.RESET} "
                    f"Goal {Colors.BOLD}{goal.id}{Colors.RESET} "
                    f"({goal.goal_type}) score {goal.priority_score:.1f} "
                    f"- {goal.title[:120]}"
                )

        if notifications:
            return "\n".join(notifications)
        return None

    def _display_all_goals(self) -> str:
        """Display all active goals grouped by type (R12.2).

        Returns:
            Formatted string with goals grouped by type.
        """
        active = [g for g in self._goals if g.status == "active"]
        if not active:
            return f"{Colors.YELLOW}No active goals.{Colors.RESET}"

        # Group by type
        by_type: dict[str, list[Goal]] = defaultdict(list)
        for goal in active:
            by_type[goal.goal_type].append(goal)

        lines: list[str] = []
        lines.append(
            f"{Colors.BOLD}{Colors.CYAN}━━━ All Active Goals ━━━{Colors.RESET}"
        )

        for goal_type in sorted(by_type.keys()):
            goals = sorted(
                by_type[goal_type], key=lambda g: g.priority_score, reverse=True
            )
            lines.append(
                f"\n  {Colors.BOLD}{Colors.MAGENTA}{goal_type}{Colors.RESET}"
            )
            for goal in goals:
                desc = goal.title[:120]
                score_color = (
                    Colors.RED if goal.priority_score > 8.0 else Colors.YELLOW
                )
                lines.append(
                    f"    {Colors.BOLD}{goal.id}{Colors.RESET} "
                    f"score: {score_color}{goal.priority_score:.1f}{Colors.RESET} "
                    f"- {desc}"
                )

        lines.append(
            f"\n{Colors.CYAN}Commands: /goals accept <id> | "
            f"/goals dismiss <id>{Colors.RESET}"
        )
        return "\n".join(lines)

    def _accept_goal(self, goal_id: str) -> str:
        """Mark goal as accepted and dispatch to ExecutionEngine (R12.3).

        Creates an ExecutionEngine instance and executes the goal via PERP
        on a background thread. The goal status is updated to "accepted"
        immediately, then to "completed" or "failed" after execution.

        Args:
            goal_id: The goal ID to accept.

        Returns:
            Formatted result string.
        """
        goal = self._find_goal(goal_id)
        if goal is None:
            return self._invalid_id_error(goal_id)

        goal.status = "accepted"

        # Record positive signal in learning loop
        if self._learning_loop is not None:
            module = goal.context.get("module", "unknown") if goal.context else "unknown"
            self._learning_loop.record_accept(
                goal.goal_type, module, utc_now_iso()
            )

        self._persist_active_goals()

        # Dispatch to ExecutionEngine on a background thread
        try:
            from .config import load_world_model, is_world_model_enabled
            from .world_model import (
                DependencyGraph,
                BeliefSystem,
                ContractRegistry,
                EpistemicGapTracker,
                GraphMaintenanceEngine,
            )
            from .world_model_store import WorldModelStore

            if is_world_model_enabled(self._project_root):
                store = load_world_model(self._project_root)
                graph_data = store.load_graph()

                # Build graph
                from .models import Node as NodeModel, Edge as EdgeModel
                graph = DependencyGraph()
                for node_dict in graph_data.get("nodes", []):
                    try:
                        graph.add_node(NodeModel.from_dict(node_dict))
                    except (KeyError, TypeError):
                        continue
                for edge_dict in graph_data.get("edges", []):
                    try:
                        graph.add_edge(EdgeModel.from_dict(edge_dict))
                    except (KeyError, TypeError):
                        continue

                # Create maintenance engine for edge reinforcement
                beliefs = BeliefSystem()
                contracts = ContractRegistry()
                gaps = EpistemicGapTracker()
                maintenance = GraphMaintenanceEngine(graph, beliefs, contracts, gaps)

                # Create perp_callback for PERP orchestration
                def _perp_callback(task_description, project_info, compiled_models):
                    """Execute goal via PERP orchestration."""
                    from .agents import _orchestrate_worker, SwarmController
                    from .config import get_compiled_models, get_project_info

                    # Build project_info if not provided
                    if not project_info:
                        project_info = get_project_info(self._project_root)
                    if not compiled_models:
                        compiled_models = get_compiled_models()

                    # Run orchestration synchronously on this thread
                    SwarmController.stop_event.clear()
                    SwarmController.resume_event.set()
                    SwarmController.is_active = True
                    try:
                        _orchestrate_worker(task_description, project_info, compiled_models)
                        return True
                    except Exception:
                        return False
                    finally:
                        SwarmController.is_active = False

                engine = ExecutionEngine(
                    store=store,
                    graph=graph,
                    perp_callback=_perp_callback,
                    maintenance_engine=maintenance,
                    project_root=self._project_root,
                )

                import threading
                def _run_execution():
                    try:
                        engine.execute_goal(goal, {}, [])
                    except Exception as e:
                        logger.error("Goal execution failed for %s: %s", goal_id, e)

                exec_thread = threading.Thread(target=_run_execution, daemon=True)
                exec_thread.start()

        except Exception as e:
            logger.error("Failed to dispatch goal %s to ExecutionEngine: %s", goal_id, e)

        return (
            f"{Colors.GREEN}✓ Goal {Colors.BOLD}{goal_id}{Colors.RESET}"
            f"{Colors.GREEN} accepted and dispatched for execution.{Colors.RESET}"
        )

    def _dismiss_goal(self, goal_id: str) -> str:
        """Mark goal as dismissed and record negative signal (R12.4).

        Args:
            goal_id: The goal ID to dismiss.

        Returns:
            Formatted result string.
        """
        goal = self._find_goal(goal_id)
        if goal is None:
            return self._invalid_id_error(goal_id)

        goal.status = "dismissed"
        goal.resolved_at = utc_now_iso()

        # Record negative signal in learning loop
        if self._learning_loop is not None:
            module = goal.context.get("module", "unknown") if goal.context else "unknown"
            self._learning_loop.record_dismiss(
                goal.goal_type, module, utc_now_iso()
            )

        self._persist_active_goals()

        return (
            f"{Colors.YELLOW}✗ Goal {Colors.BOLD}{goal_id}{Colors.RESET}"
            f"{Colors.YELLOW} dismissed.{Colors.RESET}"
        )

    def _invalid_id_error(self, goal_id: str) -> str:
        """Show error for invalid goal id and list active ids (R12.5).

        Args:
            goal_id: The invalid goal ID.

        Returns:
            Error message with list of active IDs.
        """
        active = [g for g in self._goals if g.status == "active"]
        active_ids = [g.id for g in active]

        lines: list[str] = []
        lines.append(
            f"{Colors.RED}Error: Goal '{goal_id}' not found.{Colors.RESET}"
        )
        if active_ids:
            lines.append(f"Active goal IDs: {', '.join(active_ids)}")
        else:
            lines.append("No active goals available.")
        return "\n".join(lines)

    def _find_goal(self, goal_id: str) -> Goal | None:
        """Find an active goal by ID."""
        for goal in self._goals:
            if goal.id == goal_id and goal.status == "active":
                return goal
        return None

    def _paths_match(self, goal_path: str, file_path: str) -> bool:
        """Check if a goal's target file matches the given file path.

        Handles both relative and absolute paths.
        """
        # Normalize paths for comparison
        norm_goal = os.path.normpath(goal_path)
        norm_file = os.path.normpath(file_path)

        # Direct match
        if norm_goal == norm_file:
            return True

        # Check if one ends with the other (relative vs absolute)
        if norm_file.endswith(norm_goal) or norm_goal.endswith(norm_file):
            return True

        return False

    def _node_in_file(self, node_id: str, file_path: str) -> bool:
        """Check if a node ID corresponds to the given file.

        Node IDs follow the pattern "module.class.function" or "module.function".
        We check if the module portion matches the file's module name.
        """
        # Extract module from file path
        file_base = os.path.basename(file_path)
        if file_base.endswith(".py"):
            file_module = file_base[:-3]
        else:
            file_module = file_base

        # Check if the node_id starts with the file module
        node_parts = node_id.split(".")
        if node_parts and node_parts[0] == file_module:
            return True

        return False

    def _load_active_goals(self) -> list[Goal]:
        """Load active goals from .kognisant/goals/active.json."""
        path = os.path.join(
            self._project_root, ".kognisant", "goals", "active.json"
        )
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return [Goal.from_dict(g) for g in data]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _persist_active_goals(self) -> None:
        """Persist active goals to .kognisant/goals/active.json."""
        goals_dir = os.path.join(self._project_root, ".kognisant", "goals")
        os.makedirs(goals_dir, exist_ok=True)

        active_path = os.path.join(goals_dir, "active.json")
        data = [g.to_dict() for g in self._goals]

        tmp_path = active_path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, active_path)
        except OSError as e:
            logger.error("Failed to persist active goals: %s", e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
