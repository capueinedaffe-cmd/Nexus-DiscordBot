import asyncio
import aiosqlite

DB_PATH = "data_db/nexus.db"  # Ajustá la ruta si es distinta


async def listar_personajes():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, owner_id, name, is_npc, level, elemento, fue, res, agi, vit_max, mana_max FROM characters"
        )
        rows = await cursor.fetchall()
        print("📋 Personajes registrados:\n")
        for r in rows:
            tipo = "NPC" if r["is_npc"] else "PJ"
            print(f"  ID {r['id']} | {r['name']} ({tipo}, Nv.{r['level']}) | Elemento: {r['elemento']}")
            print(f"           FUE:{r['fue']} RES:{r['res']} AGI:{r['agi']} VIT:{r['vit_max']} MANA:{r['mana_max']}")
        print()


async def actualizar_personaje(personaje_id: int, campo: str, valor):
    campos_permitidos = {
        "name", "elemento", "level", "fue", "res", "agi", "vit_max", "mana_max",
        "ph", "energia", "victorias", "derrotas", "equipo_arma_principal",
        "equipo_arma_secundaria", "equipo_cabeza", "equipo_torso",
        "equipo_piernas", "equipo_accesorio"
    }
    if campo not in campos_permitidos:
        print(f"❌ Campo '{campo}' no permitido. Campos válidos: {campos_permitidos}")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE characters SET {campo} = ? WHERE id = ?", (valor, personaje_id))
        await db.commit()
        print(f"✅ Personaje ID {personaje_id} actualizado: {campo} = {valor}")


async def main():
    await listar_personajes()

    # Ejemplo: cambiar el elemento del personaje ID 1 a "mistico"
    # Descomentá y ajustá según lo que necesites:
    
    # await actualizar_personaje(1, "elemento", "mistico")
    # await actualizar_personaje(1, "level", 5)
    # await actualizar_personaje(1, "fue", 15)

    # Volvé a listar para confirmar
    # await listar_personajes()


if __name__ == "__main__":
    asyncio.run(main())
