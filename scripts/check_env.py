import os
import subprocess
import sys

def check_env():
    print("Checking environment...")
    keys = ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY"]
    for k in keys:
        val = os.getenv(k)
        if val:
            print(f"✅ {k} is set (length: {len(val)})")
        else:
            print(f"❌ {k} is missing")

if __name__ == "__main__":
    check_env()