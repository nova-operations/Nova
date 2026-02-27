with open("nova/telegram_bot.py", "r") as f:
    text = f.read()

text = text.replace("def handle_message(", "def handle_message(")
# Need to update agent.py to accept videos and documents
