"""World Model in-memory operations.

This module contains the DependencyGraph class for managing nodes and edges
in-memory with BFS traversal, caching, and shard-aware queries.
"""

from collections import OrderedDict, deque

from cli_kognisant.models import Belief, Contract, Edge, EpistemicGap, Node, TraceRecord, clamp_confidence, generate_uuid, utc_now_iso


# ───────────────────────────────────────────────────────────
# DependencyGraph
# ───────────────────────────────────────────────────────────


class DependencyGraph:
    """In-memory dependency graph with dict-based adjacency storage.

    Nodes are stored by id. Edges are indexed by id, source, and target
    for efficient lookups in all directions.
    """

    _CACHE_MAX_SIZE = 100

    def __init__(self) -> None:
        """Initialize empty graph with node/edge storage and cache."""
        # Primary storage
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}

        # Adjacency indexes: node_id -> set of edge_ids
        self._edges_from: dict[str, set[str]] = {}
        self._edges_to: dict[str, set[str]] = {}

        # LRU cache for query_reachable
        # Key: (node_id, max_hops, edge_types_tuple | None, min_confidence)
        # Value: (list[Node], {edge_id: version})
        self._reachable_cache: OrderedDict[
            tuple, tuple[list[Node], dict[str, int]]
        ] = OrderedDict()

    # ─── Node operations ──────────────────────────────────────

    def add_node(self, node: Node) -> None:
        """Add a node to the graph. Overwrites if id already exists."""
        self._nodes[node.id] = node
        if node.id not in self._edges_from:
            self._edges_from[node.id] = set()
        if node.id not in self._edges_to:
            self._edges_to[node.id] = set()

    def remove_node(self, node_id: str) -> None:
        """Remove a node and all associated edges (incoming and outgoing)."""
        if node_id not in self._nodes:
            return

        # Collect all edge ids to remove (both directions)
        edge_ids_to_remove: set[str] = set()
        if node_id in self._edges_from:
            edge_ids_to_remove.update(self._edges_from[node_id])
        if node_id in self._edges_to:
            edge_ids_to_remove.update(self._edges_to[node_id])

        # Remove each edge cleanly
        for edge_id in edge_ids_to_remove:
            self._remove_edge_internal(edge_id)

        # Remove node and its index entries
        del self._nodes[node_id]
        self._edges_from.pop(node_id, None)
        self._edges_to.pop(node_id, None)

    def get_node(self, node_id: str) -> Node | None:
        """Get a node by id, or None if not found."""
        return self._nodes.get(node_id)

    # ─── Edge operations ──────────────────────────────────────

    def add_edge(self, edge: Edge) -> None:
        """Add an edge to the graph. Overwrites if id already exists."""
        # If replacing an existing edge with same id, clean up old indexes
        if edge.id in self._edges:
            self._remove_edge_internal(edge.id)

        self._edges[edge.id] = edge

        # Update adjacency indexes
        if edge.source not in self._edges_from:
            self._edges_from[edge.source] = set()
        self._edges_from[edge.source].add(edge.id)

        if edge.target not in self._edges_to:
            self._edges_to[edge.target] = set()
        self._edges_to[edge.target].add(edge.id)

    def remove_edge(self, edge_id: str) -> None:
        """Remove an edge by id."""
        self._remove_edge_internal(edge_id)

    def _remove_edge_internal(self, edge_id: str) -> None:
        """Internal edge removal with index cleanup."""
        edge = self._edges.get(edge_id)
        if edge is None:
            return

        # Clean up adjacency indexes
        source_set = self._edges_from.get(edge.source)
        if source_set is not None:
            source_set.discard(edge_id)

        target_set = self._edges_to.get(edge.target)
        if target_set is not None:
            target_set.discard(edge_id)

        del self._edges[edge_id]

    def get_edges_from(self, node_id: str) -> list[Edge]:
        """Get all outgoing edges from a node."""
        edge_ids = self._edges_from.get(node_id, set())
        return [self._edges[eid] for eid in edge_ids if eid in self._edges]

    def get_edges_to(self, node_id: str) -> list[Edge]:
        """Get all incoming edges to a node."""
        edge_ids = self._edges_to.get(node_id, set())
        return [self._edges[eid] for eid in edge_ids if eid in self._edges]

    # ─── Merge edge (complementary evidence - R5.3) ───────────

    def merge_edge(self, new_edge: Edge) -> None:
        """Merge a new edge with existing edges using complementary evidence.

        If an edge of the same type already exists between the same source
        and target nodes, set confidence to the maximum of the two scores.
        Otherwise, add the new edge normally.
        """
        # Find existing edge of same type between same nodes
        existing_edge = self._find_edge_by_type(
            new_edge.source, new_edge.target, new_edge.edge_type
        )

        if existing_edge is not None:
            # Complementary evidence: take max confidence
            old_confidence = existing_edge.confidence
            new_confidence = max(existing_edge.confidence, new_edge.confidence)
            existing_edge.confidence = new_confidence

            # Update version if confidence changed by more than 0.1
            if abs(new_confidence - old_confidence) > 0.1:
                existing_edge.version += 1

            # If the new edge has dynamic provenance, mark as not conditional
            if new_edge.provenance == "dynamic":
                existing_edge.conditional = False
        else:
            # No existing edge of same type — just add it
            self.add_edge(new_edge)

    def _find_edge_by_type(
        self, source: str, target: str, edge_type: str
    ) -> Edge | None:
        """Find an edge of a specific type between two nodes."""
        edge_ids = self._edges_from.get(source, set())
        for eid in edge_ids:
            edge = self._edges.get(eid)
            if edge and edge.target == target and edge.edge_type == edge_type:
                return edge
        return None

    # ─── BFS Reachability Query (R5.5) ────────────────────────

    def query_reachable(
        self,
        node_id: str,
        max_hops: int,
        edge_types: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list[Node]:
        """BFS traversal up to max_hops with edge type and confidence filters.

        Returns list of reachable nodes (excluding start node). Returns empty
        list if node_id does not exist in the graph (R5.6).

        Results are cached per-session. Cache key includes all query params.
        Cache is invalidated if any edge version in the result snapshot changed.
        LRU eviction at 100 entries max.
        """
        # Clamp max_hops to [1, 10]
        max_hops = max(1, min(10, max_hops))

        # R5.6: node not found → return empty list
        if node_id not in self._nodes:
            return []

        # Build cache key
        edge_types_key = tuple(sorted(edge_types)) if edge_types else None
        cache_key = (node_id, max_hops, edge_types_key, min_confidence)

        # Check cache
        if cache_key in self._reachable_cache:
            cached_nodes, edge_version_snapshot = self._reachable_cache[cache_key]
            # Validate snapshot: check if any edge version changed
            if self._is_cache_valid(edge_version_snapshot):
                # Move to end for LRU
                self._reachable_cache.move_to_end(cache_key)
                return cached_nodes

            # Invalidate stale entry
            del self._reachable_cache[cache_key]

        # Perform BFS
        visited: set[str] = {node_id}
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        result_nodes: list[Node] = []
        relevant_edge_ids: set[str] = set()

        while queue:
            current_id, hops = queue.popleft()

            if hops >= max_hops:
                continue

            # Traverse outgoing edges
            for edge_id in self._edges_from.get(current_id, set()):
                edge = self._edges.get(edge_id)
                if edge is None:
                    continue

                # Apply edge type filter
                if edge_types and edge.edge_type not in edge_types:
                    continue

                # Apply confidence threshold filter
                if edge.confidence < min_confidence:
                    continue

                relevant_edge_ids.add(edge_id)

                target = edge.target
                if target not in visited:
                    visited.add(target)
                    node = self._nodes.get(target)
                    if node is not None:
                        result_nodes.append(node)
                        queue.append((target, hops + 1))

        # Build edge version snapshot for cache validation
        edge_version_snapshot = {
            eid: self._edges[eid].version
            for eid in relevant_edge_ids
            if eid in self._edges
        }

        # Store in cache with LRU eviction
        self._reachable_cache[cache_key] = (result_nodes, edge_version_snapshot)
        self._reachable_cache.move_to_end(cache_key)

        # Evict oldest if over capacity
        while len(self._reachable_cache) > self._CACHE_MAX_SIZE:
            self._reachable_cache.popitem(last=False)

        return result_nodes

    def _is_cache_valid(self, edge_version_snapshot: dict[str, int]) -> bool:
        """Check if all edge versions in the snapshot still match current state."""
        for edge_id, version in edge_version_snapshot.items():
            edge = self._edges.get(edge_id)
            if edge is None:
                # Edge was removed — cache is stale
                return False
            if edge.version != version:
                return False
        return True

    # ─── Shard-aware operations ───────────────────────────────

    def get_nodes_in_module(self, module: str) -> list[Node]:
        """Get all nodes belonging to a specific module."""
        return [
            node for node in self._nodes.values() if node.module == module
        ]

    def get_cross_module_edges(self, modules: list[str]) -> list[Edge]:
        """Get edges that cross between the specified modules.

        Returns edges where source is in one module and target is in
        a different module, and both modules are in the provided list.
        """
        module_set = set(modules)
        cross_edges: list[Edge] = []

        for edge in self._edges.values():
            source_node = self._nodes.get(edge.source)
            target_node = self._nodes.get(edge.target)

            if source_node is None or target_node is None:
                continue

            # Both nodes must be in the specified modules
            if source_node.module not in module_set:
                continue
            if target_node.module not in module_set:
                continue

            # Must cross module boundaries
            if source_node.module != target_node.module:
                cross_edges.append(edge)

        return cross_edges

    # ─── Conditional edge marking (R5.4) ──────────────────────

    def mark_conditional(self, edge_id: str) -> None:
        """Mark an edge as conditional and reduce confidence by 20%.

        Used for static-only edges with no dynamic confirmation.
        """
        edge = self._edges.get(edge_id)
        if edge is None:
            return

        edge.conditional = True
        old_confidence = edge.confidence
        edge.confidence = max(0.0, edge.confidence * 0.8)

        # Increment version if confidence changed by more than 0.1
        if abs(edge.confidence - old_confidence) > 0.1:
            edge.version += 1



# ───────────────────────────────────────────────────────────
# BeliefSystem
# ───────────────────────────────────────────────────────────


class BeliefSystem:
    """In-memory belief store with provenance-based confidence, decay, and pruning.

    Beliefs are indexed by both belief id and node id for efficient lookups.
    Initial confidence is determined by provenance type (R6.2).
    """

    _PROVENANCE_CONFIDENCE: dict[str, float] = {
        "static": 1.0,
        "dynamic": 0.8,
        "llm_inference": 0.5,
        "user_assertion": 0.9,
    }

    def __init__(self) -> None:
        """Initialize empty belief store with dual indexes."""
        # Primary storage: belief_id -> Belief
        self._beliefs: dict[str, Belief] = {}
        # Index: node_id -> set of belief_ids
        self._node_index: dict[str, set[str]] = {}

    def add_belief(self, belief: Belief) -> None:
        """Add a belief, overriding confidence based on provenance (R6.2).

        The initial confidence is set according to provenance type regardless
        of whatever confidence value the Belief was created with.
        """
        # Override confidence based on provenance
        belief.confidence = clamp_confidence(
            self._PROVENANCE_CONFIDENCE.get(belief.provenance, 0.5)
        )

        self._beliefs[belief.id] = belief

        # Update node index
        if belief.node_id not in self._node_index:
            self._node_index[belief.node_id] = set()
        self._node_index[belief.node_id].add(belief.id)

    def get_beliefs_for_node(self, node_id: str) -> list[Belief]:
        """Get all active (non-archived) beliefs associated with a node."""
        belief_ids = self._node_index.get(node_id, set())
        return [
            self._beliefs[bid]
            for bid in belief_ids
            if bid in self._beliefs and not self._beliefs[bid].archived
        ]

    def reinforce(self, belief_id: str) -> None:
        """Increase confidence by 10% of remaining distance to 1.0 (R6.4).

        Updates last_reinforced timestamp to current UTC time.
        """
        belief = self._beliefs.get(belief_id)
        if belief is None or belief.archived:
            return

        remaining = 1.0 - belief.confidence
        belief.confidence = clamp_confidence(belief.confidence + 0.10 * remaining)
        belief.last_reinforced = utc_now_iso()

    def contradict(self, belief_id: str) -> None:
        """Reduce confidence by 30% and increment falsification count (R6.5)."""
        belief = self._beliefs.get(belief_id)
        if belief is None or belief.archived:
            return

        belief.confidence = clamp_confidence(belief.confidence * 0.7)
        belief.falsification_count += 1

    def apply_localized_decay(
        self, modified_nodes: list[str], graph: DependencyGraph
    ) -> list[str]:
        """Apply 5% decay to beliefs near modified nodes (R6.3).

        Uses graph.query_reachable(node, 2) to find affected nodes within
        two hops. Decay is applied as a batch at tick start before any
        reinforcement/contradiction.

        Returns list of pruned belief ids (beliefs that dropped below 0.1
        threshold get pruned).
        """
        # Collect all affected node ids (modified nodes + their 2-hop neighbors)
        affected_node_ids: set[str] = set(modified_nodes)
        for node_id in modified_nodes:
            reachable = graph.query_reachable(node_id, 2)
            for node in reachable:
                affected_node_ids.add(node.id)

        # Apply 5% decay to all beliefs associated with affected nodes
        for node_id in affected_node_ids:
            belief_ids = self._node_index.get(node_id, set())
            for bid in belief_ids:
                belief = self._beliefs.get(bid)
                if belief is not None and not belief.archived:
                    belief.confidence = clamp_confidence(belief.confidence * 0.95)

        # Prune beliefs that dropped below threshold
        pruned = self.prune_below_threshold(threshold=0.1)
        return [b.id for b in pruned]

    def prune_below_threshold(self, threshold: float = 0.1) -> list[Belief]:
        """Archive beliefs below threshold with removal reason (R6.6).

        Sets archived=True and archive_reason with final confidence,
        falsification count, and triggering event.

        Returns list of archived beliefs.
        """
        pruned: list[Belief] = []

        for belief in self._beliefs.values():
            if belief.archived:
                continue
            if belief.confidence < threshold:
                belief.archived = True
                belief.archive_reason = (
                    f"Confidence {belief.confidence:.4f} below threshold {threshold}; "
                    f"falsification_count={belief.falsification_count}; "
                    f"triggered by decay/contradiction"
                )
                pruned.append(belief)

        return pruned


# ───────────────────────────────────────────────────────────
# ContractRegistry
# ───────────────────────────────────────────────────────────


class ContractRegistry:
    """Registry of implicit calling contracts between nodes.

    Contracts are indexed by (source_node, target_node) tuple.
    Tracks expected arguments, return types, error modes, and confidence.
    Emits violation events when confidence drops below threshold (R7.5).
    """

    _VIOLATION_THRESHOLD = 0.3

    def __init__(self) -> None:
        """Initialize empty registry with contract storage and event queue."""
        # Primary storage: (source_node, target_node) -> Contract
        self._contracts: dict[tuple[str, str], Contract] = {}
        # Pending violation events: list of (source, target, contract_id)
        self._pending_violations: list[tuple[str, str, str]] = []

    def register_contract(self, contract: Contract) -> None:
        """Register a contract with duplicate checking (R7.1).

        If a contract already exists for the same (source_node, target_node),
        it will not be overwritten.
        """
        key = (contract.source_node, contract.target_node)
        if key in self._contracts:
            return
        self._contracts[key] = contract

    def get_contract(self, source: str, target: str) -> Contract | None:
        """Get a contract by source and target node ids."""
        return self._contracts.get((source, target))

    def auto_register_from_signature(
        self,
        source: str,
        target: str,
        expected_args: list[str],
        expected_return: str | None = None,
    ) -> None:
        """Auto-register a contract from static analysis (R7.2).

        Creates a new contract with provenance=static and confidence=0.7
        if one does not already exist for the (source, target) pair.
        """
        key = (source, target)
        if key in self._contracts:
            return

        contract = Contract(
            id=generate_uuid(),
            source_node=source,
            target_node=target,
            expected_args=expected_args,
            expected_return=expected_return,
            confidence=0.7,
            provenance="static",
            last_verified=utc_now_iso(),
        )
        self._contracts[key] = contract

    def check_violation(
        self, source: str, target: str, observed_args: list[str]
    ) -> bool:
        """Check if observed args violate the contract (R7.3).

        Compares observed args against contract expected_args.
        On mismatch (different count or content), reduces confidence by 20%
        (multiply by 0.8), clamped at 0.0.

        Returns True if a violation was detected, False otherwise.
        Also triggers violation event emission if threshold is crossed (R7.5).
        """
        contract = self._contracts.get((source, target))
        if contract is None:
            return False

        # Check for mismatch: different arg count or different content
        is_mismatch = (
            len(observed_args) != len(contract.expected_args)
            or observed_args != contract.expected_args
        )

        if not is_mismatch:
            return False

        # Reduce confidence by 20% (multiply by 0.8), clamp at 0.0
        contract.confidence = clamp_confidence(contract.confidence * 0.8)

        # Emit violation event if confidence dropped below threshold (R7.5)
        self._maybe_emit_violation(contract)

        return True

    def reinforce_contract(self, source: str, target: str) -> None:
        """Reinforce contract confidence when static analysis confirms match (R7.4).

        Increases confidence by 10% of remaining distance to 1.0.
        If confidence rises above threshold after being violated,
        resets violated flag (R7.5).
        """
        contract = self._contracts.get((source, target))
        if contract is None:
            return

        remaining = 1.0 - contract.confidence
        contract.confidence = clamp_confidence(
            contract.confidence + 0.10 * remaining
        )
        contract.last_verified = utc_now_iso()

        # Reset violated flag if confidence rises above threshold (R7.5)
        if contract.violated and contract.confidence > self._VIOLATION_THRESHOLD:
            contract.violated = False

    def assert_contract(
        self,
        source: str,
        target: str,
        expected_args: list[str],
        expected_return: str | None = None,
        expected_errors: list[str] | None = None,
    ) -> None:
        """User assertion of a contract (R7.6).

        Creates or updates a contract with provenance=user_assertion
        and confidence=0.9.
        """
        key = (source, target)
        if key in self._contracts:
            # Update existing contract
            contract = self._contracts[key]
            contract.expected_args = expected_args
            contract.expected_return = expected_return
            contract.expected_errors = expected_errors or []
            contract.provenance = "user_assertion"
            contract.confidence = 0.9
            contract.version += 1
            contract.last_verified = utc_now_iso()
            # Reset violated if confidence now above threshold
            if contract.violated and contract.confidence > self._VIOLATION_THRESHOLD:
                contract.violated = False
        else:
            # Create new contract
            contract = Contract(
                id=generate_uuid(),
                source_node=source,
                target_node=target,
                expected_args=expected_args,
                expected_return=expected_return,
                expected_errors=expected_errors or [],
                confidence=0.9,
                provenance="user_assertion",
                last_verified=utc_now_iso(),
            )
            self._contracts[key] = contract

    def get_violated_contracts(self) -> list[Contract]:
        """Return all contracts currently in violated state (confidence < 0.3)."""
        return [
            c for c in self._contracts.values()
            if c.confidence < self._VIOLATION_THRESHOLD
        ]

    def get_pending_violations(self) -> list[tuple[str, str, str]]:
        """Return and clear the pending violation event queue.

        Returns list of (source_node, target_node, contract_id) tuples
        representing violation events that the GoalGenerator can poll.
        """
        events = self._pending_violations[:]
        self._pending_violations.clear()
        return events

    def _maybe_emit_violation(self, contract: Contract) -> None:
        """Emit a violation event if confidence is below threshold (R7.5).

        Only emits once per contract until confidence rises above 0.3 again.
        Sets violated=True to prevent duplicate emissions.
        """
        if (
            contract.confidence < self._VIOLATION_THRESHOLD
            and not contract.violated
        ):
            contract.violated = True
            self._pending_violations.append(
                (contract.source_node, contract.target_node, contract.id)
            )

# ───────────────────────────────────────────────────────────
# EpistemicGapTracker
# ───────────────────────────────────────────────────────────


class EpistemicGapTracker:
    """Tracks knowledge gaps in the world model (R8).

    Explicitly records what the system does not know, including unexercised
    functions, untested branches, and nodes needing dynamic confirmation.
    Gaps are indexed by (node_id, gap_type) for efficient dedup lookup.
    """

    # Thresholds for evaluate_gaps
    _UNEXERCISED_THRESHOLD = 5  # PERP executions before flagging
    _DYNAMIC_CONFIRMATION_TICKS = 10  # decay ticks before flagging

    def __init__(self) -> None:
        """Initialize empty gap tracker with dedup index."""
        # Primary storage: gap_id -> EpistemicGap
        self._gaps: dict[str, EpistemicGap] = {}
        # Dedup index: (node_id, gap_type) -> gap_id (only open gaps)
        self._open_index: dict[tuple[str, str], str] = {}
        # Track decay ticks for dynamic_confirmation_needed evaluation
        self._tick_count: int = 0

    def record_gap(self, gap: EpistemicGap) -> None:
        """Record a new gap with deduplication (R8.2, R8.3).

        Does not create a gap if one of the same type already exists
        open for that node.
        """
        key = (gap.node_id, gap.gap_type)

        # Dedup: skip if an open gap of same type exists for same node
        if key in self._open_index:
            existing_id = self._open_index[key]
            existing = self._gaps.get(existing_id)
            if existing is not None and existing.resolution_status == "open":
                return

        self._gaps[gap.id] = gap
        if gap.resolution_status == "open":
            self._open_index[key] = gap.id

    def resolve_gap(self, gap_id: str) -> None:
        """Resolve a gap by setting status=resolved with timestamp (R8.5)."""
        gap = self._gaps.get(gap_id)
        if gap is None:
            return

        gap.resolution_status = "resolved"
        gap.resolved_at = utc_now_iso()

        # Remove from open index
        key = (gap.node_id, gap.gap_type)
        if self._open_index.get(key) == gap_id:
            del self._open_index[key]

    def get_open_gaps(self) -> list[EpistemicGap]:
        """Return all gaps with resolution_status == 'open'."""
        return [
            gap for gap in self._gaps.values()
            if gap.resolution_status == "open"
        ]

    def get_gaps_for_module(self, module: str) -> list[EpistemicGap]:
        """Return open gaps where node belongs to the specified module.

        Uses the node_id naming convention: module = node_id.split('.')[0].
        """
        results: list[EpistemicGap] = []
        for gap in self._gaps.values():
            if gap.resolution_status != "open":
                continue
            # Extract module from node_id convention (module.class.function)
            node_module = gap.node_id.split(".")[0] if "." in gap.node_id else gap.node_id
            if node_module == module:
                results.append(gap)
        return results

    def evaluate_gaps(
        self,
        graph: DependencyGraph,
        trace_records: list[TraceRecord],
        coverage_data: dict | None,
    ) -> None:
        """Scan for new gaps and resolve existing ones based on evidence (R8.2-R8.6).

        Detects three gap types:
        - "unexercised_function": nodes with no outgoing dynamic-provenance edges
          after 5 trace_records involving their module.
        - "untested_branch": from coverage_data, only if coverage_data is not None.
        - "dynamic_confirmation_needed": nodes with only static edges after 10
          decay ticks.

        Also resolves existing gaps when evidence arrives.
        """
        self._tick_count += 1

        # --- Resolve existing gaps based on new evidence ---
        self._resolve_from_traces(graph, trace_records)
        if coverage_data is not None:
            self._resolve_from_coverage(coverage_data)

        # --- Detect unexercised_function gaps (R8.2) ---
        self._detect_unexercised_functions(graph, trace_records)

        # --- Detect untested_branch gaps (R8.3) --- skip if no coverage (R8.6)
        if coverage_data is not None:
            self._detect_untested_branches(graph, coverage_data)

        # --- Detect dynamic_confirmation_needed gaps (R8.4) ---
        if self._tick_count >= self._DYNAMIC_CONFIRMATION_TICKS:
            self._detect_dynamic_confirmation_needed(graph)

    def _resolve_from_traces(
        self, graph: DependencyGraph, trace_records: list[TraceRecord]
    ) -> None:
        """Resolve open gaps when dynamic traces provide evidence (R8.5).

        For unexercised_function gaps: resolved if a dynamic edge now exists.
        For dynamic_confirmation_needed gaps: resolved if a dynamic edge now exists.
        """
        # Collect node_ids that have dynamic outgoing edges
        nodes_with_dynamic_edges: set[str] = set()
        for node_id in self._get_all_node_ids(graph):
            edges = graph.get_edges_from(node_id)
            if any(e.provenance == "dynamic" for e in edges):
                nodes_with_dynamic_edges.add(node_id)

        # Resolve unexercised_function and dynamic_confirmation_needed gaps
        for gap in list(self._gaps.values()):
            if gap.resolution_status != "open":
                continue
            if gap.gap_type in ("unexercised_function", "dynamic_confirmation_needed"):
                if gap.node_id in nodes_with_dynamic_edges:
                    self.resolve_gap(gap.id)

    def _resolve_from_coverage(self, coverage_data: dict) -> None:
        """Resolve untested_branch gaps when coverage data shows coverage (R8.5)."""
        for gap in list(self._gaps.values()):
            if gap.resolution_status != "open":
                continue
            if gap.gap_type != "untested_branch":
                continue
            # Check if the node_id now has coverage
            # coverage_data format: {node_id: {"covered": bool, ...}}
            node_coverage = coverage_data.get(gap.node_id)
            if node_coverage and node_coverage.get("covered", False):
                self.resolve_gap(gap.id)

    def _detect_unexercised_functions(
        self, graph: DependencyGraph, trace_records: list[TraceRecord]
    ) -> None:
        """Detect function nodes with no dynamic edges after threshold (R8.2).

        A function node is flagged when its module has been involved in at
        least 5 PERP executions but the node has no outgoing dynamic edges.
        """
        if len(trace_records) < self._UNEXERCISED_THRESHOLD:
            return

        # Count trace records per module (using file_operations paths)
        module_trace_counts: dict[str, int] = {}
        for record in trace_records:
            # Track which modules are touched in this trace session
            modules_in_record: set[str] = set()
            for file_op in record.file_operations:
                # Extract module from file path: check all path components
                parts = file_op.file_path.replace("\\", "/").split("/")
                for part in parts:
                    # Module can be a directory name or a .py file stem
                    clean = part[:-3] if part.endswith(".py") else part
                    if clean:
                        modules_in_record.add(clean)
            for mod in modules_in_record:
                module_trace_counts[mod] = module_trace_counts.get(mod, 0) + 1

        # Check all function nodes
        for node_id, node in self._get_all_nodes(graph):
            if node.node_type != "function":
                continue

            module = node.module
            # Check if module has been involved in >= threshold executions
            if module_trace_counts.get(module, 0) < self._UNEXERCISED_THRESHOLD:
                continue

            # Check if node has any outgoing dynamic edges
            edges = graph.get_edges_from(node_id)
            has_dynamic = any(e.provenance == "dynamic" for e in edges)

            if not has_dynamic:
                gap = EpistemicGap(
                    id=generate_uuid(),
                    gap_type="unexercised_function",
                    node_id=node_id,
                    description=(
                        f"Function '{node_id}' has no dynamic trace edges "
                        f"after {len(trace_records)} PERP executions "
                        f"involving module '{module}'"
                    ),
                    discovered_at=utc_now_iso(),
                    resolution_status="open",
                )
                self.record_gap(gap)

    def _detect_untested_branches(
        self, graph: DependencyGraph, coverage_data: dict
    ) -> None:
        """Detect branches without test coverage (R8.3).

        coverage_data format: {node_id: {"covered": bool, "branches": [...]}}
        Only called when coverage_data is not None (R8.6).
        """
        for node_id, data in coverage_data.items():
            if data.get("covered", True):
                continue

            # Check node exists in graph
            node = graph.get_node(node_id)
            if node is None:
                continue

            gap = EpistemicGap(
                id=generate_uuid(),
                gap_type="untested_branch",
                node_id=node_id,
                description=(
                    f"Branch in '{node_id}' has no test coverage"
                ),
                discovered_at=utc_now_iso(),
                resolution_status="open",
            )
            self.record_gap(gap)

    def _detect_dynamic_confirmation_needed(
        self, graph: DependencyGraph
    ) -> None:
        """Detect nodes with only static edges after threshold ticks (R8.4).

        A node is flagged when all its outgoing edges are static-only
        (provenance == "static") after 10 decay ticks.
        """
        for node_id, node in self._get_all_nodes(graph):
            edges = graph.get_edges_from(node_id)
            if not edges:
                continue

            # Check if ALL outgoing edges are static-only
            all_static = all(e.provenance == "static" for e in edges)
            if all_static:
                gap = EpistemicGap(
                    id=generate_uuid(),
                    gap_type="dynamic_confirmation_needed",
                    node_id=node_id,
                    description=(
                        f"Node '{node_id}' has only static edges after "
                        f"{self._tick_count} decay ticks; needs dynamic confirmation"
                    ),
                    discovered_at=utc_now_iso(),
                    resolution_status="open",
                )
                self.record_gap(gap)

    @staticmethod
    def _get_all_node_ids(graph: DependencyGraph) -> list[str]:
        """Get all node ids from the graph."""
        return list(graph._nodes.keys())

    @staticmethod
    def _get_all_nodes(graph: DependencyGraph) -> list[tuple[str, Node]]:
        """Get all (node_id, node) pairs from the graph."""
        return list(graph._nodes.items())


# ───────────────────────────────────────────────────────────
# GraphMaintenanceEngine
# ───────────────────────────────────────────────────────────


class GraphMaintenanceEngine:
    """Engine for graph maintenance including decay, reinforcement, cycle detection, and conflict resolution.

    Coordinates localized decay (R9.1), firebreaks (R9.2), cycle detection (R9.3),
    stable edge exemption (R9.4), version increments (R9.5), and conflict resolution (R9.6, R9.7).
    """

    _FIREBREAK_HOPS = 3
    _DECAY_HOPS = 2
    _STABLE_THRESHOLD = 30

    def __init__(
        self,
        graph: DependencyGraph,
        beliefs: BeliefSystem,
        contracts: ContractRegistry,
        gaps: EpistemicGapTracker,
    ) -> None:
        """Initialize with references to all world model components."""
        self.graph = graph
        self.beliefs = beliefs
        self.contracts = contracts
        self.gaps = gaps
        # Archive of edges removed during conflict resolution
        self._archived_edges: list[Edge] = []
        # Log of detected cycles
        self._cycle_log: list[list[str]] = []

    def decay_tick(self, modified_nodes: list[str]) -> dict:
        """Execute one decay cycle (R9.1).

        Decay is applied as a batch at tick start before any reinforcement/contradiction.
        BFS from modified_nodes up to 2 hops to collect affected edges.
        Firebreak halts propagation at 3 hops total from source (R9.2).

        Returns:
            dict with keys:
            - edges_decayed: int
            - beliefs_pruned: list[str]
            - cycles_detected: list[list[str]]
            - conflicts_resolved: int
        """
        # 1. BFS from modified nodes to collect affected edges (up to 2 hops)
        affected_edges = self._collect_affected_edges(modified_nodes)

        # 2. Apply decay to affected edges (skip stable edges)
        edges_decayed = self._apply_decay(affected_edges)

        # 3. Apply belief decay via BeliefSystem
        beliefs_pruned = self.beliefs.apply_localized_decay(modified_nodes, self.graph)

        # 4. Detect cycles via DFS from modified nodes
        cycles_detected: list[list[str]] = []
        visited_global: set[str] = set()
        for node_id in modified_nodes:
            if node_id in self.graph._nodes:
                cycles = self.detect_cycles(node_id, visited_global)
                cycles_detected.extend(cycles)
        self._cycle_log.extend(cycles_detected)

        # 5. Resolve conflicts (edges of same type between same nodes)
        conflicts_resolved = self._resolve_conflicts()

        return {
            "edges_decayed": edges_decayed,
            "beliefs_pruned": beliefs_pruned,
            "cycles_detected": cycles_detected,
            "conflicts_resolved": conflicts_resolved,
        }

    def reinforce_edges(self, edge_ids: list[str]) -> None:
        """Reinforce specified edges (called after successful execution).

        For each edge:
        - Increment reinforcement_count
        - Set last_reinforced to current UTC time
        - Increase confidence by 10% of remaining distance to 1.0
        - If reinforcement_count >= 30, mark as stable (R9.4)
        """
        for edge_id in edge_ids:
            edge = self.graph._edges.get(edge_id)
            if edge is None:
                continue

            edge.reinforcement_count += 1
            edge.last_reinforced = utc_now_iso()

            # Increase confidence by 10% of remaining distance to 1.0
            old_confidence = edge.confidence
            remaining = 1.0 - edge.confidence
            edge.confidence = clamp_confidence(edge.confidence + 0.10 * remaining)

            # Version increment if confidence changed by more than 0.1 (R9.5)
            if abs(edge.confidence - old_confidence) > 0.1:
                edge.version += 1

            # Mark stable if reinforced 30+ consecutive ticks (R9.4)
            if edge.reinforcement_count >= self._STABLE_THRESHOLD:
                edge.stable = True

    def detect_cycles(self, start_node: str, visited: set) -> list[list[str]]:
        """DFS-based cycle detection from start_node (R9.3).

        Halts propagation at cycle entry point and logs the cycle path.
        Uses a recursion stack to detect back edges indicating cycles.

        Args:
            start_node: The node to start DFS from.
            visited: A set of globally visited nodes (shared across calls).

        Returns:
            List of cycle paths (each a list of node ids forming the cycle).
        """
        cycles: list[list[str]] = []
        rec_stack: list[str] = []
        rec_set: set[str] = set()

        def _dfs(node_id: str) -> None:
            visited.add(node_id)
            rec_stack.append(node_id)
            rec_set.add(node_id)

            for edge in self.graph.get_edges_from(node_id):
                neighbor = edge.target
                if neighbor in rec_set:
                    # Cycle detected: extract the cycle path
                    cycle_start_idx = rec_stack.index(neighbor)
                    cycle_path = rec_stack[cycle_start_idx:] + [neighbor]
                    cycles.append(cycle_path)
                    # Halt at cycle entry point (R9.3)
                elif neighbor not in visited:
                    _dfs(neighbor)

            rec_stack.pop()
            rec_set.discard(node_id)

        if start_node in self.graph._nodes and start_node not in visited:
            _dfs(start_node)

        return cycles

    def _collect_affected_edges(self, modified_nodes: list[str]) -> set[str]:
        """BFS from modified nodes up to 2 hops, collecting affected edge ids.

        Firebreak (R9.2): halt propagation at 3 hops total from source.
        Since we only traverse 2 hops, edges at hops 0-1 (connecting nodes within 2 hops)
        are collected. The firebreak at 3 hops ensures we never go beyond.
        """
        affected_edge_ids: set[str] = set()

        for source_node in modified_nodes:
            if source_node not in self.graph._nodes:
                continue

            # BFS up to DECAY_HOPS (2 hops)
            queue: deque[tuple[str, int]] = deque([(source_node, 0)])
            visited: set[str] = {source_node}

            while queue:
                current_id, hops = queue.popleft()

                # Firebreak: do not traverse beyond DECAY_HOPS
                if hops >= self._DECAY_HOPS:
                    continue

                # Collect edges from current node
                for edge_id in self.graph._edges_from.get(current_id, set()):
                    edge = self.graph._edges.get(edge_id)
                    if edge is None:
                        continue

                    affected_edge_ids.add(edge_id)

                    target = edge.target
                    if target not in visited:
                        # Firebreak check: don't go beyond 3 hops total
                        if hops + 1 < self._FIREBREAK_HOPS:
                            visited.add(target)
                            queue.append((target, hops + 1))

        return affected_edge_ids

    def _apply_decay(self, affected_edge_ids: set[str]) -> int:
        """Apply 10% decay to affected edges, skipping stable ones (R9.1, R9.4).

        Returns count of edges that were actually decayed.
        """
        edges_decayed = 0

        for edge_id in affected_edge_ids:
            edge = self.graph._edges.get(edge_id)
            if edge is None:
                continue

            # Skip stable edges (R9.4)
            if edge.stable:
                continue

            old_confidence = edge.confidence
            # Reduce confidence by 10% of current value (multiply by 0.9)
            edge.confidence = clamp_confidence(edge.confidence * 0.9)
            edges_decayed += 1

            # Version increment if confidence changed by more than 0.1 (R9.5)
            if abs(edge.confidence - old_confidence) > 0.1:
                edge.version += 1

        return edges_decayed

    def _resolve_conflicts(self) -> int:
        """Resolve conflicts: when two edges of same type connect same nodes (R9.6, R9.7).

        Retain higher confidence edge, archive lower.
        If equal confidence, keep the one with more recent last_reinforced.

        Returns count of conflicts resolved.
        """
        conflicts_resolved = 0

        # Group edges by (source, target, edge_type)
        edge_groups: dict[tuple[str, str, str], list[Edge]] = {}
        for edge in list(self.graph._edges.values()):
            key = (edge.source, edge.target, edge.edge_type)
            if key not in edge_groups:
                edge_groups[key] = []
            edge_groups[key].append(edge)

        # For groups with more than one edge, resolve conflicts
        for key, edges in edge_groups.items():
            if len(edges) <= 1:
                continue

            # Sort: higher confidence first; if equal, more recent last_reinforced first
            edges.sort(
                key=lambda e: (e.confidence, e.last_reinforced),
                reverse=True,
            )

            # Keep the first (highest confidence / most recent), archive the rest
            for loser in edges[1:]:
                self._archived_edges.append(loser)
                self.graph.remove_edge(loser.id)
                conflicts_resolved += 1

        return conflicts_resolved
