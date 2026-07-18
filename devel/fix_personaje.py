import asyncio
import aiosqlite

DB_PATH = "data_db/nexus.db"


async def listar_personajes():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, is_npc, level, elemento FROM characters"
        )
        rows = await cursor.fetchall()
        print("📋 Personajes:\n")
        for r in rows:
            tipo = "NPC" if r["is_npc"] else "PJ"
            print(f"  ID {r['id']} | {r['name']} ({tipo}, Nv.{r['level']}) | Elemento: {r['elemento']}")
        print()


async def cambiar_elemento(personaje_id: int, nuevo_elemento: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE characters SET elemento = ? WHERE id = ?",
            (nuevo_elemento, personaje_id)
        )
        await db.commit()
        print(f"✅ Personaje ID {personaje_id} → elemento cambiado a '{nuevo_elemento}'")


async def main():
    await listar_personajes()

    # Ajustá estos valores:
    ID_PERSONAJE = 1          # ← poné el ID del personaje
    NUEVO_ELEMENTO = "ion"   # ← poné el elemento nuevo

    await cambiar_elemento(ID_PERSONAJE, NUEVO_ELEMENTO)
    await listar_personajes()


if __name__ == "__main__":
    asyncio.run(main())
