# 🎮 Nexus RPG — Bot de Discord

**Nexus RPG** es un bot de Discord de rol (RPG) por turnos con sistema de combate físico/técnico, expediciones a zonas explorables, forja de equipamento y gestión de personajes con stats (FUE, RES, AGI, VIT, MANA, PH).

> ⚔️ **Versión actual:** v0.10  
> 🐍 **Stack:** Python 3.14+, discord.py, aiosqlite  
> 🗄️ **Base de datos:** SQLite (migrado desde PostgreSQL para compatibilidad con Termux)

---

## ✨ Características principales

### 🧙 Creación de personajes
- Sistema de puntos para distribuir stats al crear un personaje.
- Elemento innato con maestría elemental progresiva.
- Transformaciones con bonuses de stats y drenaje de MANA/PH.
- Soporte para personajes jugador (PJ) y NPC.

### ⚔️ Sistema de combate
- Combate por turnos hasta 3 vs 3.
- **Ataques básicos** con armas (cuerpo a cuerpo y a distancia).
- **Habilidades elementales** puras (ofensivas y defensivas).
- **Técnicas híbridas** (físicas + elementales) con penalización por desajuste de arma.
- Sistema de distancia, esquiva, defensa de armadura y bloqueo.
- Transformaciones en combate con vulnerabilidad al elemento opuesto.
- IA automática para turnos de NPCs.
- Pausa, rendición y timeout de turnos.

### 🗺️ Sistema de expedición
- Lobbies de preparación (hasta 4 personajes, máx. 3 por canal).
- Exploración de zonas con descubrimiento de pistas y eventos narrativos.
- Encuentros con enemigos neutrales (observar o atacar) y hostiles (combate).
- Sistema de cocina en campamento para recuperar energía grupal.
- Botín compartido y reparto al finalizar.
- Mecánica de **ayviar**: pedir refuerzos una vez por expedición.
- Eventos especiales (ej: bandada de 40 arpías menores + Matriarca).

### 🛠️ Forja y equipamento
- Panel interactivo paginado para forjar equipamento.
- 5 slots de equipo: arma principal, arma secundaria, cabeza, torso, piernas, accesorio.
- Penalización de AGI por peso total del equipamiento.
- Sistema de doble arma (una mano + una mano) con FUE dividida.

### 📊 Perfil e inventario
- Panel de perfil global con selector de personajes.
- Inventario de materiales con rareza.
- Comando `/dar_objeto` para administradores.

---

## 🚀 Instalación

### Requisitos
- Python 3.14+
- pip

### Dependencias
```bash
pip install -r requirements.txt
```

### Configuración
1. Creá un archivo `config.json` en la raíz:
```json
{
  "OWNER_ID": 1234567890123456789,
  "AYVIAR_ROLE_ID": 1234567890123456789,
  "MAX_CHARACTERS_PER_USER": 3,
  "TOTAL_POINTS": 20,
  "STAT_CONFIG": {
    "vit": {"base": 10, "max": 30, "label": "VIT"},
    "mana": {"base": 10, "max": 30, "label": "MANA"},
    "fue": {"base": 5, "max": 20, "label": "FUE"},
    "res": {"base": 5, "max": 20, "label": "RES"},
    "agi": {"base": 5, "max": 20, "label": "AGI"}
  }
}
```

2. Configurá la variable de entorno `DISCORD_TOKEN`:
```bash
export DISCORD_TOKEN="tu_token_aqui"
```

3. (Opcional) Creá las carpetas de datos:
```bash
mkdir -p data/materials data/zonas data/enemies data/abilities data/equipment data/elements data/recetas
```

### Ejecución
```bash
python main.py
```

---

## 📁 Estructura del proyecto

```
nexus-bot/
├── main.py                      # Punto de entrada del bot
├── config.py                    # Configuración compartida
├── database.py                  # Conexión SQLite y esquema
├── session_guard.py             # Guarda de sesiones globales
├── requirements.txt
├── commands/
│   ├── character_creation.py    # /crear_personaje, /crear_transformacion
│   ├── combat.py                # Sistema de combate completo
│   ├── expedition.py            # Sistema de expediciones
│   ├── items.py                 # /inventario, /dar_objeto
│   ├── forge.py                 # /forjar
│   ├── equip.py                 # /equipar
│   └── perfil.py                # /perfil
├── store/
│   ├── characters_store.py      # Acceso a personajes
│   ├── expedition_store.py      # Acceso a expediciones/zonas/enemigos
│   ├── items_store.py           # Acceso a materiales
│   ├── equipment_store.py       # Acceso a equipamento
│   └── abilities_store.py       # Acceso a habilidades
├── maths/
│   ├── combat_math.py           # Cálculos puros de combate
│   ├── expedition_math.py       # Cálculos puros de expedición
│   └── npc_ai_math.py           # IA de NPCs
└── data/
    ├── materials/materials.json
    ├── zonas/zonas.json
    ├── zonas/zona_gifs.json
    ├── enemies/enemies1.json
    ├── abilities/abilities.json
    ├── equipment/equipment.json
    ├── elements/elements.json
    └── recetas/recetas.json
```

---

## 🛡️ Comandos disponibles

### Generales
| Comando | Descripción | Permisos |
|---------|-------------|----------|
| `/ping` | Verifica que el bot está vivo | Todos |
| `/escribir <texto>` | Envía un mensaje como el bot | Owner |
| `/energia_global` | Repone energía de todos los personajes | Owner |

### Personajes
| Comando | Descripción |
|---------|-------------|
| `/crear_personaje` | Crea un nuevo personaje (máx. 3 por usuario) |
| `/crear_transformacion` | Define una transformación para un personaje |
| `/perfil [publico]` | Muestra estadísticas globales o de un personaje |

### Inventario y equipamento
| Comando | Descripción |
|---------|-------------|
| `/inventario <personaje> [publico]` | Muestra materiales del personaje |
| `/dar_objeto <usuario> <personaje> <material> <cantidad>` | Entrega materiales (owner) |
| `/forjar <personaje> [publico]` | Abre el panel de forja |
| `/equipar <personaje> [publico]` | Abre el panel de equipamento |

### Combate
| Comando | Descripción |
|---------|-------------|
| `/iniciar_combate <personaje> [personaje2] [personaje3]` | Crea/une a lobby de combate |
| `/cambiar_equipo [personaje]` | Cambia de equipo en el lobby |
| `/preparado` | Vota listo para empezar |
| `/atacar <personaje> <objetivo>` | Ataque básico |
| `/defender <personaje>` | Se pone en guardia |
| `/moverse <personaje> <direccion>` | Avanza o retrocede |
| `/usar_habilidad <personaje> <habilidad> [objetivo]` | Habilidad elemental |
| `/usar_tecnica <personaje> <tecnica> <objetivo>` | Técnica híbrida |
| `/transformar <personaje> <transformacion>` | Activa transformación |
| `/pausa` | Vota para pausar |
| `/terminar` | Vota para terminar sin resultado |
| `/rendirse <personaje>` | Vota rendición del equipo |

### Expedición
| Comando | Descripción |
|---------|-------------|
| `/iniciar_expedicion <zona> <personaje>` | Abre lobby de expedición |
| `/unirse_expedicion <personaje>` | Se une al lobby o vía ayviar |
| `/preparado_expedicion` | Vota listo para salir |
| `/enviar_ayviar` | Pide refuerzos (solo líder, 1 vez) |
| `/explorar` | El grupo explora (gasta energía) |
| `/comer <personaje> <material>` | Come ingrediente crudo del botín |
| `/acampar <personaje> <receta> [ingrediente]` | Cocina para el grupo |
| `/compartir_conocimiento` | Publica pistas de la zona |
| `/retirarse_expedicion` | Termina la expedición con éxito |

---

## ⚙️ Sistema de stats

| Stat | Rol |
|------|-----|
| **VIT** | Puntos de vida máximos |
| **MANA** | Reserva para habilidades y transformaciones |
| **FUE** | Daño físico y carga de equipamiento |
| **RES** | Defensa base, PH máximo, cocina |
| **AGI** | Iniciativa, esquiva, movimiento |
| **PH** | Puntos de habilidad (gastados en técnicas) |

### Fórmulas clave
- `PH_max = 6 + (RES // 3)`
- `Defensa base = VIT_max // 4`
- `AGI efectiva = AGI_base - max(0, (Peso_total - FUE)) × 2`

---

## 🗺️ Sistema de zonas

Las zonas se definen en `data/zonas/zonas.json` con:
- **Recursos:** materiales recolectables (con peso de probabilidad).
- **Enemigos:** bestias hostiles y neutrales (con peso de aparición).
- **Pistas:** descubrimiento progresivo con exploraciones mínimas y probabilidad incremental.
- **Evento final:** jefe opcional con loot garantizado al completar.

Las pistas descubiertas pueden compartirse públicamente para que futuras expediciones empiecen con ventaja.

---

## 🤝 Contribuir

Este es un proyecto personal en desarrollo activo. Si querés reportar bugs o sugerir mejoras, abrí un issue con:
1. Descripción del problema.
2. Pasos para reproducirlo.
3. Logs relevantes (si aplica).

---

## 📜 Licencia

Proyecto personal. Todos los derechos reservados.

---

> *"El Nexus espera. ¿Estás listo para explorarlo?"* 🗡️🛡️
