"""
combat_math.py
---------------
Funciones puras de cálculo para el sistema de combate físico y técnico
(ver sistema_combate_físico_tecnico.txt). "Puras" quiere decir que no tocan
Discord ni la base de datos: reciben números/dicts simples y devuelven
números o texto. Así son fáciles de probar y de reusar desde combat.py
sin duplicar lógica.

Contenido:
  - Tirada de Grado y esquiva (se movieron acá desde combat.py para que
    el flujo de golpe físico las pueda usar sin repetir código; combat.py
    las va a importar de acá en la Etapa 3 en vez de tener su propia copia).
  - Peso total, penalización de AGI por carga, FUE efectiva según arma(s).
  - Reglas de distancia (qué armas pueden atacar a qué distancia).
  - Penalización de técnica cuando el arma no coincide con su tipo físico.
  - Defensa y bloqueo de armadura por tipo de daño físico.
  - resolver_golpe_fisico(): junta todo lo anterior en el flujo de 4 pasos
    que describe la sección 6 del documento de combate físico.
"""

import random


# ── Tirada de Grado y esquiva ───────────────────────────────────────
GRADO_MULTIPLICADOR = {
    "fallo_parcial": 0.5,
    "estandar": 1.0,
    "limpio": 1.25,
    "critico": 1.5,
}


def tirar_grado(bono_stat):
    """d20 + stat relevante → (nombre_grado, total)."""
    total = random.randint(1, 20) + bono_stat
    if total <= 8:
        return "fallo_parcial", total
    elif total <= 14:
        return "estandar", total
    elif total <= 19:
        return "limpio", total
    else:
        return "critico", total


def calcular_esquiva(atacante_agi, defensor_agi):
    """
    % de esquiva del defensor según la diferencia de AGI.
    IMPORTANTE: para ataques físicos hay que pasar la AGI EFECTIVA
    (ya con la penalización de peso aplicada) de los dos, no la AGI base.
    """
    diff = defensor_agi - atacante_agi
    if diff >= 0:
        chance = 10 + 2 * diff
    else:
        exceso = -diff
        if exceso <= 10:
            chance = 10
        else:
            chance = 10 - 2 * (exceso - 10)
    return max(0, min(100, chance))


# ── Peso y penalización de AGI por carga ────────────────────────────
def calcular_peso_total(pesos_equipados):
    """
    pesos_equipados: lista de enteros (arma_principal, arma_secundaria,
    cabeza, torso, piernas). Los accesorios no pesan, no van en la lista.
    Los slots vacíos van como None o 0, se ignoran.
    """
    return sum(p for p in pesos_equipados if p)


def calcular_agi_efectiva(agi_base, peso_total, fue_base):
    """AGI_efectiva = AGI_base - max(0, (Peso_Total - FUE_base) * 2), mínimo 0."""
    exceso = max(0, peso_total - fue_base)
    return max(0, agi_base - exceso * 2)


# ── FUE efectiva según configuración de armas ───────────────────────
def calcular_fue_por_ataque(fue_base, arma_principal, arma_secundaria):
    """
    Devuelve una lista con la FUE_efectiva de cada ataque que corresponde
    hacer este turno con un /atacar básico:
      - Sin arma, arma a dos manos, o una sola arma de una mano → 1 ataque, FUE completa.
      - Dos armas de una mano → 2 ataques independientes, FUE_base // 2 cada uno.
    arma_principal / arma_secundaria: dict de equipment.json, o None si el slot está vacío.
    """
    dos_armas_una_mano = (
        arma_principal is not None and arma_secundaria is not None
        and arma_principal.get("manos") == 1
        and arma_secundaria.get("manos") == 1
    )
    if dos_armas_una_mano:
        return [fue_base // 2, fue_base // 2]
    return [fue_base]


# ── Distancia ────────────────────────────────────────────────────────
def puede_atacar(arma, distancia):
    """
    True si el arma (o los puños, si arma es None) puede atacar a esta distancia.
    Cuerpo a cuerpo: distancia ≤ 1. A distancia: distancia ≥ 2.
    """
    if arma is None:
        return distancia <= 1
    if arma.get("categoria") == "distancia":
        return distancia >= 2
    return distancia <= 1


def pasos_movimiento(agi_efectiva):
    """Casilleros que avanza/retrocede un personaje al mover (usa AGI efectiva)."""
    return 1 + (agi_efectiva // 6)


# ── Penalización de técnica por desajuste de arma ───────────────────
def aplicar_penalizacion_tecnica(fue_efectiva, modificador_fisico, coste_pt_base, arma, tipo_fisico_tecnica):
    """
    Si la técnica exige un tipo_fisico y el arma equipada no lo tiene
    (o no hay arma), el componente físico se reduce a la mitad y el
    costo en PT se duplica. Si la técnica es puramente elemental
    (tipo_fisico=None) o el arma coincide, no hay penalización.
    Devuelve (dano_fisico, coste_pt_final).
    """
    arma_tipo = arma.get("tipo_dano") if arma else None
    if tipo_fisico_tecnica and arma_tipo != tipo_fisico_tecnica:
        dano_fisico = (fue_efectiva + modificador_fisico) * 0.5
        coste_pt_final = coste_pt_base * 2
    else:
        dano_fisico = fue_efectiva + modificador_fisico
        coste_pt_final = coste_pt_base
    return dano_fisico, coste_pt_final


# ── Defensa y bloqueo de armadura por tipo de daño ──────────────────
def defensa_por_tipo(armadura, tipo_dano, vit_max_defensor):
    """
    DEF a restar para este tipo de daño físico. Si la armadura no define
    ese tipo puntual en "defensa_tipos", se usa DEF_base = VIT_max / 4
    (la misma DEF genérica que ya usan los ataques sin armadura).
    """
    if armadura and tipo_dano in armadura.get("defensa_tipos", {}):
        return armadura["defensa_tipos"][tipo_dano]
    return vit_max_defensor // 4


def chance_bloqueo(armadura, tipo_dano):
    """% de bloqueo de la armadura para este tipo de daño (0 si no está definido)."""
    if armadura and tipo_dano in armadura.get("bloqueo_tipos", {}):
        return armadura["bloqueo_tipos"][tipo_dano]
    return 0


def resolver_bloqueo(dano_intermedio, porcentaje_bloqueo):
    """Tira d100: si acierta el bloqueo, la mitad del daño (mínimo 1); si no, entero."""
    tirada = random.randint(1, 100)
    if tirada <= porcentaje_bloqueo:
        return max(1, dano_intermedio // 2), True
    return dano_intermedio, False


# ── Flujo completo de un golpe físico ───────────────────────────────
def resolver_golpe_fisico(fue_efectiva, tipo_dano, agi_efectiva_atacante,
                           agi_efectiva_defensor, armadura_defensor,
                           vit_max_defensor, defensor_en_guardia):
    """
    Flujo de un golpe físico (ataque básico o parte física de una técnica),
    siguiendo la sección 6 del documento: Esquiva → Tirada de Grado →
    Defensa de armadura → Bloqueo. (La distancia se valida ANTES de llamar
    a esta función, con puede_atacar(), porque si falla el ataque ni
    siquiera debería gastar recursos.)

    tipo_dano: el tipo físico del golpe (cortante/punzante/contundente/explosivo).
    armadura_defensor: dict de equipment.json de la pieza que cubre esa zona, o None.
    defensor_en_guardia: True si el objetivo usó /defender este turno.

    No aplica el daño a nadie: eso lo hace quien llama, restando el
    resultado de la VIT del objetivo. Devuelve (texto, dano_final, evadido_bool).
    """
    # Esquiva primero: si esquiva, no hace falta tirar nada más.
    chance_esquiva = calcular_esquiva(agi_efectiva_atacante, agi_efectiva_defensor)
    if random.randint(1, 100) <= chance_esquiva:
        return "💨 esquiva el golpe.", 0, True

    # Tirada de Grado
    grado, total = tirar_grado(fue_efectiva)
    multiplicador = GRADO_MULTIPLICADOR[grado]
    if defensor_en_guardia:
        multiplicador *= 0.5
    dano_bruto = max(1, round(fue_efectiva * multiplicador))

    # Defensa de armadura según el tipo de daño del golpe
    def_especifica = defensa_por_tipo(armadura_defensor, tipo_dano, vit_max_defensor)
    dano_intermedio = max(1, dano_bruto - def_especifica)

    # Bloqueo
    porcentaje_bloqueo = chance_bloqueo(armadura_defensor, tipo_dano)
    dano_final, bloqueo_exitoso = resolver_bloqueo(dano_intermedio, porcentaje_bloqueo)

    etiquetas = {
        "fallo_parcial": "fallo parcial",
        "estandar": "éxito",
        "limpio": "éxito limpio",
        "critico": "¡CRÍTICO!",
    }
    texto = f"({etiquetas[grado]}, tirada {total}) **{dano_final}** de daño"
    if bloqueo_exitoso:
        texto += " (bloqueado parcialmente)"
    texto += "."
    return texto, dano_final, False
