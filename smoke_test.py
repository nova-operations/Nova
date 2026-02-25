import asyncio
import os
from nova.agent import get_agent

async def run_smoke_test():
    print("🚀 Starting Nova Smoke Test...")
    
    # 1. Test Agent Initialization
    print("📋 Testing Agent Initialization...")
    try:
        agent = get_agent()
        print("✅ Agent initialized successfully.")
    except Exception as e:
        print(f"❌ Agent initialization failed: {e}")
        return False

    # 2. Test a simple response (Mocking LLM or using small model if possible)
    # Since we use OpenRouter, we need a valid key for a real smoke test.
    # If no key, we only test initialization.
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("⚠️ OPENROUTER_API_KEY not set. Skipping response test.")
        print("✅ Smoke test passed (Initialization only).")
        return True

    print("🧠 Testing Agent Response...")
    try:
        # We use a very simple prompt to minimize cost/time
        response = await agent.arun("Hello, are you working?")
        if response and response.content:
            print(f"✅ Agent Response: {response.content[:50]}...")
            print("✅ Smoke test passed.")
            return True
        else:
            print("❌ Agent returned empty response.")
            return False
    except Exception as e:
        print(f"❌ Agent response failed: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(run_smoke_test())
    if not success:
        exit(1)
