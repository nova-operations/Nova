import subprocess


def execute_shell_command(command: str) -> str:
    """
    Executes a shell command and returns the output.
    """
    try:
        # Run command in a restricted environment if possible, but for now we trust the agent.
        # Capturing stdout and stderr.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,  # Don't raise exception, return stderr
        )
        if result.returncode == 0:
            return result.stdout
        else:
            return f"Error (code {result.returncode}): {result.stderr}"
    except Exception as e:
        return f"Error executing command: {e}"
