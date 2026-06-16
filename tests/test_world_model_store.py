"""Tests for WorldModelStore ABC and JsonWorldModelStore.

Covers: atomic writes, graph sharding, snapshot operations,
corrupted file handling, and shard size warnings.

Requirements: R16.1, R16.2, R16.3, R16.4, R16.5, R16.6, R17.1-R17.5, R5.7, R5.8
"""

import json
import os

import pytest

from cli_kognisant.world_model_store import (
    JsonWorldModelStore,
    SHARD_SIZE_LIMIT,
    WorldModelStore,
)


@pytest.fixture
def store(tmp_path):
    """Create a JsonWorldModelStore with a temporary project root."""
    return JsonWorldModelStore(str(tmp_path))


@pytest.fixture
def populated_store(store):
    """Store with pre-populated graph, beliefs, and contracts."""
    nodes = [
        {
            "id": "mod_a.func_x",
            "node_type": "function",
            "file_path": "src/mod_a.py",
            "line_start": 10,
            "line_end": 20,
            "last_modified": "2025-01-01T00:00:00Z",
            "tags": [],
            "module": "mod_a",
        },
        {
            "id": "mod_a.func_y",
            "node_type": "function",
            "file_path": "src/mod_a.py",
            "line_start": 25,
            "line_end": 40,
            "last_modified": "2025-01-01T00:00:00Z",
            "tags": [],
            "module": "mod_a",
        },
        {
            "id": "mod_b.func_z",
            "node_type": "function",
            "file_path": "src/mod_b.py",
            "line_start": 5,
            "line_end": 15,
            "last_modified": "2025-01-01T00:00:00Z",
            "tags": [],
            "module": "mod_b",
        },
        {
            "id": "mod_c.func_w",
            "node_type": "function",
            "file_path": "src/mod_c.py",
            "line_start": 1,
            "line_end": 10,
            "last_modified": "2025-01-01T00:00:00Z",
            "tags": [],
            "module": "mod_c",
        },
    ]
    edges = [
        {
            "id": "edge-1",
            "source": "mod_a.func_x",
            "target": "mod_a.func_y",
            "edge_type": "calls",
            "confidence": 0.9,
            "provenance": "static",
        },
        {
            "id": "edge-2",
            "source": "mod_a.func_y",
            "target": "mod_b.func_z",
            "edge_type": "calls",
            "confidence": 0.8,
            "provenance": "dynamic",
        },
        {
            "id": "edge-3",
            "source": "mod_b.func_z",
            "target": "mod_c.func_w",
            "edge_type": "imports",
            "confidence": 1.0,
            "provenance": "static",
        },
    ]
    beliefs = [
        {
            "id": "belief-1",
            "statement": "func_x calls func_y",
            "node_id": "mod_a.func_x",
            "edge_id": "edge-1",
            "provenance": "static",
            "confidence": 0.9,
            "created_at": "2025-01-01T00:00:00Z",
            "last_reinforced": "2025-01-01T00:00:00Z",
            "falsification_count": 0,
            "archived": False,
            "archive_reason": None,
        },
        {
            "id": "belief-2",
            "statement": "func_z imports func_w",
            "node_id": "mod_b.func_z",
            "edge_id": "edge-3",
            "provenance": "static",
            "confidence": 1.0,
            "created_at": "2025-01-01T00:00:00Z",
            "last_reinforced": "2025-01-01T00:00:00Z",
            "falsification_count": 0,
            "archived": False,
            "archive_reason": None,
        },
    ]

    store.save_graph({"nodes": nodes, "edges": edges})
    store.save_beliefs(beliefs)
    store.save_contracts([])
    store.save_gaps([])
    return store


class TestWorldModelStoreABC:
    """Verify the ABC contract."""

    def test_cannot_instantiate_abc(self):
        """WorldModelStore is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            WorldModelStore()

    def test_subclass_must_implement_all_methods(self):
        """Incomplete subclass raises TypeError on instantiation."""

        class IncompleteStore(WorldModelStore):
            def load_graph(self):
                return {}

        with pytest.raises(TypeError):
            IncompleteStore()


class TestJsonWorldModelStoreDirectoryCreation:
    """Test directory structure creation on first access."""

    def test_creates_directory_structure(self, store, tmp_path):
        """First save creates the full directory tree."""
        store.save_graph({"nodes": [], "edges": []})

        base = tmp_path / ".kognisant" / "world_model"
        assert base.is_dir()
        assert (base / "graph").is_dir()
        assert (base / "graph" / "modules").is_dir()
        assert (base / "snapshots").is_dir()

    def test_idempotent_directory_creation(self, store, tmp_path):
        """Multiple operations don't fail on existing dirs."""
        store.save_graph({"nodes": [], "edges": []})
        store.save_graph({"nodes": [], "edges": []})
        # No error raised


class TestAtomicWrites:
    """Test atomic write behavior."""

    def test_no_tmp_files_left_after_save(self, store, tmp_path):
        """After save, no .tmp files remain."""
        store.save_beliefs([{"id": "b1", "node_id": "n1"}])

        base = tmp_path / ".kognisant" / "world_model"
        for root, dirs, files in os.walk(str(base)):
            for f in files:
                assert not f.endswith(".tmp"), f"Temp file left: {f}"

    def test_atomic_write_produces_valid_json(self, store, tmp_path):
        """Written files contain valid, parseable JSON."""
        data = [{"id": "c1", "source_node": "a", "target_node": "b"}]
        store.save_contracts(data)

        path = tmp_path / ".kognisant" / "world_model" / "contracts.json"
        with open(path) as f:
            loaded = json.load(f)
        assert loaded == data


class TestGraphSharding:
    """Test graph save/load with module-based sharding."""

    def test_round_trip_empty_graph(self, store):
        """Empty graph saves and loads correctly."""
        store.save_graph({"nodes": [], "edges": []})
        result = store.load_graph()
        assert result == {"nodes": [], "edges": []}

    def test_nodes_sharded_by_module(self, store, tmp_path):
        """Nodes are partitioned into separate module shard files."""
        nodes = [
            {"id": "a.f1", "module": "a", "node_type": "function"},
            {"id": "a.f2", "module": "a", "node_type": "function"},
            {"id": "b.f1", "module": "b", "node_type": "function"},
        ]
        store.save_graph({"nodes": nodes, "edges": []})

        modules_dir = tmp_path / ".kognisant" / "world_model" / "graph" / "modules"
        assert (modules_dir / "a.json").exists()
        assert (modules_dir / "b.json").exists()

        with open(modules_dir / "a.json") as f:
            shard_a = json.load(f)
        assert len(shard_a["nodes"]) == 2

    def test_cross_module_edges_separated(self, store, tmp_path):
        """Edges between different modules go to cross_module.json."""
        nodes = [
            {"id": "a.f1", "module": "a", "node_type": "function"},
            {"id": "b.f1", "module": "b", "node_type": "function"},
        ]
        edges = [
            {
                "id": "e1",
                "source": "a.f1",
                "target": "b.f1",
                "edge_type": "calls",
                "confidence": 0.9,
            }
        ]
        store.save_graph({"nodes": nodes, "edges": edges})

        cross_path = (
            tmp_path / ".kognisant" / "world_model" / "graph" / "cross_module.json"
        )
        with open(cross_path) as f:
            cross = json.load(f)
        assert len(cross) == 1
        assert cross[0]["id"] == "e1"

    def test_intra_module_edges_in_shard(self, store, tmp_path):
        """Edges within a module are stored in the module shard."""
        nodes = [
            {"id": "a.f1", "module": "a", "node_type": "function"},
            {"id": "a.f2", "module": "a", "node_type": "function"},
        ]
        edges = [
            {
                "id": "e1",
                "source": "a.f1",
                "target": "a.f2",
                "edge_type": "calls",
                "confidence": 0.9,
            }
        ]
        store.save_graph({"nodes": nodes, "edges": edges})

        shard_path = (
            tmp_path / ".kognisant" / "world_model" / "graph" / "modules" / "a.json"
        )
        with open(shard_path) as f:
            shard = json.load(f)
        assert len(shard["edges"]) == 1

    def test_index_json_written(self, store, tmp_path):
        """index.json maps module names to node ID lists."""
        nodes = [
            {"id": "a.f1", "module": "a", "node_type": "function"},
            {"id": "b.f1", "module": "b", "node_type": "function"},
        ]
        store.save_graph({"nodes": nodes, "edges": []})

        index_path = (
            tmp_path / ".kognisant" / "world_model" / "graph" / "index.json"
        )
        with open(index_path) as f:
            index = json.load(f)
        assert "a" in index
        assert "b" in index
        assert "a.f1" in index["a"]
        assert "b.f1" in index["b"]

    def test_graph_round_trip(self, store):
        """Graph data survives save/load cycle."""
        nodes = [
            {"id": "x.f1", "module": "x", "node_type": "function"},
            {"id": "y.f1", "module": "y", "node_type": "function"},
        ]
        edges = [
            {
                "id": "e1",
                "source": "x.f1",
                "target": "y.f1",
                "edge_type": "calls",
                "confidence": 0.7,
                "provenance": "static",
            }
        ]
        store.save_graph({"nodes": nodes, "edges": edges})
        result = store.load_graph()

        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["edges"][0]["id"] == "e1"

    def test_default_module_for_empty_module_field(self, store):
        """Nodes with empty module field go to _default shard."""
        nodes = [{"id": "orphan", "module": "", "node_type": "function"}]
        store.save_graph({"nodes": nodes, "edges": []})
        result = store.load_graph()
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "orphan"

    def test_stale_shard_removed(self, store, tmp_path):
        """Modules removed from graph have their shard files cleaned up."""
        nodes = [
            {"id": "a.f1", "module": "a", "node_type": "function"},
            {"id": "b.f1", "module": "b", "node_type": "function"},
        ]
        store.save_graph({"nodes": nodes, "edges": []})

        # Save again without module 'b'
        store.save_graph(
            {"nodes": [{"id": "a.f1", "module": "a", "node_type": "function"}], "edges": []}
        )

        modules_dir = tmp_path / ".kognisant" / "world_model" / "graph" / "modules"
        assert (modules_dir / "a.json").exists()
        assert not (modules_dir / "b.json").exists()


class TestCorruptedShardHandling:
    """Test handling of corrupted shard files."""

    def test_corrupted_shard_skipped_nodes_get_zero_confidence(self, store, tmp_path):
        """Corrupted shard: skip it, mark affected edges with confidence 0.0."""
        # Set up a valid graph first
        nodes = [
            {"id": "a.f1", "module": "a", "node_type": "function"},
            {"id": "b.f1", "module": "b", "node_type": "function"},
        ]
        edges = [
            {
                "id": "e1",
                "source": "a.f1",
                "target": "b.f1",
                "edge_type": "calls",
                "confidence": 0.9,
            }
        ]
        store.save_graph({"nodes": nodes, "edges": edges})

        # Corrupt module a's shard
        shard_path = (
            tmp_path / ".kognisant" / "world_model" / "graph" / "modules" / "a.json"
        )
        with open(shard_path, "w") as f:
            f.write("THIS IS NOT VALID JSON {{{")

        # Load should skip corrupted shard, mark edges confidence 0.0
        result = store.load_graph()
        # Module b's nodes should still load
        assert any(n["id"] == "b.f1" for n in result["nodes"])
        # Cross-module edge referencing corrupted node should have confidence 0.0
        for edge in result["edges"]:
            if edge.get("source") == "a.f1" or edge.get("target") == "a.f1":
                assert edge["confidence"] == 0.0

    def test_missing_index_returns_empty_graph(self, store):
        """No index.json → empty graph returned."""
        result = store.load_graph()
        assert result == {"nodes": [], "edges": []}


class TestShardSizeWarning:
    """Test shard size warning behavior."""

    def test_warning_logged_for_large_shard(self, store, tmp_path, capfd):
        """Shards exceeding 500KB trigger a warning to stderr."""
        # Create a node set that will produce a large shard
        big_nodes = []
        for i in range(5000):
            big_nodes.append(
                {
                    "id": f"big.func_{i}",
                    "module": "big",
                    "node_type": "function",
                    "file_path": f"src/big_{i}.py",
                    "line_start": 1,
                    "line_end": 100,
                    "last_modified": "2025-01-01T00:00:00Z",
                    "tags": ["some", "tags", "for", "padding"],
                }
            )
        store.save_graph({"nodes": big_nodes, "edges": []})

        captured = capfd.readouterr()
        assert "exceeds 500KB" in captured.err
        assert "big" in captured.err

    def test_warning_only_once_per_session(self, store, tmp_path, capfd):
        """Shard warning is emitted only once per session per module."""
        big_nodes = []
        for i in range(5000):
            big_nodes.append(
                {
                    "id": f"big.func_{i}",
                    "module": "big",
                    "node_type": "function",
                    "file_path": f"src/big_{i}.py",
                    "line_start": 1,
                    "line_end": 100,
                    "last_modified": "2025-01-01T00:00:00Z",
                    "tags": ["some", "tags", "for", "padding"],
                }
            )
        store.save_graph({"nodes": big_nodes, "edges": []})
        # Save again - should not warn again
        store.save_graph({"nodes": big_nodes, "edges": []})

        captured = capfd.readouterr()
        assert captured.err.count("exceeds 500KB") == 1


class TestBeliefsContractsGaps:
    """Test beliefs, contracts, and gaps load/save."""

    def test_beliefs_round_trip(self, store):
        """Beliefs survive save/load cycle."""
        beliefs = [
            {"id": "b1", "node_id": "n1", "confidence": 0.8},
            {"id": "b2", "node_id": "n2", "confidence": 0.5},
        ]
        store.save_beliefs(beliefs)
        assert store.load_beliefs() == beliefs

    def test_contracts_round_trip(self, store):
        """Contracts survive save/load cycle."""
        contracts = [
            {"id": "c1", "source_node": "a", "target_node": "b", "confidence": 0.7}
        ]
        store.save_contracts(contracts)
        assert store.load_contracts() == contracts

    def test_gaps_round_trip(self, store):
        """Gaps survive save/load cycle."""
        gaps = [{"id": "g1", "gap_type": "untested_branch", "node_id": "n1"}]
        store.save_gaps(gaps)
        assert store.load_gaps() == gaps

    def test_load_missing_beliefs_returns_empty(self, store):
        """Loading beliefs when file doesn't exist returns empty list."""
        assert store.load_beliefs() == []

    def test_load_missing_contracts_returns_empty(self, store):
        """Loading contracts when file doesn't exist returns empty list."""
        assert store.load_contracts() == []

    def test_load_missing_gaps_returns_empty(self, store):
        """Loading gaps when file doesn't exist returns empty list."""
        assert store.load_gaps() == []


class TestSnapshotCreation:
    """Test create_snapshot behavior."""

    def test_create_snapshot_returns_path(self, populated_store):
        """create_snapshot returns the path to the snapshot directory."""
        path = populated_store.create_snapshot(["mod_a.func_x"])
        assert os.path.isdir(path)

    def test_snapshot_contains_manifest(self, populated_store):
        """Snapshot directory contains manifest.json."""
        path = populated_store.create_snapshot(["mod_a.func_x"])
        manifest_path = os.path.join(path, "manifest.json")
        assert os.path.isfile(manifest_path)

        with open(manifest_path) as f:
            manifest = json.load(f)
        assert "mod_a.func_x" in manifest["node_ids"]
        assert manifest["node_count"] > 0

    def test_snapshot_contains_graph_fragment(self, populated_store):
        """Snapshot directory contains graph_fragment.json with nodes+edges."""
        path = populated_store.create_snapshot(["mod_a.func_x"])
        frag_path = os.path.join(path, "graph_fragment.json")
        assert os.path.isfile(frag_path)

        with open(frag_path) as f:
            fragment = json.load(f)
        assert "nodes" in fragment
        assert "edges" in fragment
        # func_x is included
        node_ids = [n["id"] for n in fragment["nodes"]]
        assert "mod_a.func_x" in node_ids

    def test_snapshot_2_hop_neighbors(self, populated_store):
        """Snapshot includes nodes within 2 hops."""
        # func_x -> func_y (1 hop) -> func_z (2 hops)
        path = populated_store.create_snapshot(["mod_a.func_x"])
        frag_path = os.path.join(path, "graph_fragment.json")

        with open(frag_path) as f:
            fragment = json.load(f)
        node_ids = {n["id"] for n in fragment["nodes"]}
        assert "mod_a.func_x" in node_ids
        assert "mod_a.func_y" in node_ids  # 1 hop
        assert "mod_b.func_z" in node_ids  # 2 hops

    def test_snapshot_beliefs_fragment(self, populated_store):
        """Snapshot includes beliefs associated with snapshot nodes."""
        path = populated_store.create_snapshot(["mod_a.func_x"])
        beliefs_path = os.path.join(path, "beliefs_fragment.json")
        assert os.path.isfile(beliefs_path)

        with open(beliefs_path) as f:
            beliefs = json.load(f)
        # belief-1 is for mod_a.func_x
        belief_ids = [b["id"] for b in beliefs]
        assert "belief-1" in belief_ids


class TestSnapshotRestore:
    """Test restore_snapshot behavior."""

    def test_restore_replaces_graph_fragment(self, populated_store):
        """Restoring a snapshot replaces affected nodes/edges."""
        # Create snapshot
        path = populated_store.create_snapshot(["mod_a.func_x"])

        # Modify the graph (simulate a bad change)
        graph = populated_store.load_graph()
        for node in graph["nodes"]:
            if node["id"] == "mod_a.func_x":
                node["tags"] = ["CORRUPTED"]
        populated_store.save_graph(graph)

        # Restore
        populated_store.restore_snapshot(path)

        # Verify restoration
        restored_graph = populated_store.load_graph()
        for node in restored_graph["nodes"]:
            if node["id"] == "mod_a.func_x":
                assert "CORRUPTED" not in node.get("tags", [])

    def test_restore_replaces_beliefs(self, populated_store):
        """Restoring a snapshot replaces beliefs for affected nodes."""
        path = populated_store.create_snapshot(["mod_a.func_x"])

        # Modify beliefs
        beliefs = populated_store.load_beliefs()
        for b in beliefs:
            if b["id"] == "belief-1":
                b["confidence"] = 0.01
        populated_store.save_beliefs(beliefs)

        # Restore
        populated_store.restore_snapshot(path)

        # Verify
        restored_beliefs = populated_store.load_beliefs()
        for b in restored_beliefs:
            if b["id"] == "belief-1":
                assert b["confidence"] == 0.9


class TestSnapshotDelete:
    """Test delete_snapshot behavior."""

    def test_delete_removes_directory(self, populated_store):
        """delete_snapshot removes the snapshot directory."""
        path = populated_store.create_snapshot(["mod_a.func_x"])
        assert os.path.isdir(path)

        populated_store.delete_snapshot(path)
        assert not os.path.exists(path)

    def test_delete_nonexistent_path_no_error(self, populated_store):
        """Deleting a non-existent path doesn't raise."""
        populated_store.delete_snapshot("/tmp/nonexistent_snapshot_path_xyz")
        # No error raised


class TestCorruptedSnapshotRestore:
    """Test restore behavior with corrupted snapshot files."""

    def test_missing_manifest_logs_error(self, populated_store, tmp_path, caplog):
        """Missing manifest.json during restore logs error."""
        import logging

        # Create a fake empty snapshot dir
        fake_snapshot = str(tmp_path / "fake_snapshot")
        os.makedirs(fake_snapshot)

        with caplog.at_level(logging.ERROR):
            populated_store.restore_snapshot(fake_snapshot)

        assert "manifest" in caplog.text.lower() or "missing" in caplog.text.lower()

    def test_corrupted_graph_fragment_marks_nodes_zero(
        self, populated_store, tmp_path, caplog
    ):
        """Corrupted graph_fragment.json marks affected nodes confidence 0.0."""
        import logging

        # Create snapshot then corrupt graph_fragment
        path = populated_store.create_snapshot(["mod_a.func_x"])
        graph_frag = os.path.join(path, "graph_fragment.json")
        with open(graph_frag, "w") as f:
            f.write("NOT VALID JSON")

        with caplog.at_level(logging.ERROR):
            populated_store.restore_snapshot(path)

        assert "corrupted" in caplog.text.lower() or "graph_fragment" in caplog.text.lower()
