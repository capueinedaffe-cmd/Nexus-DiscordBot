"""
combat.py
---------
Sistema de combate RPG: turnos, acciones, efectos de elementos.
"""

import discord
from discord import app_commands
from config import OWNER_ID
from store.characters_store import get_character, get_user_characters

async def owner_check(interaction: discord.Interaction) -> bool:
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "No tenés permiso para usar este comando.", ephemeral=True
        )
        return False
    return True

def setup_combat_commands(bot):
    logger = __import__('logging').getLogger(__name__)
    logger.info("Registrando comandos de combate...")

    # Los comandos de combate irán aquí

    logger.info("✅ Comandos de combate registrados")

def start_background_tasks():
    """Inicia las tareas de fondo para timeouts de combate."""
    pass
