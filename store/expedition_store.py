"""
expedition_store.py
--------------------
Acceso a datos del sistema de expedición: carga el bestiario y las zonas
desde JSON (igual que abilities_store.py/equipment_store.py), y maneja
las tablas de PostgreSQL de expeditions/participantes/loot/conocimiento
público. Las funciones de cálculo puro (sorteo de recursos/enemigos,
probabilidad de pista, etc.) viven en maths/expedition_math.py, no acá.
"""

import json
from database import get_db_connection

with open("data/zonas/zonas.json", encoding="utf-8") as f:
    _ZONAS_DATA = json.load(f)
ZONAS = _ZONAS_DATA["zonas"]

with open("data/enemies/enemies1.json", encoding="utf-8") as f:
    _ENEMIGOS_DATA = json.load(f)
ENEMIGOS = _ENEMIGOS_DATA["enemigos"]


def get_zona(zona_id):
    return ZONAS.get(zona_id)


def get_enemy(enemy_id):
    return ENEMIGOS.get(enemy_id)


# ── Expediciones ──────────────────────────────────────────────────────
async def get_active_expedition(thread_id: int):
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(
            "SELECT * FROM expeditions WHERE thread_id = $1 AND estado = 'activa'", thread_id
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def create_expedition(thread_id: int, zona_id: str, pistas_iniciales: int = 0) -> dict:
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow('''
            INSERT INTO expeditions (thread_id, zona_id, pistas)
            VALUES ($1, $2, $3)
            RETURNING *
        ''', thread_id, zona_id, pistas_iniciales)
        return dict(row)
    finally:
        await conn.close()


async def add_participant(expedition_id: int, character_id: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO expedition_participants (expedition_id, character_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
        ''', expedition_id, character_id)
    finally:
        await conn.close()


async def get_participant_ids(expedition_id: int) -> list:
    """Devuelve los character_id de todos los participantes."""
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            "SELECT character_id FROM expedition_participants WHERE expedition_id = $1", expedition_id
        )
        return [row["character_id"] for row in rows]
    finally:
        await conn.close()


async def incrementar_exploraciones(expedition_id: int) -> int:
    """Suma 1 al contador de exploraciones y devuelve el nuevo valor."""
    conn = await get_db_connection()
    try:
        return await conn.fetchval('''
            UPDATE expeditions SET exploraciones = exploraciones + 1
            WHERE id = $1 RETURNING exploraciones
        ''', expedition_id)
    finally:
        await conn.close()


async def sumar_pista(expedition_id: int) -> int:
    conn = await get_db_connection()
    try:
        return await conn.fetchval('''
            UPDATE expeditions SET pistas = pistas + 1
            WHERE id = $1 RETURNING pistas
        ''', expedition_id)
    finally:
        await conn.close()


async def registrar_arpia_derrotada(expedition_id: int) -> int:
    conn = await get_db_connection()
    try:
        return await conn.fetchval('''
            UPDATE expeditions SET arpias_derrotadas = arpias_derrotadas + 1
            WHERE id = $1 RETURNING arpias_derrotadas
        ''', expedition_id)
    finally:
        await conn.close()


async def marcar_evento_final_completado(expedition_id: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute(
            "UPDATE expeditions SET evento_final_completado = TRUE WHERE id = $1", expedition_id
        )
    finally:
        await conn.close()


async def marcar_jefe_oculto_completado(expedition_id: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute(
            "UPDATE expeditions SET jefe_oculto_completado = TRUE WHERE id = $1", expedition_id
        )
    finally:
        await conn.close()


async def finalizar_expedition(expedition_id: int, exito: bool) -> None:
    """
    Cierra la expedición. Si exito=True, cada participante recibe una
    COPIA COMPLETA de todo lo recolectado (no se reparte/divide). Si
    exito=False, el inventario temporal de la expedición se descarta.
    """
    from store.items_store import add_material  # import local: evita ciclo con items_store

    conn = await get_db_connection()
    try:
        if exito:
            loot_rows = await conn.fetch(
                "SELECT material_id, cantidad FROM expedition_loot WHERE expedition_id = $1", expedition_id
            )
            participant_rows = await conn.fetch(
                "SELECT character_id FROM expedition_participants WHERE expedition_id = $1", expedition_id
            )
            for p in participant_rows:
                for item in loot_rows:
                    await add_material(p["character_id"], item["material_id"], item["cantidad"])

        await conn.execute('''
            UPDATE expeditions
            SET estado = $2, finished_at = CURRENT_TIMESTAMP
            WHERE id = $1
        ''', expedition_id, "exito" if exito else "fracaso")
    finally:
        await conn.close()


# ── Loot temporal de la expedición ──────────────────────────────────
async def agregar_loot(expedition_id: int, material_id: str, cantidad: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO expedition_loot (expedition_id, material_id, cantidad)
            VALUES ($1, $2, $3)
            ON CONFLICT (expedition_id, material_id)
            DO UPDATE SET cantidad = expedition_loot.cantidad + EXCLUDED.cantidad
        ''', expedition_id, material_id, cantidad)
    finally:
        await conn.close()


async def get_loot(expedition_id: int) -> list:
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            "SELECT material_id, cantidad FROM expedition_loot WHERE expedition_id = $1 ORDER BY material_id",
            expedition_id
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


# ── Veneno demorado del hongo jummi (por personaje, dentro de la expedición) ─
async def get_jummi_contador(expedition_id: int, character_id: int) -> int:
    conn = await get_db_connection()
    try:
        val = await conn.fetchval('''
            SELECT jummi_contador FROM expedition_participants
            WHERE expedition_id = $1 AND character_id = $2
        ''', expedition_id, character_id)
        return val or 0
    finally:
        await conn.close()


async def set_jummi_contador(expedition_id: int, character_id: int, valor: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            UPDATE expedition_participants SET jummi_contador = $3
            WHERE expedition_id = $1 AND character_id = $2
        ''', expedition_id, character_id, valor)
    finally:
        await conn.close()


# ── Pistas compartidas por zona ──────────────────────────────────────
async def get_pistas_publicas(zona_id: str) -> int:
    conn = await get_db_connection()
    try:
        val = await conn.fetchval(
            "SELECT pistas_publicas FROM zona_conocimiento_publico WHERE zona_id = $1", zona_id
        )
        return val or 0
    finally:
        await conn.close()


async def hacer_publico(zona_id: str, pistas: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO zona_conocimiento_publico (zona_id, pistas_publicas)
            VALUES ($1, $2)
            ON CONFLICT (zona_id) DO UPDATE
            SET pistas_publicas = GREATEST(zona_conocimiento_publico.pistas_publicas, EXCLUDED.pistas_publicas)
        ''', zona_id, pistas)
    finally:
        await conn.close()
