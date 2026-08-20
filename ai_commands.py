import discord
from discord.ext import commands

from logger import log
from state import LOGO_URL
from gemini_core import gemini_call_thinking, GEMINI_MODEL
from translate import detect_language_llm, LANG_FLAGS

# ────────────────────────────────────────────────
# !ai / !aipm
# ────────────────────────────────────────────────

class AICommands(commands.Cog):
    """!ai und !aipm — Gemini-Chat in der Fragesprache."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ai")
    @commands.cooldown(1, 12, commands.BucketType.user)
    async def cmd_ai(self, ctx, *, question: str = None):
        if not question or not question.strip():
            await ctx.send("Beispiel: `!ai Qui est la VHA ?`  oder  `!ai Was ist die VHA?`")
            return

        thinking = await ctx.send("**Denke nach …** 🧠")

        lang = await detect_language_llm(question)
        flag = LANG_FLAGS.get(lang, "🌐")
        footer = f"Antwort in {lang}"

        system_prompt = (
            "Du bist ein freundlicher VHA-Alliance Assistent. "
            "Antworte IMMER in derselben Sprache wie die Frage. "
            "Natürlich und direkt."
        )
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question.strip()}]

        try:
            answer = await gemini_call_thinking(
                model=GEMINI_MODEL,
                temperature=0.7,
                max_tokens=1000,
                messages=messages
            )
            color = 0x5865F2
        except Exception as e:
            answer = f"Fehler: {str(e)}"
            color = 0xFF0000
            footer = "Fehler"

        embed = discord.Embed(title=f"VHA KI • Antwort {flag}", description=answer, color=color)
        embed.set_author(name="VHA ALLIANCE", icon_url=LOGO_URL)
        embed.add_field(name="→ Deine Frage", value=question[:900], inline=False)
        embed.set_footer(text=f"VHA • Gemini • {GEMINI_MODEL} • {footer}", icon_url=LOGO_URL)
        await thinking.edit(embed=embed)

    @commands.command(name="aipm")
    @commands.cooldown(1, 12, commands.BucketType.user)
    async def cmd_aipm(self, ctx, *, question: str = None):
        """Wie !ai — Antwort wird nur per DM an den Fragesteller geschickt."""
        if not question or not question.strip():
            await ctx.send("Beispiel: `!aipm Qui est la VHA ?`  oder  `!aipm Was ist die VHA?`", delete_after=10)
            return

        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        confirm = await ctx.send(f"📬 {ctx.author.mention} Ich schicke dir die Antwort per DM!")

        lang = await detect_language_llm(question)
        flag = LANG_FLAGS.get(lang, "🌐")
        footer = f"Antwort in {lang}"

        system_prompt = (
            "Du bist ein freundlicher VHA-Alliance Assistent. "
            "Antworte IMMER in derselben Sprache wie die Frage. "
            "Natürlich und direkt."
        )
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question.strip()}]

        try:
            answer = await gemini_call_thinking(
                model=GEMINI_MODEL,
                temperature=0.7,
                max_tokens=1000,
                messages=messages
            )
            color = 0x5865F2
        except Exception as e:
            answer = f"Fehler: {str(e)}"
            color = 0xFF0000
            footer = "Fehler"

        embed = discord.Embed(title=f"VHA KI • Antwort {flag}", description=answer, color=color)
        embed.set_author(name="VHA ALLIANCE", icon_url=LOGO_URL)
        embed.add_field(name="→ Deine Frage", value=question[:900], inline=False)
        embed.set_footer(text=f"VHA • Gemini • {GEMINI_MODEL} • {footer} • Privat", icon_url=LOGO_URL)

        try:
            await ctx.author.send(embed=embed)
            try:
                await confirm.delete()
            except discord.NotFound:
                pass
        except discord.Forbidden:
            # User hat DMs deaktiviert → Bestätigung löschen, Fehlermeldung zeigen
            try:
                await confirm.delete()
            except discord.NotFound:
                pass
            await ctx.send(
                f"❌ {ctx.author.mention} Ich konnte dir keine DM schicken. "
                "Bitte aktiviere DMs von Servermitgliedern in deinen Discord-Einstellungen.",
                delete_after=15
            )

    async def cog_command_error(self, ctx, error):
        if ctx.command and ctx.command.name == "aipm" and isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ {ctx.author.mention} Bitte warte noch {error.retry_after:.0f}s.",
                delete_after=5
            )
            return
        # Sonst wie Original: Fehler nicht abfangen → default discord handling
        raise error


async def setup(bot):
    await bot.add_cog(AICommands(bot))