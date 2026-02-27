import re

with open("tests/test_bot_handlers.py", "r") as f:
    content = f.read()

# I want to update the tests to remove handle_multimodal since it's deleted and replace it with handle_message testing directly.
