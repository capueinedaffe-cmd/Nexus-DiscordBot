"""
perfil.py
---------
Comando /perfil: panel con selector para ver estadísticas globales
del usuario o el detalle de un personaje específico.
"""

import discord
from discord import app_commands
from store.characters_store import get_user_characters
from store.equipment_store import EQUIPAMENTO

ELEMENTOS_NOMBRES = {}
try:
    import json
    with open("data/elements/elements.json", encoding="utf-8") as f:
        ELEMENTOS_NOMBRES = json.load(f)["nombres"]
except FileNotFoundError:
    pass


def _build_global_embed(user: discord.User, characters):
    total_victorias = sum(c.victorias for c in characters)
    total_derrotas = sum(c.derrotas for c in characters)
    total_batallas = total_victorias + total_derrotas

    lista = "\n".join(
        f"• {c.name} ({'NPC' if c.is_npc else 'PJ'}, nivel {c.level})"
        for c in characters
    ) or "Todavía no tenés personajes."

    embed = discord.Embed(
        title=f"📊 Perfil global de {user.display_name}",
        description=(
            f"Batallas jugadas: **{total_batallas}**\n"
            f"Victorias: **{total_victorias}**\n"
            f"Derrotas: **{total_derrotas}**\n\n"
            f"**Personajes:**\n{lista}"
        ),
        color=discord.Color.blurple(),
    )
    return embed


def _build_character_embed(char):
    elemento_label = ELEMENTOS_NOMBRES.get(char.elemento, char.elemento)
    nivel_maestria = char.maestria_nivel()
    usos = char.maestria_usos.get(char.elemento, 0)
    usos_para_siguiente = 10 - (usos % 10) if nivel_maestria < 10 else 0

    slots_orden = ["arma", "cabeza", "torso", "piernas", "accesorio"]
    slot_labels = {
        "arma": "Arma", "cabeza": "Cabeza", "torso": "Torso",
        "piernas": "Piernas", "accesorio": "Accesorio",
    }
    equipo_lines = []
    for slot in slots_orden:
        eid = char.equipo.get(slot)
        nombre = EQUIPAMENTO.get(eid, {}).get("nombre", eid) if eid else "Nada"
        equipo_lines.append(f"{slot_labels[slot]}: **{nombre}**")
    equipo_texto = "\n".join(equipo_lines)

    embed = discord.Embed(
        title=f"📄 {char.name}",
        description=(
            f"Nivel: **{char.level}**\n"
            f"Elemento innato: **{elemento_label}**\n"
            f"Maestría elemental: **{nivel_maestria}/10** "
            + (f"({usos_para_siguiente} usos para el próximo nivel)" if nivel_maestria < 10 else "(máxima)")
            + "\n\n"
            f"**Estadísticas**\n"
            f"VIT: {char.vit_max}  |  MANA: {char.mana_max}  |  FUE: {char.fue}  |  RES: {char.res}  |  AGI: {char.agi}\n\n"
            f"**Equipamento**\n{equipo_texto}\n\n"
            f"Victorias: **{char.victorias}**  |  Derrotas: **{char.derrotas}**"
        ),
        color=discord.Color.green(),
    )
    return embed


class PerfilSelect(discord.ui.Select):
    def __init__(self, owner_id, characters):
        options = [discord.SelectOption(label="📊 Resumen global", value="global")]
        for c in characters:
            options.append(discord.SelectOption(label=c.name, value=str(c.id)))
        super().__init__(placeholder="Elegí qué ver", options=options[:25])
        self.owner_id = owner_id
        self.characters = characters

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "global":
            embed = _build_global_embed(interaction.user, self.characters)
        else:
            char = next(c for c in self.characters if str(c.id) == self.values[0])
            embed = _build_character_embed(char)
        await interaction.response.edit_message(embed=embed, view=self.view)


class PerfilView(discord.ui.View):
    def __init__(self, owner_id, characters):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(PerfilSelect(owner_id, characters))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id


def setup_profile_commands(bot):
    @bot.tree.command(name="perfil", description="Mostrá tus estadísticas globales o las de un personaje")
    async def perfil(interaction: discord.Interaction):
        characters = await get_user_characters(interaction.user.id, include_npc=True)
        view = PerfilView(interaction.user.id, characters)
        embed = _build_global_embed(interaction.user, characters)
        await interaction.response.send_message(embed=embed, view=view)
