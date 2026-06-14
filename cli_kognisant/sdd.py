"""
Spec-Driven Development (SDD) — Living Workflow Engine

Specs are stateful workflows that progress through stages:
  DEFINE → DESIGN → PLAN → BUILD → VERIFY → DONE

Each stage can be driven interactively (AI-assisted authoring)
or resumed from where the user left off.
"""

import json
import os
import re
import sys
import time

from .colors import Colors, Spinner
from .network import query_model_api


# ───────────────────────────────────────────────────────────
# Spec States
# ───────────────────────────────────────────────────────────

SPEC_STATES = ["DEFINE", "DESIGN", "PLAN", "BUILD", "VERIFY", "DONE"]


# ───────────────────────────────────────────────────────────
# Spec State Manager
# ───────────────────────────────────────────────────────────


class SpecManager:
    """Manages the lifecycle of a feature spec as a state machine."""

    def __init__(self, project_root, feature_name):
        self.project_root = project_root
        self.feature_name = feature_name
        self.spec_dir = os.path.join(
            project_root, ".kognisant", "specs", feature_name
        )
        self.spec_json_path = os.path.join(self.spec_dir, "spec.json")
        self.requirements_path = os.path.join(self.spec_dir, "requirements.md")
        self.design_path = os.path.join(self.spec_dir, "design.md")
        self.tasks_path = os.path.join(self.spec_dir, "tasks.md")
        self.state = None

    def exists(self):
        """Check if this spec already exists on disk."""
        return os.path.exists(self.spec_dir)

    def load(self):
        """Load spec state from spec.json, or return None if not found."""
        if not os.path.exists(self.spec_json_path):
            return None
        try:
            with open(self.spec_json_path, "r", encoding="utf-8") as f:
                self.state = json.load(f)
            return self.state
        except Exception:
            return None

    def save(self):
        """Persist current spec state to spec.json."""
        os.makedirs(self.spec_dir, exist_ok=True)
        try:
            with open(self.spec_json_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            return True
        except Exception as e:
            print(f"{Colors.RED}[Error] Failed to save spec state: {e}{Colors.RESET}")
            return False

    def initialize(self):
        """Create a brand new spec in DEFINE state."""
        os.makedirs(self.spec_dir, exist_ok=True)
        self.state = {
            "feature_name": self.feature_name,
            "status": "DEFINE",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tasks": [],
            "current_task_index": 0,
        }
        self.save()
        return self.state

    def get_status(self):
        """Return the current lifecycle status."""
        if not self.state:
            self.load()
        if not self.state:
            return None
        return self.state.get("status", "DEFINE")

    def advance_status(self, new_status):
        """Move to the next lifecycle stage."""
        if new_status in SPEC_STATES:
            self.state["status"] = new_status
            self.state["last_updated"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            self.save()

    def get_tasks(self):
        """Return the task list from state."""
        if not self.state:
            self.load()
        if not self.state:
            return []
        return self.state.get("tasks", [])

    def get_progress(self):
        """Returns (completed_count, total_count)."""
        tasks = self.get_tasks()
        done = sum(1 for t in tasks if t.get("status") == "done")
        return done, len(tasks)

    def mark_task_done(self, task_index):
        """Mark a specific task as completed."""
        tasks = self.state.get("tasks", [])
        if 0 <= task_index < len(tasks):
            tasks[task_index]["status"] = "done"
            tasks[task_index]["completed_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            # Advance current_task_index
            self.state["current_task_index"] = task_index + 1
            self.state["last_updated"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            self.save()
            self._sync_tasks_md()

    def mark_task_in_progress(self, task_index):
        """Mark a specific task as in progress."""
        tasks = self.state.get("tasks", [])
        if 0 <= task_index < len(tasks):
            tasks[task_index]["status"] = "in_progress"
            self.state["last_updated"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            self.save()
            self._sync_tasks_md()

    def _sync_tasks_md(self):
        """Regenerate tasks.md from current state for human readability."""
        tasks = self.state.get("tasks", [])
        done_count, total = self.get_progress()
        status = self.state.get("status", "BUILD")

        lines = [
            f"# Implementation Tasks: {self.feature_name}\n",
            f"Status: **{status}** ({done_count}/{total} completed)\n",
            f"Last updated: {self.state.get('last_updated', 'N/A')}\n",
            "",
        ]

        current_phase = None
        for task in tasks:
            phase = task.get("phase", 1)
            if phase != current_phase:
                current_phase = phase
                lines.append(f"## Phase {phase}\n")

            check = "[x]" if task.get("status") == "done" else "[ ]"
            marker = " ← current" if task.get("status") == "in_progress" else ""
            lines.append(f"- {check} {task['description']}{marker}")

        lines.append("")

        try:
            with open(self.tasks_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    def write_requirements(self, content):
        """Write requirements.md content."""
        os.makedirs(self.spec_dir, exist_ok=True)
        with open(self.requirements_path, "w", encoding="utf-8") as f:
            f.write(content)

    def write_design(self, content):
        """Write design.md content."""
        os.makedirs(self.spec_dir, exist_ok=True)
        with open(self.design_path, "w", encoding="utf-8") as f:
            f.write(content)

    def read_requirements(self):
        """Read requirements.md if it exists."""
        if os.path.exists(self.requirements_path):
            with open(self.requirements_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def read_design(self):
        """Read design.md if it exists."""
        if os.path.exists(self.design_path):
            with open(self.design_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def read_tasks(self):
        """Read tasks.md if it exists."""
        if os.path.exists(self.tasks_path):
            with open(self.tasks_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def set_tasks(self, tasks_list):
        """Set the task list and write tasks.md."""
        self.state["tasks"] = tasks_list
        self.state["current_task_index"] = 0
        self.state["last_updated"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        self.save()
        self._sync_tasks_md()

    def get_next_task(self):
        """Get the next pending task, or None if all done."""
        tasks = self.state.get("tasks", [])
        for i, task in enumerate(tasks):
            if task.get("status") != "done":
                return i, task
        return None, None

    def get_spec_context_for_agent(self):
        """Build a context string for injecting into PERP agent prompts."""
        reqs = self.read_requirements()
        design = self.read_design()
        tasks = self.get_tasks()
        done_count, total = self.get_progress()

        context = (
            f"SPEC-DRIVEN DEVELOPMENT CONTEXT: '{self.feature_name}'\n"
            f"Status: {self.state.get('status', 'UNKNOWN')} ({done_count}/{total} tasks done)\n\n"
        )

        if reqs:
            context += f"REQUIREMENTS:\n```markdown\n{reqs}\n```\n\n"
        if design:
            context += f"DESIGN:\n```markdown\n{design}\n```\n\n"

        if tasks:
            context += "TASK CHECKLIST:\n"
            for i, t in enumerate(tasks):
                check = "✓" if t.get("status") == "done" else "○"
                marker = " ← NEXT" if t.get("status") != "done" and all(
                    tasks[j].get("status") == "done" for j in range(i)
                ) else ""
                context += f"  {check} [{i+1}] {t['description']}{marker}\n"

        return context


# ───────────────────────────────────────────────────────────
# AI-Assisted Spec Authoring
# ───────────────────────────────────────────────────────────


def _get_user_input(prompt_text):
    """Get user input with a clean prompt. Returns None on EOF/interrupt."""
    try:
        return input(prompt_text).strip()
    except (EOFError, KeyboardInterrupt):
        return None


def _ai_generate(model_config, prompt, fallback=""):
    """Call the LLM to generate content. Returns fallback on failure."""
    if not model_config or model_config.get("name") == "mock":
        return fallback

    try:
        result = query_model_api(
            model_config["api_base_url"],
            model_config.get("api_key", ""),
            model_config["name"],
            [{"role": "user", "content": prompt}],
            protocol=model_config.get("protocol", "openai"),
        )
        if result:
            # Strip code fences if present
            result = result.strip()
            if result.startswith("```markdown"):
                result = result[len("```markdown"):].strip()
            if result.startswith("```"):
                result = result[3:].strip()
            if result.endswith("```"):
                result = result[:-3].strip()
            return result
    except Exception as e:
        print(
            f"  {Colors.YELLOW}[AI Warning] Generation failed: {e}. Using template.{Colors.RESET}"
        )
    return fallback


def run_define_stage(spec_manager, model_config=None, project_info=None):
    """Interactive DEFINE stage — gather requirements via conversation."""
    print(f"\n  {'─' * 50}")
    print(f"  📝 {Colors.BOLD}DEFINE Stage{Colors.RESET} — What should this feature do?")
    print(f"  {'─' * 50}\n")

    print(
        f"  Describe what you want to build. Be as detailed or brief as you like —\n"
        f"  Kognisant will help flesh it out.\n"
    )

    description = _get_user_input(f"  {Colors.CYAN}▸{Colors.RESET} Describe the feature:\n    ")
    if description is None:
        return False

    if not description:
        print(f"  {Colors.YELLOW}No description provided. Skipping AI generation.{Colors.RESET}")
        # Write minimal template
        template = (
            f"# Feature Requirements: {spec_manager.feature_name}\n\n"
            f"## Overview\n"
            f"TODO: Describe what this feature does.\n\n"
            f"## Functional Requirements\n"
            f"- [ ] Requirement 1\n\n"
            f"## Success Criteria\n"
            f"- [ ] How do we know this is complete?\n"
        )
        spec_manager.write_requirements(template)
        spec_manager.advance_status("DESIGN")
        print(f"\n  ✅ Template created. You can edit it manually at:")
        print(f"     {spec_manager.requirements_path}\n")
        return True

    # AI-assisted requirements generation
    spinner = Spinner("Generating requirements")
    spinner.start()

    project_files = ""
    if project_info:
        files_list = project_info.get("files", [])[:50]
        project_files = f"\nProject files for context:\n{chr(10).join('- ' + f for f in files_list)}\n"

    prompt = (
        f"You are a senior software architect. Based on the following feature description, "
        f"generate a clear, structured requirements document in Markdown.\n\n"
        f"Feature name: {spec_manager.feature_name}\n"
        f"Description: {description}\n"
        f"{project_files}\n"
        f"Generate a requirements document with these sections:\n"
        f"1. ## Overview — 2-3 sentence summary\n"
        f"2. ## Functional Requirements — numbered list of specific behaviors\n"
        f"3. ## Non-Functional Requirements — performance, security, compatibility\n"
        f"4. ## Success Criteria — measurable acceptance criteria\n\n"
        f"Use markdown checkbox format (- [ ]) for each requirement.\n"
        f"Be specific and actionable. Output ONLY the markdown content."
    )

    fallback = (
        f"# Feature Requirements: {spec_manager.feature_name}\n\n"
        f"## Overview\n{description}\n\n"
        f"## Functional Requirements\n"
        f"- [ ] {description}\n\n"
        f"## Success Criteria\n"
        f"- [ ] Feature works as described\n"
    )

    content = _ai_generate(model_config, prompt, fallback)

    # Ensure title is present
    if not content.startswith("#"):
        content = f"# Feature Requirements: {spec_manager.feature_name}\n\n{content}"

    spinner.stop()

    spec_manager.write_requirements(content)
    spec_manager.advance_status("DESIGN")

    # Count requirements
    req_count = content.count("- [ ]") + content.count("- [x]")
    print(f"  ✅ Requirements drafted ({req_count} items)")
    print(f"  📄 {spec_manager.requirements_path}\n")

    return True


def run_design_stage(spec_manager, model_config=None, project_info=None):
    """Interactive DESIGN stage — generate architecture from requirements."""
    print(f"\n  {'─' * 50}")
    print(f"  🏗️  {Colors.BOLD}DESIGN Stage{Colors.RESET} — Architecture & boundaries")
    print(f"  {'─' * 50}\n")

    requirements = spec_manager.read_requirements()
    if not requirements:
        print(f"  {Colors.YELLOW}No requirements found. Please run DEFINE stage first.{Colors.RESET}")
        return False

    print(f"  Requirements loaded ({len(requirements)} chars)")
    print(f"  Generating architecture design...\n")

    spinner = Spinner("Designing architecture")
    spinner.start()

    project_files = ""
    if project_info:
        files_list = project_info.get("files", [])[:50]
        project_files = f"\nExisting project files:\n{chr(10).join('- ' + f for f in files_list)}\n"

    prompt = (
        f"You are a senior software architect. Based on the requirements below, "
        f"generate a design document that specifies HOW to build this feature.\n\n"
        f"Feature: {spec_manager.feature_name}\n"
        f"Requirements:\n```markdown\n{requirements}\n```\n"
        f"{project_files}\n"
        f"Generate a design document with these sections:\n"
        f"1. ## Architecture — High-level component interactions\n"
        f"2. ## Files & Boundaries — Which files to create/modify (use backticks for paths like `cli_kognisant/auth.py`)\n"
        f"3. ## Data Structures — Core models, schemas, or types\n"
        f"4. ## Interface Contract — Public APIs, CLI args, or message formats\n"
        f"5. ## Testing Strategy — How to validate\n\n"
        f"Be specific about file paths. Reference existing project files where relevant.\n"
        f"Output ONLY the markdown content."
    )

    fallback = (
        f"# Design Document: {spec_manager.feature_name}\n\n"
        f"## Architecture\nTODO: Describe component interactions.\n\n"
        f"## Files & Boundaries\n- `cli_kognisant/{spec_manager.feature_name}.py`\n\n"
        f"## Data Structures\nTODO: Define core models.\n\n"
        f"## Interface Contract\nTODO: Document APIs.\n\n"
        f"## Testing Strategy\n- Unit tests in `tests/test_{spec_manager.feature_name}.py`\n"
    )

    content = _ai_generate(model_config, prompt, fallback)

    if not content.startswith("#"):
        content = f"# Design Document: {spec_manager.feature_name}\n\n{content}"

    spinner.stop()

    spec_manager.write_design(content)
    spec_manager.advance_status("PLAN")

    # Extract file boundaries
    boundaries = re.findall(r"`([a-zA-Z0-9_\-\./]+)`", content)
    file_boundaries = sorted(set(b for b in boundaries if "/" in b or "." in b))

    print(f"  ✅ Design document created")
    if file_boundaries:
        print(f"  📐 Boundaries: {', '.join(file_boundaries[:8])}")
    print(f"  📄 {spec_manager.design_path}\n")

    return True


def run_plan_stage(spec_manager, model_config=None, project_info=None):
    """Interactive PLAN stage — generate implementation tasks from requirements + design."""
    print(f"\n  {'─' * 50}")
    print(f"  📋 {Colors.BOLD}PLAN Stage{Colors.RESET} — Generate implementation tasks")
    print(f"  {'─' * 50}\n")

    requirements = spec_manager.read_requirements()
    design = spec_manager.read_design()

    if not requirements:
        print(f"  {Colors.YELLOW}No requirements found. Run DEFINE stage first.{Colors.RESET}")
        return False

    spinner = Spinner("Planning implementation tasks")
    spinner.start()

    prompt = (
        f"You are a senior software engineer. Based on the requirements and design below, "
        f"generate a phased list of implementation tasks.\n\n"
        f"Feature: {spec_manager.feature_name}\n"
        f"Requirements:\n```markdown\n{requirements}\n```\n\n"
    )

    if design:
        prompt += f"Design:\n```markdown\n{design}\n```\n\n"

    prompt += (
        f"Generate tasks as a JSON array. Each task has:\n"
        f'- "description": clear, actionable instruction for a developer or AI agent\n'
        f'- "phase": integer (1 = scaffolding/discovery, 2 = core logic, 3 = integration/testing)\n\n'
        f"Rules:\n"
        f"- Tasks should be atomic and independently executable\n"
        f"- Phase 1: file creation, structure setup, reading existing code\n"
        f"- Phase 2: implementing core logic, writing functions/classes\n"
        f"- Phase 3: wiring into CLI/chat, writing tests, updating docs\n"
        f"- Aim for 5-12 tasks total\n\n"
        f"Output ONLY a valid JSON array like:\n"
        f'[{{"description": "Create auth.py module with JWT signing", "phase": 1}}, ...]\n'
    )

    fallback_tasks = [
        {"description": f"Identify and read relevant existing files", "phase": 1},
        {"description": f"Create core module for {spec_manager.feature_name}", "phase": 1},
        {"description": f"Implement primary feature logic", "phase": 2},
        {"description": f"Add error handling and validation", "phase": 2},
        {"description": f"Wire into CLI or chat commands", "phase": 3},
        {"description": f"Write unit tests", "phase": 3},
        {"description": f"Update documentation", "phase": 3},
    ]

    result = _ai_generate(model_config, prompt, "")

    tasks_list = None
    if result:
        try:
            # Try to parse JSON from response
            cleaned = result.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
            # Handle case where result might start with [ directly
            if not cleaned.startswith("["):
                # Try to find the array in the text
                match = re.search(r"\[[\s\S]*\]", cleaned)
                if match:
                    cleaned = match.group(0)
            tasks_list = json.loads(cleaned)
        except (json.JSONDecodeError, AttributeError):
            tasks_list = None

    if not tasks_list or not isinstance(tasks_list, list):
        tasks_list = fallback_tasks

    # Normalize and enrich tasks
    normalized_tasks = []
    for task in tasks_list:
        if isinstance(task, dict) and "description" in task:
            normalized_tasks.append({
                "description": task["description"],
                "phase": int(task.get("phase", 2)),
                "status": "pending",
            })

    spinner.stop()

    spec_manager.set_tasks(normalized_tasks)
    spec_manager.advance_status("BUILD")

    print(f"  ✅ {len(normalized_tasks)} implementation tasks generated across {len(set(t['phase'] for t in normalized_tasks))} phases")
    print(f"  📄 {spec_manager.tasks_path}\n")

    # Show task preview
    print(f"  {Colors.BOLD}Task Preview:{Colors.RESET}")
    current_phase = None
    for i, task in enumerate(normalized_tasks):
        if task["phase"] != current_phase:
            current_phase = task["phase"]
            print(f"    {Colors.CYAN}Phase {current_phase}:{Colors.RESET}")
        print(f"      □ {task['description']}")
    print()

    return True


def run_build_next_task(spec_manager, model_config=None, project_info=None, compiled_models=None):
    """Execute the next pending task via PERP swarm."""
    idx, task = spec_manager.get_next_task()
    if idx is None:
        print(f"  ✅ {Colors.GREEN}All tasks completed!{Colors.RESET}")
        spec_manager.advance_status("VERIFY")
        return True

    desc = task["description"]
    done_count, total = spec_manager.get_progress()

    print(f"\n  {'─' * 50}")
    print(f"  🔨 {Colors.BOLD}BUILD Stage{Colors.RESET} — Task {idx + 1}/{total}")
    print(f"  {'─' * 50}")
    print(f"  Task: {Colors.CYAN}{desc}{Colors.RESET}\n")

    spec_manager.mark_task_in_progress(idx)

    # Execute via PERP swarm with spec context injected
    from .agents import perp_orchestrate

    # Build enriched task description with spec context
    spec_context = spec_manager.get_spec_context_for_agent()
    enriched_task = (
        f"{desc}\n\n"
        f"CONTEXT: This is task {idx + 1} of {total} in the spec '{spec_manager.feature_name}'.\n"
        f"{spec_context}"
    )

    if compiled_models:
        perp_orchestrate(
            enriched_task,
            project_info,
            compiled_models,
            force_mock=(not model_config or model_config.get("name") == "mock"),
        )

    # Mark done after execution (trust PERP swarm completed it)
    spec_manager.mark_task_done(idx)

    done_count, total = spec_manager.get_progress()
    print(f"  📊 Progress: {done_count}/{total} tasks completed\n")

    # Check if all done
    if done_count == total:
        spec_manager.advance_status("VERIFY")
        print(f"  🎉 {Colors.GREEN}{Colors.BOLD}All tasks completed! Moving to VERIFY stage.{Colors.RESET}\n")

    return True


def run_verify_stage(spec_manager, model_config=None, project_info=None):
    """VERIFY stage — validate implementation against requirements."""
    print(f"\n  {'─' * 50}")
    print(f"  ✔️  {Colors.BOLD}VERIFY Stage{Colors.RESET} — Validate against requirements")
    print(f"  {'─' * 50}\n")

    requirements = spec_manager.read_requirements()
    design = spec_manager.read_design()
    done_count, total = spec_manager.get_progress()

    print(f"  Tasks completed: {done_count}/{total}")
    print(f"  Requirements document: {'✅ Present' if requirements else '❌ Missing'}")
    print(f"  Design document: {'✅ Present' if design else '❌ Missing'}\n")

    if done_count < total:
        remaining = total - done_count
        print(f"  {Colors.YELLOW}⚠️  {remaining} tasks still pending.{Colors.RESET}")
        choice = _get_user_input(f"  Mark spec as done anyway? [y/n]: ")
        if choice and choice.lower() != "y":
            return False

    spec_manager.advance_status("DONE")
    print(f"  ✅ {Colors.GREEN}{Colors.BOLD}Spec '{spec_manager.feature_name}' marked as DONE!{Colors.RESET}\n")
    return True


# ───────────────────────────────────────────────────────────
# Interactive Spec Flow (called from CLI)
# ───────────────────────────────────────────────────────────


def spec_interactive_flow(project_root, feature_name, resume=False, model_config=None, project_info=None, compiled_models=None):
    """Main interactive spec flow — creates or resumes a spec workflow."""
    spec = SpecManager(project_root, feature_name)

    if resume or spec.exists():
        # Resume existing spec
        state = spec.load()
        if not state:
            if not spec.exists():
                print(f"  {Colors.RED}[Error] Spec '{feature_name}' not found.{Colors.RESET}")
                return False
            # Spec dir exists but no spec.json — legacy spec, migrate it
            spec.initialize()
            # Check if requirements/design/tasks already exist
            if os.path.exists(spec.requirements_path):
                spec.advance_status("DESIGN")
            if os.path.exists(spec.design_path):
                spec.advance_status("PLAN")
            if os.path.exists(spec.tasks_path):
                spec.advance_status("BUILD")
            state = spec.state

        status = state.get("status", "DEFINE")
        done_count, total = spec.get_progress()

        print(f"\n  {'═' * 50}")
        print(f"  🛠️  {Colors.BOLD}Spec: {feature_name}{Colors.RESET}")
        print(f"  Status: {Colors.CYAN}{status}{Colors.RESET}", end="")
        if total > 0:
            print(f" ({done_count}/{total} tasks done)")
        else:
            print()
        print(f"  {'═' * 50}")

        return _resume_from_status(spec, status, model_config, project_info, compiled_models)
    else:
        # New spec
        print(f"\n  {'═' * 50}")
        print(f"  🛠️  {Colors.BOLD}New Spec: {feature_name}{Colors.RESET}")
        print(f"  {'═' * 50}")

        spec.initialize()
        return _resume_from_status(spec, "DEFINE", model_config, project_info, compiled_models)


def _resume_from_status(spec, status, model_config=None, project_info=None, compiled_models=None):
    """Resume execution from a given status, running subsequent stages with user prompts."""

    if status == "DEFINE":
        if not run_define_stage(spec, model_config, project_info):
            return False
        choice = _get_user_input(f"  Ready to design? [{Colors.GREEN}y{Colors.RESET}/n/edit]: ")
        if choice is None or choice.lower() == "n":
            print(f"  💾 Spec saved. Resume with: kognisant spec {spec.feature_name} --resume\n")
            return True
        if choice.lower() == "edit":
            print(f"  📝 Edit the file, then resume: kognisant spec {spec.feature_name} --resume")
            return True
        status = "DESIGN"

    if status == "DESIGN":
        if not run_design_stage(spec, model_config, project_info):
            return False
        choice = _get_user_input(f"  Ready to plan tasks? [{Colors.GREEN}y{Colors.RESET}/n/edit]: ")
        if choice is None or choice.lower() == "n":
            print(f"  💾 Spec saved. Resume with: kognisant spec {spec.feature_name} --resume\n")
            return True
        if choice.lower() == "edit":
            print(f"  📝 Edit the file, then resume: kognisant spec {spec.feature_name} --resume")
            return True
        status = "PLAN"

    if status == "PLAN":
        if not run_plan_stage(spec, model_config, project_info):
            return False
        choice = _get_user_input(f"  Start building? [{Colors.GREEN}y{Colors.RESET}/n/later]: ")
        if choice is None or choice.lower() in ("n", "later"):
            print(f"  💾 Spec saved. Resume with: kognisant spec {spec.feature_name} --resume\n")
            return True
        status = "BUILD"

    if status == "BUILD":
        done_count, total = spec.get_progress()
        if total == 0:
            print(f"  {Colors.YELLOW}No tasks defined. Run PLAN stage first.{Colors.RESET}")
            return False

        if done_count == total:
            spec.advance_status("VERIFY")
            status = "VERIFY"
        else:
            # Show build menu
            _show_build_menu(spec, model_config, project_info, compiled_models)
            return True

    if status == "VERIFY":
        return run_verify_stage(spec, model_config, project_info)

    if status == "DONE":
        print(f"\n  ✅ {Colors.GREEN}Spec '{spec.feature_name}' is already complete!{Colors.RESET}\n")
        return True

    return True


def _show_build_menu(spec, model_config=None, project_info=None, compiled_models=None):
    """Show the BUILD stage interactive menu."""
    done_count, total = spec.get_progress()
    tasks = spec.get_tasks()

    print(f"\n  {Colors.BOLD}Remaining tasks:{Colors.RESET}")
    pending_shown = 0
    for i, task in enumerate(tasks):
        if task.get("status") != "done":
            marker = f"{Colors.CYAN}→{Colors.RESET} " if pending_shown == 0 else "  "
            print(f"    {marker}□ {task['description']}")
            pending_shown += 1
        else:
            print(f"    {Colors.GREEN}  ✓ {task['description']}{Colors.RESET}")

    print(f"\n  [{Colors.CYAN}c{Colors.RESET}] Continue building (execute all remaining)")
    print(f"  [{Colors.CYAN}n{Colors.RESET}] Execute next task only")
    print(f"  [{Colors.CYAN}s{Colors.RESET}] Show full spec")
    print(f"  [{Colors.CYAN}e{Colors.RESET}] Edit tasks")
    print(f"  [{Colors.CYAN}q{Colors.RESET}] Save and quit\n")

    choice = _get_user_input(f"  {Colors.BOLD}Select:{Colors.RESET} ")

    if choice is None or choice.lower() == "q":
        print(f"  💾 Spec saved. Resume with: kognisant spec {spec.feature_name} --resume\n")
        return

    if choice.lower() == "n":
        run_build_next_task(spec, model_config, project_info, compiled_models)
    elif choice.lower() == "c":
        # Run all remaining tasks sequentially
        while True:
            idx, task = spec.get_next_task()
            if idx is None:
                spec.advance_status("VERIFY")
                print(f"  🎉 {Colors.GREEN}{Colors.BOLD}All tasks completed!{Colors.RESET}\n")
                break
            run_build_next_task(spec, model_config, project_info, compiled_models)
    elif choice.lower() == "s":
        _show_full_spec(spec)
        _show_build_menu(spec, model_config, project_info, compiled_models)
    elif choice.lower() == "e":
        print(f"  📝 Edit tasks at: {spec.tasks_path}")
        print(f"     Then resume with: kognisant spec {spec.feature_name} --resume\n")


def _show_full_spec(spec):
    """Display the full spec overview."""
    print(f"\n  {'═' * 50}")
    print(f"  📋 {Colors.BOLD}Full Spec: {spec.feature_name}{Colors.RESET}")
    print(f"  {'═' * 50}\n")

    reqs = spec.read_requirements()
    if reqs:
        print(f"  {Colors.BOLD}Requirements:{Colors.RESET}")
        for line in reqs.split("\n")[:15]:
            print(f"    {line}")
        if reqs.count("\n") > 15:
            print(f"    ... ({reqs.count(chr(10)) - 15} more lines)")
        print()

    design = spec.read_design()
    if design:
        print(f"  {Colors.BOLD}Design:{Colors.RESET}")
        for line in design.split("\n")[:15]:
            print(f"    {line}")
        if design.count("\n") > 15:
            print(f"    ... ({design.count(chr(10)) - 15} more lines)")
        print()

    tasks = spec.get_tasks()
    if tasks:
        done_count, total = spec.get_progress()
        print(f"  {Colors.BOLD}Tasks ({done_count}/{total}):{Colors.RESET}")
        for task in tasks:
            check = f"{Colors.GREEN}✓{Colors.RESET}" if task.get("status") == "done" else "□"
            print(f"    {check} {task['description']}")
        print()


# ───────────────────────────────────────────────────────────
# Spec Status Display (for CLI status command)
# ───────────────────────────────────────────────────────────


def get_all_specs_status(project_root):
    """Returns a list of all specs with their current status."""
    specs_dir = os.path.join(project_root, ".kognisant", "specs")
    if not os.path.exists(specs_dir):
        return []

    results = []
    try:
        for name in sorted(os.listdir(specs_dir)):
            spec_path = os.path.join(specs_dir, name)
            if os.path.isdir(spec_path):
                spec = SpecManager(project_root, name)
                state = spec.load()
                if state:
                    done, total = spec.get_progress()
                    results.append({
                        "name": name,
                        "status": state.get("status", "UNKNOWN"),
                        "tasks_done": done,
                        "tasks_total": total,
                        "last_updated": state.get("last_updated", "N/A"),
                    })
                else:
                    # Legacy spec without spec.json
                    results.append({
                        "name": name,
                        "status": "LEGACY",
                        "tasks_done": 0,
                        "tasks_total": 0,
                        "last_updated": "N/A",
                    })
    except Exception:
        pass

    return results


# ───────────────────────────────────────────────────────────
# Legacy compatibility — compile_spec and validate_spec
# (Used by agents.py for SDD auto-detection)
# ───────────────────────────────────────────────────────────


def compile_spec(project_root, feature_name, spec_info):
    """Parses requirements.md, design.md, and tasks.md into a machine-readable contract spec.json."""
    spec = SpecManager(project_root, feature_name)
    state = spec.load()

    # If we already have state with tasks, use those
    if state and state.get("tasks"):
        return state

    # Otherwise compile from markdown (legacy behavior)
    spec_dir = os.path.join(project_root, ".kognisant", "specs", feature_name)
    os.makedirs(spec_dir, exist_ok=True)

    requirements = []
    design_boundaries = []
    tasks = []

    # Parse requirements
    req_content = spec_info.get("requirements", "")
    for line in req_content.splitlines():
        line = line.strip()
        if (
            line.startswith("-")
            or line.startswith("*")
            or (line and line[0].isdigit() and "." in line[:3])
        ):
            cleaned = line.lstrip("-*0123456789. ").strip()
            # Remove checkbox markers
            cleaned = cleaned.replace("[ ]", "").replace("[x]", "").replace("[X]", "").strip()
            if cleaned:
                requirements.append(cleaned)

    # Parse design boundaries
    design_content = spec_info.get("design", "")
    matches = re.findall(r"`([a-zA-Z0-9_\-\./]+)`", design_content)
    for m in matches:
        if "/" in m or "." in m:
            design_boundaries.append(m)
    design_boundaries = sorted(list(set(design_boundaries)))

    # Parse tasks
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
                tasks.append({
                    "description": task_desc,
                    "status": "done" if is_done else "pending",
                    "phase": (
                        2
                        if any(w in task_desc.lower() for w in ["edit", "write", "implement", "refactor"])
                        else 1
                    ),
                })

    compiled_spec = {
        "feature_name": feature_name,
        "status": "BUILD",
        "compiled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requirements": requirements,
        "design_boundaries": design_boundaries,
        "tasks": tasks,
        "current_task_index": 0,
    }

    # Save to spec.json
    spec.state = compiled_spec
    spec.save()

    return compiled_spec


def validate_spec(compiled_spec):
    """Runs deterministic validation on the compiled spec."""
    errors = []
    warnings = []

    tasks = compiled_spec.get("tasks", [])
    requirements = compiled_spec.get("requirements", [])
    boundaries = compiled_spec.get("design_boundaries", [])

    if not tasks:
        errors.append(
            "Tasks list is empty. Define implementation tasks before executing."
        )

    if not requirements and tasks:
        warnings.append(
            "Requirements list is empty. Consider adding requirements for traceability."
        )

    if not boundaries:
        warnings.append(
            "No file boundaries defined in design. Target files help enforce sandboxing."
        )

    return {"errors": errors, "warnings": warnings}
