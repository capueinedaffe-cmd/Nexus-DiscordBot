"""
events.py
---------
Eventos de la expedición: consecuencias narrativas/mecánicas que se
disparan como reacción a lo que pasa en el juego (derrotar un jefe,
completar una zona, matar 40 arpías, etc.), no como respuesta directa
a un comando de combate. combat.py se limita a resolver combates;
expedition.py (cuando exista) va a llamar a las funciones de acá para
disparar estos eventos en el momento narrativo correcto.

Por ahora, el único evento implementado es el desafío de la Matriarca
Arpía Furiosa: es INEVITABLE en cuanto se derrota a las 40 arpías
menores (no es una elección del jugador, es consecuencia directa).
"""

from commands.combat import Fighter, CombatSession, ACTIVE_COMBATS, _resolver_turnos_npc
from store.characters_store import get_character
from store.expedition_store import construir_personaje_enemigo, armar_oleadas_arpias, ARPIAS_POR_OLEADA

async def iniciar_evento_matriarca(channel_id: int, personajes_convocados: list) -> tuple:
    """
    Arma la sesión de combate de las 40 arpías + Matriarca oculta como
    evento inevitable. personajes_convocados: lista de Character ya
    resueltos (uno por jugador participante). Devuelve (session, texto_npc_inicial,
    combate_termino_de_una) — esto último cubre el caso límite de que la
    iniciativa ponga a una arpía primera y el combate se resuelva solo
    antes de que cualquier jugador llegue a actuar.

    NOTA: esto todavía no valida "¿ya completaron el evento final de la
    Montaña?" — esa condición la va a chequear expedition.py ANTES de
    llamar a esta función, comparando expedition["evento_final_completado"].
    """
    if channel_id in ACTIVE_COMBATS:
        raise RuntimeError("Ya hay un combate en curso en este canal.")

    primera_oleada_ids, resto_oleadas = armar_oleadas_arpias()
    resto_oleadas.append(["matriarca_arpia_furiosa"])

    jugadores_fighters = [Fighter(char, team=0) for char in personajes_convocados]
    arpias_iniciales = [Fighter(construir_personaje_enemigo("arpia_menor"), team=1)
                         for _ in range(ARPIAS_POR_OLEADA)]

    session = CombatSession(
        channel_id,
        jugadores_fighters + arpias_iniciales,
        oleadas_enemigos=resto_oleadas,
        equipo_oleadas=1,
    )
    ACTIVE_COMBATS[channel_id] = session

    # Por si la iniciativa puso a una arpía primera, antes de que cualquier
    # jugador pueda actuar (mismo caso límite que en /preparado de combat.py).
    texto_npc_inicial, combate_termino_de_una = await _resolver_turnos_npc(session)

    return session, texto_npc_inicial, combate_termino_de_una

def setup_test_event_commands(bot):
    """Comandos de prueba temporales — se eliminan cuando expedition.py dispare esto de verdad."""
    import discord
    from discord import app_commands
    from commands.combat import mi_personaje_lobby_autocomplete

    @bot.tree.command(name="desafiar_matriarca", description="[Prueba] Inicia la pelea de las 40 arpías + Matriarca oculta")
    @app_commands.describe(personaje="Tu personaje a convocar")
    @app_commands.autocomplete(personaje=mi_personaje_lobby_autocomplete)
    async def desafiar_matriarca(interaction: discord.Interaction, personaje: str):
        char = await get_character(interaction.user.id, personaje)
        if not char:
            await interaction.response.send_message(
                f"No tenés un personaje llamado **{personaje}**.", ephemeral=True
            )
            return

        try:
            session, texto_npc_inicial, combate_termino_de_una = await iniciar_evento_matriarca(
                interaction.channel_id, [char]
            )
        except RuntimeError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        init_text = "\n".join(f"{name}: {roll}" for name, roll in session.initiative_log)
        embed = session.status_embed(title="🦅 ¡Las arpías enloquecidas atacan!")
        embed.add_field(name="Iniciativa (1d6 + AGI)", value=init_text, inline=False)
        embed.add_field(
            name="Oleada",
            value=f"{session.oleada_actual}/{session.oleadas_totales} — la Matriarca aparece al final si sobreviven todas.",
            inline=False,
        )
        if texto_npc_inicial:
            embed.description = texto_npc_inicial + "\n\n" + embed.description

        await interaction.response.send_message("¡El desafío comienza!", ephemeral=True)

        if combate_termino_de_una:
            from commands.combat import _persist_combat_stats
            winning_team = session.winning_team()
            ganadores = [f.name for f in session.fighters if f.team == winning_team]
            embed.title = "🏆 Combate finalizado"
            embed.description += f"\n\n**Equipo {winning_team + 1} gana!** ({', '.join(ganadores)})"
            session.status_message = await interaction.channel.send(embed=embed)
            await _persist_combat_stats(session, {winning_team: "victoria", 1 - winning_team: "derrota"})
            del ACTIVE_COMBATS[interaction.channel_id]
        else:
            session.status_message = await interaction.channel.send(embed=embed)
