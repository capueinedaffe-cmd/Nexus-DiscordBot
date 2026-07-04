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
def calcular_ataques_basicos(fue_base, arma_principal, arma_secundaria):
    """
    Devuelve la lista de golpes que corresponde resolver este turno con
    un /atacar básico. Cada golpe es un dict {"arma": dict_o_None, "fue_efectiva": int}
    para saber con qué arma (y qué tipo_dano) se resuelve cada uno:
      - Sin arma, arma a dos manos, o una sola arma de una mano → 1 golpe,
        FUE completa, con el arma principal (o None = puños).
      - Dos armas de una mano → 2 golpes independientes, cada uno con su
        propia arma y FUE_base // 2.
    """
    dos_armas_una_mano = (
        arma_principal is not None and arma_secundaria is not None
        and arma_principal.get("manos") == 1
        and arma_secundaria.get("manos") == 1
    )
    if dos_armas_una_mano:
        return [
            {"arma": arma_principal, "fue_efectiva": fue_base // 2},
            {"arma": arma_secundaria, "fue_efectiva": fue_base // 2},
        ]
    return [{"arma": arma_principal, "fue_efectiva": fue_base}]


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
def combinar_defensas(piezas_armadura):
    """
    piezas_armadura: lista con las piezas equipadas que dan defensa física
    (cabeza, torso, piernas — cada una dict de equipment.json o None).
    Como no hay un sistema de "a qué zona del cuerpo pega el golpe", se
    tratan las tres piezas juntas como una sola armadura: se suma la DEF
    y el %bloqueo de cada tipo de daño entre todas las piezas equipadas
    (el bloqueo no puede pasar de 100%).
    """
    defensa_total = {}
    bloqueo_total = {}
    for pieza in piezas_armadura:
        if not pieza:
            continue
        for tipo, valor in pieza.get("defensa_tipos", {}).items():
            defensa_total[tipo] = defensa_total.get(tipo, 0) + valor
        for tipo, valor in pieza.get("bloqueo_tipos", {}).items():
            bloqueo_total[tipo] = min(100, bloqueo_total.get(tipo, 0) + valor)
    return {"defensa_tipos": defensa_total, "bloqueo_tipos": bloqueo_total}


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


# ── FUE efectiva para técnicas (siempre un solo golpe) ──────────────
def fue_efectiva_tecnica(fue_base, arma_principal, arma_secundaria):
    """
    A diferencia del ataque básico, una técnica siempre es un solo golpe
    (nunca duplica como el doble ataque de dos armas de una mano). Pero
    si las dos manos están ocupadas por dos armas de una mano, la FUE
    igual se reduce a la mitad porque estás sosteniendo dos cosas.
    Con un arma a dos manos, una sola de una mano, o sin arma: FUE completa.
    """
    if arma_principal is not None and arma_secundaria is not None:
        return fue_base // 2
    return fue_base


# ── Flujo completo de un golpe físico ───────────────────────────────
def resolver_golpe_fisico(bono_dado, dano_base, tipo_dano, agi_efectiva_atacante,
                           agi_efectiva_defensor, armadura_defensor,
                           vit_max_defensor, defensor_en_guardia):
    """
    Flujo de un golpe físico (ataque básico o técnica), siguiendo la
    sección 6 del documento: Esquiva → Tirada de Grado → Defensa de
    armadura → Bloqueo. (La distancia se valida ANTES de llamar a esta
    función, con puede_atacar(), porque si falla el ataque ni siquiera
    debería gastar recursos.)

    bono_dado: lo que se suma al d20 en la Tirada de Grado. Para un
      ataque básico es la FUE_efectiva de ese golpe; para una técnica
      es la FUE_efectiva SIN sumar RES ni el modificador (ver el
      documento: "d20 + FUE_efectiva" es igual para ambos casos).
    dano_base: la base que se multiplica por el resultado de esa tirada.
      Para un básico es la misma FUE_efectiva que bono_dado. Para una
      técnica es FUE_efectiva (ya con la posible penalización de arma
      aplicada) + RES + modificador_dano de la técnica.
    tipo_dano: el tipo físico del golpe (cortante/punzante/contundente/explosivo).
    armadura_defensor: dict combinado de combinar_defensas(), o {} si no tiene nada puesto.
    defensor_en_guardia: True si el objetivo usó /defender este turno.

    No aplica el daño a nadie: eso lo hace quien llama, restando el
    resultado de la VIT del objetivo. Devuelve (texto, dano_final, evadido_bool).
    """
    chance_esquiva = calcular_esquiva(agi_efectiva_atacante, agi_efectiva_defensor)
    if random.randint(1, 100) <= chance_esquiva:
        return "💨 esquiva el golpe.", 0, True

    grado, total = tirar_grado(bono_dado)
    multiplicador = GRADO_MULTIPLICADOR[grado]
    if defensor_en_guardia:
        multiplicador *= 0.5
    dano_bruto = max(1, round(dano_base * multiplicador))

    def_especifica = defensa_por_tipo(armadura_defensor, tipo_dano, vit_max_defensor)
    dano_intermedio = max(1, dano_bruto - def_especifica)

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
