"""
main.py
-------
Punto de entrada del bot de Discord NEXUS.
Usa slash commands (/) mediante discord.py.

El token se lee de la variable de entorno DISCORD_TOKEN,
configurada en Railway (nunca escrita directamente en el código).
"""

# NEXUS RPG DISCORD BOT V0.10
import os
import discord
import logging
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

from config import OWNER_ID
from commands.perfil import setup_profile_commands
from commands.items import setup_item_commands
from commands.equip import setup_equip_commands

# ── Configurar logging ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from database import init_db
from commands.character_creation import setup_character_commands
from commands.combat import setup_combat_commands, start_background_tasks
from commands.forge import setup_forge_commands
from commands.expedition import setup_expedition_commands
from events import setup_test_event_commands

# ── Configuración básica ──��───────────────────────────────────────
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
        for cmd in bot.tree.get_commands():
            logger.info(f"  - {cmd.name}")
    except Exception as e:
        logger.error(f"⚠️ Error sincronizando comandos: {e}", exc_info=True)

    start_background_tasks()
    print("✅ Tareas de fondo (timeouts) iniciadas")


# ── Comandos utilidades ───────────────────────────────────────────────
@bot.tree.command(name="ping", description="Verifica que el bot está vivo")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! (⁠≧⁠▽⁠≦⁠)")

@bot.tree.command(name="escribir", description="[Solo owner] Envía un mensaje por el bot")
@app_commands.describe(texto="Texto a enviar")
async def escribir(interaction: discord.Interaction, texto: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("No tenés permiso para usar este comando.", ephemeral=True)
        return
    await interaction.response.send_message("Mensaje enviado.", ephemeral=True)
    await interaction.channel.send(texto)

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
logger.info("=== INICIANDO REGISTRO DE COMANDOS ===")
try:
    setup_character_commands(bot)
    logger.info("✅ Comandos de character_creation registrados")
except Exception as e:
    logger.error(f"❌ Error registrando character_creation: {e}", exc_info=True)

try:
    setup_combat_commands(bot)
    logger.info("✅ Comandos de combat registrados")
except Exception as e:
    logger.error(f"❌ Error registrando combat: {e}", exc_info=True)

try:
    setup_profile_commands(bot)
    logger.info("✅ Comando de perfil registrado")
except Exception as e:
    logger.error(f"❌ Error registrando perfil: {e}", exc_info=True)

try:
    setup_item_commands(bot)
    logger.info("✅ Comandos de items registrados")
except Exception as e:
    logger.error(f"❌ Error registrando items: {e}", exc_info=True)

try:
    setup_forge_commands(bot)
    logger.info("✅ Comando de forja registrado")
except Exception as e:
    logger.error(f"❌ Error registrando forja: {e}", exc_info=True)

try:
    setup_equip_commands(bot)
    logger.info("✅ Comando de equipar registrado")
except Exception as e:
    logger.error(f"❌ Error registrando equipar: {e}", exc_info=True)

try:
    setup_test_event_commands(bot)
    logger.info("✅ Comando de prueba de eventos registrado")
except Exception as e:
    logger.error(f"❌ Error registrando eventos de prueba: {e}", exc_info=True)

try:
    setup_expedition_commands(bot)
    logger.info("✅ Comando de prueba de expedición registrado")
except Exception as e:
    logger.error(f"❌ Error registrando expedición: {e}", exc_info=True)
    
logger.info("=== FIN REGISTRO DE COMANDOS ===")

# ── Arranque ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Iniciando bot...")
    bot.run(TOKEN)
