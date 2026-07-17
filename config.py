"""
config.py
---------
Configuración compartida entre todos los módulos del bot.
Lee valores de config.json para evitar desincronización.
"""

import json

with open("config.json") as f:
    _CONFIG = json.load(f)

AYVIAR_ROLE_ID = _CONFIG.get("1503575455301767198")
OWNER_ID = _CONFIG.get("1255702109714776158")
