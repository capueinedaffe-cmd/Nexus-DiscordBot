"""
config.py
---------
Configuración compartida entre todos los módulos del bot.
Lee valores de config.json para evitar desincronización.
"""

import json

with open("config.json") as f:
    _CONFIG = json.load(f)

OWNER_ID = _CONFIG["OWNER_ID"]
