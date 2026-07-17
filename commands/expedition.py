"""
commands/expedition.py
-----------------------
Comandos de exploración. Flujo:
  1. /iniciar_expedicion → abre un lobby en el hilo (máx. 4, como el
     lobby de combate). El líder elige la zona.
  2. /unirse_expedicion  → se suma al lobby (antes de empezar) o, si la
     expedición ya está en curso, solo entra si /enviar_ayviar abrió la
     puerta y hay lugar o alguien incapacitado.
  3. /preparado_expedicion → vota listo; con todos listos, se crea la
     expedición de verdad en la BD y el lobby se cierra.
  4. /enviar_ayviar → pide ayuda: pinguea un rol y abre la puerta de
     ingreso si hay cupo libre o alguien incapacitado.

La lógica de /explorar y /acampar se agrega en la Etapa 5.4.2, sobre
este mismo archivo.
"""

import time
import discord
from discord import app_commands

from store.characters_store import get_character, get_user_characters, reset_energia_global
from store.expedition_store import (
    get_zona, get_active_expedition, create_expedition,
    add_participant, get_participant_ids, get_pistas_publicas,
    set_ayviar_activo,
)
from database import get_db_connection
from config import OWNER_ID, AYVIAR_ROLE_ID

MAX_PARTICIPANTES_EXPEDICION = 4

# Lobbies en memoria, igual que LOBBIES de combat.py — un lobby por hilo,
# se descarta al iniciar la expedición de verdad (o si nadie lo usa).
LOBBIES_EXPEDICION = {}


class ExpeditionLobby:
    def __init__(self, thread_id, zona_id, zona_nombre):
        self.thread_id = thread_id
        self.zona_id = zona_id
        self.zona_nombre = zona_nombre
        self.participants = []   # lista de Character
        self.ready_votes = set()
        self.created_at = time.time()
        self.status_message = None

    def owner_ids(self):
        return set(c.owner_id for c in self.participants)

    def has_owner(self, owner_id):
        return any(c.owner_id == owner_id for c in self.participants)

    def build_embed(self):
        nombres = [c.name for c in self.participants]
        embed = discord.Embed(
            title=f"🗺️ Preparación de expedición: {self.zona_nombre}",
            description=(
                f"**Participantes ({len(self.participants)}/{MAX_PARTICIPANTES_EXPEDICION}):** "
                f"{', '.join(nombres) if nombres else '—'}\n\n"
                f"Listos: {len(self.ready_votes)}/{len(self.owner_ids())}\n\n"
                f"Usá `/unirse_expedicion` para sumarte y `/preparado_expedicion` cuando estés listo."
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

    # ── /iniciar_expedicion ──────────────────────────────────────
    @bot.tree.command(name="iniciar_expedicion", description="Abre un lobby de expedición en este hilo (máximo 4)")
    @app_commands.describe(zona="Zona a explorar", personaje="Tu personaje que participa")
    @app_commands.autocomplete(zona=zona_autocomplete, personaje=personaje_propio_autocomplete)
    async def iniciar_expedicion(interaction: discord.Interaction, zona: str, personaje: str):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "Las expediciones solo se pueden iniciar dentro de un hilo (la zona).", ephemeral=True
            )
            return

        zona_datos = get_zona(zona)
        if not zona_datos:
            await interaction.response.send_message("Esa zona no existe.", ephemeral=True)
            return

        if interaction.channel_id in LOBBIES_EXPEDICION:
            await interaction.response.send_message(
                "Ya hay un lobby de expedición abierto en este hilo. Usá `/unirse_expedicion`.", ephemeral=True
            )
            return

        if await get_active_expedition(interaction.channel_id):
            await interaction.response.send_message(
                "Ya hay una expedición en curso en este hilo.", ephemeral=True
            )
            return

        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        lobby = ExpeditionLobby(interaction.channel_id, zona, zona_datos["nombre"])
        lobby.participants.append(char)
        LOBBIES_EXPEDICION[interaction.channel_id] = lobby

        await interaction.response.send_message(embed=lobby.build_embed())
        lobby.status_message = await interaction.original_response()

    # ── /unirse_expedicion ───────────────────────────────────────
    @bot.tree.command(name="unirse_expedicion", description="Suma tu personaje al lobby, o entra a una expedición en curso si hay lugar/ayviar")
    @app_commands.describe(personaje="Tu personaje que se suma")
    @app_commands.autocomplete(personaje=personaje_propio_autocomplete)
    async def unirse_expedicion(interaction: discord.Interaction, personaje: str):
        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        # Caso 1: todavía está en lobby (expedición sin empezar)
        lobby = LOBBIES_EXPEDICION.get(interaction.channel_id)
        if lobby:
            if len(lobby.participants) >= MAX_PARTICIPANTES_EXPEDICION:
                await interaction.response.send_message("El lobby ya está completo (4/4).", ephemeral=True)
                return
            if any(c.id == char.id for c in lobby.participants):
                await interaction.response.send_message(f"**{char.name}** ya está en el lobby.", ephemeral=True)
                return
            lobby.participants.append(char)
            await interaction.response.send_message(f"**{char.name}** se unió al lobby.", ephemeral=True)
            await _publish_lobby(interaction, lobby)
            return

        # Caso 2: expedición ya en curso — solo entra si ayviar_activo
        # y hay lugar (menos de 4) o alguien incapacitado (energía 0).
        expedition = await get_active_expedition(interaction.channel_id)
        if not expedition:
            await interaction.response.send_message(
                "No hay ninguna expedición ni lobby en este hilo. Iniciá uno con `/iniciar_expedicion`.",
                ephemeral=True,
            )
            return

        if not expedition["ayviar_activo"]:
            await interaction.response.send_message(
                "La expedición ya está en curso y no se puede entrar ahora. "
                "Alguien del grupo tiene que usar `/enviar_ayviar` para pedir ayuda primero.",
                ephemeral=True,
            )
            return

        participantes_actuales = await get_participant_ids(expedition["id"])
        if char.id in participantes_actuales:
            await interaction.response.send_message(f"**{char.name}** ya está en esta expedición.", ephemeral=True)
            return

        await add_participant(expedition["id"], char.id)
        await set_ayviar_activo(expedition["id"], False)  # se cierra la puerta apenas entra alguien
        await interaction.response.send_message(
            f"🐣 **{char.name}** respondió al llamado del ayviar y se unió a la expedición."
        )

    # ── /preparado_expedicion ────────────────────────────────────
    @bot.tree.command(name="preparado_expedicion", description="Marca que estás listo para empezar la expedición")
    async def preparado_expedicion(interaction: discord.Interaction):
        lobby = LOBBIES_EXPEDICION.get(interaction.channel_id)
        if not lobby:
            await interaction.response.send_message("No hay ningún lobby de expedición aquí.", ephemeral=True)
            return

        if not lobby.has_owner(interaction.user.id):
            await interaction.response.send_message("No tenés personajes en este lobby.", ephemeral=True)
            return

        lobby.ready_votes.add(interaction.user.id)

        if lobby.ready_votes >= lobby.owner_ids():
            pistas_iniciales = await get_pistas_publicas(lobby.zona_id)
            expedition = await create_expedition(lobby.thread_id, lobby.zona_id, pistas_iniciales)
            for char in lobby.participants:
                await add_participant(expedition["id"], char.id)
            del LOBBIES_EXPEDICION[interaction.channel_id]

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
            await interaction.response.send_message("¡Todos listos! La expedición comienza.")
            await interaction.channel.send(embed=embed)
        else:
            await interaction.response.send_message(
                f"Listo registrado ({len(lobby.ready_votes)}/{len(lobby.owner_ids())}).", ephemeral=True
            )
            await _publish_lobby(interaction, lobby)

    # ── /enviar_ayviar ───────────────────────────────────────────
    @bot.tree.command(name="enviar_ayviar", description="Pide ayuda urgente: abre la puerta para que alguien más se una")
    async def enviar_ayviar(interaction: discord.Interaction):
        expedition = await get_active_expedition(interaction.channel_id)
        if not expedition:
            await interaction.response.send_message("No hay ninguna expedición en curso en este hilo.", ephemeral=True)
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

        await set_ayviar_activo(expedition["id"], True)

        mencion_rol = f"<@&{AYVIAR_ROLE_ID}>" if AYVIAR_ROLE_ID else "@aquí"
        await interaction.response.send_message(
            f"🐣📯 {mencion_rol} ¡Se necesita ayuda urgente en la expedición! "
            f"Un ayviar chillón sale volando a pedir refuerzos. "
            f"Cualquiera puede usar `/unirse_expedicion` ahora mismo."
        )

    # ── /energia_global ──────────────────────────────────────────
    @bot.tree.command(name="energia_global", description="[Solo owner] Repone la energía de TODOS los personajes al máximo")
    async def energia_global(interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No tenés permiso para usar este comando.", ephemeral=True)
            return
        await reset_energia_global()
        await interaction.response.send_message("🔋 Energía de todos los personajes repuesta al máximo.")
