"""
events.py
---------
Eventos de la expedición: consecuencias narrativas/mecánicas que se
disparan como reacción a lo que pasa en el juego (derrotar un jefe,
completar una zona, matar 40 arpías, etc.), no como respuesta directa
a un comando de combate. combat.py se limita a resolver combates;
expedition.py (cuando exista) va a llamar a las funciones de acá para
disparar estos eventos en el momento narrativo correcto.

Por ahora, el único evento implementado es el desafío de la Matriarca
Arpía Furiosa: es INEVITABLE en cuanto se derrota a las 40 arpías
menores (no es una elección del jugador, es consecuencia directa).
"""

from commands.combat import Fighter, CombatSession, ACTIVE_COMBATS
from store.characters_store import get_character
from store.expedition_store import construir_personaje_enemigo, armar_oleadas_arpias, ARPIAS_POR_OLEADA


async def iniciar_evento_matriarca(channel_id: int, personajes_convocados: list) -> CombatSession:
    """
    Arma la sesión de combate de las 40 arpías + Matriarca oculta como
    evento inevitable. personajes_convocados: lista de Character ya
    resueltos (uno por jugador participante). Devuelve la CombatSession
    creada y ya registrada en ACTIVE_COMBATS.

    NOTA: esto todavía no valida "¿ya completaron el evento final de la
    Montaña?" — esa condición la va a chequear expedition.py ANTES de
    llamar a esta función, comparando expedition["evento_final_completado"].
    """
    if channel_id in ACTIVE_COMBATS:
        raise RuntimeError("Ya hay un combate en curso en este canal.")

    primera_oleada_ids, resto_oleadas = armar_oleadas_arpias()
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
    ACTIVE_COMBATS[channel_id] = session
    return session
