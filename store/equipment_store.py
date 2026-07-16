# equipment_store.py
import json
from database import get_db_connection

with open("data/equipment/equipment.json", encoding="utf-8") as f:
    _DATA = json.load(f)
EQUIPAMENTO = _DATA["equipamento"]


def get_equipment(equipment_id):
    return EQUIPAMENTO.get(equipment_id)


async def add_equipment(character_id: int, equipment_id: str, cantidad: int = 1) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO character_equipment (character_id, equipment_id, cantidad)
            VALUES (?, ?, ?)
            ON CONFLICT(character_id, equipment_id)
            DO UPDATE SET cantidad = cantidad + excluded.cantidad
        ''', (character_id, equipment_id, cantidad))
        await conn.commit()
    finally:
        await conn.close()


async def get_equipment_inventory(character_id: int) -> list:
    conn = await get_db_connection()
    try:
        cursor = await conn.execute(
            "SELECT equipment_id, cantidad FROM character_equipment WHERE character_id = ? ORDER BY equipment_id",
            (character_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()
