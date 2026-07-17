"""
commands/expedition.py
-----------------------
Comandos de exploración. Flujo:
  1. /iniciar_expedicion   → abre un lobby (máx. 4 personajes, máx. 3
     lobbies simultáneos por canal). Quien lo crea queda como líder.
  2. /unirse_expedicion    → se suma al lobby, o entra a una expedición
     ya en curso solo si /enviar_ayviar abrió cupos.
  3. /preparado_expedicion → vota listo; con todos listos, se crea la
     expedición real (hasta 3 activas simultáneas por hilo) y se cierra el lobby.
  4. /enviar_ayviar        → SOLO el líder, UNA vez por expedición: pide
     ayuda, pinguea el rol de Nexus y abre hasta 3 cupos para que se
     sumen jugadores nuevos (si hay lugar libre o alguien incapacitado).
     Se puede usar aunque el grupo esté en medio de un combate.

La lógica de /explorar y /acampar se agrega en una etapa posterior.
"""

import time
import discord
from discord import app_commands

import asyncio
import random

from store.characters_store import (
    get_character, get_user_characters, reset_energia_global,
    get_character_by_id, update_energia,
)
from store.expedition_store import (
    get_zona, get_gif_zona, get_enemy, create_expedition,
    add_participant, get_participant_ids, get_pistas_publicas,
    get_active_expeditions_by_thread, get_active_expedition_for_owner,
    get_expedition_esperando_ayviar, usar_ayviar, consumir_cupo_ayviar,
    agregar_loot, incrementar_exploraciones, sumar_pista,
    finalizar_expedition, construir_personaje_enemigo,
)
from maths.expedition_math import (
    ENERGIA_MAXIMA, COSTE_EXPLORAR, gastar_energia, grupo_incapacitado,
    sortear_recurso, sortear_enemigo, hay_pista, cantidad_enemigos_hostiles,
)
from database import get_db_connection
from config import OWNER_ID, AYVIAR_ROLE_ID
from session_guard import usuario_ocupado

MAX_PARTICIPANTES_EXPEDICION = 4
MAX_SESIONES_POR_CANAL = 3
LOBBY_EXPEDICION_TIMEOUT_SECONDS = 5 * 60  # 5 minutos, igual que el de combate
AYVIAR_CUPOS = 3

# {channel_id: [ExpeditionLobby, ...]} — hasta MAX_SESIONES_POR_CANAL por canal
LOBBIES_EXPEDICION = {}

_bot_ref = None  # se setea en setup_expedition_commands, para la tarea de timeout


def _agregar_lobby(channel_id, lobby):
    LOBBIES_EXPEDICION.setdefault(channel_id, []).append(lobby)


def _quitar_lobby(channel_id, lobby):
    lista = LOBBIES_EXPEDICION.get(channel_id)
    if not lista:
        return
    if lobby in lista:
        lista.remove(lobby)
    if not lista:
        del LOBBIES_EXPEDICION[channel_id]


def _lobby_de_owner(channel_id, owner_id):
    for lobby in LOBBIES_EXPEDICION.get(channel_id, []):
        if owner_id in lobby.owner_ids():
            return lobby
    return None


class ExpeditionLobby:
    def __init__(self, channel_id, zona_id, zona_nombre, lider_owner_id):
        self.channel_id = channel_id
        self.zona_id = zona_id
        self.zona_nombre = zona_nombre
        self.lider_owner_id = lider_owner_id
        self.participants = []   # lista de Character
        self.ready_votes = set()
        self.created_at = time.time()
        self.status_message = None

    def owner_ids(self):
        return set(c.owner_id for c in self.participants)

    def has_owner(self, owner_id):
        return any(c.owner_id == owner_id for c in self.participants)

    def is_expired(self):
        return (time.time() - self.created_at) > LOBBY_EXPEDICION_TIMEOUT_SECONDS

    def build_embed(self):
        nombres = [c.name for c in self.participants]
        embed = discord.Embed(
            title=f"🗺️ Preparación de expedición: {self.zona_nombre}",
            description=(
                f"**Participantes ({len(self.participants)}/{MAX_PARTICIPANTES_EXPEDICION}):** "
                f"{', '.join(nombres) if nombres else '—'}\n\n"
                f"Listos: {len(self.ready_votes)}/{len(self.owner_ids())}\n\n"
                f"Usá `/unirse_expedicion` para sumarte y `/preparado_expedicion` cuando estés listo.\n"
                f"El lobby se cancela solo si no todos confirman en 5 minutos."
            ),
            color=discord.Color.blurple(),
        )
        return embed


async def personaje_propio_autocomplete(interaction: discord.Interaction, current: str):
    chars = await get_user_characters(interaction.user.id, include_npc=False)
    return [
        app_commands.Choice(name=c.name, value=c.name)
        for c in chars if current.lower() in c.name.lower()
    ][:25]


async def zona_autocomplete(interaction: discord.Interaction, current: str):
    from store.expedition_store import ZONAS
    return [
        app_commands.Choice(name=datos["nombre"], value=zona_id)
        for zona_id, datos in ZONAS.items()
        if current.lower() in datos["nombre"].lower()
    ][:25]


async def _publish_lobby(interaction, lobby):
    if lobby.status_message:
        try:
            await lobby.status_message.edit(embed=lobby.build_embed())
            return
        except discord.NotFound:
            pass
    lobby.status_message = await interaction.channel.send(embed=lobby.build_embed())

async def _iniciar_combate_expedicion(interaction, expedition, enemy_ids, personajes):
    """
    Crea un CombatSession real (el mismo sistema de combate.py) con los
    participantes de la expedición como equipo 0 y los enemy_ids dados
    como equipo 1. Queda registrado en ACTIVE_COMBATS del canal, y se le
    marca expedition_id para poder engancharlo con el loot más adelante.
    """
    from commands.combat import (
        Fighter, CombatSession, ACTIVE_COMBATS, MAX_SESIONES_POR_CANAL,
        _agregar_combate, _resolver_turnos_npc,
    )

    combates_actuales = ACTIVE_COMBATS.get(interaction.channel_id, [])
    if len(combates_actuales) >= MAX_SESIONES_POR_CANAL:
        await interaction.followup.send(
            "⚠️ Hay demasiados combates activos en este canal ahora mismo — "
            "el encuentro se resuelve como un cruce sin consecuencias por esta vez."
        )
        return

    jugadores_fighters = [Fighter(char, team=0) for char in personajes]
    enemigos_fighters = [Fighter(construir_personaje_enemigo(eid), team=1) for eid in enemy_ids]

    session = CombatSession(interaction.channel_id, jugadores_fighters + enemigos_fighters)
    session.expedition_id = expedition["id"]  # usado en la Etapa 5.5 para repartir el loot al ganar
    _agregar_combate(interaction.channel_id, session)

    texto_npc_inicial, combate_termino_de_una = await _resolver_turnos_npc(session)

    nombres_enemigos = ", ".join(f.name for f in enemigos_fighters)
    init_text = "\n".join(f"{name}: {roll}" for name, roll in session.initiative_log)
    embed = session.status_embed(title=f"⚔️ ¡Emboscada! Aparece: {nombres_enemigos}")
    embed.add_field(name="Iniciativa (1d6 + AGI)", value=init_text, inline=False)
    if texto_npc_inicial:
        embed.description = texto_npc_inicial + "\n\n" + embed.description

    await interaction.followup.send("¡El combate comienza!")
    session.status_message = await interaction.channel.send(embed=embed)
    # combate_termino_de_una (caso límite de iniciativa) se deja para la Etapa 5.5,
    # donde se conecta el cierre de combate con finalizar_expedition/loot.

class NeutralEncounterView(discord.ui.View):
    """Botones para decidir atacar u observar a una criatura neutral encontrada al explorar."""

    def __init__(self, expedition, enemy_id, personajes, channel_id):
        super().__init__(timeout=120)
        self.expedition = expedition
        self.enemy_id = enemy_id
        self.personajes = personajes
        self.channel_id = channel_id
        self.owner_ids = set(c.owner_id for c in personajes)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.owner_ids:
            await interaction.response.send_message("No participás de esta expedición.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Atacar", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def atacar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        enemy_data = get_enemy(self.enemy_id)
        if self.enemy_id == "arpia_menor":
            await interaction.followup.send(
                "⚠️ ¡Atacaron a una arpía menor! El resto de la bandada no lo va a perdonar..."
            )
        await interaction.followup.send(f"El grupo decide atacar a **{enemy_data['nombre']}**.")
        await _iniciar_combate_expedicion(interaction, self.expedition, [self.enemy_id], self.personajes)

    @discord.ui.button(label="Observar", style=discord.ButtonStyle.secondary, emoji="👀")
    async def observar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        enemy_data = get_enemy(self.enemy_id)
        loot_posible = enemy_data.get("loot_al_observar", {})
        for material_id, prob in loot_posible.items():
            if random.random() < prob:
                await agregar_loot(self.expedition["id"], material_id, 1)
                await interaction.followup.send(
                    f"👁️ El grupo observa a **{enemy_data['nombre']}** en silencio... "
                    f"y encuentra **1x {material_id}** que dejó caer."
                )
                return
        await interaction.followup.send(f"👁️ El grupo observa a **{enemy_data['nombre']}** y sigue su camino.")

def setup_expedition_commands(bot):
    global _bot_ref
    _bot_ref = bot

    # ── /iniciar_expedicion ──────────────────────────────────────
    @bot.tree.command(name="iniciar_expedicion", description="Abre un lobby de expedición (máximo 4 personajes)")
    @app_commands.describe(zona="Zona a explorar", personaje="Tu personaje que participa")
    @app_commands.autocomplete(zona=zona_autocomplete, personaje=personaje_propio_autocomplete)
    async def iniciar_expedicion(interaction: discord.Interaction, zona: str, personaje: str):
        if await usuario_ocupado(interaction.user.id):
            await interaction.response.send_message(
                "Ya estás en un combate o expedición activa. No podés iniciar otro.", ephemeral=True
            )
            return

        zona_datos = get_zona(zona)
        if not zona_datos:
            await interaction.response.send_message("Esa zona no existe.", ephemeral=True)
            return

        lobbies_actuales = LOBBIES_EXPEDICION.get(interaction.channel_id, [])
        if len(lobbies_actuales) >= MAX_SESIONES_POR_CANAL:
            await interaction.response.send_message(
                f"Ya hay {MAX_SESIONES_POR_CANAL} lobbies de expedición preparándose aquí. Esperá a que alguno empiece.",
                ephemeral=True,
            )
            return

        activas_actuales = await get_active_expeditions_by_thread(interaction.channel_id)
        if len(activas_actuales) >= MAX_SESIONES_POR_CANAL:
            await interaction.response.send_message(
                f"Ya hay {MAX_SESIONES_POR_CANAL} expediciones en curso en este canal.", ephemeral=True
            )
            return

        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        lobby = ExpeditionLobby(interaction.channel_id, zona, zona_datos["nombre"], interaction.user.id)
        lobby.participants.append(char)
        _agregar_lobby(interaction.channel_id, lobby)

        await interaction.response.send_message(embed=lobby.build_embed())
        lobby.status_message = await interaction.original_response()

    # ── /unirse_expedicion ───────────────────────────────────────
    @bot.tree.command(name="unirse_expedicion", description="Suma tu personaje al lobby, o entra vía ayviar si hay cupo")
    @app_commands.describe(personaje="Tu personaje que se suma")
    @app_commands.autocomplete(personaje=personaje_propio_autocomplete)
    async def unirse_expedicion(interaction: discord.Interaction, personaje: str):
        if await usuario_ocupado(interaction.user.id):
            await interaction.response.send_message(
                "Ya estás en un combate o expedición activa.", ephemeral=True
            )
            return

        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        # Caso 1: hay un lobby tuyo o con lugar en este canal
        lobby = _lobby_de_owner(interaction.channel_id, interaction.user.id)
        if not lobby:
            for candidato in LOBBIES_EXPEDICION.get(interaction.channel_id, []):
                if len(candidato.participants) < MAX_PARTICIPANTES_EXPEDICION:
                    lobby = candidato
                    break

        if lobby:
            if any(c.id == char.id for c in lobby.participants):
                await interaction.response.send_message(f"**{char.name}** ya está en el lobby.", ephemeral=True)
                return
            if len(lobby.participants) >= MAX_PARTICIPANTES_EXPEDICION:
                await interaction.response.send_message("Ese lobby ya está completo (4/4).", ephemeral=True)
                return
            lobby.participants.append(char)
            await interaction.response.send_message(f"**{char.name}** se unió al lobby.", ephemeral=True)
            await _publish_lobby(interaction, lobby)
            return

        # Caso 2: no hay lobby — buscar una expedición en curso con cupos de ayviar abiertos
        expedition = await get_expedition_esperando_ayviar(interaction.channel_id)
        if not expedition:
            await interaction.response.send_message(
                "No hay ningún lobby ni cupo de ayviar abierto en este canal ahora mismo.",
                ephemeral=True,
            )
            return

        participantes_actuales = await get_participant_ids(expedition["id"])
        if char.id in participantes_actuales:
            await interaction.response.send_message(f"**{char.name}** ya está en esa expedición.", ephemeral=True)
            return

        await add_participant(expedition["id"], char.id)
        await consumir_cupo_ayviar(expedition["id"])
        await interaction.response.send_message(
            f"🐣 **{char.name}** respondió al llamado del ayviar y se unió a la expedición."
        )

    # ── /preparado_expedicion ────────────────────────────────────
    @bot.tree.command(name="preparado_expedicion", description="Marca que estás listo para empezar la expedición")
    async def preparado_expedicion(interaction: discord.Interaction):
        lobby = _lobby_de_owner(interaction.channel_id, interaction.user.id)
        if not lobby:
            await interaction.response.send_message("No tenés ningún lobby de expedición aquí.", ephemeral=True)
            return

        lobby.ready_votes.add(interaction.user.id)

        if lobby.ready_votes < lobby.owner_ids():
            await interaction.response.send_message(
                f"Listo registrado ({len(lobby.ready_votes)}/{len(lobby.owner_ids())}).", ephemeral=True
            )
            await _publish_lobby(interaction, lobby)
            return

        activas_actuales = await get_active_expeditions_by_thread(interaction.channel_id)
        if len(activas_actuales) >= MAX_SESIONES_POR_CANAL:
            await interaction.response.send_message(
                f"Ya hay {MAX_SESIONES_POR_CANAL} expediciones en curso en este canal. Esperá a que alguna termine.",
                ephemeral=True,
            )
            return

        pistas_iniciales = await get_pistas_publicas(lobby.zona_id)
        expedition = await create_expedition(
            lobby.channel_id, lobby.zona_id, lobby.lider_owner_id, pistas_iniciales
        )
        for char in lobby.participants:
            await add_participant(expedition["id"], char.id)
        _quitar_lobby(interaction.channel_id, lobby)

        texto_pistas = (
            f" (arranca con {pistas_iniciales} pista(s) ya conocidas públicamente)"
            if pistas_iniciales > 0 else ""
        )
        embed = discord.Embed(
            title=f"🗺️ ¡Expedición en marcha!: {lobby.zona_nombre}",
            description=(
                f"Participantes: {', '.join(c.name for c in lobby.participants)}{texto_pistas}\n\n"
                f"Usá `/explorar` para avanzar."
            ),
            color=discord.Color.green(),
        )
        gif_url = get_gif_zona(lobby.zona_id)
        if gif_url:
            embed.set_image(url=gif_url)

        await interaction.response.send_message("¡Todos listos! La expedición comienza.")
        await interaction.channel.send(embed=embed)

    # ── /enviar_ayviar ───────────────────────────────────────────
    @bot.tree.command(name="enviar_ayviar", description="[Solo el líder] Pide ayuda urgente, una sola vez por expedición")
    async def enviar_ayviar(interaction: discord.Interaction):
        expedition = await get_active_expedition_for_owner(interaction.channel_id, interaction.user.id)
        if not expedition:
            await interaction.response.send_message(
                "No participás de ninguna expedición activa en este canal.", ephemeral=True
            )
            return

        if expedition["lider_owner_id"] != interaction.user.id:
            await interaction.response.send_message(
                "Solo el líder de la expedición puede pedir ayuda con el ayviar.", ephemeral=True
            )
            return

        if expedition["ayviar_usado"]:
            await interaction.response.send_message(
                "Ya usaste el ayviar en esta expedición. Solo se puede una vez.", ephemeral=True
            )
            return

        participant_ids = await get_participant_ids(expedition["id"])
        conn = await get_db_connection()
        try:
            cursor = await conn.execute(
                f"SELECT energia FROM characters WHERE id IN ({','.join('?' * len(participant_ids))})",
                tuple(participant_ids),
            )
            rows = await cursor.fetchall()
        finally:
            await conn.close()

        hay_incapacitado = any(row["energia"] <= 0 for row in rows)
        hay_lugar = len(participant_ids) < MAX_PARTICIPANTES_EXPEDICION

        if not hay_incapacitado and not hay_lugar:
            await interaction.response.send_message(
                "El grupo está completo y nadie está incapacitado — no hace falta pedir ayuda todavía.",
                ephemeral=True,
            )
            return

        await usar_ayviar(expedition["id"], cupos=AYVIAR_CUPOS)

        mencion_rol = f"<@&{AYVIAR_ROLE_ID}>" if AYVIAR_ROLE_ID else "@aquí"
        await interaction.response.send_message(
            f"🐣📯 {mencion_rol} ¡Se necesita ayuda urgente en la expedición! "
            f"Un ayviar chillón sale volando a pedir refuerzos. "
            f"Hasta {AYVIAR_CUPOS} personas pueden usar `/unirse_expedicion` ahora mismo."
        )

    # ── /energia_global ──────────────────────────────────────────
    @bot.tree.command(name="energia_global", description="[Solo owner] Repone la energía de TODOS los personajes al máximo")
    async def energia_global(interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No tenés permiso para usar este comando.", ephemeral=True)
            return
        await reset_energia_global()
        await interaction.response.send_message("🔋 Energía de todos los personajes repuesta al máximo.")

    # ── /explorar ────────────────────────────────────────────────
    @bot.tree.command(name="explorar", description="El grupo explora la zona (gasta 1 energía a todos)")
    async def explorar(interaction: discord.Interaction, personaje: str = None):
        expedition = await get_active_expedition_for_owner(interaction.channel_id, interaction.user.id)
        if not expedition:
            await interaction.response.send_message(
                "No participás de ninguna expedición activa en este canal.", ephemeral=True
            )
            return

        await interaction.response.defer()

        participant_ids = await get_participant_ids(expedition["id"])
        personajes = [await get_character_by_id(cid) for cid in participant_ids]

        # Gastar energía a todos los participantes (todos exploran a la vez)
        for char in personajes:
            nueva_energia = gastar_energia(char.energia, COSTE_EXPLORAR)
            await update_energia(char.id, nueva_energia)
            char.energia = nueva_energia

        # Si el grupo entero quedó incapacitado, la expedición fracasa acá mismo.
        if grupo_incapacitado([c.energia for c in personajes]):
            await finalizar_expedition(expedition["id"], exito=False)
            await interaction.followup.send(
                "💤 **Todo el grupo quedó incapacitado.** La expedición fracasa y se pierde "
                "todo lo recolectado (excepto la experiencia)."
            )
            return

        zona = get_zona(expedition["zona_id"])
        exploraciones_nuevas = await incrementar_exploraciones(expedition["id"])

        # Evento de pista: se anuncia solo, con unos segundos de suspenso,
        # ANTES de mostrar el resultado normal de la exploración.
        if hay_pista(zona["pista"], exploraciones_nuevas):
            pistas_nuevas = await sumar_pista(expedition["id"])
            embed_pista = discord.Embed(
                title="🔍 ¡Pista encontrada!",
                description=(
                    f"El grupo encuentra una pista en **{zona['nombre']}**. "
                    f"Pistas: {pistas_nuevas}/{zona['pistas_necesarias']}."
                ),
                color=discord.Color.gold(),
            )
            await interaction.followup.send(embed=embed_pista)
            await asyncio.sleep(3)

        # Resultado normal de la exploración: 50/50 recurso vs enemigo.
        if random.random() < 0.5:
            resultado = sortear_recurso(zona)
            if not resultado:
                await interaction.followup.send("El grupo explora, pero no encuentra nada esta vez.")
                return
            material_id, cantidad = resultado
            await agregar_loot(expedition["id"], material_id, cantidad)
            await interaction.followup.send(
                f"🌿 El grupo encuentra **{cantidad}x {material_id}**. Se guarda en el botín de la expedición."
            )
            return

        enemy_id = sortear_enemigo(zona)
        if not enemy_id:
            await interaction.followup.send("El grupo explora, pero no encuentra nada esta vez.")
            return

        enemy_data = get_enemy(enemy_id)

        if enemy_data.get("huye"):
            await interaction.followup.send(
                f"🏃 Un **{enemy_data['nombre']}** aparece, pero huye antes de que puedan reaccionar."
            )
            return

        if enemy_data.get("neutral"):
            view = NeutralEncounterView(expedition, enemy_id, personajes, interaction.channel_id)
            await interaction.followup.send(
                f"👁️ El grupo se topa con **{enemy_data['nombre']}**. Parece tranquilo... ¿qué hacen?",
                view=view,
            )
            return

        # Hostil: combate inmediato, sin elección.
        cantidad_hostiles = cantidad_enemigos_hostiles()
        await _iniciar_combate_expedicion(
            interaction, expedition, [enemy_id] * cantidad_hostiles, personajes
        )

def start_expedition_background_tasks():
    """Llamar desde on_ready, igual que start_background_tasks() de combat.py."""
    if not check_expedition_lobby_timeouts.is_running():
        check_expedition_lobby_timeouts.start()


from discord.ext import tasks

@tasks.loop(seconds=30)
async def check_expedition_lobby_timeouts():
    for channel_id in list(LOBBIES_EXPEDICION.keys()):
        for lobby in list(LOBBIES_EXPEDICION.get(channel_id, [])):
            if lobby.is_expired():
                _quitar_lobby(channel_id, lobby)
                if _bot_ref:
                    channel = _bot_ref.get_channel(channel_id)
                    if channel:
                        await channel.send(f"⌛ Se canceló un lobby de expedición ({lobby.zona_nombre}) por inactividad.")
