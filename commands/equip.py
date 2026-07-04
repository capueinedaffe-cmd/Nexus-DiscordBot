"""
commands/equip.py
------------------
Comando /equipar: panel de dos niveles para asignar equipamento
a las 5 casillas de un personaje (arma, cabeza, torso, piernas, accesorio).
"""

import discord
from discord import app_commands
from store.characters_store import get_character, get_user_characters, update_equipment
from store.equipment_store import EQUIPAMENTO, get_equipment_inventory

SLOTS = ["arma_principal", "arma_secundaria", "cabeza", "torso", "piernas", "accesorio"]
SLOT_LABELS = {
    "arma_principal": "Arma principal", "arma_secundaria": "Arma secundaria",
    "cabeza": "Cabeza", "torso": "Torso",
    "piernas": "Piernas", "accesorio": "Accesorio",
}


def _nombre_equipo(equipment_id):
    if not equipment_id:
        return "Nada"
    return EQUIPAMENTO.get(equipment_id, {}).get("nombre", equipment_id)


class EquipItemView(discord.ui.View):
    """Sub-panel: elegir qué poner en una casilla específica."""

    def __init__(self, parent: "EquipSlotsView", slot: str):
        super().__init__(timeout=180)
        self.parent = parent
        self.slot = slot
        self.bloqueada = False  # se pone True si la secundaria no se puede tocar (arma principal a dos manos)

        # "nada" siempre primero, después el equipamento propio que calce en este slot
        if slot == "arma_principal":
            candidatos = [
                eid for eid in parent.propio_equipamento
                if EQUIPAMENTO.get(eid, {}).get("slot") == "arma"
            ]
        elif slot == "arma_secundaria":
            principal_id = parent.current_equipo.get("arma_principal")
            principal = EQUIPAMENTO.get(principal_id) if principal_id else None
            if principal and principal.get("manos") == 2:
                # El arma principal ocupa las dos manos: no hay nada para elegir acá.
                candidatos = []
                self.bloqueada = True
            else:
                candidatos = [
                    eid for eid in parent.propio_equipamento
                    if EQUIPAMENTO.get(eid, {}).get("slot") == "arma"
                    and EQUIPAMENTO.get(eid, {}).get("manos") == 1
                ]
        else:
            candidatos = [
                eid for eid in parent.propio_equipamento
                if EQUIPAMENTO.get(eid, {}).get("slot") == slot
            ]

        self.opciones = [None] + candidatos
        actual = parent.current_equipo.get(slot)
        self.selected_index = self.opciones.index(actual) if actual in self.opciones else 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.parent.owner_id

    def build_embed(self):
        if self.bloqueada:
            desc = (
                "El arma principal actual ocupa las dos manos, así que no podés "
                "llevar una segunda arma.\n\n🔙 volvé para cambiar el arma principal."
            )
        else:
            lines = []
            for i, eid in enumerate(self.opciones):
                marker = "▶" if i == self.selected_index else " "
                lines.append(f"{marker} {_nombre_equipo(eid)}")
            desc = "\n".join(lines) + "\n\n✅ confirma, 🔙 vuelve sin cambiar."

        return discord.Embed(
            title=f"🔧 {self.parent.character.name} — {SLOT_LABELS[self.slot]}",
            description=desc,
            color=discord.Color.blue(),
        )

    @discord.ui.button(label="▲", style=discord.ButtonStyle.secondary, row=0)
    async def up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index > 0:
            self.selected_index -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▼", style=discord.ButtonStyle.secondary, row=0)
    async def down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index < len(self.opciones) - 1:
            self.selected_index += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="✅", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        elegido = self.opciones[self.selected_index]
        self.parent.current_equipo[self.slot] = elegido

        # Si se elige un arma a dos manos como principal, la secundaria se
        # vacía sola (no se puede sostener nada más).
        if self.slot == "arma_principal":
            arma = EQUIPAMENTO.get(elegido) if elegido else None
            if not arma or arma.get("manos") == 2:
                self.parent.current_equipo["arma_secundaria"] = None

        await interaction.response.edit_message(embed=self.parent.build_embed(), view=self.parent)

    @discord.ui.button(label="🔙", style=discord.ButtonStyle.danger, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.parent.build_embed(), view=self.parent)


class EquipSlotsView(discord.ui.View):
    """Panel principal: lista de las 5 casillas."""

    def __init__(self, owner_id, character, propio_equipamento):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.character = character
        self.propio_equipamento = propio_equipamento  # ids que el personaje posee
        self.current_equipo = dict(character.equipo)  # copia editable en memoria
        self.selected_index = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Este panel no es tuyo.", ephemeral=True)
            return False
        return True

    def build_embed(self):
        lines = []
        for i, slot in enumerate(SLOTS):
            marker = "▶" if i == self.selected_index else " "
            lines.append(f"{marker} {SLOT_LABELS[slot]}: **{_nombre_equipo(self.current_equipo.get(slot))}**")
        return discord.Embed(
            title=f"🛡️ Equipamento — {self.character.name}",
            description=(
                "\n".join(lines)
                + "\n\n▲▼ elegí la casilla, ✅ para modificarla, 🚪 para guardar y salir."
            ),
            color=discord.Color.dark_teal(),
        )

    @discord.ui.button(label="▲", style=discord.ButtonStyle.secondary, row=0)
    async def up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index > 0:
            self.selected_index -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▼", style=discord.ButtonStyle.secondary, row=0)
    async def down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index < len(SLOTS) - 1:
            self.selected_index += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="✅", style=discord.ButtonStyle.success, row=1)
    async def enter_slot(self, interaction: discord.Interaction, button: discord.ui.Button):
        slot = SLOTS[self.selected_index]
        sub_view = EquipItemView(self, slot)
        await interaction.response.edit_message(embed=sub_view.build_embed(), view=sub_view)

    @discord.ui.button(label="🚪", style=discord.ButtonStyle.primary, row=1)
    async def save_and_exit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_equipment(self.character.id, self.current_equipo)
        embed = self.build_embed()
        embed.title = f"✅ {embed.title} — Guardado"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


async def mi_personaje_autocomplete(interaction: discord.Interaction, current: str):
    chars = await get_user_characters(interaction.user.id, include_npc=True)
    return [
        app_commands.Choice(name=c.name, value=c.name)
        for c in chars if current.lower() in c.name.lower()
    ][:25]


def setup_equip_commands(bot):
    @bot.tree.command(name="equipar", description="Cambiá el equipamento de uno de tus personajes")
    @app_commands.describe(
        personaje="Personaje a equipar",
        publico="¿Querés que el panel lo vea todo el canal? (por defecto solo vos)",
    )
    @app_commands.autocomplete(personaje=mi_personaje_autocomplete)
    async def equipar(interaction: discord.Interaction, personaje: str, publico: bool = False):
        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        inv_rows = await get_equipment_inventory(char.id)
        propio = [row["equipment_id"] for row in inv_rows if row["cantidad"] > 0]

        view = EquipSlotsView(interaction.user.id, char, propio)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=not publico)
