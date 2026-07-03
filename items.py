"""
items.py
--------
Comandos de objetos/materiales: /dar_objeto (solo owner) y /inventario.
"""

import discord
from discord import app_commands
from config import OWNER_ID
from characters_store import get_character, get_user_characters
from items_store import MATERIALES, get_material, get_inventory, add_material


async def material_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=mat["nombre"], value=mat_id)
        for mat_id, mat in MATERIALES.items()
        if current.lower() in mat["nombre"].lower()
    ][:25]


async def mi_personaje_autocomplete(interaction: discord.Interaction, current: str):
    chars = await get_user_characters(interaction.user.id, include_npc=True)
    return [
        app_commands.Choice(name=c.name, value=c.name)
        for c in chars if current.lower() in c.name.lower()
    ][:25]


def _build_inventory_embed(char, inventario):
    if not inventario:
        desc = "El inventario está vacío."
    else:
        lineas = []
        for entrada in inventario:
            mat = get_material(entrada["material_id"]) or {"nombre": entrada["material_id"], "rareza": "?"}
            lineas.append(f"• **{mat['nombre']}** x{entrada['cantidad']} _({mat.get('rareza', '?')})_")
        desc = "\n".join(lineas)

    return discord.Embed(
        title=f"🎒 Inventario de {char.name}",
        description=desc,
        color=discord.Color.orange(),
    )


def setup_item_commands(bot):

    @bot.tree.command(name="dar_objeto", description="[Solo owner] Entrega materiales a un personaje")
    @app_commands.describe(
        usuario="Dueño del personaje que va a recibir el material",
        personaje="Nombre del personaje",
        material="Material a entregar",
        cantidad="Cantidad a entregar",
    )
    @app_commands.autocomplete(material=material_autocomplete)
    async def dar_objeto(interaction: discord.Interaction, usuario: discord.Member,
                          personaje: str, material: str, cantidad: int):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("No tenés permiso para usar este comando.", ephemeral=True)
            return
        if cantidad <= 0:
            await interaction.response.send_message("La cantidad tiene que ser mayor a 0.", ephemeral=True)
            return

        mat = get_material(material)
        if not mat:
            await interaction.response.send_message("Ese material no existe en el banco.", ephemeral=True)
            return

        char = await get_character(usuario.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"**{usuario.display_name}** no tiene un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        await add_material(char.id, material, cantidad)
        await interaction.response.send_message(
            f"✅ Se entregaron **{cantidad}x {mat['nombre']}** a **{char.name}**.", ephemeral=True
        )

    @bot.tree.command(name="inventario", description="Mostrá los materiales de uno de tus personajes")
    @app_commands.describe(personaje="Personaje a consultar")
    @app_commands.autocomplete(personaje=mi_personaje_autocomplete)
    async def inventario(interaction: discord.Interaction, personaje: str):
        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        inv = await get_inventory(char.id)
        embed = _build_inventory_embed(char, inv)
        await interaction.response.send_message(embed=embed, ephemeral=True)
