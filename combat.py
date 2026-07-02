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
from discord import app_commands
from discord.ext import tasks

from config import OWNER_ID
from characters_store import get_character, get_user_characters, apply_level_penalty

TURN_TIMEOUT_SECONDS = 10 * 60   # 10 minutos
LOBBY_TIMEOUT_SECONDS = 5 * 60   # 5 minutos
MAX_PARTICIPANTS = 6             # 3 vs 3


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
    def __init__(self, character, team):
        self.character = character          # referencia a la ficha permanente
        self.owner_id = character.owner_id
        self.name = character.name
        self.is_npc = character.is_npc
        self.team = team                    # 0 o 1

        self.vit_max = character.vit_max
        self.vit = character.vit_max
        self.mana_max = character.mana_max
        self.mana = character.mana_max
        self.fue = character.fue
        self.res = character.res
        self.agi = character.agi
        self.ph = 0
        self.is_defending = False

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
        )


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
    def __init__(self, channel_id, fighters):
        self.channel_id = channel_id
        self.fighters = fighters   # lista de Fighter, orden = orden de turnos
        self.turn_index = 0
        self.round_number = 1
        self.paused = False
        self.pause_votes = set()
        self.resume_votes = set()
        self.terminate_votes = set()
        self.last_action_time = time.time()
        self.status_message = None   # mensaje del panel, se edita en vez de duplicarse
        self._roll_initiative()

    def _roll_initiative(self):
        rolls = [(random.randint(1, 6) + f.agi, f) for f in self.fighters]
        rolls.sort(key=lambda x: x[0], reverse=True)
        self.fighters = [f for _, f in rolls]
        self.initiative_log = [(f.name, r) for r, f in rolls]

    def owner_ids(self):
        return set(f.owner_id for f in self.fighters)

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

    def team_alive(self, team):
        return any(f.alive for f in self.fighters if f.team == team)

    def is_over(self):
        return not self.team_alive(0) or not self.team_alive(1)

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


async def mi_personaje_lobby_autocomplete(interaction: discord.Interaction, current: str):
    chars = get_user_characters(interaction.user.id, include_npc=True)
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
            char = get_character(interaction.user.id, nombre)
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
            fighters = [Fighter(char, team) for char, team in lobby.participants]
            session = CombatSession(interaction.channel_id, fighters)
            ACTIVE_COMBATS[interaction.channel_id] = session
            del LOBBIES[interaction.channel_id]

            init_text = "\n".join(f"{name}: {roll}" for name, roll in session.initiative_log)
            embed = session.status_embed(title="⚔️ ¡Combate iniciado!")
            embed.add_field(name="Iniciativa (1d6 + AGI)", value=init_text, inline=False)

            await interaction.response.send_message("¡Todos listos! El combate comienza.", ephemeral=True)
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
    @bot.tree.command(name="atacar", description="Ataque básico (genera +2 PH), pasa el turno")
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

        damage = max(1, attacker.fue - target.defense)
        if target.is_defending:
            damage = max(1, damage // 2)
            target.is_defending = False

        target.vit = max(0, target.vit - damage)
        attacker.ph = min(attacker.ph_max, attacker.ph + 2)

        result_line = f"⚔️ **{attacker.name}** ataca a **{target.name}** causando **{damage}** de daño. (+2 PH)"

        if session.is_over():
            await _end_combat_victory(interaction, session, result_line)
            return

        session.advance_turn()
        embed = session.status_embed()
        embed.description = result_line + "\n\n" + embed.description
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
        fighter.ph = min(fighter.ph_max, fighter.ph + 1)
        result_line = f"🛡️ **{fighter.name}** se pone en guardia. (+1 PH)"

        session.advance_turn()
        embed = session.status_embed()
        embed.description = result_line + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
        await _publish(interaction, session, embed)

    # ── /usar_habilidad ──────────────────────────────────────────
    @bot.tree.command(name="usar_habilidad", description="Usa una habilidad con costo definido, pasa el turno")
    @app_commands.describe(
        personaje="Tu personaje que actúa", objetivo="A quién ataca la habilidad",
        nombre="Nombre de la habilidad", costo_ph="Costo en PH",
        costo_mana="Costo en MANA", dano="Daño que causa",
    )
    @app_commands.autocomplete(personaje=personaje_autocomplete, objetivo=objetivo_autocomplete)
    async def usar_habilidad(interaction: discord.Interaction, personaje: str, objetivo: str,
                              nombre: str, costo_ph: int, costo_mana: int, dano: int):
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

        if attacker.ph < costo_ph or attacker.mana < costo_mana:
            faltante = []
            if attacker.ph < costo_ph:
                faltante.append(f"PH ({attacker.ph}/{costo_ph})")
            if attacker.mana < costo_mana:
                faltante.append(f"MANA ({attacker.mana}/{costo_mana})")
            await interaction.response.send_message(
                f"No alcanza: {', '.join(faltante)}.", ephemeral=True
            )
            return

        attacker.ph -= costo_ph
        attacker.mana -= costo_mana

        # A diferencia de /atacar, el daño de una habilidad ya es el valor
        # que definiste al usarla. No se le resta la defensa del objetivo
        # porque ese número ya representa el efecto final de la habilidad.
        damage = max(1, dano)
        if target.is_defending:
            damage = max(1, damage // 2)
            target.is_defending = False
        target.vit = max(0, target.vit - damage)

        result_line = (
            f"✨ **{attacker.name}** usa **{nombre}** contra **{target.name}** "
            f"causando **{damage}** de daño. (−{costo_ph} PH  −{costo_mana} MANA)"
        )

        if session.is_over():
            await _end_combat_victory(interaction, session, result_line)
            return

        session.advance_turn()
        embed = session.status_embed()
        embed.description = result_line + "\n\n" + embed.description
        await interaction.response.send_message("Acción registrada.", ephemeral=True)
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

    # Las tareas de fondo (timeouts) se arrancan desde main.py en on_ready,
    # no acá, porque en este punto todavía no hay un event loop corriendo.


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
            apply_level_penalty(offender.character)
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
