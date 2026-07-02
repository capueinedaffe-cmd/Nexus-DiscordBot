# characters_store.py
from database import get_db_connection
from typing import Optional, List
import json

# Cargar constantes desde JSON (opcional, pero recomendado)
with open("config.json") as f:
    CONFIG = json.load(f)
MAX_CHARACTERS_PER_USER = CONFIG["MAX_CHARACTERS_PER_USER"]

class Character:
    def __init__(self, row):
        self.id = row["id"]
        self.owner_id = row["owner_id"]
        self.name = row["name"]
        self.is_npc = row["is_npc"]
        self.level = row["level"]
        self.vit_max = row["vit_max"]
        self.mana_max = row["mana_max"]
        self.fue = row["fue"]
        self.res = row["res"]
        self.agi = row["agi"]

    @property
    def ph_max(self):
        return 6 + (self.res // 3)

    @property
    def defense(self):
        return self.vit_max // 4

# --- Funciones de acceso a datos (TODAS async) ---

async def count_player_characters(owner_id: int) -> int:
    conn = await get_db_connection()
    try:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM characters WHERE owner_id = $1 AND is_npc = FALSE",
            owner_id
        )
    finally:
        await conn.close()

async def get_user_characters(owner_id: int, include_npc: bool = True) -> List[Character]:
    conn = await get_db_connection()
    try:
        if include_npc:
            rows = await conn.fetch("SELECT * FROM characters WHERE owner_id = $1", owner_id)
        else:
            rows = await conn.fetch("SELECT * FROM characters WHERE owner_id = $1 AND is_npc = FALSE", owner_id)
        return [Character(dict(row)) for row in rows]
    finally:
        await conn.close()

async def get_character(owner_id: int, name: str) -> Optional[Character]:
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(
            "SELECT * FROM characters WHERE owner_id = $1 AND LOWER(name) = LOWER($2)",
            owner_id, name
        )
        return Character(dict(row)) if row else None
    finally:
        await conn.close()

async def add_character(character: Character) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO characters (owner_id, name, is_npc, level, vit_max, mana_max, fue, res, agi)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ''', character.owner_id, character.name, character.is_npc, character.level,
            character.vit_max, character.mana_max, character.fue, character.res, character.agi)
    finally:
        await conn.close()

async def apply_level_penalty(character: Character) -> None:
    if character.is_npc:
        return
    new_level = max(1, character.level - 1)
    conn = await get_db_connection()
    try:
        await conn.execute(
            "UPDATE characters SET level = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            new_level, character.id
        )
    finally:
        await conn.close()
    character.level = new_level
