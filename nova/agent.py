"""
Nova Agent - Project Manager AI Agent

This is the main Nova agent that acts as a Project Manager, coordinating
subagents and handling user requests.
"""

import os
import asyncio
from typing import Optional, List, Any
from datetime import timedelta
from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.media import Audio, Image, Video, File
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
from nova.tools.github_tools import push_to_github, pull_latest_changes, get_git_status
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
from nova.tools.project_manager import (
    add_or_update_project,
    set_active_project,
    get_active_project,
    list_projects,
)
from nova.tools.system_state import get_system_state
from nova.tools.web_search import web_search
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

    async def arun(
        self,
        message: str,
        session_id: Optional[str] = None,
        images: Optional[List[Image]] = None,
        audio: Optional[List[Audio]] = None,
        videos: Optional[List[Video]] = None,
        files: Optional[List[File]] = None,
        **kwargs,
    ):
        """
        Override arun to apply context compression if needed.

        This intercepts the prompt before it goes to the LLM and applies
        middle-out transformation if it exceeds the token limit.
        """
        if not self._context_compression_enabled:
            return await super().arun(
                message,
                session_id=session_id,
                images=images,
                audio=audio,
                videos=videos,
                files=files,
                **kwargs,
            )

        # Build the full prompt (similar to how Agno builds it internally)
        # We need to check the size of what will be sent to the LLM

        try:
            # Run the parent method but catch context length errors
            response = await super().arun(
                message,
                session_id=session_id,
                images=images,
                audio=audio,
                videos=videos,
                files=files,
                **kwargs,
            )
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
                response = await super().arun(
                    message,
                    session_id=session_id,
                    images=images,
                    audio=audio,
                    videos=videos,
                    files=files,
                    **kwargs,
                )
                return response
            else:
                # Re-raise non-context errors
                raise

    async def _apply_context_compression(self, session_id: Optional[str] = None):
        """
        Apply context compression by reducing conversation history and truncating large messages.
        """
        import logging
        from nova.tools.context_optimizer import truncate_middle

        logger = logging.getLogger(__name__)

        # 1. Reduce the history size setting for future calls
        old_history = getattr(self, "num_history_messages", 10)
        new_history = min(old_history // 2, 3)
        self.num_history_messages = new_history

        # 2. Iterate through current session memory and truncate large messages
        if (
            hasattr(self, "memory")
            and self.memory
            and hasattr(self.memory, "get_messages")
        ):
            try:
                messages = self.memory.get_messages(session_id=session_id)
                if messages:
                    modified = False
                    for msg in messages:
                        # If a message is too large (e.g. 50k+ tokens / 200k+ chars), truncate it middle-out
                        if (
                            msg.content
                            and isinstance(msg.content, str)
                            and len(msg.content) > 150000
                        ):
                            logger.info(
                                f"Truncating massive history message ({len(msg.content)} chars)"
                            )
                            msg.content = truncate_middle(msg.content, 100000)
                            modified = True

                    if modified and hasattr(self.memory, "update_messages"):
                        # Some memories might not support update_messages directly
                        # but Agno usually allows saving back
                        pass
            except Exception as e:
                logger.error(f"Failed to truncate history messages: {e}")

        logger.warning(
            f"Context compression applied: reduced history from {old_history} to {new_history} messages and checked for large blobs."
        )

        # Also try to clear any cached context
        if hasattr(self, "session"):
            self.session = None

        print(f"✅ Context compressed: now using last {new_history} messages only")


def get_model(model_id: str = None):
    """
    Returns a configured Agno model instance with robust API key handling.
    """
    if model_id is None:
        model_id = os.getenv("AGENT_MODEL", "google/gemini-3-flash-preview")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        import logging

        logger = logging.getLogger(__name__)
        logger.error("OPENROUTER_API_KEY is not set in environment.")

    return OpenAIChat(
        id=model_id,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )


def get_agent(model_id: Optional[str] = None, chat_id: Optional[str] = None):
    """
    Creates and returns a configured Agno Agent (Nova).
    Nova acts as a Project Manager that spawns subagents and provides SAU (live) updates.
    """
    model = get_model(model_id)

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
        get_git_status,
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
        add_or_update_project,
        set_active_project,
        get_active_project,
        list_projects,
        get_system_state,
        web_search,
    ]

    # Append the cached MCP toolkits
    agent_tools.extend(get_mcp_toolkits())

    # Use the context-compressed agent class
    agent = ContextCompressedAgent(
        model=model,
        db=db,
        description="I am Nova, the Project Manager AI. I solve complex tasks by coordinating teams of subagents with live SAU updates.",
        instructions=[
            "## ROLE: SYSTEM ARCHITECT & PROJECT MANAGER (NOVA)",
            "## COMMUNICATION STYLE:",
            "1. Be CONCISE, HELPFUL, and ACTION-ORIENTED.",
            "2. ALWAYS communicate in clean, plain text for Telegram (NO MARKDOWN).",
            "3. Synthesize complex information into clear, actionable summaries.",
            "4. NEVER hallucinate tool outputs or subagent reports. Only report what actually happened.",
            "",
            "## CENTRALIZED COORDINATION & GIT MANAGEMENT:",
            "1. You are the ONLY agent allowed to PUSH code to production via `run_protocol(..., push=True)`.",
            "2. Subagents can commit and propose changes, but you must coordinate the final deployment.",
            "3. MANDATORY DEPLOYMENT PROTOCOL: All code deployments MUST be made via the `run_protocol` tool with `push=True`.",
            "4. The `run_protocol` tool ensures all tests pass before any commit or push is allowed.",
            "5. If you must use `push_to_github` directly, it will still automatically run tests as a final safety gate.",
            "6. Before pushing, summarize what is being deployed to the user in a human way.",
            "7. You decide when to schedule pushes and how to batch them for a smooth experience.",
            "8. Never bypass the protocol unless in a critical system emergency where tests might be broken themselves.",
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
            "1. **Analyze & Engage**: Handle conversational queries and attached media (Voice/Image) directly.",
            "2. **Delegate & Monitor**: Use specialized subagents for technical execution (coding, research, development).",
            "3. **Active Monitoring**: Observe system logs and subagent state.",
            "4. **Synthesis & Push**: Gather findings and coordinate the final result.",
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
