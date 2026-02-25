# Agno Tool Construction Guide

When building tools for the Agno framework, follow these best practices:

1. **Docstrings are UI**: The docstrings are used by the LLM to understand how to call the tool. Be extremely specific about arguments.
2. **Type Hints**: Always use Python type hints (`str`, `int`, `List[str]`, etc.).
3. **Implicit Imports**: Ensure any library used in the tool is imported inside the tool file or at the top level of `nova/tools/`.
4. **Return Strings**: Tools should generally return strings (Success messages or Error descriptions) so the LLM can read them directly.
5. **JSON Results**: If returning complex data, return it as a JSON string.
