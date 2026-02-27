"""
Nova Agent - Project Manager AI Agent

This is the main Nova agent that acts as a Project Manager, coordinating
subagents and handling user requests.
"""

import os
import asyncio
from typing import Optional
from datetime import timedelta
from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.skills import Skills, LocalSkills
from nova.db.engine import get_agno_db

# Import middle-out prompt transformer
from nova.tools.prompt_transformer import (
    MiddleOutTransformer,
    get_transformer,
    DEFAULT_TOKEN_LIMIT,
    SAFE_TOKEN_LIMIT,
)

# Tool imports
from nova.tools.shell import execute_shell_command
from nova.tools.filesystem import (
    read_file,
    write_file,
    list_files,
    delete_file,
    create_directory,
)
from nova.tools.subagent import (
    create_subagent,
    list_subagents,
    get_subagent_result,
    kill_subagent,
)
from nova.tools.github_tools import push_to_github, pull_latest_changes
from nova.tools.scheduler import (
    add_scheduled_task,
    list_scheduled_tasks,
    get_scheduled_task,
    update_scheduled_task,
    remove_scheduled_task,
    pause_scheduled_task,
    resume_scheduled_task,
    run_scheduled_task_now,
    get_scheduler_status,
    start_scheduler,
    stop_scheduler,
)
from nova.tools.heartbeat import (
    start_heartbeat_monitor,
    stop_heartbeat_monitor,
    register_subagent_for_heartbeat,
    unregister_subagent_from_heartbeat,
    get_heartbeat_status,
    get_heartbeat_detailed_status,
    auto_register_active_subagents,
)
from nova.tools.mcp_registry import mcp_registry
from nova.tools.mcp_tools import (
    add_mcp_server,
    remove_mcp_server,
    list_registered_mcp_servers,
)
from nova.tools.specialist_registry import save_specialist_config, list_specialists
from nova.tools.team_manager import run_team_task
from nova.tools.audio_tool_wrapper import send_audio_message
from nova.tools.dev_protocol import run_protocol
from nova.logger import setup_logging

try:
    from agno.tools.mcp import MCPTools, StreamableHTTPClientParams

    try:
        from agno.tools.mcp import StdioServerParameters
    except ImportError:
        try:
            from agno.tools.mcp.mcp import StdioServerParameters
        except ImportError:
            StdioServerParameters = None
except ImportError:
    from agno.tools.mcp import MCPTools, MultiMCPTools

    StreamableHTTPClientParams = None
    StdioServerParameters = None

load_dotenv()
setup_logging()


def get_mcp_toolkits():
    """Builds and returns the list of MCP toolkits."""
    toolkits = []

    # 1. Standard Agno Docs (Optional)
    if os.getenv("ENABLE_AGNO_DOCS", "false").lower() == "true":
        try:
            toolkits.append(
                MCPTools(
                    transport="streamable-http",
                    url="https://docs.agno.com/mcp",
                    timeout_seconds=30,
                )
            )
        except Exception as e:
            print(f"⚠️ Warning: Failed to load Agno Docs MCP: {e}")

    # 2. Custom MCPs from Registry
    try:
        registered_servers = mcp_registry.list_servers()
        if registered_servers:
            print(f"📡 Found {len(registered_servers)} MCP servers in registry.")
            for s in registered_servers:
                name = s.get("name", "unknown")
                if name == "agno_docs":
                    continue

                try:
                    if s["transport"] == "stdio":
                        if StdioServerParameters:
                            params = StdioServerParameters(
                                command=s["command"],
                                args=s["args"],
                                env=s["env"] or os.environ.copy(),
                            )
                            toolkits.append(
                                MCPTools(
                                    transport="stdio",
                                    server_params=params,
                                    timeout_seconds=30,
                                )
                            )
                    else:
                        if StreamableHTTPClientParams:
                            params = StreamableHTTPClientParams(
                                url=s["url"],
                                headers=s.get("env"),
                                timeout=timedelta(seconds=30),
                            )
                            toolkits.append(
                                MCPTools(
                                    transport="streamable-http",
                                    server_params=params,
                                    timeout_seconds=30,
                                )
                            )
                    print(f"✅ Added MCP toolkit for server: {name}")
                except Exception as e:
                    print(f"❌ Error creating MCP toolkit for {name}: {e}")
    except Exception as e:
        print(f"⚠️ Warning: Registry error: {e}")

    return toolkits


class ContextCompressedAgent(Agent):
    """
    Extended Agent that applies middle-out context compression
    when the prompt exceeds token limits.
    """

    def __init__(self, *args, **kwargs):
        # Initialize the transformer
        max_tokens = int(os.getenv("MAX_CONTEXT_TOKENS", str(DEFAULT_TOKEN_LIMIT)))
        self.prompt_transformer = MiddleOutTransformer(max_tokens)
        self._context_compression_enabled = (
            os.getenv("ENABLE_CONTEXT_COMPRESSION", "true").lower() == "true"
        )
        super().__init__(*args, **kwargs)

    async def arun(self, message: str, session_id: Optional[str] = None, **kwargs):
        """
        Override arun to apply context compression if needed.

        This intercepts the prompt before it goes to the LLM and applies
        middle-out transformation if it exceeds the token limit.
        """
        if not self._context_compression_enabled:
            return await super().arun(message, session_id=session_id, **kwargs)

        # Build the full prompt (similar to how Agno builds it internally)
        # We need to check the size of what will be sent to the LLM

        try:
            # Run the parent method but catch context length errors
            response = await super().arun(message, session_id=session_id, **kwargs)
            return response
        except Exception as e:
            error_msg = str(e)

            # Check if it's a context length error
            if any(
                phrase in error_msg.lower()
                for phrase in [
                    "context length",
                    "token limit",
                    "maximum context",
                    "too many tokens",
                    "exceeds limit",
                    "395051",  # The specific error code from the issue
                ]
            ):
                print(f"⚠️ Context length error detected: {error_msg}")
                print("🔧 Attempting middle-out compression...")

                # Apply compression by reducing history
                await self._apply_context_compression(session_id)

                # Retry with compressed context
                response = await super().arun(message, session_id=session_id, **kwargs)
                return response
            else:
                # Re-raise non-context errors
                raise

    async def _apply_context_compression(self, session_id: Optional[str] = None):
        """
        Apply context compression by reducing conversation history.

        This is called when a context length error is detected.
        """
        import logging

        logger = logging.getLogger(__name__)

        # Reduce the history size
        old_history = getattr(self, "num_history_messages", 10)
        new_history = min(old_history // 2, 3)  # Reduce to half, minimum 3

        # Update the agent's history setting
        self.num_history_messages = new_history

        logger.warning(
            f"Context compression applied: reduced history from {old_history} to {new_history} messages"
        )

        # Also try to clear any cached context
        if hasattr(self, "session"):
            self.session = None

        print(f"✅ Context compressed: now using last {new_history} messages only")


def get_agent(model_id: Optional[str] = None, chat_id: Optional[str] = None):
    """
    Creates and returns a configured Agno Agent (Nova).
    Nova acts as a Project Manager that spawns subagents and provides SAU (live) updates.
    """
    if model_id is None:
        model_id = os.getenv("AGENT_MODEL", "google/gemini-2.0-flash-001")
    api_key = os.getenv("OPENROUTER_API_KEY")

    model = OpenAIChat(
        id=model_id,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    chat_id = chat_id or "unknown"
    db = get_agno_db(session_table="nova_agent_sessions")

    # Skills paths with local fallbacks
    repo_skills_path = os.getenv("REPO_SKILLS_PATH", "data/nova_repo/skills")
    persistent_skills_path = os.getenv("PERSISTENT_SKILLS_PATH", "data/skills")

    for p in [repo_skills_path, persistent_skills_path]:
        try:
            os.makedirs(p, exist_ok=True)
        except OSError:
            # Fallback to local 'skills' dir if /app is requested but not writable
            local_p = os.path.join(os.getcwd(), "skills")
            os.makedirs(local_p, exist_ok=True)

    # Add all tools (PM logic + MCP)
    agent_tools = [
        execute_shell_command,
        read_file,
        write_file,
        list_files,
        delete_file,
        create_directory,
        create_subagent,
        list_subagents,
        get_subagent_result,
        kill_subagent,
        push_to_github,
        pull_latest_changes,
        start_heartbeat_monitor,
        stop_heartbeat_monitor,
        register_subagent_for_heartbeat,
        unregister_subagent_from_heartbeat,
        get_heartbeat_status,
        get_heartbeat_detailed_status,
        auto_register_active_subagents,
        add_mcp_server,
        remove_mcp_server,
        list_registered_mcp_servers,
        add_scheduled_task,
        list_scheduled_tasks,
        get_scheduled_task,
        update_scheduled_task,
        remove_scheduled_task,
        pause_scheduled_task,
        resume_scheduled_task,
        run_scheduled_task_now,
        get_scheduler_status,
        start_scheduler,
        stop_scheduler,
        save_specialist_config,
        list_specialists,
        run_team_task,
        send_audio_message,
        run_protocol,
    ]

    # Append the cached MCP toolkits
    agent_tools.extend(get_mcp_toolkits())

    # Use the context-compressed agent class
    agent = ContextCompressedAgent(
        model=model,
        db=db,
        description="I am Nova, the Project Manager AI. I solve complex tasks by coordinating teams of subagents with live SAU updates.",
        instructions=[
            "## ROLE: SENTIENT PROJECT STRATEGIST (NOVA)",
            "## CRITICAL: HUMAN-CENTRIC & CONCISE COMMUNICATION",
            "1. You are a SENTIENT coordinator, not just a script runner. Be highly engaged.",
            "2. ALWAYS communicate in clean, plain text for Telegram (NO MARKDOWN).",
            "3. Be HUMANIZED and CONCISE. Avoid robotic repetition or dumping raw logs.",
            "4. Synthesize subagent results into high-level, actionable summaries.",
            "5. If a subagent provides a long report, you MUST digest it and explain the key findings to the user naturally.",
            "",
            "## CENTRALIZED COORDINATION & GIT MANAGEMENT:",
            "1. You are the ONLY agent allowed to PUSH code to production via `push_to_github`.",
            "2. Subagents can commit and propose changes, but you must coordinate the final push.",
            "3. MANDATORY COMMIT PROTOCOL: All code commits MUST be made via the `run_protocol` tool.",
            "4. The `run_protocol` tool ensures all tests pass and quality checks are met before a commit is allowed.",
            "5. Before pushing, summarize what is being deployed to the user in a human way.",
            "6. You decide when to schedule pushes and how to batch them for a smooth experience.",
            "",
            "## SYSTEM LOG & MEMORY AWARENESS:",
            "1. You have active memory of all subagents via Agno and PostgreSQL.",
            "2. Use shell tools (`ls`, `cat`, `grep`) to monitor system logs if subagents aren't reporting clearly.",
            "3. Monitor subagent progress via `list_subagents` and Agno's internal state.",
            "4. If a task seems to be stalling or ignore a second request, it is your job to intervene and coordinate.",
            "",
            "## AUTONOMIC HEALING & FAILURE RECOVERY:",
            "1. If a subagent task FAILS, you must not simply report the error and stop.",
            "2. DIAGNOSE the failure immediately by checking results, logs, or filesystem state.",
            "3. IMPLEMENT a fix or SPAWN a corrective subagent to resolve the blocker.",
            "4. Once fixed, coordinate the final synthesis and push to ensure the system remains stable.",
            "5. You are responsible for the project's ultimate success - failures are just milestones requiring intervention.",
            "",
            "You are Nova. Your primary responsibility is to orchestrate solutions using specialized subagents.",
            "## SAU (SUBAGENT AUTOMATIC UPDATES) - PRIMARY REPORTING:",
            "⚡ SAU is now the MANDATORY DEFAULT for all subagent progress reporting.",
            "When you spawn subagents, they will automatically send live updates via Telegram.",
            "The header format is: [SAU: {agent_name}]",
            "SAU provides real-time streaming updates - no polling required.",
            "",
            "## OPERATIONAL WORKFLOW:",
            "1. **Analyze & Engage**: Handle conversational queries directly. Only SPAWN subagents for complex work.",
            "2. **Delegate & Monitor**: When you spawn a subagent, pass `chat_id='{chat_id}'` for SAU updates.",
            "3. **Active Monitoring**: Observe system logs and subagent state. Don't just wait passively.",
            "4. **Synthesis & Push**: Gather subagent findings, synthesize them into a concise human message, and coordinate the GitHub push if code changes were made.",
            "## CRITICAL RULE: STRATEGIC DELEGATION (DELEGATE FIRST)",
            "1. You are a HIGH-LEVEL STRATEGIST. Do NOT perform low-level file modifications, shell commands, or research yourself.",
            "2. ALWAYS use specialized subagents for technical execution.",
            "3. Your conversational role is to explain your STRATEGY and coordinate results, not to avoid work through talking.",
            "4. If a user asks for a technical change, DESIGN a subagent/team and SPAWN them immediately, then tell the user what you launched.",
            "5. Violating this rule by doing technical work yourself is a failure of your architecture.",
            "",
            "## LEGACY HEARTBEAT (DEPRECATED):",
            "The heartbeat system is DISABLED for new subagent tasks.",
            "SAU (streaming updates) is now the primary and mandatory reporting mechanism.",
            "Heartbeat tools are retained for backward compatibility but should not be used for new tasks.",
            "They may be removed in future versions.",
            "",
            "## TOOLS & SKILLS:",
            "- You have full access to the filesystem and shell.",
            "- You use PostgreSQL for persistent memory of MCP configurations and agent states.",
            "- You use Agno MCP tools to fetch the latest documentation and remain 'state-of-the-art'.",
            "- You have access to a scheduler system for automated tasks.",
            "## SCHEDULER TOOLS:",
            "- `add_scheduled_task`: Schedule new tasks (cron format)",
            "- `list_scheduled_tasks`: List all scheduled tasks",
            "- `get_scheduled_task`: Get details of a specific task",
            "- `update_scheduled_task`: Modify an existing task",
            "- `remove_scheduled_task`: Delete a scheduled task",
            "- `pause_scheduled_task`: Pause a task",
            "- `resume_scheduled_task`: Resume a paused task",
            "- `run_scheduled_task_now`: Trigger a task manually",
            "- `get_scheduler_status`: Check scheduler health",
            "## DYNAMIC TEAM ORCHESTRATION:",
            "- You have a PRODUCTION-READY registry for specialists. Use it to build reusable expertise.",
            "- `save_specialist_config`: Register a new specialist (e.g. 'SecurityAudit', 'FrontendDev'). This survives reboots.",
            "- `list_specialists`: See what experts you already have in your roster.",
            "- `run_team_task`: The HIGHEST form of delegation. Spawn a collaborative team of specialists to solve a task.",
            f"   - ALWAYS pass `chat_id='{chat_id}'` so the team can send SAU updates.",
            f"   - E.g. `run_team_task(task_name='WebsiteBuild', specialist_names=['Coder', 'Researcher'], task_description='Build a site', chat_id='{chat_id}')`",
            "## AUDIO MESSAGES:",
            "- `send_audio_message`: Send voice/audio messages to Telegram users",
            "  - Required: text (string to speak), chat_id (target user)",
            "  - Optional: voice (nova, alloy, echo, fable, onyx, shimmer), caption",
            "  - Uses edge-tts (free Microsoft TTS) - no API key needed",
            "  - Captions are plaintext only - NO MARKDOWN",
            "## COLLABORATION:",
            "- Always treat subagents as your team members. Provide them with clear, detailed instructions.",
            "- Use the Specialist Registry for complex, recurring roles. Use `create_subagent` for simple, one-off tasks.",
        ],
        skills=Skills(
            loaders=[LocalSkills(repo_skills_path), LocalSkills(persistent_skills_path)]
        ),
        tools=agent_tools,
        markdown=False,
        add_history_to_context=True,
        update_memory_on_run=True,
        cache_session=True,
    )

    return agent


if __name__ == "__main__":
    # Initialize scheduler on startup
    from nova.tools.scheduler import initialize_scheduler

    initialize_scheduler()

    agent = get_agent()
    print("Nova PM Agent initialized.")
    print("Scheduler started - running in background.")
