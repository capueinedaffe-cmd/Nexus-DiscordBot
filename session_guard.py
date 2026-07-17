"""
session_guard.py
-----------------
Regla global: un usuario (owner_id) solo puede tener UNA sesión activa
a la vez en todo el bot — ya sea un lobby de preparación (combate o
expedición) o una sesión en curso (combate o expedición). Vive en su
propio archivo para que combat.py y expedition.py puedan consultarlo
sin importarse entre sí.
"""

from store.expedition_store import esta_en_expedicion_activa


async def usuario_ocupado(owner_id: int) -> bool:
    from commands.combat import LOBBIES, ACTIVE_COMBATS
    from commands.expedition import LOBBIES_EXPEDICION

    for lobbies in LOBBIES.values():
        for lobby in lobbies:
            if owner_id in lobby.owner_ids():
                return True

    for session in ACTIVE_COMBATS.values():
        if owner_id in session.owner_ids():
            return True

    for lobbies in LOBBIES_EXPEDICION.values():
        for lobby in lobbies:
            if owner_id in lobby.owner_ids():
                return True

    return await esta_en_expedicion_activa(owner_id)
