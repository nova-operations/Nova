import os
import json
import logging
from nova.tools.project_manager import get_active_project
from nova.tools.context_optimizer import wrap_tool_output_optimization


def _resolve_path(path: str) -> str:
    """
    Resolves relative paths against the active project directory.
    Absolute paths are left unchanged.
    """
    if os.path.isabs(path):
        return path

    try:
        active_json = get_active_project()
        active_data = json.loads(active_json)

        if active_data.get("status") == "success":
            base_dir = active_data.get("absolute_path")
            return os.path.abspath(os.path.join(base_dir, path))
    except Exception as e:
        logging.warning(f"Failed to get active project for filesystem: {e}")

    return os.path.abspath(path)


@wrap_tool_output_optimization
def read_file(filepath: str) -> str:
    """Reads the content of a file."""
    try:
        abs_path = _resolve_path(filepath)
        with open(abs_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {filepath}: {e}"


@wrap_tool_output_optimization
def write_file(filepath: str, content: str) -> str:
    """Writes content to a file."""
    try:
        abs_path = _resolve_path(filepath)
        # Ensure directory exists
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write(content)
        return f"File written successfully to {abs_path}."
    except Exception as e:
        return f"Error writing file {filepath}: {e}"


@wrap_tool_output_optimization
def list_files(path: str = ".") -> str:
    """Lists files in a directory."""
    try:
        abs_path = _resolve_path(path)
        if not os.path.exists(abs_path):
            return f"Error: directory does not exist: {abs_path}"
        return "\n".join(os.listdir(abs_path))
    except Exception as e:
        return f"Error listing files in {path}: {e}"


@wrap_tool_output_optimization
def list_files_under_directory(path: str = ".") -> str:
    """Lists all files recursively under a directory.
    
    Returns a list of all files (with paths relative to the given directory).
    """
    try:
        abs_path = _resolve_path(path)
        if not os.path.exists(abs_path):
            return f"Error: directory does not exist: {abs_path}"
        if not os.path.isdir(abs_path):
            return f"Error: path is not a directory: {abs_path}"
        
        all_files = []
        for root, dirs, files in os.walk(abs_path):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, abs_path)
                all_files.append(rel_path)
        
        return "\n".join(all_files)
    except Exception as e:
        return f"Error listing files under {path}: {e}"


@wrap_tool_output_optimization
def delete_file(filepath: str) -> str:
    """Deletes a file."""
    try:
        abs_path = _resolve_path(filepath)
        if not os.path.exists(abs_path):
            return f"Error: file does not exist: {abs_path}"
        os.remove(abs_path)
        return f"File deleted successfully: {abs_path}"
    except Exception as e:
        return f"Error deleting file {filepath}: {e}"


@wrap_tool_output_optimization
def create_directory(path: str) -> str:
    """Creates a directory."""
    try:
        abs_path = _resolve_path(path)
        os.makedirs(abs_path, exist_ok=True)
        return f"Directory created successfully: {abs_path}"
    except Exception as e:
        return f"Error creating directory {path}: {e}"