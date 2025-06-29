import aiosqlite
from pathlib import Path

DB_FILE = Path("db/messages.sqlite")
DB_FILE.parent.mkdir(exist_ok=True)

async def get_db():
    db = await aiosqlite.connect(DB_FILE)
    db.row_factory = aiosqlite.Row
    return db

async def create_table():
    db = await get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            from_agent TEXT,
            to_agent TEXT,
            content TEXT,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            done INTEGER DEFAULT 0
        )
    """)
    await db.commit()
    await db.close()