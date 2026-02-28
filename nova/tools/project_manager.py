"""
Project Manager Tool for Nova

This module provides tools for managing multiple project environments
using the database-backed ProjectContext.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from nova.db.engine import get_session_factory
from nova.db.deployment_models import ProjectContext
from nova.tools.context_optimizer import wrap_tool_output_optimization

logger = logging.getLogger(__name__)


@wrap_tool_output_optimization
def set_active_project(name: str) -> str:
    """
    Sets the active project in Nova's context. Only one project can be active at a time.

    Args:
        name: The exact name of the project.

    Returns:
        Status message confirming the project change.
    """
    session_factory = get_session_factory()
    session = session_factory()

    try:
        # Check if project exists
        project = (
            session.query(ProjectContext).filter(ProjectContext.name == name).first()
        )
        if not project:
            return f"❌ Project '{name}' not found. Please add it first using add_or_update_project."

        # Deactivate all others
        session.query(ProjectContext).update({ProjectContext.is_active: False})

        # Activate target
        project.is_active = True
        session.commit()

        return f"✅ Successfully set active project to '{name}' (Path: {project.absolute_path})"

    except Exception as e:
        session.rollback()
        logger.error(f"Error setting active project: {e}")
        return f"❌ Database error: {str(e)}"
    finally:
        session.close()


@wrap_tool_output_optimization
def add_or_update_project(name: str, absolute_path: str, git_remote: str = "") -> str:
    """
    Registers a new project or updates an existing one in Nova's database.

    Args:
        name: A unique identifier for the project.
        absolute_path: The absolute directory path on the server where the project lives.
        git_remote: The git origin URL for the project (optional).

    Returns:
        Confirmation message.
    """
    if not absolute_path.startswith("/"):
        return "❌ Error: absolute_path must start with '/' (be an absolute path)."

    if not os.path.exists(absolute_path):
        return f"❌ Error: the path '{absolute_path}' does not exist on the filesystem."

    session_factory = get_session_factory()
    session = session_factory()

    try:
        project = (
            session.query(ProjectContext).filter(ProjectContext.name == name).first()
        )

        if project:
            project.absolute_path = absolute_path
            if git_remote:
                project.git_remote = git_remote
            msg = f"✅ Updated existing project '{name}'."
        else:
            project = ProjectContext(
                name=name,
                absolute_path=absolute_path,
                git_remote=git_remote,
                is_active=False,
            )
            session.add(project)
            msg = f"✅ Added new project '{name}'."

        session.commit()

        # If it's the only project, make it active
        project_count = session.query(ProjectContext).count()
        if project_count == 1:
            set_active_project(name)
            msg += f" Automatically set as active project."

        return msg

    except Exception as e:
        session.rollback()
        logger.error(f"Error adding project: {e}")
        return f"❌ Database error: {str(e)}"
    finally:
        session.close()


@wrap_tool_output_optimization
def get_active_project() -> str:
    """
    Returns the currently active project context as a JSON string.
    Use this to determine the current working directory for file and git operations.

    Returns:
        JSON string containing active project details, or null if no active project.
    """
    session_factory = get_session_factory()
    session = session_factory()

    try:
        project = session.query(ProjectContext).filter(ProjectContext.is_active).first()

        if not project:
            return json.dumps({"status": "error", "message": "No active project set."})

        return json.dumps(
            {
                "status": "success",
                "id": project.id,
                "name": project.name,
                "absolute_path": project.absolute_path,
                "git_remote": project.git_remote,
                "metadata": json.loads(project.metadata_json)
                if project.metadata_json
                else {},
            }
        )

    except Exception as e:
        logger.error(f"Error getting active project: {e}")
        return json.dumps({"status": "error", "message": str(e)})
    finally:
        session.close()


@wrap_tool_output_optimization
def list_projects() -> str:
    """
    Lists all registered projects in the database.

    Returns:
        Formatted string listing all projects and identifying the active one.
    """
    session_factory = get_session_factory()
    session = session_factory()

    try:
        projects = session.query(ProjectContext).all()

        if not projects:
            return "No projects registered. Use add_or_update_project to add one."

        lines = ["📚 **Registered Projects:**"]
        for p in projects:
            active_mark = "🌟 [ACTIVE]" if p.is_active else "  "
            lines.append(f"{active_mark} **{p.name}** -> `{p.absolute_path}`")
            if p.git_remote:
                lines.append(f"     Git: {p.git_remote}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        return f"❌ Database error: {str(e)}"
    finally:
        session.close()
