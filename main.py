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
from discord import app_commands
from discord.ext import commands

# ── Configuración básica ──────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "No se encontró DISCORD_TOKEN. "
        "Configuralo como variable de entorno en Railway."
    )

# Intents: permisos que el bot necesita para funcionar
intents = discord.Intents.default()
intents.message_content = True  # Necesario si en el futuro usamos comandos con prefijo

bot = commands.Bot(command_prefix="!", intents=intents)


# ── Evento: bot listo ──────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} slash commands sincronizados")
    except Exception as e:
        print(f"⚠️ Error sincronizando comandos: {e}")


# ── Comando de prueba ───────────────────────────────────────────────
@bot.tree.command(name="ping", description="Verifica que el bot está vivo")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! El bot está funcionando.")


# ── Arranque ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
