import sqlite3
import json
import os
from typing import Dict, List, Optional

DB_PATH = "/app/data/nova_memory.db"

class PersistentMemory:
    def __init__(self):
        # Initialize the database and table for MCP servers
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        self._initialize_db()

    def _initialize_db(self):
        """Create the necessary tables if they don't exist."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS mcp_servers (
                name TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                args TEXT,
                env TEXT
            )
        ''')
        # Table for general persistent memory/preferences
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        self.conn.commit()

    def save_mcp_server(self, name: str, command: str, args: List[str] = None, env: Dict[str, str] = None):
        """Save an MCP server configuration to the database."""
        args_json = json.dumps(args) if args else None
        env_json = json.dumps(env) if env else None
        
        self.cursor.execute('''
            INSERT OR REPLACE INTO mcp_servers (name, command, args, env)
            VALUES (?, ?, ?, ?)
        ''', (name, command, args_json, env_json))
        self.conn.commit()
        return f"Server '{name}' saved to persistent database."

    def get_all_mcp_servers(self) -> List[Dict]:
        """Retrieve all saved MCP server configurations."""
        self.cursor.execute('SELECT name, command, args, env FROM mcp_servers')
        rows = self.cursor.fetchall()
        servers = []
        for row in rows:
            servers.append({
                "name": row[0],
                "command": row[1],
                "args": json.loads(row[2]) if row[2] else [],
                "env": json.loads(row[3]) if row[3] else {}
            })
        return servers

    def store_memory(self, key: str, value: Any):
        """Store any key-value pair persistently."""
        val_json = json.dumps(value)
        self.cursor.execute('INSERT OR REPLACE INTO memory (key, value) VALUES (?, ?)', (key, val_json))
        self.conn.commit()

    def get_memory(self, key: str) -> Optional[Any]:
        """Retrieve a value from persistent memory."""
        self.cursor.execute('SELECT value FROM memory WHERE key = ?', (key,))
        row = self.cursor.fetchone()
        return json.loads(row[0]) if row else None

# Global instance for shared use
nova_db = PersistentMemory()