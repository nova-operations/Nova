import subprocess
import os
import sys
from typing import Optional
from nova.tools.context_optimizer import wrap_tool_output_optimization


@wrap_tool_output_optimization
def run_protocol(
    commit_message: str, run_full_suite: bool = True, push: bool = False
) -> str:
    """
    Nova Self-Development Protocol Tool.
    Runs tests and commits changes only if tests pass.

    Args:
        commit_message: The git commit message.
        run_full_suite: Whether to run the full test suite (pytest).
        push: Whether to push the changes after successful commit.

    Returns:
        A report of the protocol execution.
    """
    report = ["### Nova Dev Protocol Report"]

    # 1. Run tests
    if run_full_suite:
        report.append("- Running full test suite...")
        try:
            # Use the current python executable to run pytest
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-v"],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                report.append("  ✅ Tests passed.")
            else:
                report.append("  ❌ Tests failed!")
                report.append("```")
                report.append(result.stdout)
                report.append(result.stderr)
                report.append("```")
                return (
                    "\n".join(report)
                    + "\n\n**PROTOCOL REJECTED: Tests must pass before commit.**"
                )
        except Exception as e:
            return f"Error running tests: {str(e)}"

    # 2. Add changes
    report.append("- Adding changes to git...")
    try:
        subprocess.run(["git", "add", "."], check=True)
        report.append("  ✅ Changes staged.")
    except Exception as e:
        return f"Error staging changes: {str(e)}"

    # 3. Commit
    report.append(f"- Committing with message: '{commit_message}'")
    try:
        # Check if there are changes to commit
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        )
        if not status.stdout.strip():
            report.append("  ⚠️ No changes to commit.")
            return "\n".join(report)

        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True,
            text=True,
            check=False,
        )

        if commit_result.returncode == 0:
            report.append("  ✅ Commit successful.")
        else:
            report.append("  ❌ Commit failed!")
            report.append("```")
            report.append(commit_result.stdout)
            report.append(commit_result.stderr)
            report.append("```")
            return "\n".join(report)
    except Exception as e:
        return f"Error committing: {str(e)}"

    report.append("\n**PROTOCOL COMPLETED SUCCESSFULLY.**")

    if push:
        report.append("- Pushing changes to GitHub...")
        from nova.tools.github_tools import push_to_github

        # We skip_tests here because they already passed in step 1
        push_report = push_to_github(commit_message=commit_message, skip_tests=True)
        report.append(f"  {push_report}")
    else:
        report.append("Handing off to Nova for push/deployment management.")

    return "\n".join(report)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python dev_protocol.py <commit_message>")
        sys.exit(1)

    msg = sys.argv[1]
    print(run_protocol(msg))
