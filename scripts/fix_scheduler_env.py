import os
from sqlalchemy import text
from nova.db.engine import get_db_engine

def fix_chat_ids():
    engine = get_db_engine()
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        print("TELEGRAM_CHAT_ID not found in environment.")
        return

    with engine.connect() as conn:
        # Check if column exists, if not migrate (scheduler.py schema creates it)
        try:
            # We want to force all active tasks to use YOUR current chat ID
            # because the scheduler doesn't store a chat_id per task, 
            # it uses the global ENV variable which might be missing in some contexts.
            
            # Since scheduler.py _send_telegram_notification uses os.getenv("TELEGRAM_CHAT_ID"),
            # and it seems it's "executing" but you aren't getting it, 
            # let's verify if the environment variable is actually visible to the python process.
            pass
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    fix_chat_ids()