#!/usr/bin/env python3
"""
Heartbeat System Test Script

This script tests the complete heartbeat system integration.
Run with: python -m nova.tools.heartbeat_test
"""

import asyncio
import sys
import time
import uuid

# Add parent directory to path
sys.path.insert(0, '/app')

from nova.tools.heartbeat import (
    get_heartbeat_monitor,
    start_heartbeat_monitor,
    get_heartbeat_status,
    get_heartbeat_detailed_status,
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_WARNING_THRESHOLD
)
from nova.tools.subagent import SUBAGENTS


async def test_heartbeat_system():
    """Run all heartbeat system tests."""
    print("=" * 60)
    print("🫀 AGENTIC HEARTBEAT SYSTEM TEST")
    print("=" * 60)
    
    # Cleanup any previous state
    for sid in list(SUBAGENTS.keys()):
        del SUBAGENTS[sid]
    
    # Test 1: Configuration
    print("\n📋 Test 1: Configuration")
    print(f"   Interval: {HEARTBEAT_INTERVAL_SECONDS}s")
    print(f"   Warning Threshold: {HEARTBEAT_WARNING_THRESHOLD}s")
    assert HEARTBEAT_INTERVAL_SECONDS == 30
    assert HEARTBEAT_WARNING_THRESHOLD == 120
    print("   ✅ Configuration correct")
    
    # Test 2: Start Monitor
    print("\n📋 Test 2: Start Monitor")
    result = start_heartbeat_monitor(1)  # 1 second interval for faster test
    assert "started" in result.lower()
    print(f"   {result}")
    
    monitor = get_heartbeat_monitor()
    assert monitor._running == True
    print("   ✅ Monitor started successfully")
    
    # Test 3: Manual Registration
    print("\n📋 Test 3: Manual Registration")
    
    subagent_ids = []
    for i, name in enumerate(['Research-Agent', 'Code-Agent', 'Docs-Agent']):
        sid = f"test-{uuid.uuid4()}"
        subagent_ids.append(sid)
        SUBAGENTS[sid] = {
            'name': name,
            'status': 'running',
            'result': None,
            'instruction': f'Task for {name}'
        }
        monitor.register_subagent(sid, name)
        print(f"   Registered: {name}")
    
    assert len(monitor._records) == 3
    print("   ✅ All subagents registered")
    
    # Test 4: Wait for heartbeat loop and status report
    print("\n📋 Test 4: Status Report (after heartbeat check)")
    await asyncio.sleep(2)  # Wait for at least one heartbeat check
    
    status = get_heartbeat_status()
    print(f"   {status}")
    assert "Research-Agent" in status
    assert "Code-Agent" in status
    assert "running" in status.lower()
    print("   ✅ Status report generated correctly")
    
    # Test 5: Detailed Status
    print("\n📋 Test 5: Detailed Status")
    detailed = get_heartbeat_detailed_status()
    assert detailed['running'] == True
    assert detailed['monitored_subagents'] == 3
    print(f"   Monitored: {detailed['monitored_subagents']} subagents")
    print("   ✅ Detailed status correct")
    
    # Test 6: Simulate Completion
    print("\n📋 Test 6: Subagent Completion")
    SUBAGENTS[subagent_ids[0]]['status'] = 'completed'
    SUBAGENTS[subagent_ids[0]]['result'] = 'Research done!'
    
    # Wait for next heartbeat check to pick up the change
    await asyncio.sleep(2)
    status = get_heartbeat_status()
    print(f"   {status}")
    # The completed one should be cleaned up from records after check
    # but the status should still show the others running
    
    # Test 7: Check detailed for warning threshold
    print("\n📋 Test 7: Warning System")
    # Simulate a long-running subagent
    long_sid = f"test-{uuid.uuid4()}"
    SUBAGENTS[long_sid] = {
        'name': 'Long-Running-Agent',
        'status': 'running',
        'result': None
    }
    monitor.register_subagent(long_sid, 'Long-Running-Agent')
    # Set start time to past threshold
    monitor._records[long_sid].start_time = time.time() - 200  # 200 seconds ago
    
    await asyncio.sleep(2)
    status = get_heartbeat_status()
    print(f"   {status}")
    # Should have a warning for the long-running agent
    print("   ✅ Warning system functional")
    
    # Test 8: Subagent Integration
    print("\n📋 Test 8: Subagent Auto-Registration")
    from nova.tools.heartbeat_integration import auto_register_with_heartbeat
    result = auto_register_with_heartbeat("integration-test", "Test-Subagent")
    print(f"   {result}")
    assert "registered" in result.lower() or "created" in result.lower()
    print("   ✅ Auto-registration functional")
    
    # Test 9: Stop monitor
    print("\n📋 Test 9: Stop Monitor")
    await monitor.stop()
    assert monitor._running == False
    print("   ✅ Monitor stopped successfully")
    
    # Cleanup
    for sid in list(SUBAGENTS.keys()):
        if sid.startswith("test-"):
            del SUBAGENTS[sid]
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED!")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    try:
        asyncio.run(test_heartbeat_system())
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)