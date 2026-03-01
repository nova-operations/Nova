"""
Shared Memory Layer for Nova Agent System
==========================================

All agents (Nova, specialists, teams) must use a SINGLE shared Agno DB
backed by the same session_table. This is what enables cross-agent memory:

    - Same db + same user_id = memories written by one agent are visible to all others
    - Same session_id = conversation history flows between agents in the same call chain

Agno memory sharing pattern:
    agent_1 and agent_2 share memory when they have:
        1. The same `db` instance (or pointing to the same table)
        2. The same `user_id` passed at .arun()
        3. `update_memory_on_run=True` on each agent
"""

from nova.db.engine import get_agno_db

# The single shared session table. All agents (Nova, specialists, teams) use
# this same table. Memories are keyed by user_id and accessible across agents.
SHARED_SESSION_TABLE = "nova_shared_agent_sessions"


def get_shared_db():
    """
    Returns the shared Agno DB instance used by all agents in the system.
    This is the key to cross-agent memory sharing:
        - Nova uses this
        - Every specialist uses this
        - Every team uses this
    All memories written by any agent under the same user_id are visible to all.
    """
    return get_agno_db(session_table=SHARED_SESSION_TABLE)
