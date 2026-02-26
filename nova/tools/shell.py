import subprocess
import asyncio
import os
import logging
from typing import Optional
from nova.tools.streaming_utils import (
    send_streaming_progress,
    _get_telegram_bot,
    strip_all_formatting,
)

logger = logging.getLogger(__name__)


async def _stream_shell_output(
    command: str,
    chat_id: Optional[str] = None,
    subagent_name: str = "Shell"
) -> str:
    """
    Execute a shell command and stream each line of output as a separate Telegram message.
    This provides real-time feedback to the user.
    """
    if chat_id is None:
        chat_id = os.getenv("DEFAULT_TELEGRAM_CHAT_ID")
    
    try:
        # Run command in a restricted environment
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )
        
        stdout_lines = []
        stderr_lines = []
        
        # Read stdout line by line and stream each
        for line in iter(process.stdout.readline, ''):
            if line:
                line = line.rstrip()
                stdout_lines.append(line)
                # Send each line immediately via SAU
                if chat_id and line.strip():
                    await send_streaming_progress(
                        chat_id=chat_id,
                        name=subagent_name,
                        progress=f"[stdout] {line}"
                    )
        
        process.stdout.close()
        process.wait()
        
        # Read any remaining stderr
        stderr_output = process.stderr.read()
        if stderr_output:
            for line in stderr_output.splitlines():
                line = line.strip()
                if line:
                    stderr_lines.append(line)
                    if chat_id and line:
                        await send_streaming_progress(
                            chat_id=chat_id,
                            name=subagent_name,
                            progress=f"[stderr] {line}"
                        )
        
        process.stderr.close()
        
        returncode = process.returncode
        
        if returncode == 0:
            result = "\n".join(stdout_lines)
            return result if result else "Command completed successfully (no output)."
        else:
            error_msg = "\n".join(stderr_lines) if stderr_lines else f"Error (code {returncode})"
            return f"Error (code {returncode}): {error_msg}"
            
    except Exception as e:
        error_msg = f"Error executing command: {e}"
        if chat_id:
            await send_streaming_progress(
                chat_id=chat_id,
                name=subagent_name,
                progress=error_msg
            )
        return error_msg


def execute_shell_command(
    command: str,
    chat_id: Optional[str] = None,
    subagent_name: str = "Shell"
) -> str:
    """
    Executes a shell command and returns the output.
    For backwards compatibility, returns the full output.
    
    For streaming mode (new behavior), the command output is sent line-by-line
    to Telegram via SAU if chat_id is provided.
    """
    # Check if we're in an async context that can use streaming
    try:
        loop = asyncio.get_running_loop()
        # We have an event loop - try async execution
        # Create a task and run it synchronously (blocking wait)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                _stream_shell_output(command, chat_id, subagent_name)
            )
            return future.result()
    except RuntimeError:
        # No event loop - use sync version
        return _execute_shell_command_sync(command)


def _execute_shell_command_sync(command: str) -> str:
    """Synchronous shell execution for when async is not available."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
        else:
            return f"Error (code {result.returncode}): {result.stderr}"
    except Exception as e:
        return f"Error executing command: {e}"