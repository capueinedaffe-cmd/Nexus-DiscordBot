# abilities_store.py
import json

with open("abilities.json", encoding="utf-8") as f:
    _DATA = json.load(f)

HABILIDADES = _DATA["habilidades"]

TIER_MIN_LEVEL = {
    "basica": 1,
    "intermedia": 5,
    "avanzada": 8,
    "maestra": 10,
}

def get_ability(ability_id):
    return HABILIDADES.get(ability_id)

def min_level_for(tier):
    return TIER_MIN_LEVEL.get(tier, 1)
