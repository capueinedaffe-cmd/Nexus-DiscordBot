import os
import asyncpg
from typing import Optional, List, Any

DATABASE_URL = os.environ.get("DATABASE_URL")

async def get_db_connection():
    """Crea y devuelve una conexión asíncrona a la BD."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada.")
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    """Crea la tabla 'characters' si no existe."""
    conn = await get_db_connection()
    try:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS characters (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                is_npc BOOLEAN NOT NULL DEFAULT FALSE,
                level INTEGER NOT NULL DEFAULT 1,
                vit_max INTEGER NOT NULL,
                mana_max INTEGER NOT NULL,
                fue INTEGER NOT NULL,
                res INTEGER NOT NULL,
                agi INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_id, name)
            )
        ''')
    finally:
        await conn.close()