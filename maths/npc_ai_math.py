"""
npc_ai.py
---------
IA básica para los turnos automáticos de los NPCs en combate (enemigos
de la expedición). No conoce nada de Discord ni modifica el combate
directamente: solo mira el estado actual (el Fighter del NPC y sus
posibles objetivos) y devuelve una decisión simple para que combat.py
la ejecute.

Diseño actual (deliberadamente simple, pensado para crecer):
  - Ataca siempre al objetivo vivo con menos VIT del equipo contrario.
  - Si el arma equipada no puede atacar a la distancia actual, en vez
    de perder el turno como haría un jugador, decide moverse (acercarse
    si es cuerpo a cuerpo, alejarse si es a distancia) para poder atacar
    el turno siguiente.
  - Todavía no usa habilidades ni técnicas (el NPC ya tiene PH/MANA
    armados en Fighter para cuando se quiera sumar esa lógica más adelante).

Devuelve siempre un dict con la forma:
  {"accion": "atacar", "objetivo": Fighter}
  {"accion": "mover", "direccion": "avanzar" | "retroceder"}
  {"accion": "esperar"}  # solo si no hay ningún objetivo vivo (no debería pasar en un combate normal)
"""

from maths import combat_math as cmath


def elegir_objetivo(npc, objetivos_posibles):
    """Devuelve el objetivo vivo con menos VIT actual, o None si no hay ninguno."""
    vivos = [f for f in objetivos_posibles if f.alive]
    if not vivos:
        return None
    return min(vivos, key=lambda f: f.vit)


def decidir_turno(npc, objetivos_posibles):
    """
    npc: el Fighter del NPC al que le toca el turno.
    objetivos_posibles: lista de Fighters del equipo contrario (vivos o no,
    se filtra acá adentro).
    """
    objetivo = elegir_objetivo(npc, objetivos_posibles)
    if not objetivo:
        return {"accion": "esperar"}

    golpes = cmath.calcular_ataques_basicos(npc.fue, npc.arma_principal, npc.arma_secundaria)

    # Si CUALQUIERA de los golpes de este turno no puede conectar a la
    # distancia actual, el NPC prefiere moverse antes que fallar el ataque.
    puede_atacar_ya = all(cmath.puede_atacar(g["arma"], npc.distancia) for g in golpes)

    if puede_atacar_ya:
        return {"accion": "atacar", "objetivo": objetivo}

    # ¿Hacia dónde tiene que moverse? Si el arma principal es a distancia,
    # necesita alejarse; si es cuerpo a cuerpo (o no tiene arma), acercarse.
    arma_principal = npc.arma_principal
    if arma_principal and arma_principal.get("categoria") == "distancia":
        direccion = "retroceder"
    else:
        direccion = "avanzar"

    return {"accion": "mover", "direccion": direccion}
