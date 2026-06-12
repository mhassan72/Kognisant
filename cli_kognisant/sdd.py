import json
import os
import re
import time


def compile_spec(project_root, feature_name, spec_info):
    """Parses requirements.md, design.md, and tasks.md into a machine-readable contract spec.json (Phase 0)."""
    spec_dir = os.path.join(project_root, ".kognisant", "specs", feature_name)
    os.makedirs(spec_dir, exist_ok=True)

    requirements = []
    design_boundaries = []
    tasks = []

    # 1. Parse Requirements (requirements.md)
    req_content = spec_info.get("requirements", "")
    for line in req_content.splitlines():
        line = line.strip()
        if (
            line.startswith("-")
            or line.startswith("*")
            or (line and line[0].isdigit() and "." in line[:3])
        ):
            cleaned = line.lstrip("-*0123456789. ").strip()
            if cleaned:
                requirements.append(cleaned)

    # 2. Parse Design Boundaries (design.md) - Extract backticked files/directories
    design_content = spec_info.get("design", "")
    matches = re.findall(r"`([a-zA-Z0-9_\-\./]+)`", design_content)
    for m in matches:
        if "/" in m or "." in m:  # Identifies file paths or packages
            design_boundaries.append(m)
    design_boundaries = sorted(list(set(design_boundaries)))

    # 3. Parse Tasks Checklist (tasks.md)
    tasks_content = spec_info.get("tasks", "")
    for idx, line in enumerate(tasks_content.splitlines(), 1):
        line = line.strip()
        if "- [ ]" in line or "- [x]" in line or "- [X]" in line:
            is_done = "- [ ]" not in line
            task_desc = (
                line.replace("- [ ]", "")
                .replace("- [x]", "")
                .replace("- [X]", "")
                .strip()
            )
            if task_desc:
                tasks.append(
                    {
                        "id": idx,
                        "description": task_desc,
                        "completed": is_done,
                        "phase": (
                            2
                            if "edit" in task_desc.lower()
                            or "write" in task_desc.lower()
                            or "implement" in task_desc.lower()
                            or "refactor" in task_desc.lower()
                            else 1
                        ),
                    }
                )

    compiled_spec = {
        "feature_name": feature_name,
        "compiled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requirements": requirements,
        "design_boundaries": design_boundaries,
        "tasks": tasks,
        "task_state": "NOT_STARTED",
    }

    # Save compiled contract
    spec_json_path = os.path.join(spec_dir, "spec.json")
    try:
        with open(spec_json_path, "w", encoding="utf-8") as f:
            json.dump(compiled_spec, f, indent=2)
    except Exception:
        pass

    return compiled_spec


def validate_spec(compiled_spec):
    """Runs cheap, deterministic static syntax validation on the compiled spec (Phase 1)."""
    errors = []
    warnings = []

    reqs = compiled_spec.get("requirements", [])
    tasks = compiled_spec.get("tasks", [])
    boundaries = compiled_spec.get("design_boundaries", [])

    if not reqs and tasks:
        warnings.append(
            "Requirements list is empty. Populate requirements.md to map against tasks."
        )

    if not tasks:
        errors.append(
            "Tasks checklist is empty. Populate tasks.md to define the execution workload."
        )

    # Heuristic unmapped requirements check (warning)
    stopwords = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "to",
        "for",
        "with",
        "in",
        "on",
        "at",
        "by",
        "of",
        "from",
        "as",
        "is",
    }
    for req in reqs:
        req_words = {
            w.lower()
            for w in re.findall(r"\w+", req)
            if w.lower() not in stopwords and len(w) > 2
        }
        mapped = False
        for task in tasks:
            task_desc = task["description"].lower()
            if any(word in task_desc for word in req_words):
                mapped = True
                break
        if not mapped and req_words:
            warnings.append(
                f"Requirement '{req[:50]}...' appears to be unmapped to any subtask in tasks.md."
            )

    # Missing design boundaries warning
    if not boundaries:
        warnings.append(
            "No files are backticked in design.md. Target files are required to enforce boundary security."
        )

    return {"errors": errors, "warnings": warnings}
