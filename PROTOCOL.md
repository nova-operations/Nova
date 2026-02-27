# Nova Self-Development Protocol

This protocol ensures that Nova maintains high code quality and reliability through disciplined development practices.

## Mandatory Workflow

1.  **Identify Objective**: Clearly define the feature to add or the bug to fix.
2.  **Test-First Implementation (TDD)**:
    *   Write a new test case in the `tests/` directory that describes the desired behavior.
    *   Run the test and verify it fails (Red).
3.  **Code Development**:
    *   Implement the minimum necessary code to make the test pass.
    *   Run the test and verify it passes (Green).
4.  **Refactor and Verify**:
    *   Refactor the code for clarity and maintainability.
    *   Run the **full test suite** to ensure no regressions were introduced.
5.  **Quality Check**:
    *   Check for linting warnings and type errors.
    *   Address any critical warnings.
6.  **Secure Commit**:
    *   Use the `dev_protocol` tool to commit changes.
    *   This tool will automatically re-run tests and block the commit if any checks fail.

## Using the `dev_protocol` Tool

Nova should use the `dev_protocol.py` tool for all commits.

```python
# Example usage within Nova
from nova.tools.dev_protocol import run_protocol

result = run_protocol(
    commit_message="feat: add advanced task prioritization",
    run_full_suite=True
)
```

## Push Hand-off
Once the commit is successful, the `dev_protocol` tool will hand off the changes to Nova's internal deployment/push mechanism if configured.
