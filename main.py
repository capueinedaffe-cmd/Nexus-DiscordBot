"""
main.py
-------
Punto de entrada del bot de Discord NEXUS.
Usa slash commands (/) mediante discord.py.

El token se lee de la variable de entorno DISCORD_TOKEN,
configurada en Railway (nunca escrita directamente en el código).
"""

import os
import discord
from discord.ext import commands

from character_creation import setup_character_commands
from combat import setup_combat_commands

# ── Configuración básica ──────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "No se encontró DISCORD_TOKEN. "
        "Configuralo como variable de entorno en Railway."
    )

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} slash commands sincronizados")
    except Exception as e:
        print(f"⚠️ Error sincronizando comandos: {e}")


@bot.tree.command(name="ping", description="Verifica que el bot está vivo")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! El bot está funcionando.")


# ── Registrar comandos de cada módulo ───────────────────────────────
setup_character_commands(bot)
setup_combat_commands(bot)


# ── Arranque ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
