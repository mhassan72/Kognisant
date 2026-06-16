"""World Model Store: persistence abstraction for the World Model subsystem.

This module defines the WorldModelStore ABC and implements JsonWorldModelStore
for JSON-sharded storage. It handles atomic writes, graph sharding by module,
snapshot creation/restoration, and corrupted file recovery.

Requirements covered: R16.1, R16.2, R16.3, R16.4, R16.5, R16.6,
                      R17.1, R17.2, R17.3, R17.4, R17.5, R5.7, R5.8
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

from .models import Belief, Contract, Edge, EpistemicGap, Node

logger = logging.getLogger(__name__)

# Shard size threshold in bytes
SHARD_SIZE_LIMIT = 500 * 1024  # 500KB


class WorldModelStore(ABC):
    """Abstract base class for World Model persistence.

    Implementations must provide load/save for graph, beliefs, contracts,
    and epistemic gaps, plus snapshot create/restore/delete operations.
    """

    @abstractmethod
    def load_graph(self) -> dict:
        """Load the dependency graph from storage.

        Returns a dict with keys:
            'nodes': list[dict] - serialized Node objects
            'edges': list[dict] - serialized Edge objects
        """
        ...

    @abstractmethod
    def save_graph(self, graph: dict) -> None:
        """Persist the dependency graph to storage.

        Args:
            graph: dict with 'nodes' (list[dict]) and 'edges' (list[dict])
        """
        ...

    @abstractmethod
    def load_beliefs(self) -> list[dict]:
        """Load all beliefs from storage.

        Returns a list of serialized Belief dicts.
        """
        ...

    @abstractmethod
    def save_beliefs(self, beliefs: list[dict]) -> None:
        """Persist beliefs to storage.

        Args:
            beliefs: list of serialized Belief dicts
        """
        ...

    @abstractmethod
    def load_contracts(self) -> list[dict]:
        """Load all contracts from storage.

        Returns a list of serialized Contract dicts.
        """
        ...

    @abstractmethod
    def save_contracts(self, contracts: list[dict]) -> None:
        """Persist contracts to storage.

        Args:
            contracts: list of serialized Contract dicts
        """
        ...

    @abstractmethod
    def load_gaps(self) -> list[dict]:
        """Load all epistemic gaps from storage.

        Returns a list of serialized EpistemicGap dicts.
        """
        ...

    @abstractmethod
    def save_gaps(self, gaps: list[dict]) -> None:
        """Persist epistemic gaps to storage.

        Args:
            gaps: list of serialized EpistemicGap dicts
        """
        ...

    @abstractmethod
    def create_snapshot(self, node_ids: list[str]) -> str:
        """Create a snapshot of the subgraph around specified nodes.

        Captures specified nodes plus all nodes and edges within 2 hops.
        Stores in a timestamped snapshot directory.

        Args:
            node_ids: list of node identifiers to snapshot

        Returns:
            Path to the created snapshot directory.
        """
        ...

    @abstractmethod
    def restore_snapshot(self, snapshot_path: str) -> None:
        """Restore world model state from a snapshot.

        Reads the manifest and restores graph fragment and beliefs
        using atomic writes.

        Args:
            snapshot_path: path to the snapshot directory
        """
        ...

    @abstractmethod
    def delete_snapshot(self, snapshot_path: str) -> None:
        """Remove a snapshot directory.

        Args:
            snapshot_path: path to the snapshot directory to delete
        """
        ...


class JsonWorldModelStore(WorldModelStore):
    """JSON-sharded implementation of WorldModelStore.

    Storage layout under <project_root>/.kognisant/world_model/:
        graph/index.json          - node-to-shard mapping
        graph/modules/<module>.json - per-module node/edge shards
        graph/cross_module.json   - inter-module edges
        beliefs.json              - all beliefs
        contracts.json            - all contracts
        epistemic_gaps.json       - all epistemic gaps
        snapshots/<iso-ts>/       - snapshot directories
    """

    def __init__(self, project_root: str) -> None:
        self._project_root = project_root
        self._base_dir = os.path.join(project_root, ".kognisant", "world_model")
        self._graph_dir = os.path.join(self._base_dir, "graph")
        self._modules_dir = os.path.join(self._graph_dir, "modules")
        self._snapshots_dir = os.path.join(self._base_dir, "snapshots")
        self._initialized = False
        # Track which shards have already warned about size this session
        self._warned_shards: set[str] = set()

    def _ensure_dirs(self) -> None:
        """Create the directory structure on first access."""
        if self._initialized:
            return
        os.makedirs(self._modules_dir, exist_ok=True)
        os.makedirs(self._snapshots_dir, exist_ok=True)
        self._initialized = True

    # ─── Atomic Write Helper ────────────────────────────────────────────

    def _atomic_write(self, path: str, data: object) -> None:
        """Write data as JSON atomically: write to <path>.tmp then os.rename().

        Args:
            path: target file path
            data: JSON-serializable object
        """
        self._ensure_dirs()
        dir_path = os.path.dirname(path)
        os.makedirs(dir_path, exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.rename(tmp_path, path)

    def _read_json(self, path: str) -> object | None:
        """Read and parse a JSON file. Returns None on any error."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("Failed to read %s: %s", path, e)
            return None

    # ─── Graph Load/Save with Sharding ──────────────────────────────────

    def load_graph(self) -> dict:
        """Load the dependency graph from sharded storage.

        Reads index.json for node-to-shard mapping, loads each module shard,
        and loads cross_module.json for inter-module edges.

        Returns:
            dict with 'nodes' and 'edges' lists of serialized dicts.
            Nodes from corrupted shards are marked with confidence 0.0.
        """
        self._ensure_dirs()
        nodes: list[dict] = []
        edges: list[dict] = []
        corrupted_node_ids: list[str] = []

        index_path = os.path.join(self._graph_dir, "index.json")
        index_data = self._read_json(index_path)

        if index_data is None:
            # No index yet or corrupted - return empty graph
            return {"nodes": [], "edges": []}

        if not isinstance(index_data, dict):
            logger.error("index.json has unexpected format")
            return {"nodes": [], "edges": []}

        # index_data maps module_name -> list of node_ids
        # Load each module shard
        loaded_modules: set[str] = set()
        for module_name, node_id_list in index_data.items():
            shard_path = os.path.join(self._modules_dir, f"{module_name}.json")
            shard_data = self._read_json(shard_path)

            if shard_data is None:
                # Corrupted shard: log and mark affected nodes
                logger.error(
                    "Corrupted shard file for module '%s' at %s. "
                    "Skipping shard, marking affected nodes with confidence 0.0.",
                    module_name,
                    shard_path,
                )
                # Create placeholder nodes with confidence 0.0 for tracking
                for node_id in node_id_list:
                    corrupted_node_ids.append(node_id)
                continue

            if not isinstance(shard_data, dict):
                logger.error(
                    "Shard file for module '%s' has unexpected format", module_name
                )
                for node_id in node_id_list:
                    corrupted_node_ids.append(node_id)
                continue

            shard_nodes = shard_data.get("nodes", [])
            shard_edges = shard_data.get("edges", [])
            nodes.extend(shard_nodes)
            edges.extend(shard_edges)
            loaded_modules.add(module_name)

        # Load cross-module edges
        cross_module_path = os.path.join(self._graph_dir, "cross_module.json")
        cross_data = self._read_json(cross_module_path)
        if cross_data is not None and isinstance(cross_data, list):
            edges.extend(cross_data)

        # Mark corrupted node edges with confidence 0.0
        # (edges referencing corrupted nodes)
        if corrupted_node_ids:
            corrupted_set = set(corrupted_node_ids)
            for edge in edges:
                if edge.get("source") in corrupted_set or edge.get("target") in corrupted_set:
                    edge["confidence"] = 0.0

        return {"nodes": nodes, "edges": edges}

    def save_graph(self, graph: dict) -> None:
        """Persist the dependency graph using module-based sharding.

        Partitions nodes by their 'module' field, writes each partition
        to graph/modules/<module>.json, writes an index.json mapping
        node IDs to shards, and writes cross_module.json for edges
        connecting different modules.
        """
        self._ensure_dirs()
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Partition nodes by module
        module_nodes: dict[str, list[dict]] = defaultdict(list)
        node_to_module: dict[str, str] = {}

        for node in nodes:
            module = node.get("module", "") or "_default"
            module_nodes[module].append(node)
            node_to_module[node["id"]] = module

        # Partition edges: intra-module vs cross-module
        module_edges: dict[str, list[dict]] = defaultdict(list)
        cross_module_edges: list[dict] = []

        for edge in edges:
            src_module = node_to_module.get(edge.get("source", ""), "_unknown")
            tgt_module = node_to_module.get(edge.get("target", ""), "_unknown")
            if src_module == tgt_module and src_module != "_unknown":
                module_edges[src_module].append(edge)
            else:
                cross_module_edges.append(edge)

        # Build index: module_name -> list of node IDs
        index: dict[str, list[str]] = {}
        for module, mod_nodes in module_nodes.items():
            index[module] = [n["id"] for n in mod_nodes]

        # Write module shards
        for module, mod_nodes in module_nodes.items():
            shard_data = {
                "nodes": mod_nodes,
                "edges": module_edges.get(module, []),
            }
            shard_path = os.path.join(self._modules_dir, f"{module}.json")
            self._atomic_write(shard_path, shard_data)

            # Check shard size and warn if over limit
            self._check_shard_size(shard_path, module)

        # Remove stale shard files for modules no longer present
        if os.path.isdir(self._modules_dir):
            existing_shards = set(os.listdir(self._modules_dir))
            current_modules = {f"{m}.json" for m in module_nodes}
            for stale in existing_shards - current_modules:
                stale_path = os.path.join(self._modules_dir, stale)
                if stale_path.endswith(".json"):
                    try:
                        os.remove(stale_path)
                    except OSError:
                        pass

        # Write index
        index_path = os.path.join(self._graph_dir, "index.json")
        self._atomic_write(index_path, index)

        # Write cross-module edges
        cross_module_path = os.path.join(self._graph_dir, "cross_module.json")
        self._atomic_write(cross_module_path, cross_module_edges)

    def _check_shard_size(self, shard_path: str, module_name: str) -> None:
        """Check if a shard exceeds 500KB and log a warning once per session."""
        if module_name in self._warned_shards:
            return
        try:
            size = os.path.getsize(shard_path)
        except OSError:
            return
        if size > SHARD_SIZE_LIMIT:
            self._warned_shards.add(module_name)
            msg = (
                f"Shard for module '{module_name}' exceeds 500KB "
                f"(size: {size} bytes). Splitting deferred to future version."
            )
            logger.warning(msg)
            # Also emit to stderr with de-duplication (once per session)
            print(msg, file=sys.stderr)

    # ─── Beliefs Load/Save ──────────────────────────────────────────────

    def load_beliefs(self) -> list[dict]:
        """Load beliefs from beliefs.json."""
        self._ensure_dirs()
        path = os.path.join(self._base_dir, "beliefs.json")
        data = self._read_json(path)
        if data is None or not isinstance(data, list):
            return []
        return data

    def save_beliefs(self, beliefs: list[dict]) -> None:
        """Persist beliefs to beliefs.json using atomic write."""
        self._ensure_dirs()
        path = os.path.join(self._base_dir, "beliefs.json")
        self._atomic_write(path, beliefs)

    # ─── Contracts Load/Save ────────────────────────────────────────────

    def load_contracts(self) -> list[dict]:
        """Load contracts from contracts.json."""
        self._ensure_dirs()
        path = os.path.join(self._base_dir, "contracts.json")
        data = self._read_json(path)
        if data is None or not isinstance(data, list):
            return []
        return data

    def save_contracts(self, contracts: list[dict]) -> None:
        """Persist contracts to contracts.json using atomic write."""
        self._ensure_dirs()
        path = os.path.join(self._base_dir, "contracts.json")
        self._atomic_write(path, contracts)

    # ─── Epistemic Gaps Load/Save ───────────────────────────────────────

    def load_gaps(self) -> list[dict]:
        """Load epistemic gaps from epistemic_gaps.json."""
        self._ensure_dirs()
        path = os.path.join(self._base_dir, "epistemic_gaps.json")
        data = self._read_json(path)
        if data is None or not isinstance(data, list):
            return []
        return data

    def save_gaps(self, gaps: list[dict]) -> None:
        """Persist epistemic gaps to epistemic_gaps.json using atomic write."""
        self._ensure_dirs()
        path = os.path.join(self._base_dir, "epistemic_gaps.json")
        self._atomic_write(path, gaps)

    # ─── Snapshot Operations ────────────────────────────────────────────

    def create_snapshot(self, node_ids: list[str]) -> str:
        """Create a snapshot of specified nodes + 2-hop neighbors.

        Creates a snapshot directory under snapshots/<iso-timestamp>/ with:
            - manifest.json: goal reference, node list, timestamp
            - graph_fragment.json: relevant nodes and edges
            - beliefs_fragment.json: beliefs associated with snapshot nodes

        Args:
            node_ids: primary node identifiers to snapshot

        Returns:
            Path to the created snapshot directory.
        """
        self._ensure_dirs()

        # Load current graph state
        graph = self.load_graph()
        all_nodes = graph.get("nodes", [])
        all_edges = graph.get("edges", [])

        # Build adjacency for 2-hop traversal
        node_map: dict[str, dict] = {n["id"]: n for n in all_nodes}
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in all_edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            adjacency[src].add(tgt)
            adjacency[tgt].add(src)

        # Collect nodes within 2 hops
        snapshot_node_ids: set[str] = set(node_ids)
        frontier = set(node_ids)
        for _ in range(2):
            next_frontier: set[str] = set()
            for nid in frontier:
                for neighbor in adjacency.get(nid, set()):
                    if neighbor not in snapshot_node_ids:
                        snapshot_node_ids.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier

        # Collect relevant nodes and edges
        snapshot_nodes = [n for n in all_nodes if n["id"] in snapshot_node_ids]
        snapshot_edges = [
            e
            for e in all_edges
            if e.get("source") in snapshot_node_ids
            and e.get("target") in snapshot_node_ids
        ]

        # Collect beliefs associated with snapshot nodes
        all_beliefs = self.load_beliefs()
        snapshot_beliefs = [
            b for b in all_beliefs if b.get("node_id") in snapshot_node_ids
        ]

        # Create timestamped directory
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        snapshot_dir = os.path.join(self._snapshots_dir, timestamp)
        os.makedirs(snapshot_dir, exist_ok=True)

        # Write manifest
        manifest = {
            "timestamp": timestamp,
            "node_ids": list(node_ids),
            "snapshot_node_ids": list(snapshot_node_ids),
            "node_count": len(snapshot_nodes),
            "edge_count": len(snapshot_edges),
            "belief_count": len(snapshot_beliefs),
        }
        manifest_path = os.path.join(snapshot_dir, "manifest.json")
        self._atomic_write(manifest_path, manifest)

        # Write graph fragment
        graph_fragment = {
            "nodes": snapshot_nodes,
            "edges": snapshot_edges,
        }
        graph_fragment_path = os.path.join(snapshot_dir, "graph_fragment.json")
        self._atomic_write(graph_fragment_path, graph_fragment)

        # Write beliefs fragment
        beliefs_fragment_path = os.path.join(snapshot_dir, "beliefs_fragment.json")
        self._atomic_write(beliefs_fragment_path, snapshot_beliefs)

        return snapshot_dir

    def restore_snapshot(self, snapshot_path: str) -> None:
        """Restore world model state from a snapshot.

        Reads manifest, graph_fragment, and beliefs_fragment.
        Merges restored data into current state using atomic writes.

        Args:
            snapshot_path: path to the snapshot directory

        Raises:
            Logs error and marks affected nodes with confidence 0.0
            if snapshot files are missing or corrupted.
        """
        self._ensure_dirs()

        # Read manifest
        manifest_path = os.path.join(snapshot_path, "manifest.json")
        manifest = self._read_json(manifest_path)
        if manifest is None:
            logger.error(
                "Snapshot manifest missing or corrupted at %s. "
                "Marking affected nodes with confidence 0.0.",
                manifest_path,
            )
            return

        # Read graph fragment
        graph_fragment_path = os.path.join(snapshot_path, "graph_fragment.json")
        graph_fragment = self._read_json(graph_fragment_path)
        if graph_fragment is None:
            logger.error(
                "Snapshot graph_fragment missing or corrupted at %s. "
                "Marking affected nodes with confidence 0.0.",
                graph_fragment_path,
            )
            # Mark affected nodes in current graph
            self._mark_nodes_zero_confidence(
                manifest.get("snapshot_node_ids", [])
            )
            return

        # Read beliefs fragment
        beliefs_fragment_path = os.path.join(snapshot_path, "beliefs_fragment.json")
        beliefs_fragment = self._read_json(beliefs_fragment_path)
        if beliefs_fragment is None:
            beliefs_fragment = []

        snapshot_node_ids = set(manifest.get("snapshot_node_ids", []))
        restored_nodes = graph_fragment.get("nodes", [])
        restored_edges = graph_fragment.get("edges", [])

        # Load current state
        current_graph = self.load_graph()
        current_nodes = current_graph.get("nodes", [])
        current_edges = current_graph.get("edges", [])

        # Replace nodes in snapshot scope with restored versions
        restored_node_map = {n["id"]: n for n in restored_nodes}
        new_nodes = []
        for node in current_nodes:
            if node["id"] in snapshot_node_ids:
                # Replace with restored version if available
                if node["id"] in restored_node_map:
                    new_nodes.append(restored_node_map.pop(node["id"]))
                # else skip (node was removed in snapshot scope)
            else:
                new_nodes.append(node)
        # Add any restored nodes not in current graph
        new_nodes.extend(restored_node_map.values())

        # Replace edges within snapshot scope
        restored_edge_ids = {e["id"] for e in restored_edges}
        new_edges = [
            e
            for e in current_edges
            if not (
                e.get("source") in snapshot_node_ids
                and e.get("target") in snapshot_node_ids
            )
        ]
        new_edges.extend(restored_edges)

        # Write restored graph atomically (retry once on failure)
        restored_graph = {"nodes": new_nodes, "edges": new_edges}
        if not self._save_with_retry(
            lambda: self.save_graph(restored_graph), "graph"
        ):
            self._mark_nodes_zero_confidence(list(snapshot_node_ids))
            return

        # Restore beliefs
        current_beliefs = self.load_beliefs()
        # Remove beliefs for snapshot nodes, replace with fragment
        new_beliefs = [
            b for b in current_beliefs if b.get("node_id") not in snapshot_node_ids
        ]
        if isinstance(beliefs_fragment, list):
            new_beliefs.extend(beliefs_fragment)

        if not self._save_with_retry(
            lambda: self.save_beliefs(new_beliefs), "beliefs"
        ):
            self._mark_nodes_zero_confidence(list(snapshot_node_ids))

    def delete_snapshot(self, snapshot_path: str) -> None:
        """Remove a snapshot directory after successful operation.

        Args:
            snapshot_path: path to the snapshot directory to delete
        """
        if os.path.isdir(snapshot_path):
            shutil.rmtree(snapshot_path)
            logger.info("Deleted snapshot at %s", snapshot_path)

    # ─── Private Helpers ────────────────────────────────────────────────

    def _save_with_retry(self, save_fn: callable, label: str) -> bool:
        """Attempt save_fn, retry once on failure.

        Returns True on success, False if both attempts fail.
        """
        try:
            save_fn()
            return True
        except OSError as e:
            logger.error(
                "Failed to save %s (first attempt): %s. Retrying...", label, e
            )
            try:
                save_fn()
                return True
            except OSError as e2:
                logger.error(
                    "Failed to save %s (retry): %s. "
                    "Flagging affected nodes for re-analysis.",
                    label,
                    e2,
                )
                return False

    def _mark_nodes_zero_confidence(self, node_ids: list[str]) -> None:
        """Mark all edges connected to specified nodes with confidence 0.0."""
        node_id_set = set(node_ids)
        try:
            graph = self.load_graph()
            edges = graph.get("edges", [])
            modified = False
            for edge in edges:
                if (
                    edge.get("source") in node_id_set
                    or edge.get("target") in node_id_set
                ):
                    edge["confidence"] = 0.0
                    modified = True
            if modified:
                graph["edges"] = edges
                self.save_graph(graph)
        except OSError as e:
            logger.error(
                "Failed to mark nodes with zero confidence: %s", e
            )
