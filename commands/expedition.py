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

from store.characters_store import get_character, get_user_characters, reset_energia_global
from store.expedition_store import (
    get_zona, get_gif_zona, create_expedition,
    add_participant, get_participant_ids, get_pistas_publicas,
    get_active_expeditions_by_thread, get_active_expedition_for_owner,
    get_expedition_esperando_ayviar, usar_ayviar, consumir_cupo_ayviar,
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
