"""
forge.py
--------
Comando /forjar: panel paginado para craftear equipamento a partir
de materiales del inventario del personaje.
"""

import discord
from discord import app_commands
from store/characters_store import get_character, get_user_characters
from store/items_store import MATERIALES, get_inventory, remove_material
from store/equipment_store import EQUIPAMENTO, add_equipment

PAGE_SIZE = 5


def _receta_texto(receta):
    return ", ".join(f"{c}x {MATERIALES.get(m, {}).get('nombre', m)}" for m, c in receta.items())


class ForgeConfirmView(discord.ui.View):
    def __init__(self, forge_view: "ForgeView", equipment_id: str):
        super().__init__(timeout=60)
        self.forge_view = forge_view
        self.equipment_id = equipment_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.forge_view.owner_id

    @discord.ui.button(label="Sí, forjar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        eq = EQUIPAMENTO[self.equipment_id]
        character_id = self.forge_view.character.id

        for mat_id, cant in eq["receta"].items():
            ok = await remove_material(character_id, mat_id, cant)
            if not ok:
                await interaction.response.edit_message(
                    content="Algo cambió en tu inventario y ya no te alcanza. Forjado cancelado.", view=None
                )
                return

        await add_equipment(character_id, self.equipment_id, 1)

        for mat_id, cant in eq["receta"].items():
            self.forge_view.inventario[mat_id] = self.forge_view.inventario.get(mat_id, 0) - cant

        await interaction.response.edit_message(
            content=f"✅ Forjaste **{eq['nombre']}**. Ya está en tu inventario de equipamento.", view=None
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Forjado cancelado.", view=None)


class ForgeView(discord.ui.View):
    def __init__(self, owner_id, character, inventario):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.character = character
        self.inventario = inventario  # {material_id: cantidad}
        self.ids = list(EQUIPAMENTO.keys())
        self.page = 0
        self.selected_index = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Este panel no es tuyo.", ephemeral=True)
            return False
        return True

    @property
    def total_pages(self):
        return max(1, (len(self.ids) + PAGE_SIZE - 1) // PAGE_SIZE)

    @property
    def page_ids(self):
        start = self.page * PAGE_SIZE
        return self.ids[start:start + PAGE_SIZE]

    @property
    def selected_id(self):
        page_ids = self.page_ids
        if not page_ids:
            return None
        idx = min(self.selected_index, len(page_ids) - 1)
        return page_ids[idx]

    def build_embed(self):
        page_ids = self.page_ids
        if not page_ids:
            desc = "Todavía no hay equipamento definido en el banco."
        else:
            lines = []
            for i, eid in enumerate(page_ids):
                eq = EQUIPAMENTO[eid]
                marker = "▶" if i == self.selected_index else " "
                lines.append(f"{marker} **{eq['nombre']}** ({eq['slot']}) — {_receta_texto(eq['receta'])}")
            desc = (
                "\n".join(lines)
                + f"\n\nPágina {self.page + 1}/{self.total_pages}\n"
                "Usá ▲▼ para elegir, ◀▶ para cambiar de página, 🔨 para forjar."
            )

        return discord.Embed(
            title=f"🔨 Forja — {self.character.name}",
            description=desc,
            color=discord.Color.dark_orange(),
        )

    @discord.ui.button(label="▲", style=discord.ButtonStyle.secondary, row=0)
    async def up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index > 0:
            self.selected_index -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▼", style=discord.ButtonStyle.secondary, row=0)
    async def down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index < len(self.page_ids) - 1:
            self.selected_index += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page - 1) % self.total_pages
        self.selected_index = 0
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page + 1) % self.total_pages
        self.selected_index = 0
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🔨", style=discord.ButtonStyle.success, row=2)
    async def forge(self, interaction: discord.Interaction, button: discord.ui.Button):
        eid = self.selected_id
        if not eid:
            await interaction.response.send_message("No hay nada seleccionado.", ephemeral=True)
            return

        eq = EQUIPAMENTO[eid]
        faltantes = []
        for mat_id, cant in eq["receta"].items():
            disponible = self.inventario.get(mat_id, 0)
            if disponible < cant:
                nombre_mat = MATERIALES.get(mat_id, {}).get("nombre", mat_id)
                faltantes.append(f"{nombre_mat} ({disponible}/{cant})")

        if faltantes:
            await interaction.response.send_message(
                f"No tenés suficientes materiales: {', '.join(faltantes)}.", ephemeral=True
            )
            return

        confirm_view = ForgeConfirmView(self, eid)
        await interaction.response.send_message(
            f"¿Confirmás forjar **{eq['nombre']}**? Vas a gastar: {_receta_texto(eq['receta'])}",
            view=confirm_view, ephemeral=True,
        )


async def mi_personaje_autocomplete(interaction: discord.Interaction, current: str):
    chars = await get_user_characters(interaction.user.id, include_npc=True)
    return [
        app_commands.Choice(name=c.name, value=c.name)
        for c in chars if current.lower() in c.name.lower()
    ][:25]


def setup_forge_commands(bot):
    @bot.tree.command(name="forjar", description="Abre el panel de forja de equipamento")
    @app_commands.describe(personaje="Personaje que va a forjar")
    @app_commands.autocomplete(personaje=mi_personaje_autocomplete)
    async def forjar(interaction: discord.Interaction, personaje: str):
        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        inv_rows = await get_inventory(char.id)
        inventario = {row["material_id"]: row["cantidad"] for row in inv_rows}

        view = ForgeView(interaction.user.id, char, inventario)
        await interaction.response.send_message(embed=view.build_embed(), view=view)
