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

        # Columna nueva para equipamento e ID de personajes
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS character_materials (
                id SERIAL PRIMARY KEY,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                material_id TEXT NOT NULL,
                cantidad INTEGER NOT NULL DEFAULT 0,
                UNIQUE(character_id, material_id)
            )
        ''')

        # Columna para forjar equipamento a partir de objetos 
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS character_equipment (
                id SERIAL PRIMARY KEY,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                equipment_id TEXT NOT NULL,
                cantidad INTEGER NOT NULL DEFAULT 0,
                UNIQUE(character_id, equipment_id)
            )
        ''')
        
        # Columna para equipamento
        # Nota: "equipo_arma" (una sola casilla de arma) se reemplazó por dos
        # casillas para poder soportar doble empuñadura (dos armas de una mano).
        await conn.execute('''
            ALTER TABLE characters
            ADD COLUMN IF NOT EXISTS equipo_arma_principal TEXT,
            ADD COLUMN IF NOT EXISTS equipo_arma_secundaria TEXT,
            ADD COLUMN IF NOT EXISTS equipo_cabeza TEXT,
            ADD COLUMN IF NOT EXISTS equipo_torso TEXT,
            ADD COLUMN IF NOT EXISTS equipo_piernas TEXT,
            ADD COLUMN IF NOT EXISTS equipo_accesorio TEXT
        ''')

        # Migración: si la BD todavía tiene la columna vieja "equipo_arma" de
        # una sola casilla, movemos ese valor a "equipo_arma_principal" antes
        # de borrarla, para no perder el equipamento ya asignado.
        vieja_columna = await conn.fetchval('''
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'characters' AND column_name = 'equipo_arma'
        ''')
        if vieja_columna:
            await conn.execute('''
                UPDATE characters
                SET equipo_arma_principal = equipo_arma
                WHERE equipo_arma_principal IS NULL AND equipo_arma IS NOT NULL
            ''')
            await conn.execute('ALTER TABLE characters DROP COLUMN equipo_arma')

        # ── Sistema de expedición ────────────────────────────────
        # Energía por personaje. Máximo fijo 10 (se controla en el código,
        # no acá). No se resetea sola al salir de una expedición.
        await conn.execute('''
            ALTER TABLE characters
            ADD COLUMN IF NOT EXISTS energia INTEGER NOT NULL DEFAULT 10
        ''')

        # Una expedición = un hilo de Discord. thread_id es único porque
        # solo puede haber una expedición activa por hilo.
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS expeditions (
                id SERIAL PRIMARY KEY,
                thread_id BIGINT NOT NULL UNIQUE,
                zona_id TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'activa',
                exploraciones INTEGER NOT NULL DEFAULT 0,
                pistas INTEGER NOT NULL DEFAULT 0,
                arpias_derrotadas INTEGER NOT NULL DEFAULT 0,
                evento_final_completado BOOLEAN NOT NULL DEFAULT FALSE,
                jefe_oculto_completado BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            )
        ''')

        # Quiénes participan (para repartir la copia del loot al terminar).
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS expedition_participants (
                expedition_id INTEGER NOT NULL REFERENCES expeditions(id) ON DELETE CASCADE,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                PRIMARY KEY (expedition_id, character_id)
            )
        ''')

        # Inventario temporal de la expedición (no es de ningún personaje
        # todavía). Se copia entero a cada participante solo si hay éxito.
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS expedition_loot (
                expedition_id INTEGER NOT NULL REFERENCES expeditions(id) ON DELETE CASCADE,
                material_id TEXT NOT NULL,
                cantidad INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (expedition_id, material_id)
            )
        ''')

        # Conocimiento público por zona (sistema de pistas compartidas):
        # si el líder hace público el descubrimiento, futuras expediciones
        # a esa zona arrancan con este contador de pistas ya puesto.
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS zona_conocimiento_publico (
                zona_id TEXT PRIMARY KEY,
                pistas_publicas INTEGER NOT NULL DEFAULT 0
            )
        ''')

    finally:
        await conn.close()
