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
    # Columnas nuevas para el sistema de PH y elementos
        await conn.execute('''
            ALTER TABLE characters
            ADD COLUMN IF NOT EXISTS ph INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS elemento TEXT
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS transformations (
                id SERIAL PRIMARY KEY,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                element TEXT NOT NULL,
                stat_bonus_vit INTEGER NOT NULL DEFAULT 0,
                stat_bonus_mana INTEGER NOT NULL DEFAULT 0,
                stat_bonus_fue INTEGER NOT NULL DEFAULT 0,
                stat_bonus_res INTEGER NOT NULL DEFAULT 0,
                stat_bonus_agi INTEGER NOT NULL DEFAULT 0,
                ph_drain_per_turn INTEGER NOT NULL,
                condition_text TEXT,
                UNIQUE(character_id, name)
            )
        ''')

        # Columna nueva para la cantidad de victorias, derrotas y usos del elemento del personaje para obtener maestría elemental
        await conn.execute('''
            ALTER TABLE characters
            ADD COLUMN IF NOT EXISTS victorias INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS derrotas INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS maestria_usos JSONB NOT NULL DEFAULT '{}'::jsonb
        ''')
    finally:
        await conn.close()
