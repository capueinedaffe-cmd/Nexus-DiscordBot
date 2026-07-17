"""
events.py
---------
Eventos de la expedición: consecuencias narrativas/mecánicas que se
disparan como reacción a lo que pasa en el juego (derrotar un jefe,
completar una zona, atacar una arpía menor, etc.), no como respuesta
directa a un comando de combate. combat.py se limita a resolver
combates y llama a procesar_fin_combate_expedicion() como único punto
de enganche; toda la lógica de qué significa ganar ese combate vive acá.
"""

import discord

from commands.combat import Fighter, CombatSession, ACTIVE_COMBATS, _agregar_combate, _resolver_turnos_npc
from store.characters_store import get_character
from store.expedition_store import (
    construir_personaje_enemigo, armar_oleadas_arpias, ARPIAS_POR_OLEADA,
    get_zona, get_enemy, agregar_loot, marcar_evento_final_completado,
    marcar_jefe_oculto_completado, registrar_arpia_derrotada,
)


async def iniciar_combate_arpias(expedition, personajes_convocados: list, channel_id: int, incluir_matriarca: bool):
    """
    Arma la pelea de 40 arpías menores (10 oleadas de 4). Si incluir_matriarca
    es True, al final de las 40 aparece la Matriarca Arpía Furiosa como
    oleada extra (esto solo debe pasar si la zona ya completó su evento
    final). Devuelve (session, texto_npc_inicial, combate_termino_de_una).
    """
    if channel_id in ACTIVE_COMBATS and len(ACTIVE_COMBATS[channel_id]) >= 3:
        raise RuntimeError("Ya hay demasiados combates activos en este canal.")

    primera_oleada_ids, resto_oleadas = armar_oleadas_arpias()
    if incluir_matriarca:
        resto_oleadas.append(["matriarca_arpia_furiosa"])

    jugadores_fighters = [Fighter(char, team=0) for char in personajes_convocados]
    arpias_iniciales = [Fighter(construir_personaje_enemigo("arpia_menor"), team=1)
                         for _ in range(ARPIAS_POR_OLEADA)]

    session = CombatSession(
        channel_id,
        jugadores_fighters + arpias_iniciales,
        oleadas_enemigos=resto_oleadas,
        equipo_oleadas=1,
    )
    session.expedition_id = expedition["id"] if expedition else None
    _agregar_combate(channel_id, session)

    texto_npc_inicial, combate_termino_de_una = await _resolver_turnos_npc(session)
    return session, texto_npc_inicial, combate_termino_de_una


async def procesar_fin_combate_expedicion(session, winning_team: int):
    """
    Hook llamado por combat.py cuando termina un combate que tiene
    session.expedition_id seteado. Si ganó el equipo de jugadores (team 0):
      - tira el loot de cada enemigo derrotado según su tabla del bestiario.
      - si alguno de los derrotados es el enemy_id del evento final de la
        zona, marca evento_final_completado y agrega el loot garantizado.
      - si alguno es una arpia_menor derrotada en un combate de oleadas,
        suma al contador de arpías derrotadas de la expedición.
      - si el derrotado es la Matriarca, marca jefe_oculto_completado y
        agrega su recompensa fija.
    Si ganó el equipo enemigo (team 1), no hay loot ni consecuencias:
    la expedición sigue viva (una derrota puntual no la termina sola,
    salvo que deje al grupo entero incapacitado, lo cual se chequea en
    /explorar, no acá).
    """
    expedition_id = getattr(session, "expedition_id", None)
    if not expedition_id or winning_team != 0:
        return

    derrotados = [f for f in session.fighters if f.team == 1 and not f.alive]
    if not derrotados:
        return

    zona_id = None
    zona = None
    # No siempre sabemos la zona acá (el hook solo recibe la sesión), así
    # que la resolvemos vía expedition_store si hace falta el evento final.
    from store.expedition_store import get_db_connection as _gdc  # import local, evita ciclo
    conn = await _gdc()
    try:
        cursor = await conn.execute("SELECT zona_id FROM expeditions WHERE id = ?", (expedition_id,))
        row = await cursor.fetchone()
        zona_id = row["zona_id"] if row else None
    finally:
        await conn.close()
    if zona_id:
        zona = get_zona(zona_id)

    arpias_derrotadas_este_combate = 0

    for fighter in derrotados:
        enemy_id = getattr(fighter.character, "enemy_id", None)
        if not enemy_id:
            continue
        enemy_data = get_enemy(enemy_id) or {}

        # Loot normal según probabilidad del bestiario (1 unidad por acierto)
        import random
        for material_id, prob in enemy_data.get("loot", {}).items():
            if random.random() < prob:
                await agregar_loot(expedition_id, material_id, 1)

        # Evento final de la zona
        if zona and zona.get("evento_final", {}).get("enemy_id") == enemy_id:
            await marcar_evento_final_completado(expedition_id)
            for material_id in zona["evento_final"].get("loot_garantizado", []):
                await agregar_loot(expedition_id, material_id, 1)

        # Conteo de arpías para el jefe oculto
        if enemy_id == "arpia_menor":
            arpias_derrotadas_este_combate += 1

        # Matriarca derrotada
        if enemy_id == "matriarca_arpia_furiosa":
            await marcar_jefe_oculto_completado(expedition_id)
            for material_id, prob in enemy_data.get("loot", {}).items():
                if random.random() < prob:
                    await agregar_loot(expedition_id, material_id, 1)

    for _ in range(arpias_derrotadas_este_combate):
        await registrar_arpia_derrotada(expedition_id)
