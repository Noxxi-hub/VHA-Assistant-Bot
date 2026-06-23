# ════════════════════════════════════════════════
#  Log-Cog  •  VHA Alliance
#  Zeichnet alle Aktionen auf (add/delete)
#  Nur für die Rolle "Creator" sichtbar
#  SQLite statt MongoDB
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import logging
from db_helper import add_log, get_logs, clear_logs, count_logs

log = logging.getLogger("VHABot.Log")

LOG_ROLE = "dev"

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)


def has_log_permission(member: discord.Member) -> bool:
    return any(r.name == LOG_ROLE for r in member.roles) or member.guild_permissions.administrator


class LogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="log", aliases=["logs", "verlauf", "historique", "historico"], invoke_without_command=True)
    async def cmd_log(self, ctx, anzahl: int = 20):
        """Zeigt die letzten X Log-Einträge. Nur für Creator."""
        if not has_log_permission(ctx.author):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            try:
                await ctx.message.delete()
            except Exception:
                pass
            return

        try:
            anzahl = min(anzahl, 50)
            entries = get_logs(anzahl)
        except Exception as e:
            await ctx.send("❌ Fehler beim Laden des Logs.", delete_after=10)
            return

        if not entries:
            await ctx.send("📭 Noch keine Log-Einträge.", delete_after=10)
            return

        embed = discord.Embed(
            title=f"📋 Activity Log • Letzte {len(entries)} Einträge",
            color=0x2C3E50
        )

        lines = []
        for e in entries:
            action = e["action"]
            user = e["user"]
            details = e["details"]
            date = e["date"]

            if "hinzugefügt" in action or "gesetzt" in action:
                icon = "✅"
            elif "gelöscht" in action:
                icon = "🗑️"
            else:
                icon = "📝"

            lines.append(f"{icon} `{date}` **{user}** – {action}: {details}")

        chunk = ""
        field_num = 1
        for line in lines:
            if len(chunk) + len(line) + 1 > 1000:
                embed.add_field(name=f"Einträge {field_num}", value=chunk, inline=False)
                chunk = line + "\n"
                field_num += 1
            else:
                chunk += line + "\n"

        if chunk:
            embed.add_field(
                name="Einträge" if field_num == 1 else f"Einträge {field_num}",
                value=chunk,
                inline=False
            )

        total = count_logs()
        embed.set_footer(text=f"Gesamt: {total} Einträge • Nur für Creator sichtbar")
        await ctx.send(embed=embed, delete_after=120)
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @cmd_log.command(name="clear", aliases=["leeren", "vider", "limpar"])
    async def log_clear(self, ctx):
        """Löscht alle Log-Einträge. Nur für Creator."""
        if not has_log_permission(ctx.author):
            await ctx.send("❌ Keine Berechtigung.", delete_after=5)
            try:
                await ctx.message.delete()
            except Exception:
                pass
            return

        try:
            clear_logs()
        except Exception as e:
            await ctx.send("❌ Fehler beim Löschen.", delete_after=10)
            return

        await ctx.send("✅ Log geleert.", delete_after=10)
        try:
            await ctx.message.delete()
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(LogCog(bot))
