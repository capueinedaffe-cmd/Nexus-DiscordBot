"""
expedition_math.py
-------------------
Funciones puras del sistema de expedición (ver Propuesta de sistema de
expedición v0.11). Igual que combat_math.py: no tocan Discord ni la BD,
solo reciben datos simples y devuelven resultados, para poder reusarlas
y probarlas fácil desde expedition_store.py y los comandos.
"""

import random

ENERGIA_MAXIMA = 10
COSTE_EXPLORAR = 1
COSTE_COMBATE = 2
RECUPERACION_CRUDO = 1          # comer un material comestible crudo, fuera de /acampar
TURNOS_VENENO_JUMMI = 3          # exploraciones hasta que el jummi crudo incapacita


# ── Energía y estados ────────────────────────────────────────────────
def gastar_energia(energia_actual, costo):
    return max(0, energia_actual - costo)


def recuperar_energia(energia_actual, cantidad):
    return min(ENERGIA_MAXIMA, energia_actual + cantidad)


def esta_incapacitado(energia):
    return energia <= 0


def grupo_incapacitado(energias):
    """energias: lista de la energía actual de cada participante."""
    return all(e <= 0 for e in energias)


# ── Selección ponderada genérica (recursos y enemigos usan lo mismo) ─
def elegir_ponderado(opciones, clave_peso="peso"):
    """
    opciones: lista de dicts, cada uno con una clave de peso (por defecto "peso").
    Devuelve uno al azar, con más chance los que tengan más peso.
    None si la lista está vacía.
    """
    if not opciones:
        return None
    total = sum(o[clave_peso] for o in opciones)
    tirada = random.uniform(0, total)
    acumulado = 0
    for opcion in opciones:
        acumulado += opcion[clave_peso]
        if tirada <= acumulado:
            return opcion
    return opciones[-1]  # solo por errores de redondeo de punto flotante


def sortear_recurso(zona):
    """zona: dict de zonas.json. Devuelve (material_id, cantidad) o None si no hay recursos."""
    recurso = elegir_ponderado(zona.get("recursos", []))
    if not recurso:
        return None
    cantidad = random.randint(recurso["cantidad_min"], recurso["cantidad_max"])
    return recurso["material_id"], cantidad


def sortear_enemigo(zona):
    """zona: dict de zonas.json. Devuelve el enemy_id elegido, o None si la zona no tiene enemigos."""
    entrada = elegir_ponderado(zona.get("enemigos", []))
    return entrada["enemy_id"] if entrada else None


# ── Pistas ────────────────────────────────────────────────────────────
def probabilidad_pista(config_pista, exploraciones_hechas):
    """
    config_pista: zona["pista"] ({"exploraciones_minimas", "prob_base", "incremento"}).
    0% si todavía no se llegó al mínimo de exploraciones; de ahí en más,
    prob_base + incremento acumulado por cada exploración extra (tope 100%).
    """
    minimas = config_pista["exploraciones_minimas"]
    if exploraciones_hechas < minimas:
        return 0.0
    extra = exploraciones_hechas - minimas
    prob = config_pista["prob_base"] + config_pista["incremento"] * extra
    return min(1.0, prob)


def hay_pista(config_pista, exploraciones_hechas):
    """Tira la moneda de si esta exploración encuentra una pista."""
    return random.random() < probabilidad_pista(config_pista, exploraciones_hechas)


# ── Cocina ────────────────────────────────────────────────────────────
def probabilidad_cocina(res_cocinero):
    """RES del cocinero × 10%, tope 100%."""
    return min(1.0, res_cocinero * 0.10)


def cocinar_exitoso(res_cocinero):
    return random.random() < probabilidad_cocina(res_cocinero)

# ── Esencia ────────────────────────────────────────────────────────────
MAX_ESENCIAS_POR_PERSONAJE = 10

def puede_consumir_esencia(esencias_consumidas_actual):
    return esencias_consumidas_actual < MAX_ESENCIAS_POR_PERSONAJE
