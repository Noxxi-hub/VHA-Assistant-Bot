import discord
from discord.ext import commands

from logger import log
import state
from state import LOGO_URL, NOXXI_ID
from gemini_core import token_counter

# ────────────────────────────────────────────────
# RESTLICHE BEFEHLE: help / ping / translate / kanalid / clean
# ────────────────────────────────────────────────

class Commands(commands.Cog):
    """help, ping, translate, kanalid, clean."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def cmd_help(self, ctx):
        embed = discord.Embed(
            title="VHA Bot – Befehle / Commandes / Comandos",
            color=0x5865F2
        )
        embed.set_author(name="VHA ALLIANCE", icon_url=LOGO_URL)

        embed.add_field(
            name="🌐 Übersetzer / Traducteur / Tradutor",
            value=(
                "`!translate on` / `!translate off` – An • Aus / Activer • Désactiver / Ativar • Desativar\n"
                "`!translate status` – Status / Statut / Estado\n"
                "`!ai [Text]` – KI fragen / Poser une question / Perguntar à IA\n"
                "`!übersetze` / `!traduire` – Bild übersetzen / Traduire image / Traduzir imagem"
            ),
            inline=False
        )

        embed.add_field(
            name="📍 Koordinaten / Coordonnées / Coordenadas  🔐 R5 • R4",
            value=(
                "`!koordinaten` / `!coordonnees` – Liste mit 🗑️ Delete-Buttons\n"
                "`!koordinaten add NAME R X Y` – Hinzufügen / Ajouter / Adicionar"
            ),
            inline=False
        )

        embed.add_field(
            name="👥 Spieler-IDs / Joueurs / Jogadores  🔐 R5 • R4",
            value=(
                "`!spieler` / `!joueur` – Liste mit 🗑️ Delete-Buttons\n"
                "`!spieler add NAME ID` – Hinzufügen / Ajouter / Adicionar\n"
                "`!spieler suche NAME/ID` – Suchen / Rechercher / Pesquisar"
            ),
            inline=False
        )

        embed.add_field(
            name="⚔️ SVS Koordinaten  🔐 R5 • R4",
            value=(
                "`!svs` – Alle Server & Koordinaten\n"
                "`!svs R77` – Server R77 mit 🗑️ Delete-Buttons\n"
                "`!svs server` – Verfügbare Server\n"
                "`!svs add SERVER NAME R X Y` – Hinzufügen"
            ),
            inline=False
        )

        embed.add_field(
            name="🌐 Sprachen / Langues / Idiomas  🔐 R5 • R4",
            value=(
                "`!sprachen` / `!languages` / `!idiomas` – Globale Sprachen ein/ausschalten mit Buttons\n"
                "`!raumsprachen [Kanal-ID]` – Sprachen nur für einen bestimmten Raum einstellen (nur Bot-Kanal, nur R5/Dev)\n"
                "`!kanalid` – Alle Kanäle mit ID als Direktnachricht (für !raumsprachen)\n"
                "💡 Kein Eintrag = globale Einstellungen • 🚫 Deaktivieren = keine Übersetzung im Raum"
            ),
            inline=False
        )

        embed.add_field(
            name="🏗️ Server-Struktur  🔐 Bot DEV",
            value=(
                "`!server export` – Aktuelle Struktur speichern\n"
                "`!server preview` – Gespeicherte Struktur anzeigen\n"
                "`!server import` – Struktur auf neuem Server erstellen"
            ),
            inline=False
        )
        embed.add_field(
            name="🗑️ Kanal leeren  🔐 Bot DEV",
            value=(
                "`!clean` – Alle Nachrichten im aktuellen Kanal löschen (mit Bestätigung)\n"
                "`!clean 50` – 50 Nachrichten im aktuellen Kanal löschen\n"
                "`!clean [Kanal-ID]` – Alle Nachrichten in einem anderen Kanal löschen\n"
                "`!clean [Kanal-ID] 50` – 50 Nachrichten in einem anderen Kanal löschen\n"
                "⚠️ Nur Nachrichten jünger als 14 Tage können gelöscht werden"
            ),
            inline=False
        )

        embed.add_field(
            name="📊 Status",
            value="`!ping` – Bot-Status / Latenz",
            inline=False
        )

        embed.set_thumbnail(url=LOGO_URL)
        embed.set_footer(text="VHA - Powering Communication", icon_url=LOGO_URL)
        await ctx.send(embed=embed)

    @commands.command(name="ping")
    async def cmd_ping(self, ctx):
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="🏓 Pong!",
            color=0x57F287 if latency < 200 else 0xF39C12
        )
        embed.add_field(name="📡 Latenz / Latence", value=f"`{latency}ms`", inline=True)
        embed.add_field(name="📊 Tokens heute / Today", value=f"`{token_counter['total']}`", inline=True)
        embed.set_footer(text="VHA Bot • Online", icon_url=LOGO_URL)
        await ctx.send(embed=embed)

    @commands.command(name="translate")
    @commands.has_permissions(manage_messages=True)
    async def cmd_translate(self, ctx, action: str = None):
        if action is None:
            await ctx.send(
                "❓ Benutzung: `!translate on` / `!translate off` / `!translate status`\n"
                "Usage: `!translate on` / `!translate off` / `!translate status`"
            )
            return

        action = action.lower()

        if action == "on":
            state.translate_active = True
            embed = discord.Embed(title="VHA System • Übersetzung", color=0x57F287)
            embed.add_field(name="Deutsch ↔ Français ↔ Português", value="Aktiviert / Activée / Ativada", inline=False)
            await ctx.send(embed=embed)

        elif action == "off":
            state.translate_active = False
            embed = discord.Embed(title="VHA System • Übersetzung", color=0xED4245)
            embed.add_field(name="Deutsch ↔ Français ↔ Português", value="Deaktiviert / Désactivée / Desativada", inline=False)
            await ctx.send(embed=embed)

        elif action == "status":
            if state.translate_active:
                embed = discord.Embed(title="VHA System • Übersetzung", color=0x57F287)
                embed.add_field(name="Deutsch ↔ Français ↔ Português", value="Aktiviert / Activée / Ativada", inline=False)
            else:
                embed = discord.Embed(title="VHA System • Übersetzung", color=0xED4245)
                embed.add_field(name="Deutsch ↔ Français ↔ Português", value="Deaktiviert / Désactivée / Desativada", inline=False)
            await ctx.send(embed=embed)

        else:
            await ctx.send(
                "❓ Unbekannte Option. Benutze: `!translate on` / `!translate off` / `!translate status`"
            )

    @commands.command(name="kanalid", aliases=["channelid", "kanalids"])
    async def cmd_kanalid(self, ctx):
        """Zeigt alle Textkanäle mit ihrer ID — nur für den Aufrufer sichtbar."""
        if not ctx.author.guild_permissions.administrator:
            member_roles = {r.name.upper() for r in ctx.author.roles}
            if not member_roles & {"R5", "R4", "DEV"}:
                await ctx.send("❌ Keine Berechtigung.", delete_after=5)
                return

        lines = []
        for category, channels in ctx.guild.by_category():
            cat_name = category.name if category else "Ohne Kategorie"
            text_channels = [c for c in channels if isinstance(c, discord.TextChannel)]
            if not text_channels:
                continue
            lines.append(f"**{cat_name}**")
            for ch in text_channels:
                lines.append(f"• #{ch.name} — `{ch.id}`")

        # Aufteilen falls zu lang für eine Nachricht
        chunks = []
        current = []
        length = 0
        for line in lines:
            if length + len(line) > 1800:
                chunks.append("\n".join(current))
                current = [line]
                length = len(line)
            else:
                current.append(line)
                length += len(line)
        if current:
            chunks.append("\n".join(current))

        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"📋 Kanal-IDs • {ctx.guild.name}" + (f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""),
                description=chunk,
                color=0x5865F2
            )
            embed.set_footer(text="Nur für dich sichtbar • Für !raumsprachen [ID] verwenden")
            await ctx.author.send(embed=embed)

        await ctx.send("📬 Ich habe dir die Kanal-IDs als Direktnachricht geschickt!", delete_after=8)

    @commands.command(name="clean", aliases=["clear", "purge", "löschen"])
    async def cmd_clean(self, ctx, *args):
        """
        Löscht Nachrichten. Nur für NOXXI.
        Verwendung:
          !clean                        → alles im aktuellen Kanal
          !clean 50                     → 50 Nachrichten im aktuellen Kanal
          !clean [Kanal-ID]             → alles in einem anderen Kanal
          !clean [Kanal-ID] 50          → 50 Nachrichten in einem anderen Kanal
        """
        import asyncio as _asyncio

        if ctx.author.id != NOXXI_ID:
            await ctx.send("❌ Dieser Befehl ist nur für ausgewählte Personen.", delete_after=5)
            return

        # Befehlsnachricht sofort löschen
        try:
            await ctx.message.delete()
        except Exception:
            pass

        # Args parsen: Kanal-ID (>100000) und/oder Menge
        target_channel = ctx.channel
        menge = None

        for arg in args:
            try:
                val = int(arg)
                if val > 100000:
                    # Kanal-ID
                    ch = ctx.guild.get_channel(val)
                    if not ch:
                        await ctx.send(f"❌ Kanal `{val}` nicht gefunden.", delete_after=6)
                        return
                    target_channel = ch
                else:
                    menge = val
            except ValueError:
                await ctx.send(f"❌ Ungültiger Parameter: `{arg}`", delete_after=6)
                return

        # Unterschied ob aktueller oder anderer Kanal
        remote = target_channel.id != ctx.channel.id
        channel_mention = f"<#{target_channel.id}>" if remote else "diesem Kanal"

        if menge is not None and (menge < 1 or menge > 1000):
            await ctx.send("❌ Bitte eine Zahl zwischen 1 und 1000 angeben.", delete_after=6)
            return

        # Alles löschen → Bestätigung
        if menge is None:
            confirm_msg = await ctx.send(
                f"⚠️ **Alle Nachrichten in {channel_mention} löschen?**\n"
                "Reagiere mit ✅ zum Bestätigen oder ❌ zum Abbrechen.\n"
                "*(Nur Nachrichten jünger als 14 Tage können gelöscht werden)*",
            )
            await confirm_msg.add_reaction("✅")
            await confirm_msg.add_reaction("❌")

            def check(reaction, user):
                return (
                    user == ctx.author
                    and str(reaction.emoji) in ["✅", "❌"]
                    and reaction.message.id == confirm_msg.id
                )

            try:
                reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            except _asyncio.TimeoutError:
                await confirm_msg.edit(content="⏰ Timeout — Abgebrochen.", delete_after=5)
                return

            if str(reaction.emoji) == "❌":
                await confirm_msg.edit(content="❌ Abgebrochen.", delete_after=5)
                return

            await confirm_msg.delete()
            status = await ctx.send(f"🗑️ Lösche alle Nachrichten in {channel_mention}...")

            deleted_total = 0
            while True:
                deleted = await target_channel.purge(limit=100)
                deleted_total += len(deleted)
                if len(deleted) < 100:
                    break

            await status.edit(
                content=f"✅ **{deleted_total} Nachrichten** in {channel_mention} **gelöscht.**\n"
                        f"*(Diese Meldung verschwindet in 8 Sekunden)*"
            )
            await _asyncio.sleep(8)
            try:
                await status.delete()
            except Exception:
                pass

        else:
            # Bestimmte Anzahl löschen
            deleted = await target_channel.purge(limit=menge)
            status = await ctx.send(
                f"✅ **{len(deleted)} Nachrichten** in {channel_mention} **gelöscht.**\n"
                f"*(Diese Meldung verschwindet in 6 Sekunden)*"
            )
            await _asyncio.sleep(6)
            try:
                await status.delete()
            except Exception:
                pass

    async def cog_command_error(self, ctx, error):
        if ctx.command and ctx.command.name == "translate" and isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Du hast keine Berechtigung dafür. / Tu n'as pas la permission.")
            return
        if ctx.command and ctx.command.name == "clean" and isinstance(error, commands.BadArgument):
            await ctx.send(
                "❌ Ungültige Eingabe.\n"
                "Beispiele: `!clean` · `!clean 50` · `!clean 1234567890` · `!clean 1234567890 50`",
                delete_after=8
            )
            return
        # Sonst wie Original: Fehler nicht abfangen → default discord handling
        raise error


async def setup(bot):
    await bot.add_cog(Commands(bot))