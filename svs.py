# ════════════════════════════════════════════════
#  SVS-Koordinaten-Cog  •  VHA Alliance
#  Server vs Server Koordinaten nach Servern
#  SQLite statt MongoDB
#  Erlaubte Rollen: Administrator, R5, R4
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import logging
from db_helper import get_all_svs, add_svs, delete_svs_by_id, get_distinct_servers, count_svs, init_db
from log import add_log

log = logging.getLogger("VHABot.SVS")

ALLOWED_ROLES = {"R5", "R4"}

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)

INITIAL_SVS = [
    {"server": "R77", "name": "Centre gnz1", "r": 77, "x": 244, "y": 574},
    {"server": "R77", "name": "Centre gnz2", "r": 77, "x": 229, "y": 437},
    {"server": "R77", "name": "Centre gnz",  "r": 77, "x": 269, "y": 453},
    {"server": "R77", "name": "Centre srg",  "r": 77, "x": 177, "y": 532},
]


def has_permission(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    member_roles = {r.name.upper() for r in member.roles}
    allowed_upper = {r.upper() for r in ALLOWED_ROLES}
    return bool(member_roles & allowed_upper)


class SVSDeleteView(discord.ui.View):
    def __init__(self, coord_id: int, coord_name: str):
        super().__init__(timeout=300)
        self.coord_id = coord_id
        self.coord_name = coord_name

    @discord.ui.button(label="🗑️ Löschen / Supprimer / Apagar", style=discord.ButtonStyle.danger)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or
                any(r.name.upper() in {"R5", "R4", "DEV"} for r in interaction.user.roles)):
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        try:
            deleted = delete_svs_by_id(self.coord_id)
            if deleted:
                add_log("SVS Koordinate gelöscht", interaction.user.display_name, self.coord_name)
            button.disabled = True
            button.label = "✅ Gelöscht"
            button.style = discord.ButtonStyle.secondary
            await interaction.message.edit(view=self)
            await interaction.response.send_message(
                f"🗑️ **{self.coord_name}** gelöscht / supprimé / apagado",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Fehler: {e}", ephemeral=True)


class SVSCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            init_db()
            if count_svs() == 0:
                for s in INITIAL_SVS:
                    add_svs(s["server"], s["name"], s["r"], s["x"], s["y"])
                log.info("Initiale SVS Koordinaten eingefügt!")
        except Exception as e:
            log.error(f"SVS Init-Fehler: {e}")

    @commands.group(name="svs", aliases=["svs_koordinaten", "servervsserver"], invoke_without_command=True)
    async def svs(self, ctx, server: str = None):
        try:
            data = get_all_svs(server)
        except Exception as e:
            await ctx.send("❌ Fehler beim Laden der SVS Koordinaten.")
            return

        if not data:
            msg = f"📭 Keine Koordinaten für Server **{server}**." if server else "📭 Keine SVS Koordinaten gespeichert."
            await ctx.send(msg)
            return

        servers = {}
        for k in data:
            s = k["server"]
            if s not in servers:
                servers[s] = []
            servers[s].append(k)

        if server:
            s_name = list(servers.keys())[0]
            coords = servers[s_name]
            await ctx.send(f"⚔️ **SVS Koordinaten • {s_name}** ({len(coords)} Einträge)")
            for k in coords:
                embed = discord.Embed(color=0xE74C3C)
                embed.add_field(
                    name=f"📍 {k['name']}",
                    value=f"R:{k['r']}, X:{k['x']}, Y:{k['y']}",
                    inline=False
                )
                view = SVSDeleteView(int(k["_id"]), k["name"])
                await ctx.send(embed=embed, view=view)
        else:
            embed = discord.Embed(
                title="⚔️ SVS Koordinaten • Server vs Server",
                color=0xE74C3C
            )
            for s_name, coords in servers.items():
                lines = [f"`{k['name']:<15}` R:{k['r']}, X:{k['x']}, Y:{k['y']}" for k in coords]
                embed.add_field(
                    name=f"🌍 Server {s_name} ({len(coords)})",
                    value="\n".join(lines)[:1000],
                    inline=False
                )
            embed.set_footer(text=f"Gesamt: {len(data)} • !svs SERVER für Details & Löschen")
            await ctx.send(embed=embed)

    @svs.command(name="add", aliases=["hinzufügen", "ajouter", "adicionar"])
    async def svs_add(self, ctx, server: str, name: str, r: int, x: int, y: int):
        if not has_permission(ctx.author):
            await ctx.send("❌ Keine Berechtigung / Pas d'autorisation / Sem permissão")
            return

        try:
            add_svs(server, name, r, x, y)
            add_log("SVS Koordinate hinzugefügt", ctx.author.display_name, f"{server} • {name} R:{r} X:{x} Y:{y}")
        except Exception as e:
            await ctx.send("❌ Fehler beim Speichern.")
            return

        embed = discord.Embed(
            title="✅ SVS Koordinate hinzugefügt",
            color=0x57F287
        )
        embed.add_field(name="🌍 Server", value=server.upper(), inline=True)
        embed.add_field(name="📍 Name", value=name, inline=True)
        embed.add_field(name="📌 Position", value=f"R:{r}, X:{x}, Y:{y}", inline=True)
        embed.set_footer(text=f"Hinzugefügt von {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @svs_add.error
    async def add_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❓ Nutzung: `!svs add SERVER NAME R X Y`\n"
                "Beispiel: `!svs add R77 \"Centre gnz1\" 77 244 574`"
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ R, X und Y müssen Zahlen sein.")

    @svs.command(name="server", aliases=["servers", "liste", "list"])
    async def svs_server(self, ctx):
        try:
            servers = get_distinct_servers()
        except Exception:
            await ctx.send("❌ Fehler.")
            return

        if not servers:
            await ctx.send("📭 Keine Server eingetragen.")
            return

        embed = discord.Embed(title="⚔️ SVS • Verfügbare Server", color=0xE74C3C)
        server_list = "\n".join([f"• **{s}** — `!svs {s}`" for s in sorted(servers)])
        embed.add_field(name="🌍 Server", value=server_list, inline=False)
        embed.set_footer(text="!svs SERVER für Koordinaten mit Delete-Buttons")
        await ctx.send(embed=embed)

    @svs.command(name="help", aliases=["hilfe", "aide", "ajuda"])
    async def svs_help(self, ctx):
        embed = discord.Embed(title="⚔️ SVS – Hilfe / Aide / Ajuda", color=0xE74C3C)
        embed.add_field(
            name="🇩🇪 Befehle",
            value=(
                "`!svs` – Alle Server & Koordinaten\n"
                "`!svs R77` – Koordinaten von Server R77\n"
                "`!svs server` – Verfügbare Server\n"
                "`!svs add SERVER NAME R X Y` – Hinzufügen\n"
                "**Beispiel:** `!svs add R77 Zentrum 77 244 574`"
            ),
            inline=False
        )
        embed.add_field(
            name="🔐 Berechtigung / Permission",
            value="Administrator, R5, R4",
            inline=False
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(SVSCog(bot))
