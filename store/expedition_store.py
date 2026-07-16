"""
expedition_store.py
--------------------
Acceso a datos del sistema de expedición: carga el bestiario y las zonas
desde JSON (igual que abilities_store.py/equipment_store.py), y maneja
las tablas de SQLite de expeditions/participantes/loot/conocimiento
público. Las funciones de cálculo puro (sorteo de recursos/enemigos,
probabilidad de pista, etc.) viven en maths/expedition_math.py, no acá.
"""

import json
from database import get_db_connection
from store.characters_store import Character

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


def construir_personaje_enemigo(enemy_id: str) -> Character:
    """
    Arma un Character 'de mentira' para un enemigo del bestiario, sin
    tocar la base de datos: id y owner_id quedan en None porque este
    personaje no pertenece a nadie y no se guarda entre combates.
    """
    enemy = get_enemy(enemy_id)
    if not enemy:
        raise ValueError(f"No existe el enemigo '{enemy_id}' en el bestiario.")

    equipo = enemy.get("equipo", {})
    row = {
        "id": None,
        "owner_id": None,
        "name": enemy["nombre"],
        "is_npc": 1,
        "level": enemy["nivel"],
        "vit_max": enemy["vit_max"],
        "mana_max": enemy["mana_max"],
        "ph": 0,
        "fue": enemy["fue"],
        "res": enemy["res"],
        "agi": enemy["agi"],
        "elemento": enemy["elemento"],
        "victorias": 0,
        "derrotas": 0,
        "maestria_usos": {},
        "equipo_arma_principal": equipo.get("arma_principal"),
        "equipo_arma_secundaria": equipo.get("arma_secundaria"),
        "equipo_cabeza": equipo.get("cabeza"),
        "equipo_torso": equipo.get("torso"),
        "equipo_piernas": equipo.get("piernas"),
        "equipo_accesorio": equipo.get("accesorio"),
        "energia": 10,
        "esencias_consumidas": 0,
    }
    return Character(row)


# ── Expediciones ──────────────────────────────────────────────────────
async def get_active_expedition(thread_id: int):
    conn = await get_db_connection()
    try:
        cursor = await conn.execute(
            "SELECT * FROM expeditions WHERE thread_id = ? AND estado = 'activa'", (thread_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def create_expedition(thread_id: int, zona_id: str, pistas_iniciales: int = 0) -> dict:
    conn = await get_db_connection()
    try:
        cursor = await conn.execute('''
            INSERT INTO expeditions (thread_id, zona_id, pistas)
            VALUES (?, ?, ?)
        ''', (thread_id, zona_id, pistas_iniciales))
        await conn.commit()
        nuevo_id = cursor.lastrowid
        cursor2 = await conn.execute("SELECT * FROM expeditions WHERE id = ?", (nuevo_id,))
        row = await cursor2.fetchone()
        return dict(row)
    finally:
        await conn.close()


async def add_participant(expedition_id: int, character_id: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO expedition_participants (expedition_id, character_id)
            VALUES (?, ?)
            ON CONFLICT(expedition_id, character_id) DO NOTHING
        ''', (expedition_id, character_id))
        await conn.commit()
    finally:
        await conn.close()


async def get_participant_ids(expedition_id: int) -> list:
    """Devuelve los character_id de todos los participantes."""
    conn = await get_db_connection()
    try:
        cursor = await conn.execute(
            "SELECT character_id FROM expedition_participants WHERE expedition_id = ?", (expedition_id,)
        )
        rows = await cursor.fetchall()
        return [row["character_id"] for row in rows]
    finally:
        await conn.close()


async def incrementar_exploraciones(expedition_id: int) -> int:
    """Suma 1 al contador de exploraciones y devuelve el nuevo valor."""
    conn = await get_db_connection()
    try:
        await conn.execute(
            "UPDATE expeditions SET exploraciones = exploraciones + 1 WHERE id = ?", (expedition_id,)
        )
        await conn.commit()
        cursor = await conn.execute("SELECT exploraciones FROM expeditions WHERE id = ?", (expedition_id,))
        row = await cursor.fetchone()
        return row["exploraciones"]
    finally:
        await conn.close()


async def sumar_pista(expedition_id: int) -> int:
    conn = await get_db_connection()
    try:
        await conn.execute("UPDATE expeditions SET pistas = pistas + 1 WHERE id = ?", (expedition_id,))
        await conn.commit()
        cursor = await conn.execute("SELECT pistas FROM expeditions WHERE id = ?", (expedition_id,))
        row = await cursor.fetchone()
        return row["pistas"]
    finally:
        await conn.close()


async def registrar_arpia_derrotada(expedition_id: int) -> int:
    conn = await get_db_connection()
    try:
        await conn.execute(
            "UPDATE expeditions SET arpias_derrotadas = arpias_derrotadas + 1 WHERE id = ?", (expedition_id,)
        )
        await conn.commit()
        cursor = await conn.execute("SELECT arpias_derrotadas FROM expeditions WHERE id = ?", (expedition_id,))
        row = await cursor.fetchone()
        return row["arpias_derrotadas"]
    finally:
        await conn.close()


async def marcar_evento_final_completado(expedition_id: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute(
            "UPDATE expeditions SET evento_final_completado = 1 WHERE id = ?", (expedition_id,)
        )
        await conn.commit()
    finally:
        await conn.close()


async def marcar_jefe_oculto_completado(expedition_id: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute(
            "UPDATE expeditions SET jefe_oculto_completado = 1 WHERE id = ?", (expedition_id,)
        )
        await conn.commit()
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
            cursor_loot = await conn.execute(
                "SELECT material_id, cantidad FROM expedition_loot WHERE expedition_id = ?", (expedition_id,)
            )
            loot_rows = await cursor_loot.fetchall()

            cursor_part = await conn.execute(
                "SELECT character_id FROM expedition_participants WHERE expedition_id = ?", (expedition_id,)
            )
            participant_rows = await cursor_part.fetchall()

            for p in participant_rows:
                for item in loot_rows:
                    await add_material(p["character_id"], item["material_id"], item["cantidad"])

        await conn.execute('''
            UPDATE expeditions
            SET estado = ?, finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', ("exito" if exito else "fracaso", expedition_id))
        await conn.commit()
    finally:
        await conn.close()


# ── Loot temporal de la expedición ──────────────────────────────────
async def agregar_loot(expedition_id: int, material_id: str, cantidad: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO expedition_loot (expedition_id, material_id, cantidad)
            VALUES (?, ?, ?)
            ON CONFLICT(expedition_id, material_id)
            DO UPDATE SET cantidad = cantidad + excluded.cantidad
        ''', (expedition_id, material_id, cantidad))
        await conn.commit()
    finally:
        await conn.close()


async def get_loot(expedition_id: int) -> list:
    conn = await get_db_connection()
    try:
        cursor = await conn.execute(
            "SELECT material_id, cantidad FROM expedition_loot WHERE expedition_id = ? ORDER BY material_id",
            (expedition_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


# ── Veneno demorado del hongo jummi (por personaje, dentro de la expedición) ─
async def get_jummi_contador(expedition_id: int, character_id: int) -> int:
    conn = await get_db_connection()
    try:
        cursor = await conn.execute('''
            SELECT jummi_contador FROM expedition_participants
            WHERE expedition_id = ? AND character_id = ?
        ''', (expedition_id, character_id))
        row = await cursor.fetchone()
        return row["jummi_contador"] if row else 0
    finally:
        await conn.close()


async def set_jummi_contador(expedition_id: int, character_id: int, valor: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            UPDATE expedition_participants SET jummi_contador = ?
            WHERE expedition_id = ? AND character_id = ?
        ''', (valor, expedition_id, character_id))
        await conn.commit()
    finally:
        await conn.close()


# ── Pistas compartidas por zona ──────────────────────────────────────
async def get_pistas_publicas(zona_id: str) -> int:
    conn = await get_db_connection()
    try:
        cursor = await conn.execute(
            "SELECT pistas_publicas FROM zona_conocimiento_publico WHERE zona_id = ?", (zona_id,)
        )
        row = await cursor.fetchone()
        return row["pistas_publicas"] if row else 0
    finally:
        await conn.close()


async def hacer_publico(zona_id: str, pistas: int) -> None:
    conn = await get_db_connection()
    try:
        await conn.execute('''
            INSERT INTO zona_conocimiento_publico (zona_id, pistas_publicas)
            VALUES (?, ?)
            ON CONFLICT(zona_id) DO UPDATE
            SET pistas_publicas = MAX(pistas_publicas, excluded.pistas_publicas)
        ''', (zona_id, pistas))
        await conn.commit()
    finally:
        await conn.close()

# ── Pelea especial: 40 arpías menores (Montaña, post evento final) ─
ARPIAS_POR_OLEADA = 4
OLEADAS_ARPIAS = 10

def armar_oleadas_arpias():
    """
    Devuelve (primera_oleada_ids, resto_oleadas) para las 40 arpías menores:
    10 oleadas de 4 arpía_menor cada una. primera_oleada_ids es la lista
    que arma el Fighter inicial de la sesión; resto_oleadas es la lista
    de listas que CombatSession va a ir revelando con avanzar_oleada_si_corresponde().
    """
    todas = [["arpia_menor"] * ARPIAS_POR_OLEADA for _ in range(OLEADAS_ARPIAS)]
    return todas[0], todas[1:]
