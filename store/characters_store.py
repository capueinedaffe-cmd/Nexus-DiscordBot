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
        self.ph = row["ph"]
        self.fue = row["fue"]
        self.res = row["res"]
        self.agi = row["agi"]
        self.elemento = row["elemento"]
        self.victorias = row.get("victorias", 0)
        self.derrotas = row.get("derrotas", 0)
        raw_maestria = row.get("maestria_usos")
        self.equipo = {
            "arma": row.get("equipo_arma"),
            "cabeza": row.get("equipo_cabeza"),
            "torso": row.get("equipo_torso"),
            "piernas": row.get("equipo_piernas"),
            "accesorio": row.get("equipo_accesorio"),
        }
        
        if isinstance(raw_maestria, str):
            self.maestria_usos = json.loads(raw_maestria) if raw_maestria else {}
        elif isinstance(raw_maestria, dict):
            self.maestria_usos = raw_maestria
        else:
            self.maestria_usos = {}

    def maestria_nivel(self, elemento=None):
        elemento = elemento or self.elemento
        usos = self.maestria_usos.get(elemento, 0)
        return min(10, usos // 10)

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
            INSERT INTO characters (owner_id, name, is_npc, level, vit_max, mana_max, fue, res, agi, elemento, ph)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ''', character.owner_id, character.name, character.is_npc, character.level,
            character.vit_max, character.mana_max, character.fue, character.res, character.agi, character.elemento, character.ph)
    finally:
        await conn.close()

async def record_combat_result(character_id: int, resultado: Optional[str],
                                usos_habilidad: int = 0, elemento: Optional[str] = None) -> None:
    """resultado: 'victoria' | 'derrota' | None (None = no cuenta W/L, solo maestría)."""
    conn = await get_db_connection()
    try:
        if resultado == "victoria":
            await conn.execute("UPDATE characters SET victorias = victorias + 1 WHERE id = $1", character_id)
        elif resultado == "derrota":
            await conn.execute("UPDATE characters SET derrotas = derrotas + 1 WHERE id = $1", character_id)

        if usos_habilidad > 0 and elemento:
            await conn.execute('''
                UPDATE characters
                SET maestria_usos = jsonb_set(
                    maestria_usos,
                    ARRAY[$2],
                    to_jsonb(COALESCE((maestria_usos->>$2)::int, 0) + $3)
                )
                WHERE id = $1
            ''', character_id, elemento, usos_habilidad)
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

async def update_equipment(character_id: int, equipo: dict) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            UPDATE characters
            SET equipo_arma = $2, equipo_cabeza = $3, equipo_torso = $4,
                equipo_piernas = $5, equipo_accesorio = $6
            WHERE id = $1
        ''', character_id, equipo.get("arma"), equipo.get("cabeza"),
            equipo.get("torso"), equipo.get("piernas"), equipo.get("accesorio"))
    finally:
        await conn.close()

async def get_character_transformations(character_id: int) -> List[dict]:
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            "SELECT * FROM transformations WHERE character_id = $1", character_id
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()

async def add_transformation(character_id: int, name: str, element: str,
                              bonuses: dict, ph_drain: int, condition_text: str) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO transformations
                (character_id, name, element, stat_bonus_vit, stat_bonus_mana,
                 stat_bonus_fue, stat_bonus_res, stat_bonus_agi, ph_drain_per_turn, condition_text)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ''', character_id, name, element, bonuses["vit"], bonuses["mana"],
            bonuses["fue"], bonuses["res"], bonuses["agi"], ph_drain, condition_text)
    finally:
        await conn.close()
