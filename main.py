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

from database import init_db
from character_creation import setup_character_commands
from combat import setup_combat_commands, start_background_tasks

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
    await init_db()
    print(f"✅ Bot conectado como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} slash commands sincronizados")
    except Exception as e:
        print(f"⚠️ Error sincronizando comandos: {e}")

    start_background_tasks()
    print("✅ Tareas de fondo (timeouts) iniciadas")


@bot.tree.command(name="ping", description="Verifica que el bot está vivo")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! (⁠≧⁠▽⁠≦⁠)")


# El bot solo usa slash commands (/), no comandos con prefijo "!".
# Si alguien escribe "!algo" por costumbre, Discord.py lo intenta procesar
# como comando de prefijo y falla con CommandNotFound. Esto es inofensivo,
# así que lo silenciamos para no ensuciar los logs de Railway.
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


# ── Registrar comandos de cada módulo ───────────────────────────────
setup_character_commands(bot)
setup_combat_commands(bot)


# ── Arranque ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
