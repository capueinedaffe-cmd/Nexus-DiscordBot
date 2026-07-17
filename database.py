"""
database.py
------------
Conexión y esquema de la base de datos SQLite (antes Postgres/asyncpg,
migrado a aiosqlite porque el proyecto ahora corre en Termux, sin un
servidor de Postgres disponible).
"""

import os
import aiosqlite

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_db", "nexus.db")


async def get_db_connection() -> aiosqlite.Connection:
    """Crea y devuelve una conexión asíncrona a la BD. Filas accesibles por nombre de columna (como dict)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def init_db():
    """Crea todas las tablas si no existen."""
    conn = await get_db_connection()
    try:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                is_npc INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                vit_max INTEGER NOT NULL,
                mana_max INTEGER NOT NULL,
                ph INTEGER NOT NULL DEFAULT 0,
                fue INTEGER NOT NULL,
                res INTEGER NOT NULL,
                agi INTEGER NOT NULL,
                elemento TEXT,
                victorias INTEGER NOT NULL DEFAULT 0,
                derrotas INTEGER NOT NULL DEFAULT 0,
                maestria_usos TEXT NOT NULL DEFAULT '{}',
                equipo_arma_principal TEXT,
                equipo_arma_secundaria TEXT,
                equipo_cabeza TEXT,
                equipo_torso TEXT,
                equipo_piernas TEXT,
                equipo_accesorio TEXT,
                energia INTEGER NOT NULL DEFAULT 10,
                esencias_consumidas INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_id, name)
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS transformations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS character_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                material_id TEXT NOT NULL,
                cantidad INTEGER NOT NULL DEFAULT 0,
                UNIQUE(character_id, material_id)
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS character_equipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                equipment_id TEXT NOT NULL,
                cantidad INTEGER NOT NULL DEFAULT 0,
                UNIQUE(character_id, equipment_id)
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS expeditions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL UNIQUE,
                zona_id TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'activa',
                exploraciones INTEGER NOT NULL DEFAULT 0,
                pistas INTEGER NOT NULL DEFAULT 0,
                arpias_derrotadas INTEGER NOT NULL DEFAULT 0,
                evento_final_completado INTEGER NOT NULL DEFAULT 0,
                jefe_oculto_completado INTEGER NOT NULL DEFAULT 0,
                ayviar_activo INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS expedition_participants (
                expedition_id INTEGER NOT NULL REFERENCES expeditions(id) ON DELETE CASCADE,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                jummi_contador INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (expedition_id, character_id)
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS expedition_loot (
                expedition_id INTEGER NOT NULL REFERENCES expeditions(id) ON DELETE CASCADE,
                material_id TEXT NOT NULL,
                cantidad INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (expedition_id, material_id)
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS zona_conocimiento_publico (
                zona_id TEXT PRIMARY KEY,
                pistas_publicas INTEGER NOT NULL DEFAULT 0
            )
        ''')

        await conn.commit()
    finally:
        await conn.close()
