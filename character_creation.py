"""
character_creation.py
-----------------------
Panel interactivo de creación de personajes con botones (+) (-) (↻) (Continuar).

Sistema de puntos: cada personaje arranca con estadísticas base y un pool
de puntos para repartir. El botón ↻ cambia qué estadística está seleccionada,
(+) y (-) suman o restan un punto a esa estadística respetando límites.

Comandos:
  /crear_personaje      → abierto a todos, crea un "Personaje" (PJ)
  /creacion_avanzada    → solo OWNER_ID, permite elegir Personaje o NPC
"""

import discord

from config import OWNER_ID
from characters_store import (
    Character, add_character, count_player_characters,
    MAX_CHARACTERS_PER_USER,
)

# ── Configuración del sistema de puntos ────────────────────────────
STAT_ORDER = ["vit", "mana", "fue", "res", "agi"]

STAT_CONFIG = {
    "vit":  {"label": "VIT (HP)", "base": 10, "max": 30},
    "mana": {"label": "MANA",     "base": 5,  "max": 20},
    "fue":  {"label": "FUE",      "base": 1,  "max": 10},
    "res":  {"label": "RES",      "base": 1,  "max": 10},
    "agi":  {"label": "AGI",      "base": 1,  "max": 10},
}

TOTAL_POINTS = 15


async def owner_check_direct(interaction: discord.Interaction) -> bool:
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "No tenés permiso para usar este comando.", ephemeral=True
        )
        return False
    return True


# ── Panel de reparto de estadísticas ────────────────────────────────
class StatPanelView(discord.ui.View):
    """
    Vista con botones -, +, ↻ y Continuar.
    Mantiene el estado de puntos repartidos hasta que el usuario confirma.
    """

    def __init__(self, owner_id, name, is_npc):
        super().__init__(timeout=180)  # 3 minutos de inactividad y se cierra
        self.owner_id = owner_id
        self.name = name
        self.is_npc = is_npc
        self.values = {stat: STAT_CONFIG[stat]["base"] for stat in STAT_ORDER}
        self.points_left = TOTAL_POINTS
        self.selected_index = 0

    @property
    def selected_stat(self):
        return STAT_ORDER[self.selected_index]

    def build_embed(self):
        lines = []
        for stat in STAT_ORDER:
            cfg = STAT_CONFIG[stat]
            marker = "▶" if stat == self.selected_stat else " "
            lines.append(f"{marker} {cfg['label']}: **{self.values[stat]}** (máx {cfg['max']})")

        tipo = "NPC" if self.is_npc else "Personaje"
        embed = discord.Embed(
            title=f"Creación de {tipo}: {self.name}",
            description=(
                f"Puntos restantes: **{self.points_left}/{TOTAL_POINTS}**\n\n"
                + "\n".join(lines)
                + "\n\nUsá ↻ para elegir la estadística, y +/- para ajustarla."
            ),
            color=discord.Color.gold(),
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Este panel de creación no es tuyo.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="-", style=discord.ButtonStyle.danger)
    async def minus(self, interaction: discord.Interaction, button: discord.ui.Button):
        stat = self.selected_stat
        base = STAT_CONFIG[stat]["base"]
        if self.values[stat] > base:
            self.values[stat] -= 1
            self.points_left += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="+", style=discord.ButtonStyle.success)
    async def plus(self, interaction: discord.Interaction, button: discord.ui.Button):
        stat = self.selected_stat
        cfg = STAT_CONFIG[stat]
        if self.points_left > 0 and self.values[stat] < cfg["max"]:
            self.values[stat] += 1
            self.points_left -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="↻", style=discord.ButtonStyle.secondary)
    async def cycle(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_index = (self.selected_index + 1) % len(STAT_ORDER)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Continuar", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.points_left > 0:
            await interaction.response.send_message(
                f"Todavía te quedan {self.points_left} puntos por repartir.",
                ephemeral=True,
            )
            return

        character = Character(
            owner_id=self.owner_id,
            name=self.name,
            is_npc=self.is_npc,
            vit_max=self.values["vit"],
            mana_max=self.values["mana"],
            fue=self.values["fue"],
            res=self.values["res"],
            agi=self.values["agi"],
        )
        add_character(character)

        for child in self.children:
            child.disabled = True

        final_embed = self.build_embed()
        final_embed.title = f"✅ {final_embed.title} — Creado"
        final_embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=final_embed, view=self)
        self.stop()


# ── Vista intermedia: elegir Personaje o NPC (solo creación avanzada) ─
class TypeSelectView(discord.ui.View):
    def __init__(self, owner_id, name):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.name = name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Personaje", style=discord.ButtonStyle.primary)
    async def as_character(self, interaction: discord.Interaction, button: discord.ui.Button):
        if count_player_characters(self.owner_id) >= MAX_CHARACTERS_PER_USER:
            await interaction.response.edit_message(
                content=f"Ya tenés el máximo de {MAX_CHARACTERS_PER_USER} personajes. No se puede crear otro.",
                embed=None, view=None,
            )
            return
        view = StatPanelView(self.owner_id, self.name, is_npc=False)
        await interaction.response.edit_message(content=None, embed=view.build_embed(), view=view)

    @discord.ui.button(label="NPC", style=discord.ButtonStyle.secondary)
    async def as_npc(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = StatPanelView(self.owner_id, self.name, is_npc=True)
        await interaction.response.edit_message(content=None, embed=view.build_embed(), view=view)


# ── Modal para pedir el nombre ───────────────────────────────────────
class NameModal(discord.ui.Modal, title="Nombre del personaje"):
    nombre = discord.ui.TextInput(label="Nombre", max_length=32, placeholder="Ej: Onix")

    def __init__(self, advanced=False):
        super().__init__()
        self.advanced = advanced

    async def on_submit(self, interaction: discord.Interaction):
        name = str(self.nombre).strip()

        if self.advanced:
            view = TypeSelectView(interaction.user.id, name)
            await interaction.response.send_message(
                f"**{name}** — ¿Es un Personaje o un NPC?", view=view
            )
        else:
            if count_player_characters(interaction.user.id) >= MAX_CHARACTERS_PER_USER:
                await interaction.response.send_message(
                    f"Ya tenés el máximo de {MAX_CHARACTERS_PER_USER} personajes.",
                    ephemeral=True,
                )
                return
            view = StatPanelView(interaction.user.id, name, is_npc=False)
            await interaction.response.send_message(embed=view.build_embed(), view=view)


# ── Registro de comandos ───────────────────────────────────────────
def setup_character_commands(bot):

    @bot.tree.command(name="crear_personaje", description="Crea un nuevo personaje (máximo 3 por usuario)")
    async def crear_personaje(interaction: discord.Interaction):
        if count_player_characters(interaction.user.id) >= MAX_CHARACTERS_PER_USER:
            await interaction.response.send_message(
                f"Ya tenés el máximo de {MAX_CHARACTERS_PER_USER} personajes.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(NameModal(advanced=False))

    @bot.tree.command(name="creacion_avanzada", description="[Solo owner] Crea un Personaje o un NPC")
    async def creacion_avanzada(interaction: discord.Interaction):
        if not await owner_check_direct(interaction):
            return
        await interaction.response.send_modal(NameModal(advanced=True))
