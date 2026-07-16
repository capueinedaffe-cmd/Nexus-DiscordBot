"""
combat.py
---------
Sistema de combate NEXUS para Discord: por turnos, hasta 3 vs 3,
con HP, MANA, PH y las stats del sistema (FUE, RES, AGI).

Flujo:
  1. /iniciar_combate  → crea o une a una sala de espera (lobby) en el canal
  2. /cambiar_equipo   → cambia de equipo dentro del lobby
  3. /preparado        → marca listo; cuando todos están listos, empieza el combate
  4. /atacar /defender /usar_habilidad → acciones de combate, pasan el turno solas
  5. /pausa            → pide pausar (necesita consenso de todos los jugadores)
  6. Reanudar (botón)  → pide reanudar (mismo consenso)
  7. /terminar         → pide terminar sin resultado (mismo consenso)

Reglas de decisión tomadas por ausencia de especificación exacta:
  - Los votos de pausa/reanudar/terminar se cuentan por owner_id único,
    no por personaje. Si controlás 2 personajes en la pelea, tu voto cuenta 1 vez.
  - El timeout de turno (10 min) penaliza únicamente al dueño del personaje
    cuyo turno estaba activo cuando se cumplió el plazo.
  - El timeout de lobby (5 min) se mide desde que se creó el lobby, no se reinicia
    con cada /iniciar_combate adicional.
"""

import time
import random
import discord
import json
with open("data/elements/elements.json", encoding="utf-8") as f:
    ELEMENTS_DATA = json.load(f)

from discord import app_commands
from discord.ext import tasks

from config import OWNER_ID
from store.characters_store import (
    get_character, get_user_characters, apply_level_penalty,
    get_character_transformations, record_combat_result
)
from store.abilities_store import get_ability, min_level_for
from store.equipment_store import get_equipment
from maths import combat_math as cmath
from maths.npc_ai_math import decidir_turno
from store.expedition_store import construir_personaje_enemigo, armar_oleadas_arpias, ARPIAS_POR_OLEADA

TURN_TIMEOUT_SECONDS = 10 * 60   # 10 minutos
LOBBY_TIMEOUT_SECONDS = 5 * 60   # 5 minutos
MAX_PARTICIPANTS = 6             # 3 vs 3
TIPO_DANO_PUNOS = "contundente"  # Daño a puño limpio

async def owner_check(interaction: discord.Interaction) -> bool:
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "No tenés permiso para usar este comando.", ephemeral=True
        )
        return False
    return True


async def _publish(interaction: discord.Interaction, container, embed: discord.Embed):
    """
    Muestra el panel de estado (lobby o combate) editando el mensaje anterior
    si ya existe, en vez de mandar uno nuevo cada vez. Esto evita que el canal
    se llene de mensajes repetidos con cada acción.
    """
    if container.status_message:
        try:
            await container.status_message.edit(embed=embed)
            return
        except discord.NotFound:
            pass  # El mensaje anterior se borró a mano, se manda uno nuevo abajo

    container.status_message = await interaction.channel.send(embed=embed)


# ── Combatiente en vivo dentro de un combate ────────────────────────
class Fighter:
    def __init__(self, character, team, transformaciones=None):
        self.character = character
        self.owner_id = character.owner_id
        self.name = character.name
        self.is_npc = character.is_npc
        self.team = team

        self.vit_max = character.vit_max
        self.vit = character.vit_max
        self.mana_max = character.mana_max
        self.mana = character.mana_max
        self.fue = character.fue
        self.res = character.res
        self.agi = character.agi
        self.elemento = character.elemento
        self.level = character.level
        self.ph = 0  # se pisa abajo, queda así para no romper el orden de ph_max
        self.ph = self.ph_max
        self.is_defending = False
        self.escudo = None
        self.usos_habilidad_combate = 0

        # Distancia: empieza en cuerpo a cuerpo (1) para no romper el
        # comportamiento anterior de nadie que no use /moverse.
        self.distancia = 1

        # Armas y armadura equipadas (se resuelven acá, una sola vez al
        # entrar en combate, para no golpear el banco de datos JSON en
        # cada ataque). character.equipo guarda ids; get_equipment(None) = None.
        self.arma_principal = get_equipment(character.equipo.get("arma_principal"))
        self.arma_secundaria = get_equipment(character.equipo.get("arma_secundaria"))
        self._piezas_armadura = [
            get_equipment(character.equipo.get("cabeza")),
            get_equipment(character.equipo.get("torso")),
            get_equipment(character.equipo.get("piernas")),
        ]
        self.armadura_combinada = cmath.combinar_defensas(self._piezas_armadura)

        # Transformaciones precargadas para no golpear la BD en pleno combate
        self.transformaciones = {t["name"].lower(): t for t in (transformaciones or [])}
        self.transformado = False
        self.transformacion_activa = None
        self.elemento_vulnerable = None  # se usa recién en la Etapa 5

    @property
    def peso_total(self):
        pesos = [self.arma_principal, self.arma_secundaria] + self._piezas_armadura
        return cmath.calcular_peso_total(p.get("peso") if p else None for p in pesos)

    @property
    def agi_efectiva(self):
        """AGI ya penalizada por el peso de arma(s) y armadura (solo para el sistema físico)."""
        return cmath.calcular_agi_efectiva(self.agi, self.peso_total, self.fue)

    def activar_transformacion(self, trans):
        self.transformado = True
        self.transformacion_activa = trans

        self.vit_max += trans["stat_bonus_vit"]
        self.vit += trans["stat_bonus_vit"]
        self.mana_max += trans["stat_bonus_mana"]
        self.mana += trans["stat_bonus_mana"]
        self.fue += trans["stat_bonus_fue"]
        self.res += trans["stat_bonus_res"]
        self.agi += trans["stat_bonus_agi"]

        self.ph = min(self.ph_max, self.ph + 3)  # bonus fijo de activación
        self.elemento_vulnerable = ELEMENTS_DATA["opuestos"].get(trans["element"])

    def desactivar_transformacion(self):
        trans = self.transformacion_activa
        self.vit_max -= trans["stat_bonus_vit"]
        self.vit = min(self.vit, self.vit_max)
        self.mana_max -= trans["stat_bonus_mana"]
        self.mana = max(0, min(self.mana, self.mana_max))
        self.fue -= trans["stat_bonus_fue"]
        self.res -= trans["stat_bonus_res"]
        self.agi -= trans["stat_bonus_agi"]

        self.transformado = False
        self.transformacion_activa = None
        self.ph = min(self.ph, self.ph_max)  # Clampear PH al nuevo máximo
        self.elemento_vulnerable = None

    def procesar_drain_transformacion(self):
        """Llamar al empezar el turno de este fighter. Devuelve un mensaje si la forma se rompió."""
        if not self.transformado:
            return None
        trans = self.transformacion_activa
        self.mana = max(0, self.mana - 1)
        self.ph = max(0, self.ph - trans["ph_drain_per_turn"])
        if self.mana <= 0:
            nombre = trans["name"]
            self.desactivar_transformacion()
            return f"💥 La transformación **{nombre}** de **{self.name}** se rompió al quedarse sin MANA."
        return None

    @property
    def ph_max(self):
        return 6 + (self.res // 3)

    @property
    def defense(self):
        return self.vit_max // 4

    @property
    def alive(self):
        return self.vit > 0

    def bar(self, current, maximum, length=10):
        filled = round((current / maximum) * length) if maximum > 0 else 0
        filled = max(0, min(length, filled))
        return "■" * filled + "□" * (length - filled)

    def status_line(self):
        return (
            f"**{self.name}** (Equipo {self.team + 1}){' — 💀' if not self.alive else ''}\n"
            f"HP:   {self.bar(self.vit, self.vit_max)}  {self.vit}/{self.vit_max}\n"
            f"MANA: {self.bar(self.mana, self.mana_max)}  {self.mana}/{self.mana_max}\n"
            f"PH:   {self.bar(self.ph, self.ph_max)}  {self.ph}/{self.ph_max}"
            f"Distancia: {self.distancia}/5"
        )


# ── Mecánica de esquiva ─────────────────────────────
# tirar_grado, calcular_esquiva y GRADO_MULTIPLICADOR ahora viven en
# combat_math.py (se usan tanto para ataques elementales como físicos).

def resolver_ataque(attacker, target, bono_stat, danio_base, elemento=None):
    """
    Resuelve esquiva → escudo elemental (si aplica) → Tirada de Grado → daño.
    elemento=None significa ataque físico (ignora escudos elementales).
    Este resolver es el de las HABILIDADES elementales puras (/usar_habilidad);
    los ataques básicos y las técnicas usan cmath.resolver_golpe_fisico en su lugar.
    Devuelve (texto_resultado, danio_infligido, evadido_bool).
    """
    # 1. Esquiva
    chance = cmath.calcular_esquiva(attacker.agi, target.agi)
    if random.randint(1, 100) <= chance:
        target.ph = min(target.ph_max, target.ph + 2)
        return (f"💨 **{target.name}** esquiva el ataque (+2 PH).", 0, True)

    # 2. Escudo elemental — solo interactúa con ataques que tienen elemento
    if elemento and target.escudo:
        eff = ELEMENTS_DATA["efectividad"].get(elemento, {}).get(target.escudo["elemento"], "neutral")
        necesarios = {"efectivo": 0, "neutral": 1, "no_efectivo": 2}[eff]
        if necesarios > 0:
            target.escudo["hits_taken"] += 1
            if target.escudo["hits_taken"] >= necesarios:
                texto = f"💥 El escudo **{target.escudo['nombre']}** se rompe, sin daño directo."
                target.escudo = None
            else:
                texto = f"🛡️ El escudo **{target.escudo['nombre']}** absorbe el golpe."
            return (texto, 0, False)
        else:
            # Elemento efectivo: el escudo se rompe y el golpe pasa de largo con daño directo
            target.escudo = None

    # 3. Tirada de Grado
    grado, total = cmath.tirar_grado(bono_stat)
    multiplicador = cmath.GRADO_MULTIPLICADOR[grado]

    # 4. Vulnerabilidad por transformación al elemento opuesto
    if elemento and target.elemento_vulnerable == elemento:
        multiplicador *= 1.5

    if target.is_defending:
        multiplicador *= 0.5
        target.is_defending = False

    damage = max(1, round(danio_base * multiplicador))
    target.vit = max(0, target.vit - damage)

    etiquetas = {
        "fallo_parcial": "fallo parcial",
        "estandar": "éxito",
        "limpio": "éxito limpio",
        "critico": "¡CRÍTICO!",
    }
    texto = f"({etiquetas[grado]}, tirada {total}) **{damage}** de daño."
    return (texto, damage, False)

async def _resolver_turnos_npc(session):
    """
    Resuelve en cadena todos los turnos consecutivos de NPCs a partir del
    turno ACTUAL de la sesión (sin avanzar primero). Hace falta un punto
    de entrada así, separado de _avanzar_y_resolver_npcs, porque si la
    tirada de iniciativa pone a un NPC primero (antes de que cualquier
    jugador actúe), nadie puede destrabar esa situación con un comando:
    el jugador no puede actuar porque no es su turno, y ningún comando se
    ejecutó todavía para disparar la IA. Por eso esto se llama también
    justo al crear una CombatSession, no solo después de una acción.
    Devuelve (texto_acumulado, combate_terminado_bool).
    """
    lineas = []
    terminado, msg_oleada = session.is_truly_over()
    if msg_oleada:
        lineas.append(msg_oleada)

    while not terminado and session.current.is_npc:
        npc = session.current
        objetivos = [f for f in session.fighters if f.team != npc.team]
        decision = decidir_turno(npc, objetivos)

        if decision["accion"] == "atacar":
            objetivo = decision["objetivo"]
            golpes = cmath.calcular_ataques_basicos(npc.fue, npc.arma_principal, npc.arma_secundaria)
            guardia_restante = objetivo.is_defending
            for golpe in golpes:
                arma = golpe["arma"]
                tipo_dano = arma["tipo_dano"] if arma else TIPO_DANO_PUNOS
                fue_efectiva = golpe["fue_efectiva"]
                texto, danio, evadido = cmath.resolver_golpe_fisico(
                    bono_dado=fue_efectiva,
                    dano_base=fue_efectiva,
                    tipo_dano=tipo_dano,
                    agi_efectiva_atacante=npc.agi_efectiva,
                    agi_efectiva_defensor=objetivo.agi_efectiva,
                    armadura_defensor=objetivo.armadura_combinada,
                    vit_max_defensor=objetivo.vit_max,
                    defensor_en_guardia=guardia_restante,
                )
                guardia_restante = False
                objetivo.vit = max(0, objetivo.vit - danio)
                if evadido:
                    objetivo.ph = min(objetivo.ph_max, objetivo.ph + 2)
                else:
                    npc.ph = min(npc.ph_max, npc.ph + 2)
                nombre_arma = f" con **{arma['nombre']}**" if arma else ""
                lineas.append(f"🩸 **{npc.name}**{nombre_arma} → **{objetivo.name}**: {texto}")
                if not objetivo.alive:
                    break
            objetivo.is_defending = False

        elif decision["accion"] == "mover":
            pasos = cmath.pasos_movimiento(npc.agi_efectiva)
            distancia_anterior = npc.distancia
            if decision["direccion"] == "avanzar":
                npc.distancia = max(0, npc.distancia - pasos)
            else:
                npc.distancia = min(5, npc.distancia + pasos)
            lineas.append(f"🏃 **{npc.name}** se reposiciona ({distancia_anterior} → {npc.distancia}).")

        else:  # "esperar" — no debería pasar en un combate normal, pero por las dudas
            lineas.append(f"⏳ **{npc.name}** no tiene objetivos, espera.")

        terminado, msg_oleada = session.is_truly_over()
        if msg_oleada:
            lineas.append(msg_oleada)
        if terminado:
            break

        drain_msg = session.advance_turn()
        if drain_msg:
            lineas.append(drain_msg)

        terminado, msg_oleada = session.is_truly_over()
        if msg_oleada:
            lineas.append(msg_oleada)

    return "\n".join(lineas), terminado


async def _avanzar_y_resolver_npcs(session):
    """
    Avanza el turno una vez, y si el/los siguientes turnos caen en NPCs,
    los resuelve automáticamente en cadena (delegando en _resolver_turnos_npc)
    hasta que le vuelva a tocar a un jugador o el combate termine.
    Devuelve (texto_acumulado, combate_terminado_bool). No publica nada en
    Discord ni persiste resultados: eso lo sigue haciendo quien llama.
    """
    lineas = []

    drain_msg = session.advance_turn()
    if drain_msg:
        lineas.append(drain_msg)

    texto_npc, terminado = await _resolver_turnos_npc(session)
    if texto_npc:
        lineas.append(texto_npc)

    return "\n".join(lineas), terminado

  
# ── Lobby de espera antes de un combate ─────────────────────────────
class CombatLobby:
    def __init__(self, channel_id):
        self.channel_id = channel_id
        self.participants = []   # lista de tuplas (character, team)
        self.ready_votes = set()
        self.created_at = time.time()
        self.status_message = None   # mensaje del panel, se edita en vez de duplicarse

    def owner_ids(self):
        return set(c.owner_id for c, _ in self.participants)

    def team_count(self, team):
        return len([1 for c, t in self.participants if t == team])

    def has_character(self, character):
        return any(c is character for c, _ in self.participants)

    def add(self, character, team):
        self.participants.append([character, team])

    def set_team(self, character, team):
        for entry in self.participants:
            if entry[0] is character:
                entry[1] = team

    def is_expired(self):
        return (time.time() - self.created_at) > LOBBY_TIMEOUT_SECONDS

    def build_embed(self):
        team0 = [c.name for c, t in self.participants if t == 0]
        team1 = [c.name for c, t in self.participants if t == 1]

        embed = discord.Embed(
            title="⚔️ Preparación de combate",
            description=(
                f"**Equipo 1:** {', '.join(team0) if team0 else '—'}\n"
                f"**Equipo 2:** {', '.join(team1) if team1 else '—'}\n\n"
                f"Listos: {len(self.ready_votes)}/{len(self.owner_ids())} jugadores\n\n"
                f"Usá `/cambiar_equipo` para cambiar de bando y `/preparado` cuando estés listo.\n"
                f"El combate se cancela solo si no todos confirman en 5 minutos."
            ),
            color=discord.Color.orange(),
        )
        return embed


# ── Sesión de combate activo ─────────────────────────────────────────
class CombatSession:
    def __init__(self, channel_id, fighters, oleadas_enemigos=None, equipo_oleadas=None):
        """
        oleadas_enemigos: lista de listas de enemy_ids para las oleadas
          SIGUIENTES a la primera (la primera oleada ya viene incluida en
          'fighters', armada por quien crea la sesión). None o [] = combate
          normal, sin oleadas.
        equipo_oleadas: número de equipo (0 o 1) que se va reponiendo con
          esas oleadas. None si no aplica.
        """
        self.channel_id = channel_id
        self.fighters = fighters   # lista de Fighter, orden = orden de turnos
        self.turn_index = 0
        self.round_number = 1
        self.paused = False
        self.pause_votes = set()
        self.resume_votes = set()
        self.terminate_votes = set()
        self.surrender_votes = {0: set(), 1: set()}
        self.last_action_time = time.time()
        self.status_message = None   # mensaje del panel, se edita en vez de duplicarse

        self.oleadas_restantes = list(oleadas_enemigos) if oleadas_enemigos else []
        self.equipo_oleadas = equipo_oleadas
        self.oleada_actual = 1
        self.oleadas_totales = 1 + len(self.oleadas_restantes)
        self.enemigos_derrotados_total = 0  # cuenta acumulada, útil para la Etapa 5.3.3 (40 arpías)

        self._roll_initiative()

    def _roll_initiative(self):
        rolls = [(random.randint(1, 6) + f.agi, f) for f in self.fighters]
        rolls.sort(key=lambda x: x[0], reverse=True)
        self.fighters = [f for _, f in rolls]
        self.initiative_log = [(f.name, r) for r, f in rolls]

    def owner_ids(self):
        return set(f.owner_id for f in self.fighters)

    def team_owner_ids(self, team):
        """Devuelve los owner_ids únicos del equipo especificado."""
        return set(f.owner_id for f in self.fighters if f.team == team)

    @property
    def current(self):
        return self.fighters[self.turn_index]

    def alive_targets_for(self, fighter):
        """Devuelve los rivales vivos del equipo contrario."""
        return [f for f in self.fighters if f.team != fighter.team and f.alive]

    def advance_turn(self):
        """Pasa el turno al siguiente combatiente vivo."""
        n = len(self.fighters)
        for _ in range(n):
            self.turn_index = (self.turn_index + 1) % n
            if self.turn_index == 0:
                self.round_number += 1
            if self.fighters[self.turn_index].alive:
                break
        self.last_action_time = time.time()
        return self.current.procesar_drain_transformacion()

    def team_alive(self, team):
        return any(f.alive for f in self.fighters if f.team == team)

    def is_over(self):
        return not self.team_alive(0) or not self.team_alive(1)

    def avanzar_oleada_si_corresponde(self):
        """
        Si el equipo de oleadas se quedó sin nadie vivo y todavía quedan
        oleadas pendientes, hace aparecer la siguiente tanda de enemigos
        y devuelve un texto para mostrar en el canal. Si no corresponde
        (no hay sistema de oleadas acá, o ya no quedan más), devuelve None.
        """
        if self.equipo_oleadas is None:
            return None
        if self.team_alive(self.equipo_oleadas):
            return None  # el equipo de oleadas todavía tiene gente viva, no hace falta reponer
        if not self.oleadas_restantes:
            return None  # no quedan más oleadas: el combate termina de verdad

        # Antes de reponer, contamos cuántos cayeron en esta oleada que recién terminó
        self.enemigos_derrotados_total += sum(
            1 for f in self.fighters if f.team == self.equipo_oleadas and not f.alive
        )

        siguiente_ids = self.oleadas_restantes.pop(0)
        nuevos = [Fighter(construir_personaje_enemigo(eid), self.equipo_oleadas) for eid in siguiente_ids]
        self.fighters.extend(nuevos)
        self.oleada_actual += 1

        nombres = ", ".join(f.name for f in nuevos)
        return f"🌊 ¡Oleada {self.oleada_actual}/{self.oleadas_totales}! Aparecen: {nombres}."

    def is_truly_over(self):
        """
        Como is_over(), pero si corresponde reponer una oleada, la genera
        sola y NO cuenta como terminado todavía. Devuelve (terminado, mensaje_oleada_o_None).
        Este es el método que hay que usar en vez de is_over() a partir de ahora,
        en cualquier lugar donde se decida si el combate terminó.
        """
        if not self.is_over():
            return False, None
        mensaje = self.avanzar_oleada_si_corresponde()
        if mensaje:
            return False, mensaje
        return True, None
  
    def winning_team(self):
        if self.team_alive(0) and not self.team_alive(1):
            return 0
        if self.team_alive(1) and not self.team_alive(0):
            return 1
        return None

    def status_embed(self, title="Estado del combate"):
        lines = [f.status_line() for f in self.fighters]
        embed = discord.Embed(
            title=title,
            description=(
                f"Ronda {self.round_number} — Turno de **{self.current.name}**\n\n"
                + "\n\n".join(lines)
            ),
            color=discord.Color.red() if not self.paused else discord.Color.light_grey(),
        )
        if self.paused:
            embed.set_footer(text="⏸️ Combate pausado")
        return embed


# ── Almacenamiento en memoria ────────────────────────────────────────
LOBBIES = {}          # {channel_id: CombatLobby}
ACTIVE_COMBATS = {}   # {channel_id: CombatSession}

_bot_ref = None  # se setea en setup_combat_commands para uso de las tareas de fondo


# ── Vista con el botón Reanudar ──────────────────────────────────────
class ResumeView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="Reanudar", style=discord.ButtonStyle.success, emoji="▶️")
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if session.channel_id not in ACTIVE_COMBATS:
            await interaction.response.send_message("Este combate ya no existe.", ephemeral=True)
            return
        if interaction.user.id not in session.owner_ids():
            await interaction.response.send_message("No participás en este combate.", ephemeral=True)
            return

        session.resume_votes.add(interaction.user.id)
        needed = session.owner_ids()

        if session.resume_votes >= needed:
            session.paused = False
            session.resume_votes.clear()
            session.last_action_time = time.time()
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content="▶️ **Combate reanudado.**", view=self
            )
        else:
            await interaction.response.send_message(
                f"Voto para reanudar registrado ({len(session.resume_votes)}/{len(needed)}).",
                ephemeral=True,
            )


# ── Autocompletado ───────────────────────────────────────────────────
async def personaje_autocomplete(interaction: discord.Interaction, current: str):
    session = ACTIVE_COMBATS.get(interaction.channel_id)
    if not session:
        return []
    opts = [f for f in session.fighters if f.owner_id == interaction.user.id]
    return [
        app_commands.Choice(name=f.name, value=f.name)
        for f in opts if current.lower() in f.name.lower()
    ][:25]


async def objetivo_autocomplete(interaction: discord.Interaction, current: str):
    session = ACTIVE_COMBATS.get(interaction.channel_id)
    if not session:
        return []
    opts = [f for f in session.fighters if f.alive]
    return [
        app_commands.Choice(name=f"{f.name} (Equipo {f.team + 1})", value=f.name)
        for f in opts if current.lower() in f.name.lower()
    ][:25]


async def transformacion_autocomplete(interaction: discord.Interaction, current: str):
    session = ACTIVE_COMBATS.get(interaction.channel_id)
    if not session:
        return []
    nombre_personaje = interaction.namespace.personaje
    fighter = next(
        (f for f in session.fighters
         if f.owner_id == interaction.user.id
         and (not nombre_personaje or f.name.lower() == nombre_personaje.lower())),
        None,
    )
    if not fighter:
        return []
    return [
        app_commands.Choice(name=t["name"], value=t["name"])
        for t in fighter.transformaciones.values()
        if current.lower() in t["name"].lower()
    ][:25]


async def habilidad_autocomplete(interaction: discord.Interaction, current: str):
    session = ACTIVE_COMBATS.get(interaction.channel_id)
    if not session:
        return []
    nombre_personaje = interaction.namespace.personaje
    fighter = next(
        (f for f in session.fighters
         if f.owner_id == interaction.user.id
         and (not nombre_personaje or f.name.lower() == nombre_personaje.lower())),
        None,
    )
    if not fighter:
        return []

    opciones = []
    for hab_id, hab in get_ability.__globals__["HABILIDADES"].items():
        if hab.get("tipo") == "tecnica":
            continue  # las técnicas se usan con /usar_tecnica, no acá
        if hab["elemento"] != fighter.elemento:
            continue
        if hab.get("exclusiva_transformacion"):
            continue  # se habilita en la Etapa 4
        if fighter.level < min_level_for(hab["tier"]):
            continue
        if current.lower() in hab["nombre"].lower():
            opciones.append(app_commands.Choice(name=hab["nombre"], value=hab_id))
    return opciones[:25]


async def tecnica_autocomplete(interaction: discord.Interaction, current: str):
    session = ACTIVE_COMBATS.get(interaction.channel_id)
    if not session:
        return []
    nombre_personaje = interaction.namespace.personaje
    fighter = next(
        (f for f in session.fighters
         if f.owner_id == interaction.user.id
         and (not nombre_personaje or f.name.lower() == nombre_personaje.lower())),
        None,
    )
    if not fighter:
        return []

    opciones = []
    for hab_id, hab in get_ability.__globals__["HABILIDADES"].items():
        if hab.get("tipo") != "tecnica":
            continue
        if hab["elemento"] != fighter.elemento:
            continue
        if hab.get("exclusiva_transformacion"):
            continue  # se habilita en la Etapa 4
        if fighter.level < min_level_for(hab["tier"]):
            continue
        if current.lower() in hab["nombre"].lower():
            opciones.append(app_commands.Choice(name=hab["nombre"], value=hab_id))
    return opciones[:25]


async def mi_personaje_lobby_autocomplete(interaction: discord.Interaction, current: str):
    chars = await get_user_characters(interaction.user.id, include_npc=True)
    return [
        app_commands.Choice(name=c.name, value=c.name)
        for c in chars if current.lower() in c.name.lower()
    ][:25]


# ── Registro de comandos ───────────────────────────────────────────
def setup_combat_commands(bot):
    global _bot_ref
    _bot_ref = bot

    # ── /iniciar_combate ─────────────────────────────────────────
    @bot.tree.command(name="iniciar_combate", description="Crea o une un personaje a la sala de espera de combate (hasta 3 vs 3)")
    @app_commands.describe(
        personaje="Tu personaje a convocar",
        personaje2="[Solo owner] Segundo personaje a convocar",
        personaje3="[Solo owner] Tercer personaje a convocar",
    )
    @app_commands.autocomplete(
        personaje=mi_personaje_lobby_autocomplete,
        personaje2=mi_personaje_lobby_autocomplete,
        personaje3=mi_personaje_lobby_autocomplete,
    )
    async def iniciar_combate(interaction: discord.Interaction, personaje: str,
                               personaje2: str = None, personaje3: str = None):
        if interaction.channel_id in ACTIVE_COMBATS:
            await interaction.response.send_message(
                "Ya hay un combate en curso en este canal.", ephemeral=True
            )
            return

        nombres_pedidos = [n for n in (personaje, personaje2, personaje3) if n]
        if len(nombres_pedidos) > 1 and interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "Solo el anfitrión puede convocar más de un personaje a la vez.",
                ephemeral=True,
            )
            return

        characters = []
        for nombre in nombres_pedidos:
            char = await get_character(interaction.user.id, nombre)
            if not char:
                await interaction.response.send_message(
                    f"No tenés un personaje llamado **{nombre}**.", ephemeral=True
                )
                return
            characters.append(char)

        lobby = LOBBIES.get(interaction.channel_id)
        if lobby is None:
            lobby = CombatLobby(interaction.channel_id)
            LOBBIES[interaction.channel_id] = lobby

        for char in characters:
            if lobby.has_character(char):
                continue
            if len(lobby.participants) >= MAX_PARTICIPANTS:
                await interaction.response.send_message(
                    "El combate ya tiene el máximo de 6 participantes.", ephemeral=True
                )
                return
            # Asignar al equipo con menos integrantes
            team = 0 if lobby.team_count(0) <= lobby.team_count(1) else 1
            lobby.add(char, team)

        await interaction.response.send_message("Te uniste al combate.", ephemeral=True)
        await _publish(interaction, lobby, lobby.build_embed())

    # ── /cambiar_equipo ──────────────────────────────────────────
    @bot.tree.command(name="cambiar_equipo", description="Cambia de equipo dentro de la sala de espera")
    @app_commands.describe(personaje="Personaje a mover (si no lo indicás, se mueven todos los tuyos)")
    @app_commands.autocomplete(personaje=mi_personaje_lobby_autocomplete)
    async def cambiar_equipo(interaction: discord.Interaction, personaje: str = None):
        lobby = LOBBIES.get(interaction.channel_id)
        if not lobby:
            await interaction.response.send_message("No hay ningún combate en preparación aquí.", ephemeral=True)
            return

        mis_entradas = [e for e in lobby.participants if e[0].owner_id == interaction.user.id]
        if not mis_entradas:
            await interaction.response.send_message("No tenés personajes en esta sala de espera.", ephemeral=True)
            return

        if personaje:
            objetivo = [e for e in mis_entradas if e[0].name.lower() == personaje.lower()]
            if not objetivo:
                await interaction.response.send_message(f"No encontré a **{personaje}** en la sala.", ephemeral=True)
                return
            entradas_a_mover = objetivo
        else:
            entradas_a_mover = mis_entradas

        for entrada in entradas_a_mover:
            entrada[1] = 1 - entrada[1]

        await interaction.response.send_message("Cambiaste de equipo.", ephemeral=True)
        await _publish(interaction, lobby, lobby.build_embed())

    # ── /preparado ───────────────────────────────────────────────
    @bot.tree.command(name="preparado", description="Marca que estás listo para empezar el combate")
    async def preparado(interaction: discord.Interaction):
        lobby = LOBBIES.get(interaction.channel_id)
        if not lobby:
            await interaction.response.send_message("No hay ningún combate en preparación aquí.", ephemeral=True)
            return

        if interaction.user.id not in lobby.owner_ids():
            await interaction.response.send_message("No tenés personajes en esta sala de espera.", ephemeral=True)
            return

        lobby.ready_votes.add(interaction.user.id)

        if len(lobby.participants) < 2:
            await interaction.response.send_message(
                "No se puede iniciar un combate con un solo participante.", ephemeral=True
            )
            return

        if lobby.ready_votes >= lobby.owner_ids():
            # Todos listos: iniciar el combate
            fighters = []
            for char, team in lobby.participants:
                trans_rows = await get_character_transformations(char.id)
                fighters.append(Fighter(char, team, transformaciones=trans_rows))
            session = CombatSession(interaction.channel_id, fighters)
            ACTIVE_COMBATS[interaction.channel_id] = session
            del LOBBIES[interaction.channel_id]

            init_text = "\n".join(f"{name}: {roll}" for name, roll in session.initiative_log)

            # Por si la iniciativa puso a un NPC primero (antes de que
            # cualquier jugador pueda actuar), resolvemos esos turnos ya.
            texto_npc_inicial, combate_termino_de_una = await _resolver_turnos_npc(session)

            embed = session.status_embed(title="⚔️ ¡Combate iniciado!")
            embed.add_field(name="Iniciativa (1d6 + AGI)", value=init_text, inline=False)
            if texto_npc_inicial:
                embed.description = texto_npc_inicial + "\n\n" + embed.description

            await interaction.response.send_message("¡Todos listos! El combate comienza.", ephemeral=True)

            if combate_termino_de_una:
                # Caso límite: un combate donde el bando de jugadores ya
                # pierde/gana solo con la resolución de la primera tanda de
                # NPCs, sin que nadie haya llegado a actuar todavía.
                winning_team = session.winning_team()
                ganadores = [f.name for f in session.fighters if f.team == winning_team]
                embed.title = "🏆 Combate finalizado"
                embed.description += f"\n\n**Equipo {winning_team + 1} gana!** ({', '.join(ganadores)})"
                session.status_message = await interaction.channel.send(embed=embed)
                await _persist_combat_stats(session, {winning_team: "victoria", 1 - winning_team: "derrota"})
                del ACTIVE_COMBATS[interaction.channel_id]
            else:
                # Nuevo panel: es una fase distinta a la preparación, no una edición de esa.
                session.status_message = await interaction.channel.send(embed=embed)
        else:
            await interaction.response.send_message(
                f"Listo registrado ({len(lobby.ready_votes)}/{len(lobby.owner_ids())}).", ephemeral=True
            )
            await _publish(interaction, lobby, lobby.build_embed())

    # ── Helper interno de resolución de acción ──────────────────
    def _get_active_session(interaction):
        return ACTIVE_COMBATS.get(interaction.channel_id)

    def _validate_turn(session, interaction, personaje_nombre):
        """Devuelve (fighter, error_msg). Si error_msg no es None, abortar."""
        current = session.current
        if current.owner_id != interaction.user.id:
            return None, f"No es tu turno. Le toca a **{current.name}**."
        if current.name.lower() != personaje_nombre.lower():
            return None, f"En este momento actúa **{current.name}**, no {personaje_nombre}."
        return current, None

    # ── /atacar ──────────────────────────────────────────────────
    @bot.tree.command(name="atacar", description="Ataque básico con tu(s) arma(s) equipada(s), pasa el turno")
    @app_commands.describe(personaje="Tu personaje que ataca", objetivo="A quién atacás")
    @app_commands.autocomplete(personaje=personaje_autocomplete, objetivo=objetivo_autocomplete)
    async def atacar(interaction: discord.Interaction, personaje: str, objetivo: str):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate está pausado.", ephemeral=True)
            return

        attacker, err = _validate_turn(session, interaction, personaje)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        target = next((f for f in session.fighters if f.name.lower() == objetivo.lower() and f.alive), None)
        if not target or target.team == attacker.team:
            await interaction.response.send_message("Objetivo inválido.", ephemeral=True)
            return

        golpes = cmath.calcular_ataques_basicos(attacker.fue, attacker.arma_principal, attacker.arma_secundaria)

        # Paso 0: distancia. Si CUALQUIERA de los golpes de este turno no
        # puede conectar a la distancia actual, todo el ataque falla y se
        # pierde el turno sin gastar nada más (regla del documento físico).
        for golpe in golpes:
            if not cmath.puede_atacar(golpe["arma"], attacker.distancia):
                nombre_arma = golpe["arma"]["nombre"] if golpe["arma"] else "tus puños"
                await interaction.response.send_message(
                    f"**{nombre_arma}** no puede atacar a distancia {attacker.distancia}. "
                    f"Turno perdido, usá `/moverse` primero.",
                    ephemeral=True,
                )
                drain_msg = session.advance_turn()
                embed = session.status_embed()
                texto = f"❌ **{attacker.name}** falla su ataque: distancia incorrecta."
                embed.description = texto + (f"\n\n{drain_msg}" if drain_msg else "") + "\n\n" + embed.description
                await _publish(interaction, session, embed)
                return

        lineas_resultado = []
        guardia_restante = target.is_defending
        for golpe in golpes:
            arma = golpe["arma"]
            tipo_dano = arma["tipo_dano"] if arma else TIPO_DANO_PUNOS
            fue_efectiva = golpe["fue_efectiva"]
            texto, danio, evadido = cmath.resolver_golpe_fisico(
                bono_dado=fue_efectiva,
                dano_base=fue_efectiva,
                tipo_dano=tipo_dano,
                agi_efectiva_atacante=attacker.agi_efectiva,
                agi_efectiva_defensor=target.agi_efectiva,
                armadura_defensor=target.armadura_combinada,
                vit_max_defensor=target.vit_max,
                defensor_en_guardia=guardia_restante,
            )
            guardia_restante = False  # la guardia solo amortigua el primer golpe que la aprovecha
            target.vit = max(0, target.vit - danio)

            if evadido:
                target.ph = min(target.ph_max, target.ph + 2)
            else:
                ph_ganado = 2 + (1 if attacker.transformado else 0)
                attacker.ph = min(attacker.ph_max, attacker.ph + ph_ganado)

            nombre_arma = f" con **{arma['nombre']}**" if arma else ""
            lineas_resultado.append(f"⚔️ **{attacker.name}**{nombre_arma} → **{target.name}**: {texto}")

            if not target.alive:
                break

        target.is_defending = False
        result_line = "\n".join(lineas_resultado)

        terminado, msg_oleada = session.is_truly_over()
        if msg_oleada:
            result_line += f"\n\n{msg_oleada}"
        if terminado:
            await _end_combat_victory(interaction, session, result_line)
            return
          
        npc_texto, combate_termino = await _avanzar_y_resolver_npcs(session)
        texto_completo = result_line + (f"\n\n{npc_texto}" if npc_texto else "")

        if combate_termino:
            await _end_combat_victory(interaction, session, texto_completo)
            return

        embed = session.status_embed()
        embed.description = texto_completo + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
        await _publish(interaction, session, embed)
      
    # ── /defender ────────────────────────────────────────────────
    @bot.tree.command(name="defender", description="Reduce el próximo golpe a la mitad (genera +1 PH), pasa el turno")
    @app_commands.describe(personaje="Tu personaje que se defiende")
    @app_commands.autocomplete(personaje=personaje_autocomplete)
    async def defender(interaction: discord.Interaction, personaje: str):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate está pausado.", ephemeral=True)
            return

        fighter, err = _validate_turn(session, interaction, personaje)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        fighter.is_defending = True
        ph_ganado = 1 + (1 if fighter.transformado else 0)
        fighter.ph = min(fighter.ph_max, fighter.ph + ph_ganado)
        result_line = f"🛡️ **{fighter.name}** se pone en guardia. (+1 PH)"

        npc_texto, combate_termino = await _avanzar_y_resolver_npcs(session)
        texto_completo = result_line + (f"\n\n{npc_texto}" if npc_texto else "")

        if combate_termino:
            await _end_combat_victory(interaction, session, texto_completo)
            return

        embed = session.status_embed()
        embed.description = texto_completo + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
        await _publish(interaction, session, embed)

    # ── /moverse ─────────────────────────────────────────────────
    @bot.tree.command(name="moverse", description="Avanza o retrocede en distancia (consume el turno, no ataca)")
    @app_commands.describe(personaje="Tu personaje que se mueve", direccion="Acercarse o alejarse del rival")
    @app_commands.choices(direccion=[
        app_commands.Choice(name="Avanzar (acercarse)", value="avanzar"),
        app_commands.Choice(name="Retroceder (alejarse)", value="retroceder"),
    ])
    @app_commands.autocomplete(personaje=personaje_autocomplete)
    async def moverse(interaction: discord.Interaction, personaje: str, direccion: app_commands.Choice[str]):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate está pausado.", ephemeral=True)
            return

        fighter, err = _validate_turn(session, interaction, personaje)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        pasos = cmath.pasos_movimiento(fighter.agi_efectiva)
        distancia_anterior = fighter.distancia
        if direccion.value == "avanzar":
            fighter.distancia = max(0, fighter.distancia - pasos)
        else:
            fighter.distancia = min(5, fighter.distancia + pasos)

        result_line = (
            f"🏃 **{fighter.name}** {direccion.name.lower()} "
            f"({distancia_anterior} → {fighter.distancia}, {pasos} paso(s) según su AGI efectiva)."
        )

        npc_texto, combate_termino = await _avanzar_y_resolver_npcs(session)
        texto_completo = result_line + (f"\n\n{npc_texto}" if npc_texto else "")

        if combate_termino:
            await _end_combat_victory(interaction, session, texto_completo)
            return

        embed = session.status_embed()
        embed.description = texto_completo + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
        await _publish(interaction, session, embed)
  
    # ── /esperar ─────────────────────────────────────────────────
    @bot.tree.command(name="esperar", description="No hacés nada este turno")
    @app_commands.describe(personaje="Tu personaje que espera")
    @app_commands.autocomplete(personaje=personaje_autocomplete)
    async def esperar(interaction: discord.Interaction, personaje: str):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate está pausado.", ephemeral=True)
            return

        fighter, err = _validate_turn(session, interaction, personaje)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        result_line = f"⏳ **{fighter.name}** no hace nada."

        npc_texto, combate_termino = await _avanzar_y_resolver_npcs(session)
        texto_completo = result_line + (f"\n\n{npc_texto}" if npc_texto else "")

        if combate_termino:
            await _end_combat_victory(interaction, session, texto_completo)
            return

        embed = session.status_embed()
        embed.description = texto_completo + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
        await _publish(interaction, session, embed)
    
    # ── /usar_habilidad ──────────────────────────────────────────
    @bot.tree.command(name="usar_habilidad", description="Usa una habilidad de tu elemento, pasa el turno")
    @app_commands.describe(personaje="Tu personaje que actúa", habilidad="Habilidad a usar", objetivo="A quién ataca (no aplica en habilidades defensivas)")
    @app_commands.autocomplete(personaje=personaje_autocomplete, objetivo=objetivo_autocomplete, habilidad=habilidad_autocomplete)
    async def usar_habilidad(interaction: discord.Interaction, personaje: str, habilidad: str, objetivo: str = None):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate está pausado.", ephemeral=True)
            return

        attacker, err = _validate_turn(session, interaction, personaje)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        hab = get_ability(habilidad)
        if not hab:
            await interaction.response.send_message("Esa habilidad no existe en el banco.", ephemeral=True)
            return
        if hab["elemento"] != attacker.elemento:
            await interaction.response.send_message(
                f"**{attacker.name}** no puede usar habilidades de otro elemento.", ephemeral=True
            )
            return
        if hab.get("exclusiva_transformacion"):
            await interaction.response.send_message(
                "Esa habilidad requiere una transformación específica (todavía no implementado).", ephemeral=True
            )
            return

        nivel_necesario = min_level_for(hab["tier"])
        if attacker.level < nivel_necesario:
            await interaction.response.send_message(
                f"Necesitás nivel {nivel_necesario} para usar **{hab['nombre']}** (tier {hab['tier']}).",
                ephemeral=True,
            )
            return

        if attacker.ph < hab["costo_ph"] or attacker.mana < hab["costo_mana"]:
            faltante = []
            if attacker.ph < hab["costo_ph"]:
                faltante.append(f"PH ({attacker.ph}/{hab['costo_ph']})")
            if attacker.mana < hab["costo_mana"]:
                faltante.append(f"MANA ({attacker.mana}/{hab['costo_mana']})")
            await interaction.response.send_message(f"No alcanza: {', '.join(faltante)}.", ephemeral=True)
            return

        attacker.ph -= hab["costo_ph"]
        attacker.mana -= hab["costo_mana"]
        attacker.usos_habilidad_combate += 1

        # ── Habilidad defensiva: activa un escudo, no necesita objetivo ──
        if hab["tipo"] == "defensa":
            attacker.escudo = {"nombre": hab["nombre"], "elemento": hab["elemento"], "hits_taken": 0}
            result_line = f"🛡️ **{attacker.name}** activa **{hab['nombre']}** (escudo de elemento {hab['elemento']})."

            drain_msg = session.advance_turn()
            embed = session.status_embed()
            embed.description = result_line + (f"\n\n{drain_msg}" if drain_msg else "") + "\n\n" + embed.description
            await interaction.response.send_message("Acción registrada.", ephemeral=True)
            await _publish(interaction, session, embed)
            return

        # ── Habilidad de ataque ──
        if not objetivo:
            await interaction.response.send_message("Esta habilidad necesita un objetivo.", ephemeral=True)
            return

        target = next((f for f in session.fighters if f.name.lower() == objetivo.lower() and f.alive), None)
        if not target or target.team == attacker.team:
            await interaction.response.send_message("Objetivo inválido.", ephemeral=True)
            return

        damage_base = max(1, attacker.res + hab["modificador_dano"])
        texto, damage, evadido = resolver_ataque(attacker, target, attacker.res, damage_base, elemento=hab["elemento"])
        result_line = f"✨ **{attacker.name}** usa **{hab['nombre']}** contra **{target.name}**. {texto}"

        terminado, msg_oleada = session.is_truly_over()
        if msg_oleada:
            result_line += f"\n\n{msg_oleada}"
        if terminado:
            await _end_combat_victory(interaction, session, result_line)
            return

        npc_texto, combate_termino = await _avanzar_y_resolver_npcs(session)
        texto_completo = result_line + (f"\n\n{npc_texto}" if npc_texto else "")

        if combate_termino:
            await _end_combat_victory(interaction, session, texto_completo)
            return

        embed = session.status_embed()
        embed.description = texto_completo + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
        await _publish(interaction, session, embed)

    # ── /usar_tecnica ────────────────────────────────────────────
    @bot.tree.command(name="usar_tecnica", description="Usa una técnica híbrida (física + elemental), gasta PT, pasa el turno")
    @app_commands.describe(personaje="Tu personaje que actúa", tecnica="Técnica a usar", objetivo="A quién ataca")
    @app_commands.autocomplete(personaje=personaje_autocomplete, objetivo=objetivo_autocomplete, tecnica=tecnica_autocomplete)
    async def usar_tecnica(interaction: discord.Interaction, personaje: str, tecnica: str, objetivo: str):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate está pausado.", ephemeral=True)
            return

        attacker, err = _validate_turn(session, interaction, personaje)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        hab = get_ability(tecnica)
        if not hab or hab.get("tipo") != "tecnica":
            await interaction.response.send_message("Esa técnica no existe en el banco.", ephemeral=True)
            return
        if hab["elemento"] != attacker.elemento:
            await interaction.response.send_message(
                f"**{attacker.name}** no puede usar técnicas de otro elemento.", ephemeral=True
            )
            return
        if hab.get("exclusiva_transformacion"):
            await interaction.response.send_message(
                "Esa técnica requiere una transformación específica (todavía no implementado).", ephemeral=True
            )
            return

        nivel_necesario = min_level_for(hab["tier"])
        if attacker.level < nivel_necesario:
            await interaction.response.send_message(
                f"Necesitás nivel {nivel_necesario} para usar **{hab['nombre']}** (tier {hab['tier']}).",
                ephemeral=True,
            )
            return

        target = next((f for f in session.fighters if f.name.lower() == objetivo.lower() and f.alive), None)
        if not target or target.team == attacker.team:
            await interaction.response.send_message("Objetivo inválido.", ephemeral=True)
            return

        # Paso 0: distancia. Una técnica usa el arma principal (si la técnica
        # es puramente elemental y no tiene tipo_fisico, igual se valida la
        # distancia del arma equipada porque sigue siendo un golpe con ella).
        arma = attacker.arma_principal
        if not cmath.puede_atacar(arma, attacker.distancia):
            nombre_arma = arma["nombre"] if arma else "tus puños"
            await interaction.response.send_message(
                f"**{nombre_arma}** no puede atacar a distancia {attacker.distancia}. "
                f"Turno perdido, usá `/moverse` primero.",
                ephemeral=True,
            )
            drain_msg = session.advance_turn()
            embed = session.status_embed()
            texto = f"❌ **{attacker.name}** falla su técnica: distancia incorrecta."
            embed.description = texto + (f"\n\n{drain_msg}" if drain_msg else "") + "\n\n" + embed.description
            await _publish(interaction, session, embed)
            return

        # FUE efectiva de la técnica (un solo golpe, ver combat_math.fue_efectiva_tecnica)
        fue_efectiva = cmath.fue_efectiva_tecnica(attacker.fue, attacker.arma_principal, attacker.arma_secundaria)

        # Penalización si el arma equipada no coincide con el tipo_fisico de la técnica
        dano_fisico, coste_pt_final = cmath.aplicar_penalizacion_tecnica(
            fue_efectiva=fue_efectiva,
            modificador_fisico=hab["modificador_dano"],
            coste_pt_base=hab["coste_pt"],
            arma=arma,
            tipo_fisico_tecnica=hab.get("tipo_fisico"),
        )

        if attacker.ph < coste_pt_final or attacker.mana < hab["costo_mana"]:
            faltante = []
            if attacker.ph < coste_pt_final:
                faltante.append(f"PT ({attacker.ph}/{coste_pt_final})")
            if attacker.mana < hab["costo_mana"]:
                faltante.append(f"MANA ({attacker.mana}/{hab['costo_mana']})")
            await interaction.response.send_message(f"No alcanza: {', '.join(faltante)}.", ephemeral=True)
            return

        attacker.ph -= coste_pt_final
        attacker.mana -= hab["costo_mana"]
        attacker.usos_habilidad_combate += 1

        # La base de daño de una técnica suma el componente físico (ya
        # penalizado si corresponde) más RES, tal como describe la sección 6
        # del documento físico ("FUE_efectiva + RES + modificador" para técnicas).
        dano_base_total = dano_fisico + attacker.res
        tipo_dano_golpe = hab.get("tipo_fisico") or (arma["tipo_dano"] if arma else TIPO_DANO_PUNOS)

        texto, danio, evadido = cmath.resolver_golpe_fisico(
            bono_dado=fue_efectiva,
            dano_base=dano_base_total,
            tipo_dano=tipo_dano_golpe,
            agi_efectiva_atacante=attacker.agi_efectiva,
            agi_efectiva_defensor=target.agi_efectiva,
            armadura_defensor=target.armadura_combinada,
            vit_max_defensor=target.vit_max,
            defensor_en_guardia=target.is_defending,
        )
        target.is_defending = False
        target.vit = max(0, target.vit - danio)
        if evadido:
            target.ph = min(target.ph_max, target.ph + 2)
        # Nota: las técnicas NO generan PT (a diferencia de los básicos, que sí).

        result_line = f"🌀 **{attacker.name}** usa **{hab['nombre']}** contra **{target.name}**. {texto}"
        if coste_pt_final != hab["coste_pt"]:
            result_line += f" (arma incompatible con la técnica: costo de PT duplicado a {coste_pt_final})"

        terminado, msg_oleada = session.is_truly_over()
        if msg_oleada:
            result_line += f"\n\n{msg_oleada}"
        if terminado:
            await _end_combat_victory(interaction, session, result_line)
            return

        npc_texto, combate_termino = await _avanzar_y_resolver_npcs(session)
        texto_completo = result_line + (f"\n\n{npc_texto}" if npc_texto else "")

        if combate_termino:
            await _end_combat_victory(interaction, session, texto_completo)
            return

        embed = session.status_embed()
        embed.description = texto_completo + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
        await _publish(interaction, session, embed)
  
    # ── /transformar ───────────────────────────────────────────────────
    @bot.tree.command(name="transformar", description="Activa una transformación (acción libre: no gasta el turno)")
    @app_commands.describe(personaje="Tu personaje", transformacion="Transformación a activar")
    @app_commands.autocomplete(personaje=personaje_autocomplete, transformacion=transformacion_autocomplete)
    async def transformar(interaction: discord.Interaction, personaje: str, transformacion: str):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate está pausado.", ephemeral=True)
            return

        fighter, err = _validate_turn(session, interaction, personaje)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if fighter.transformado:
            await interaction.response.send_message(f"**{fighter.name}** ya está transformado.", ephemeral=True)
            return

        trans = fighter.transformaciones.get(transformacion.lower())
        if not trans:
            await interaction.response.send_message(
                f"**{fighter.name}** no tiene una transformación llamada **{transformacion}**.", ephemeral=True
            )
            return

        if fighter.ph * 2 < fighter.ph_max:
            await interaction.response.send_message(
                f"Necesitás al menos la mitad del PH máximo para transformarte ({fighter.ph}/{fighter.ph_max}).",
                ephemeral=True,
            )
            return

        fighter.activar_transformacion(trans)
        result_line = (
            f"🔥 **{fighter.name}** se transforma en **{trans['name']}**! "
            f"(−1 MANA y −{trans['ph_drain_per_turn']} PH por turno mientras dure)"
        )

        embed = session.status_embed()
        embed.description = result_line + "\n\n" + embed.description
        await interaction.response.send_message("Transformación activada. Todavía podés actuar este turno.", ephemeral=True)
        await _publish(interaction, session, embed)

    # ── /pausa ───────────────────────────────────────────────────
    @bot.tree.command(name="pausa", description="Vota para pausar el combate (requiere unanimidad)")
    async def pausa(interaction: discord.Interaction):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if session.paused:
            await interaction.response.send_message("El combate ya está pausado.", ephemeral=True)
            return
        if interaction.user.id not in session.owner_ids():
            await interaction.response.send_message("No participás en este combate.", ephemeral=True)
            return

        session.pause_votes.add(interaction.user.id)
        needed = session.owner_ids()

        if session.pause_votes >= needed:
            session.paused = True
            session.pause_votes.clear()
            view = ResumeView(session)
            await interaction.response.send_message("⏸️ **Combate pausado.**", view=view)
        else:
            await interaction.response.send_message(
                f"Voto para pausar registrado ({len(session.pause_votes)}/{len(needed)}).",
                ephemeral=True,
            )

    # ── /terminar ────────────────────────────────────────────────
    @bot.tree.command(name="terminar", description="Vota para terminar el combate sin resultado (requiere unanimidad)")
    async def terminar(interaction: discord.Interaction):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return
        if interaction.user.id not in session.owner_ids():
            await interaction.response.send_message("No participás en este combate.", ephemeral=True)
            return

        session.terminate_votes.add(interaction.user.id)
        needed = session.owner_ids()

        if session.terminate_votes >= needed:
            del ACTIVE_COMBATS[interaction.channel_id]
            await interaction.response.send_message("🏳️ **El combate terminó por acuerdo de todos los jugadores. Sin resultado.**")
        else:
            await interaction.response.send_message(
                f"Voto para terminar registrado ({len(session.terminate_votes)}/{len(needed)}).",
                ephemeral=True,
            )

    # ── /rendirse ────────────────────────────────────────────────
    @bot.tree.command(name="rendirse", description="Vota para rendir a tu equipo (requiere unanimidad del equipo)")
    @app_commands.describe(personaje="Uno de tus personajes en este combate")
    @app_commands.autocomplete(personaje=personaje_autocomplete)
    async def rendirse(interaction: discord.Interaction, personaje: str):
        session = _get_active_session(interaction)
        if not session:
            await interaction.response.send_message("No hay combate activo en este canal.", ephemeral=True)
            return

        fighter = next(
            (f for f in session.fighters
             if f.owner_id == interaction.user.id and f.name.lower() == personaje.lower()),
            None,
        )
        if not fighter:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}** en este combate.", ephemeral=True
            )
            return

        team = fighter.team
        session.surrender_votes[team].add(interaction.user.id)
        needed = session.team_owner_ids(team)

        if session.surrender_votes[team] >= needed:
            equipo_ganador = 1 - team
            ganadores = [f.name for f in session.fighters if f.team == equipo_ganador]
            embed = session.status_embed(title="🏳️ Combate finalizado por rendición")
            embed.description = (
                f"**Equipo {team + 1} se rinde.** ¡**Equipo {equipo_ganador + 1}** gana! "
                f"({', '.join(ganadores)})\n\n" + embed.description
            )
            await interaction.response.send_message("Rendición confirmada. El combate terminó.", ephemeral=True)
            await _publish(interaction, session, embed)
            await _persist_combat_stats(session, {equipo_ganador: "victoria", team: "derrota"})
            del ACTIVE_COMBATS[interaction.channel_id]
        else:
            await interaction.response.send_message(
                f"Voto para rendirse registrado ({len(session.surrender_votes[team])}/{len(needed)} de tu equipo).",
                ephemeral=True,
            )

    # Las tareas de fondo (timeouts) se arrancan desde main.py en on_ready,
    # no acá, porque en este punto todavía no hay un event loop corriendo.

async def _persist_combat_stats(session, resultados_equipo):
    """resultados_equipo: {0: 'victoria'|'derrota'|None, 1: ...}"""
    for f in session.fighters:
        resultado = resultados_equipo.get(f.team)
        await record_combat_result(
            f.character.id, resultado,
            usos_habilidad=f.usos_habilidad_combate,
            elemento=f.elemento,
        )

async def _end_combat_victory(interaction, session, result_line):
    winning_team = session.winning_team()
    ganadores = [f.name for f in session.fighters if f.team == winning_team]
    embed = session.status_embed(title="🏆 Combate finalizado")
    embed.description = (
        result_line + f"\n\n**Equipo {winning_team + 1} gana!** ({', '.join(ganadores)})\n\n"
        + embed.description
    )
    await interaction.response.send_message("Combate finalizado.", ephemeral=True)
    await _publish(interaction, session, embed)
    await _persist_combat_stats(session, {winning_team: "victoria", 1 - winning_team: "derrota"})
    del ACTIVE_COMBATS[interaction.channel_id]


def start_background_tasks():
    """
    Arranca las tareas de fondo (timeouts). Hay que llamarla desde
    on_ready del bot, nunca antes, porque recién ahí existe un
    event loop corriendo.
    """
    if not check_turn_timeouts.is_running():
        check_turn_timeouts.start()
    if not check_lobby_timeouts.is_running():
        check_lobby_timeouts.start()

# ── Tarea: revisa timeouts de turno (10 minutos sin actuar) ─────────
@tasks.loop(seconds=60)
async def check_turn_timeouts():
    now = time.time()
    for channel_id in list(ACTIVE_COMBATS.keys()):
        session = ACTIVE_COMBATS[channel_id]
        if session.paused:
            continue
        if (now - session.last_action_time) > TURN_TIMEOUT_SECONDS:
            offender = session.current
            await apply_level_penalty(offender.character)
            del ACTIVE_COMBATS[channel_id]

            if _bot_ref:
                channel = _bot_ref.get_channel(channel_id)
                if channel:
                    penalty_text = (
                        f" **{offender.name}** pierde 1 nivel por inactividad."
                        if not offender.is_npc else ""
                    )
                    await channel.send(
                        f"⏱️ **El combate terminó por timeout.** "
                        f"**{offender.name}** no actuó en 10 minutos.{penalty_text}"
                    )


# ── Tarea: revisa timeouts de lobby (5 minutos sin empezar) ─────────
@tasks.loop(seconds=30)
async def check_lobby_timeouts():
    for channel_id in list(LOBBIES.keys()):
        lobby = LOBBIES[channel_id]
        if lobby.is_expired():
            del LOBBIES[channel_id]
            if _bot_ref:
                channel = _bot_ref.get_channel(channel_id)
                if channel:
                    await channel.send("⌛ Se canceló el combate.")
