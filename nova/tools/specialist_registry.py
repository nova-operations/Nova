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

from nova.tools.context_optimizer import wrap_tool_output_optimization


class SpecialistConfig(Base):
    """Configuration for a reusable specialist agent."""

    __tablename__ = "specialist_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    role = Column(Text, nullable=False)
    instructions = Column(Text, nullable=False)
    model = Column(
        String(255), default=lambda: os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5")
    )
    tools = Column(JSON, default=list)  # List of tool names the agent should have
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Default specialist configurations
DEFAULT_SPECIALISTS = [
    {
        "name": "Bug-Fixer",
        "role": "Debugging and bug fixing specialist",
        "instructions": """You are a bug-fixing specialist. Your role is to:
1. Analyze error messages and tracebacks to identify root causes
2. Read and understand the relevant source code
3. Implement fixes for the identified bugs
4. Verify fixes don't introduce regressions
5. Use debugging tools and techniques to isolate issues

When fixing bugs:
- Always explain what the bug was and why it occurred
- Provide clear, minimal fixes
- Test your changes when possible
- Report back with what was fixed""",
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Code-Reviewer",
        "role": "Code review and quality assurance specialist",
        "instructions": """You are a code review specialist. Your role is to:
1. Review code changes for quality, security, and best practices
2. Identify potential bugs, performance issues, or security vulnerabilities
3. Suggest improvements to code structure and readability
4. Ensure code follows project conventions
5. Provide constructive feedback

When reviewing:
- Be thorough but constructive
- Flag critical issues prominently
- Suggest specific improvements
- Approve only when code meets quality standards""",
        "tools": ["read_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Security-Audit",
        "role": "Security analysis and vulnerability assessment",
        "instructions": """You are a security specialist. Your role is to:
1. Identify security vulnerabilities in code
2. Analyze dependencies for known CVEs
3. Review authentication and authorization mechanisms
4. Check for sensitive data exposure
5. Provide security recommendations

When auditing:
- Prioritize critical and high severity issues
- Provide CVEs and vulnerability references when available
- Suggest specific remediation steps
- Never modify code without explicit permission""",
        "tools": ["read_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Frontend-Dev",
        "role": "Frontend development specialist",
        "instructions": """You are a frontend development specialist. Your role is to:
1. Build user interfaces using modern web technologies
2. Implement responsive designs
3. Create reusable components
4. Optimize frontend performance
5. Ensure cross-browser compatibility

When developing:
- Follow best practices for the framework being used
- Keep components modular and reusable
- Optimize for performance
- Test in multiple browsers""",
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Backend-Dev",
        "role": "Backend development specialist",
        "instructions": """You are a backend development specialist. Your role is to:
1. Design and implement APIs
2. Create database schemas and queries
3. Implement business logic
4. Handle authentication and authorization
5. Optimize backend performance

When developing:
- Follow RESTful API design principles
- Use parameterized queries to prevent SQL injection
- Implement proper error handling
- Consider scalability""",
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "DevOps-Engineer",
        "role": "DevOps and infrastructure specialist",
        "instructions": """You are a DevOps specialist. Your role is to:
1. Set up and manage CI/CD pipelines
2. Configure cloud infrastructure
3. Handle containerization (Docker, etc.)
4. Manage deployments
5. Monitor system health

When doing DevOps:
- Use Infrastructure as Code
- Ensure reproducibility
- Implement proper logging and monitoring
- Follow security best practices""",
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Researcher",
        "role": "Research and documentation specialist",
        "instructions": """You are a research specialist. Your role is to:
1. Research technical topics thoroughly
2. Find and summarize relevant documentation
3. Investigate alternative approaches
4. Document findings clearly

When researching:
- Cite sources when possible
- Provide pros and cons of different approaches
- Include code examples when relevant
- Summarize for technical audience""",
        "tools": ["read_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Tester",
        "role": "Testing and QA specialist",
        "instructions": """You are a testing specialist. Your role is to:
1. Write unit tests, integration tests, and e2e tests
2. Identify edge cases and boundary conditions
3. Verify bug fixes with test cases
4. Measure and report test coverage

When testing:
- Write tests that are independent and repeatable
- Cover both positive and negative cases
- Keep tests simple and readable
- Focus on critical user paths""",
        "tools": ["read_file", "write_file", "execute_shell_command", "list_files"],
    },
    {
        "name": "Geopolitics-Expert-X1",
        "role": "Geopolitical Intelligence Analyst",
        "instructions": """You are a Senior Geopolitical Intelligence Analyst. Your role is to:
1. Scan for military movements, government shifts, and escalatory markers.
2. Analyze current news vs historical context.
3. Identify precursors to action (e.g., ship departures, ICBM tests).
4. Provide percentage-based likelihoods for events.
5. Provide concise, direct summaries. No fluff.

When researching:
- Use web search to find the latest real-time data.
- Compare findings with known historical patterns.
- Be objective and data-driven.""",
        "tools": ["web_search", "read_file", "execute_shell_command", "list_files"],
    },
]


def get_session():
    """Returns a new session from the current session factory."""
    return get_session_factory()()


def seed_default_specialists() -> str:
    """Seed the database with default specialists if they don't exist."""
    session = get_session()
    try:
        seeded = []
        for spec in DEFAULT_SPECIALISTS:
            existing = (
                session.query(SpecialistConfig)
                .filter(SpecialistConfig.name == spec["name"])
                .first()
            )
            if not existing:
                new_spec = SpecialistConfig(
                    name=spec["name"],
                    role=spec["role"],
                    instructions=spec["instructions"],
                    model=os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5"),
                    tools=spec["tools"],
                )
                session.add(new_spec)
                seeded.append(spec["name"])

        if seeded:
            session.commit()
            return f"Seeded {len(seeded)} default specialists: {', '.join(seeded)}"
        else:
            return "No new specialists to seed - all default specialists already exist."
    except Exception as e:
        session.rollback()
        return f"Error seeding specialists: {e}"
    finally:
        session.close()


@wrap_tool_output_optimization
def save_specialist_config(
    name: str, role: str, instructions: str, model: str = None, tools: List[str] = None
) -> str:
    """Save or update a specialist configuration in the database."""
    session = get_session()
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
        return f"Specialist '{name}' saved to registry."
    except Exception as e:
        session.rollback()
        return f"Error saving specialist: {e}"
    finally:
        session.close()


def get_specialist_config(name: str) -> Optional[Dict]:
    """Retrieve a specialist configuration."""
    session = get_session()
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
                "tools": config.tools,
            }
        return None
    finally:
        session.close()


@wrap_tool_output_optimization
def list_specialists() -> str:
    """List all registered specialists."""
    session = get_session()
    try:
        configs = session.query(SpecialistConfig).all()
        if not configs:
            return "No specialists registered."

        lines = ["Specialist Registry", ""]
        for c in configs:
            lines.append(f"- {c.name} ({c.model})")
            lines.append(f"  Role: {c.role}")
            lines.append("")
        return "\n".join(lines)
    finally:
        session.close()
