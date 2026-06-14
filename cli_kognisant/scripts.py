"""
Script CRUD operations for the global scripts folder.

Manages Python scripts in ~/.kognisant_core/scripts/ with accompanying
JSON metadata files. Uses only Python standard library modules (R13-AC3).
"""

import json
import os
import re
from datetime import datetime, timezone

from .config import GLOBAL_CORE_DIR

# Module-level scripts directory - configurable for testing
SCRIPTS_DIR = os.path.join(GLOBAL_CORE_DIR, "scripts")


def _get_scripts_dir() -> str:
    """Return the current scripts directory path, ensuring it exists."""
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    return SCRIPTS_DIR


def validate_script_name(name: str) -> str | None:
    """Validate a script name.

    Valid names:
    - Contain only lowercase alphanumeric characters, hyphens, and underscores
    - Are between 1 and 64 characters long
    - Do not contain path separators or traversal sequences

    Args:
        name: The script name to validate.

    Returns:
        None if valid, error message string if invalid.
    """
    if not name:
        return "Script name cannot be empty"

    if len(name) > 64:
        return "Script name must be 64 characters or fewer"

    # Check for path traversal sequences
    if ".." in name or "./" in name or "/" in name or "\\" in name:
        return "Script name must not contain path separators or traversal sequences"

    # Check for valid characters: lowercase alphanumeric, hyphens, underscores
    if not re.match(r"^[a-z0-9_-]+$", name):
        return "Script name must contain only lowercase alphanumeric characters, hyphens, and underscores"

    return None


def create_script(
    name: str,
    content: str,
    description: str = "",
    env_vars: list[str] | None = None,
) -> str:
    """Create a new script with metadata using atomic two-phase write.

    Sequence:
    1. Write content to {name}.py.tmp
    2. Write metadata to {name}.json.tmp
    3. Rename {name}.py.tmp → {name}.py
    4. Rename {name}.json.tmp → {name}.json

    On failure at any step: remove all .tmp files, leave no artifacts.

    Args:
        name: Script name (validated).
        content: Python script content.
        description: Human-readable description of the script.
        env_vars: List of required environment variable names.

    Returns:
        Success or error message string.
    """
    error = validate_script_name(name)
    if error:
        return f"Error: {error}"

    scripts_dir = _get_scripts_dir()
    script_path = os.path.join(scripts_dir, f"{name}.py")
    metadata_path = os.path.join(scripts_dir, f"{name}.json")
    script_tmp = os.path.join(scripts_dir, f"{name}.py.tmp")
    metadata_tmp = os.path.join(scripts_dir, f"{name}.json.tmp")

    # R6-AC7: Check if script already exists
    if os.path.exists(script_path) or os.path.exists(metadata_path):
        return f"Error: Script '{name}' already exists"

    # Prepare metadata
    metadata = {
        "name": name,
        "description": description,
        "env_vars": env_vars or [],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    def _cleanup_tmp():
        """Remove all .tmp files on failure."""
        for tmp in (script_tmp, metadata_tmp):
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    # Phase 1: Write temp files
    try:
        with open(script_tmp, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        _cleanup_tmp()
        return f"Error: Failed to write script file: {e}"

    try:
        with open(metadata_tmp, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
    except OSError as e:
        _cleanup_tmp()
        return f"Error: Failed to write metadata file: {e}"

    # Phase 2: Atomic renames
    try:
        os.rename(script_tmp, script_path)
    except OSError as e:
        _cleanup_tmp()
        return f"Error: Failed to finalize script file: {e}"

    try:
        os.rename(metadata_tmp, metadata_path)
    except OSError as e:
        # Rollback: remove the already-renamed .py file
        try:
            os.remove(script_path)
        except OSError:
            pass
        _cleanup_tmp()
        return f"Error: Failed to finalize metadata file: {e}"

    return f"Script '{name}' created successfully"


def read_script(name: str) -> str:
    """Read and return the content of a script.

    Args:
        name: Script name.

    Returns:
        Script content string, or error message if not found.
    """
    error = validate_script_name(name)
    if error:
        return f"Error: {error}"

    scripts_dir = _get_scripts_dir()
    script_path = os.path.join(scripts_dir, f"{name}.py")

    # R6-AC8: error if not found
    if not os.path.exists(script_path):
        return f"Error: Script '{name}' not found"

    try:
        with open(script_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        return f"Error: Failed to read script: {e}"


def edit_script(name: str, edits: list[dict]) -> str:
    """Apply sequential find-and-replace edits to a script.

    Each edit dict has {"old_text": "...", "new_text": "..."}.
    All edits are applied sequentially. If any old_text is not found,
    ALL edits are rolled back and an error is returned (R6-AC11).

    Args:
        name: Script name.
        edits: List of edit dicts with old_text and new_text keys.

    Returns:
        Success or error message string.
    """
    error = validate_script_name(name)
    if error:
        return f"Error: {error}"

    scripts_dir = _get_scripts_dir()
    script_path = os.path.join(scripts_dir, f"{name}.py")

    # R6-AC8: error if not found
    if not os.path.exists(script_path):
        return f"Error: Script '{name}' not found"

    try:
        with open(script_path, "r", encoding="utf-8") as f:
            original_content = f.read()
    except OSError as e:
        return f"Error: Failed to read script: {e}"

    # Apply edits sequentially
    content = original_content
    for i, edit in enumerate(edits):
        old_text = edit.get("old_text", "")
        new_text = edit.get("new_text", "")

        if old_text not in content:
            # R6-AC11: rollback ALL edits (don't write anything)
            return (
                f"Error: Text to replace not found in edit {i + 1}: "
                f"'{old_text[:50]}{'...' if len(old_text) > 50 else ''}'"
            )

        content = content.replace(old_text, new_text, 1)

    # R6-AC12: Write updated content, preserve previous state on error
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        # Attempt to restore original content
        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(original_content)
        except OSError:
            pass
        return f"Error: Failed to write script: {e}"

    return f"Script '{name}' edited successfully"


def delete_script(name: str) -> str:
    """Delete a script and its metadata file.

    Args:
        name: Script name.

    Returns:
        Success or error message string.
    """
    error = validate_script_name(name)
    if error:
        return f"Error: {error}"

    scripts_dir = _get_scripts_dir()
    script_path = os.path.join(scripts_dir, f"{name}.py")
    metadata_path = os.path.join(scripts_dir, f"{name}.json")

    # R6-AC8: error if not found
    if not os.path.exists(script_path) and not os.path.exists(metadata_path):
        return f"Error: Script '{name}' not found"

    # Remove both files
    try:
        if os.path.exists(script_path):
            os.remove(script_path)
        if os.path.exists(metadata_path):
            os.remove(metadata_path)
    except OSError as e:
        return f"Error: Failed to delete script: {e}"

    return f"Script '{name}' deleted successfully"


def list_scripts() -> str:
    """List all scripts with their metadata.

    Returns:
        Formatted list of scripts with name, description, and env_vars.
        Returns a message if no scripts exist.
    """
    scripts_dir = _get_scripts_dir()

    # Find all .json metadata files
    try:
        files = os.listdir(scripts_dir)
    except OSError:
        return "No scripts found"

    metadata_files = sorted(f for f in files if f.endswith(".json"))

    if not metadata_files:
        return "No scripts found"

    lines = []
    for meta_file in metadata_files:
        meta_path = os.path.join(scripts_dir, meta_file)
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            name = metadata.get("name", meta_file[:-5])
            description = metadata.get("description", "")
            env_vars = metadata.get("env_vars", [])

            entry = f"  {name}"
            if description:
                entry += f" - {description}"
            if env_vars:
                entry += f" [env: {', '.join(env_vars)}]"
            lines.append(entry)
        except (OSError, json.JSONDecodeError):
            # Skip malformed metadata files
            continue

    if not lines:
        return "No scripts found"

    return "Scripts:\n" + "\n".join(lines)
