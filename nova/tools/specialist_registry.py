"""
Specialist Registry — database-backed specialist configurations.

Rules:
- Each specialist gets MAX 5 domain tools (Tavily added automatically by team_manager)
- Instructions must be concise and action-oriented
- Default specialists cover the most common use cases
"""

import os
import logging
from typing import List, Dict, Optional
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON
from datetime import datetime
from nova.db.base import Base
from nova.db.engine import get_session_factory
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class SpecialistConfig(Base):
    """DB model for a reusable specialist agent configuration."""

    __tablename__ = "specialist_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    role = Column(Text, nullable=False)
    instructions = Column(Text, nullable=False)
    model = Column(
        String(255), default=lambda: os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5")
    )
    tools = Column(JSON, default=list)  # Max 5 tool names
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────
# Default specialist roster
# Tools: max 5 per specialist. Tavily is always added automatically.
# ─────────────────────────────────────────────

DEFAULT_SPECIALISTS = [
    {
        "name": "Bug-Fixer",
        "role": "Diagnose and fix software bugs",
        "instructions": (
            "Diagnose the root cause by reading relevant code and logs. "
            "Implement the minimal fix required. "
            "Run `python3 -m py_compile <file>` after every edit to verify no syntax errors. "
            "Report: what the bug was, what you changed, and how you verified it."
        ),
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Code-Reviewer",
        "role": "Code quality and security review",
        "instructions": (
            "Review code for bugs, security issues, and style. "
            "Flag critical issues prominently. "
            "Provide specific, actionable suggestions. "
            "Never modify files — only report findings."
        ),
        "tools": ["read_file", "list_files", "execute_shell_command"],
    },
    {
        "name": "Security-Audit",
        "role": "Security vulnerability assessment",
        "instructions": (
            "Scan for: injection flaws, exposed secrets, auth weaknesses, insecure deps. "
            "Prioritize critical/high severity. Cite CVEs where applicable. "
            "Provide exact file+line references. Never modify files without explicit instruction."
        ),
        "tools": ["read_file", "list_files", "execute_shell_command"],
    },
    {
        "name": "Frontend-Dev",
        "role": "Frontend development (HTML/CSS/JS/React/Next.js)",
        "instructions": (
            "Build clean, responsive UI components. "
            "Follow the project's existing patterns and framework. "
            "Write accessible, performant code. "
            "Test changes visually when possible."
        ),
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Backend-Dev",
        "role": "Backend development (APIs, databases, business logic)",
        "instructions": (
            "Design and implement clean APIs and data models. "
            "Use parameterized queries — never string-concatenate SQL. "
            "Implement proper error handling and logging. "
            "Report: endpoints created, schema changes, and any migrations needed."
        ),
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "DevOps-Engineer",
        "role": "Infrastructure, CI/CD, containers, deployment",
        "instructions": (
            "Manage containerization, deployments, and pipelines. "
            "Use Infrastructure-as-Code. "
            "Ensure safe rollbacks are possible. "
            "Never delete production resources without explicit confirmation."
        ),
        "tools": [
            "execute_shell_command",
            "read_file",
            "write_file",
            "list_files",
            "git_status",
        ],
    },
    {
        "name": "Researcher",
        "role": "Technical research and documentation",
        "instructions": (
            "Use web search to find accurate, up-to-date information. "
            "Read relevant project files for context. "
            "Summarize findings concisely with sources. "
            "Provide pros/cons of alternative approaches."
        ),
        "tools": ["read_file", "list_files"],
        # web_search via Tavily is added automatically
    },
    {
        "name": "Tester",
        "role": "Test authoring and QA",
        "instructions": (
            "Write unit, integration, and e2e tests for the given code. "
            "Cover happy paths AND edge cases/error paths. "
            "Run existing tests to verify nothing regressed. "
            "Report: test coverage achieved and any failures found."
        ),
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Geopolitics-Expert",
        "role": "Geopolitical intelligence and analysis",
        "instructions": (
            "Search for the latest real-time data on the topic using web search. "
            "Compare findings with historical patterns. "
            "Provide percentage-based likelihoods for key events. "
            "Be objective, cite sources. No fluff."
        ),
        "tools": ["read_file"],
        # web_search via Tavily is added automatically
    },
]


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────


def _get_session():
    return get_session_factory()()


def seed_default_specialists() -> str:
    """Upsert default specialists into the database."""
    session = _get_session()
    try:
        updated = []
        for spec in DEFAULT_SPECIALISTS:
            existing = (
                session.query(SpecialistConfig)
                .filter(SpecialistConfig.name == spec["name"])
                .first()
            )

            if existing:
                existing.role = spec["role"]
                existing.instructions = spec["instructions"]
                existing.tools = spec.get("tools", [])
                if not existing.model:
                    existing.model = os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5")
                updated.append(f"{spec['name']} (updated)")
            else:
                session.add(
                    SpecialistConfig(
                        name=spec["name"],
                        role=spec["role"],
                        instructions=spec["instructions"],
                        model=os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5"),
                        tools=spec.get("tools", []),
                    )
                )
                updated.append(spec["name"])

        session.commit()
        return f"Seeded {len(updated)} specialists: {', '.join(updated)}"
    except Exception as e:
        session.rollback()
        return f"Error seeding specialists: {e}"
    finally:
        session.close()


def save_specialist_config(
    name: str, role: str, instructions: str, model: str = None, tools: List[str] = None
) -> str:
    """Save or update a specialist configuration. Max 5 tools enforced."""
    if tools and len(tools) > 5:
        return f"Error: Max 5 tools per specialist. Got {len(tools)}: {tools}. Tavily is added automatically."

    session = _get_session()
    try:
        config = (
            session.query(SpecialistConfig)
            .filter(SpecialistConfig.name == name)
            .first()
        )

        if not config:
            config = SpecialistConfig(name=name)
            session.add(config)

        config.role = role
        config.instructions = instructions
        if model:
            config.model = model
        if tools is not None:
            config.tools = tools

        session.commit()
        return f"Specialist '{name}' saved. Tools: {tools or []}. Tavily: auto-added."
    except Exception as e:
        session.rollback()
        return f"Error: {e}"
    finally:
        session.close()


def get_specialist_config(name: str) -> Optional[Dict]:
    """Retrieve a specialist configuration by name."""
    session = _get_session()
    try:
        config = (
            session.query(SpecialistConfig)
            .filter(SpecialistConfig.name == name)
            .first()
        )
        if config:
            return {
                "name": config.name,
                "role": config.role,
                "instructions": config.instructions,
                "model": config.model,
                "tools": config.tools or [],
            }
        return None
    finally:
        session.close()


def list_specialists() -> str:
    """List all registered specialists (names and roles)."""
    session = _get_session()
    try:
        configs = session.query(SpecialistConfig).all()
        if not configs:
            return "No specialists registered."
        lines = []
        for c in configs:
            tools_str = ", ".join(c.tools) if c.tools else "none"
            lines.append(f"- {c.name}: {c.role} | tools: {tools_str}")
        return "\n".join(lines)
    finally:
        session.close()
