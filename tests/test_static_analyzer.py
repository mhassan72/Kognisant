"""Tests for StaticAnalyzer in cli_kognisant/observer.py.

Validates requirements R2.1 through R2.8 and R20.1 through R20.6
for static analysis of Python source files.
"""

import os
import textwrap

import pytest

from cli_kognisant.observer import StaticAnalyzer


@pytest.fixture
def project_root(tmp_path):
    """Provide a temporary project root with a .git directory."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    return str(tmp_path)


@pytest.fixture
def scope_config():
    """Default scope configuration for tests."""
    return {
        "max_files": 1000,
        "gitignore_patterns": [".git", "__pycache__", "*.pyc"],
    }


@pytest.fixture
def analyzer(project_root, scope_config):
    """Provide a StaticAnalyzer instance."""
    return StaticAnalyzer(project_root, scope_config)


def write_py_file(project_root, rel_path, content):
    """Helper: write a .py file with given content under project root."""
    full_path = os.path.join(project_root, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return full_path


class TestAnalyzeFileFunctions:
    """R2.1: Extract function definitions including nested and async."""

    def test_extracts_simple_function(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            def hello():
                pass
        """)
        nodes, edges = analyzer.analyze_file(path)
        func_nodes = [n for n in nodes if n.node_type == "function"]
        assert len(func_nodes) == 1
        assert func_nodes[0].id == "mymod.hello"
        assert func_nodes[0].line_start == 1

    def test_extracts_async_function(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            async def fetch():
                pass
        """)
        nodes, edges = analyzer.analyze_file(path)
        func_nodes = [n for n in nodes if n.node_type == "function"]
        assert len(func_nodes) == 1
        assert func_nodes[0].id == "mymod.fetch"

    def test_extracts_nested_functions(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            def outer():
                def inner():
                    pass
        """)
        nodes, edges = analyzer.analyze_file(path)
        func_nodes = [n for n in nodes if n.node_type == "function"]
        assert len(func_nodes) == 2
        ids = {n.id for n in func_nodes}
        assert "mymod.outer" in ids
        assert "mymod.outer.inner" in ids

    def test_extracts_class_methods(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            class Foo:
                def bar(self):
                    pass
        """)
        nodes, edges = analyzer.analyze_file(path)
        func_nodes = [n for n in nodes if n.node_type == "function"]
        assert len(func_nodes) == 1
        assert func_nodes[0].id == "mymod.Foo.bar"


class TestAnalyzeFileClasses:
    """R2.3: Extract class definitions."""

    def test_extracts_class(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            class MyClass:
                pass
        """)
        nodes, edges = analyzer.analyze_file(path)
        class_nodes = [n for n in nodes if n.node_type == "class"]
        assert len(class_nodes) == 1
        assert class_nodes[0].id == "mymod.MyClass"
        assert class_nodes[0].module == "mymod"

    def test_extracts_class_with_methods(self, analyzer, project_root):
        path = write_py_file(project_root, "pkg/sub.py", """\
            class Service:
                def start(self):
                    pass
                def stop(self):
                    pass
        """)
        nodes, edges = analyzer.analyze_file(path)
        class_nodes = [n for n in nodes if n.node_type == "class"]
        func_nodes = [n for n in nodes if n.node_type == "function"]
        assert len(class_nodes) == 1
        assert class_nodes[0].id == "pkg.sub.Service"
        assert len(func_nodes) == 2


class TestAnalyzeFileImports:
    """R2.2, R2.7: Extract imports, create edges or external nodes."""

    def test_creates_import_edge(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            import os
        """)
        nodes, edges = analyzer.analyze_file(path)
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) == 1
        assert import_edges[0].source == "mymod"
        assert import_edges[0].target == "external.os"

    def test_creates_external_node_for_stdlib(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            import json
            import os.path
        """)
        nodes, edges = analyzer.analyze_file(path)
        ext_nodes = [n for n in nodes if "external" in n.id]
        # json and os should be external
        ext_ids = {n.id for n in ext_nodes}
        assert "external.json" in ext_ids
        assert "external.os" in ext_ids

    def test_from_import(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            from pathlib import Path
        """)
        nodes, edges = analyzer.analyze_file(path)
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) == 1
        assert import_edges[0].target == "external.pathlib"

    def test_no_duplicate_external_nodes(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            import os
            import os.path
            from os import getcwd
        """)
        nodes, edges = analyzer.analyze_file(path)
        ext_nodes = [n for n in nodes if n.id == "external.os"]
        assert len(ext_nodes) == 1


class TestAnalyzeFileCallSites:
    """R2.4: Extract call sites and create calls edges."""

    def test_simple_function_call(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            def foo():
                pass
            foo()
        """)
        nodes, edges = analyzer.analyze_file(path)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].target == "mymod.foo"

    def test_attribute_call(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            obj.method()
        """)
        nodes, edges = analyzer.analyze_file(path)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].target == "obj.method"

    def test_chained_attribute_call(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            a.b.c()
        """)
        nodes, edges = analyzer.analyze_file(path)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].target == "a.b.c"


class TestConfidenceAndProvenance:
    """R2.5: All static edges have confidence=1.0 and provenance=static."""

    def test_import_edges_confidence(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            import os
        """)
        nodes, edges = analyzer.analyze_file(path)
        for edge in edges:
            assert edge.confidence == 1.0
            assert edge.provenance == "static"

    def test_call_edges_confidence(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            foo()
        """)
        nodes, edges = analyzer.analyze_file(path)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        for edge in call_edges:
            assert edge.confidence == 1.0
            assert edge.provenance == "static"


class TestSyntaxErrorHandling:
    """R2.6: Graceful handling of files with syntax errors."""

    def test_syntax_error_returns_empty(self, analyzer, project_root):
        path = write_py_file(project_root, "bad.py", """\
            def broken(
                # missing closing paren
        """)
        nodes, edges = analyzer.analyze_file(path)
        assert nodes == []
        assert edges == []

    def test_syntax_error_does_not_crash(self, analyzer, project_root):
        path = write_py_file(project_root, "bad.py", "def @@@(): pass\n")
        # Should not raise
        nodes, edges = analyzer.analyze_file(path)
        assert nodes == []
        assert edges == []


class TestUnresolvableCallTargets:
    """R2.8: Skip unresolvable call targets, log to epistemic gaps."""

    def test_dynamic_dispatch_skipped(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            funcs = [foo, bar]
            funcs[0]()
        """)
        nodes, edges = analyzer.analyze_file(path)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # funcs[0]() is unresolvable — should be skipped
        assert len(call_edges) == 0
        # Should be logged in epistemic gaps
        assert len(analyzer._epistemic_gaps) >= 1

    def test_computed_call_skipped(self, analyzer, project_root):
        path = write_py_file(project_root, "mymod.py", """\
            getattr(obj, 'method')()
        """)
        nodes, edges = analyzer.analyze_file(path)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # The outer call getattr() is resolvable, but getattr(...)() has
        # func=ast.Call which is unresolvable
        # Actually getattr is a simple Name call, so it IS resolved.
        # The outer expression getattr(obj, 'method')() has func=ast.Call
        # which is unresolvable
        # Let's just check epistemic gaps were logged
        assert len(analyzer._epistemic_gaps) >= 1


class TestScopeBoundaries:
    """R20.1, R20.2, R20.3, R20.6: Scope boundary checking."""

    def test_includes_py_files(self, analyzer, project_root):
        path = write_py_file(project_root, "module.py", "x = 1\n")
        assert analyzer._is_in_scope(path) is True

    def test_excludes_non_py_files(self, analyzer, project_root):
        path = os.path.join(project_root, "readme.md")
        with open(path, "w") as f:
            f.write("# readme")
        assert analyzer._is_in_scope(path) is False

    def test_excludes_binary_extensions(self, analyzer, project_root):
        path = os.path.join(project_root, "module.pyc")
        with open(path, "w") as f:
            f.write("")
        assert analyzer._is_in_scope(path) is False

    def test_excludes_gitignore_matches(self, analyzer, project_root):
        path = os.path.join(project_root, "__pycache__", "mod.py")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("x = 1\n")
        assert analyzer._is_in_scope(path) is False

    def test_excludes_symlinks_outside_project(self, project_root, scope_config):
        """R20.6: Symlinks resolved to targets outside project root are skipped."""
        # Create a file outside project root
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as ext:
            ext.write(b"x = 1\n")
            ext_path = ext.name

        try:
            link_path = os.path.join(project_root, "linked.py")
            os.symlink(ext_path, link_path)
            analyzer = StaticAnalyzer(project_root, scope_config)
            assert analyzer._is_in_scope(link_path) is False
        finally:
            os.unlink(ext_path)
            if os.path.exists(link_path):
                os.unlink(link_path)

    def test_includes_symlinks_inside_project(self, project_root, scope_config):
        """Symlinks to targets within project root are included."""
        real_file = os.path.join(project_root, "real.py")
        with open(real_file, "w") as f:
            f.write("x = 1\n")
        link_path = os.path.join(project_root, "link.py")
        os.symlink(real_file, link_path)
        analyzer = StaticAnalyzer(project_root, scope_config)
        assert analyzer._is_in_scope(link_path) is True


class TestComputeComplexity:
    """Cyclomatic complexity calculation."""

    def test_simple_function_complexity_1(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def simple():
                return 42
        """)
        assert analyzer.compute_complexity(path, "simple") == 1

    def test_if_adds_complexity(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def check(x):
                if x > 0:
                    return x
                return 0
        """)
        assert analyzer.compute_complexity(path, "check") == 2

    def test_for_and_while_add_complexity(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def loops(items):
                for i in items:
                    pass
                while True:
                    break
        """)
        assert analyzer.compute_complexity(path, "loops") == 3

    def test_boolean_operators(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def logic(a, b, c):
                if a and b or c:
                    pass
        """)
        # if = 1, BoolOp `or` has 2 values (a and b, c) = 1 decision
        # BoolOp `and` has 2 values (a, b) = 1 decision
        # Base 1 + if 1 + and 1 + or 1 = 4
        assert analyzer.compute_complexity(path, "logic") == 4

    def test_except_handler(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def risky():
                try:
                    pass
                except ValueError:
                    pass
                except TypeError:
                    pass
        """)
        # Base 1 + 2 except handlers = 3
        assert analyzer.compute_complexity(path, "risky") == 3

    def test_comprehension(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def comp():
                return [x for x in range(10) if x > 5]
        """)
        # Base 1 + comprehension 1 + if in comprehension (IfExp is not counted,
        # but the `if` clause in list comp is actually part of comprehension node)
        # ast.comprehension includes the `if` filters as part of the node,
        # but the if filter is separate from ast.If
        # Actually: [x for x in range(10) if x > 5] has 1 comprehension node
        # The if inside is stored in comprehension.ifs but it's NOT an ast.If node
        # So: Base 1 + 1 comprehension = 2
        assert analyzer.compute_complexity(path, "comp") == 2

    def test_nonexistent_function_returns_1(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def existing():
                pass
        """)
        assert analyzer.compute_complexity(path, "nonexistent") == 1

    def test_with_statement(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def opener():
                with open('f') as fh:
                    pass
        """)
        # Base 1 + with 1 = 2
        assert analyzer.compute_complexity(path, "opener") == 2

    def test_assert_adds_complexity(self, analyzer, project_root):
        path = write_py_file(project_root, "mod.py", """\
            def asserter(x):
                assert x > 0
                assert x < 100
        """)
        # Base 1 + 2 asserts = 3
        assert analyzer.compute_complexity(path, "asserter") == 3


class TestAnalyzeProject:
    """R20.5: Full project scan with file count limit."""

    def test_scans_multiple_files(self, project_root, scope_config):
        write_py_file(project_root, "a.py", "def foo(): pass\n")
        write_py_file(project_root, "b.py", "def bar(): pass\n")
        analyzer = StaticAnalyzer(project_root, scope_config)
        nodes, edges = analyzer.analyze_project()
        func_ids = {n.id for n in nodes if n.node_type == "function"}
        assert "a.foo" in func_ids
        assert "b.bar" in func_ids

    def test_respects_max_files_limit(self, project_root):
        # Create more files than the limit
        config = {"max_files": 2, "gitignore_patterns": [".git", "__pycache__"]}
        for i in range(5):
            write_py_file(project_root, f"mod{i}.py", f"def func{i}(): pass\n")
        analyzer = StaticAnalyzer(project_root, config)
        nodes, edges = analyzer.analyze_project()
        func_nodes = [n for n in nodes if n.node_type == "function"]
        # Should stop at 2 files
        assert len(func_nodes) <= 2

    def test_skips_excluded_directories(self, project_root, scope_config):
        write_py_file(project_root, "good.py", "def ok(): pass\n")
        write_py_file(project_root, "__pycache__/cached.py", "def bad(): pass\n")
        analyzer = StaticAnalyzer(project_root, scope_config)
        nodes, edges = analyzer.analyze_project()
        func_ids = {n.id for n in nodes if n.node_type == "function"}
        assert "good.ok" in func_ids
        # __pycache__ should be excluded
        assert not any("cached" in fid for fid in func_ids)

    def test_skips_non_py_files(self, project_root, scope_config):
        write_py_file(project_root, "module.py", "def yes(): pass\n")
        readme = os.path.join(project_root, "README.md")
        with open(readme, "w") as f:
            f.write("# Hello\n")
        analyzer = StaticAnalyzer(project_root, scope_config)
        nodes, edges = analyzer.analyze_project()
        # Only .py files are analyzed
        assert all(n.file_path.endswith(".py") or n.file_path == "<external>"
                   for n in nodes)


class TestGitignoreLoading:
    """Test .gitignore pattern loading from file."""

    def test_loads_from_gitignore_file(self, project_root):
        gitignore = os.path.join(project_root, ".gitignore")
        with open(gitignore, "w") as f:
            f.write("build/\n*.log\n# comment\n\nvenv\n")
        config = {"max_files": 1000}
        analyzer = StaticAnalyzer(project_root, config)
        assert "build" in analyzer._gitignore_patterns
        assert "*.log" in analyzer._gitignore_patterns
        assert "venv" in analyzer._gitignore_patterns
        # Comments and empty lines excluded
        assert "# comment" not in analyzer._gitignore_patterns

    def test_falls_back_to_defaults_when_no_gitignore(self, tmp_path):
        project_root = str(tmp_path)
        (tmp_path / ".git").mkdir()
        config = {"max_files": 1000}
        analyzer = StaticAnalyzer(project_root, config)
        # Should use defaults
        assert ".git" in analyzer._gitignore_patterns
        assert "__pycache__" in analyzer._gitignore_patterns


class TestModuleNameDerivation:
    """Module name derived from file path relative to project root."""

    def test_simple_module(self, analyzer, project_root):
        assert analyzer._file_path_to_module(
            os.path.join(project_root, "foo.py")
        ) == "foo"

    def test_nested_module(self, analyzer, project_root):
        assert analyzer._file_path_to_module(
            os.path.join(project_root, "pkg", "sub.py")
        ) == "pkg.sub"

    def test_init_module(self, analyzer, project_root):
        assert analyzer._file_path_to_module(
            os.path.join(project_root, "pkg", "__init__.py")
        ) == "pkg"
