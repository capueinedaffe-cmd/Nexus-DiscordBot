"""
characters_store.py
---------------------
Almacenamiento en memoria de los personajes creados por cada usuario.

NOTA: al ser almacenamiento en memoria, los personajes se pierden si el
bot se reinicia en Railway. Cuando quieras persistencia real, este es
el único archivo que hay que tocar para conectar una base de datos.

Regla de límite: el máximo de 3 personajes por usuario aplica solo a
personajes de tipo "Personaje" (is_npc=False). Los NPCs no cuentan
para ese límite, ya que normalmente los controla el master (vos)
como enemigos o aliados y no tendría sentido limitarlos igual.
"""

MAX_CHARACTERS_PER_USER = 3

# {owner_id: [Character, Character, ...]}
CHARACTERS = {}


class Character:
    """
    Ficha permanente de un personaje o NPC.
    Los valores de combate en vivo (vit actual, mana actual, etc.)
    NO viven acá, viven en el objeto Fighter dentro de combat.py.
    Este objeto es la ficha "de reposo".
    """

    def __init__(self, owner_id, name, is_npc,
                 vit_max, mana_max, fue, res, agi):
        self.owner_id = owner_id
        self.name = name
        self.is_npc = is_npc
        self.level = 1
        self.vit_max = vit_max
        self.mana_max = mana_max
        self.fue = fue
        self.res = res
        self.agi = agi

    @property
    def ph_max(self):
        return 6 + (self.res // 3)

    @property
    def defense(self):
        return self.vit_max // 4


def count_player_characters(owner_id):
    """Cuenta cuántos personajes (no NPC) tiene un usuario."""
    return len([c for c in CHARACTERS.get(owner_id, []) if not c.is_npc])


def get_user_characters(owner_id, include_npc=True):
    chars = CHARACTERS.get(owner_id, [])
    if include_npc:
        return chars
    return [c for c in chars if not c.is_npc]


def get_character(owner_id, name):
    for c in CHARACTERS.get(owner_id, []):
        if c.name.lower() == name.lower():
            return c
    return None


def add_character(character: Character):
    CHARACTERS.setdefault(character.owner_id, []).append(character)


def apply_level_penalty(character: Character):
    """Baja 1 nivel al personaje, nunca por debajo de nivel 1. No aplica a NPCs."""
    if character.is_npc:
        return
    character.level = max(1, character.level - 1)
