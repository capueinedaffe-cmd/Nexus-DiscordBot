# items_store.py
import json
from database import get_db_connection

with open("materials.json", encoding="utf-8") as f:
    _DATA = json.load(f)
MATERIALES = _DATA["materiales"]


def get_material(material_id):
    return MATERIALES.get(material_id)


async def add_material(character_id: int, material_id: str, cantidad: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO character_materials (character_id, material_id, cantidad)
            VALUES ($1, $2, $3)
            ON CONFLICT (character_id, material_id)
            DO UPDATE SET cantidad = character_materials.cantidad + EXCLUDED.cantidad
        ''', character_id, material_id, cantidad)
    finally:
        await conn.close()


async def remove_material(character_id: int, material_id: str, cantidad: int) -> bool:
    """Devuelve False si no había suficiente cantidad (no descuenta nada en ese caso)."""
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(
            "SELECT cantidad FROM character_materials WHERE character_id = $1 AND material_id = $2",
            character_id, material_id
        )
        actual = row["cantidad"] if row else 0
        if actual < cantidad:
            return False
        nueva = actual - cantidad
        if nueva == 0:
            await conn.execute(
                "DELETE FROM character_materials WHERE character_id = $1 AND material_id = $2",
                character_id, material_id
            )
        else:
            await conn.execute(
                "UPDATE character_materials SET cantidad = $3 WHERE character_id = $1 AND material_id = $2",
                character_id, material_id, nueva
            )
        return True
    finally:
        await conn.close()


async def get_inventory(character_id: int) -> list:
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            "SELECT material_id, cantidad FROM character_materials WHERE character_id = $1 ORDER BY material_id",
            character_id
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_material_quantity(character_id: int, material_id: str) -> int:
    conn = await get_db_connection()
    try:
        val = await conn.fetchval(
            "SELECT cantidad FROM character_materials WHERE character_id = $1 AND material_id = $2",
            character_id, material_id
        )
        return val or 0
    finally:
        await conn.close()
