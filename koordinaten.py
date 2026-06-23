# ════════════════════════════════════════════════
#  Koordinaten-Cog  •  VHA Alliance  •  Mecha Fire
#  SQLite statt MongoDB
#  Koordinaten bleiben auch nach Neustart erhalten!
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import logging
from db_helper import get_all_koordinaten, add_koord, delete_koord, koord_exists, init_db
from log import add_log

log = logging.getLogger("VHABot.Koordinaten")

ALLOWED_ROLES = {"R5", "R4"}

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)

INITIAL_KOORDINATEN = [
    {"name": "2525", "r": 75, "x": 286, "y": 907},
    {"name": "ADH",  "r": 75, "x": 190, "y": 367},
    {"name": "AMFS", "r": 75, "x": 390, "y": 327},
    {"name": "AXIM", "r": 75, "x": 176, "y": 531},
    {"name": "BRSS", "r": 75, "x": 337, "y": 778},
    {"name": "FOO",  "r": 75, "x": 258, "y": 402},
    {"name": "GD6",  "r": 75, "x": 125, "y": 862},
    {"name": "GLBL", "r": 75, "x": 236, "y": 593},
    {"name": "ION",  "r": 75, "x": 329, "y": 620},
    {"name": "RICO", "r": 75, "x": 435, "y": 203},
    {"name": "RIZE", "r": 75, "x": 217, "y": 802},
    {"name": "TRCL", "r": 75, "x": 170, "y": 703},
    {"name": "Tuna", "r": 75, "x": 214, "y": 659},
    {"name": "VNZL", "r": 75, "x": 165, "y": 456},
]


def has_permission(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    member_roles = {r.name.upper() for r in member.roles}
    allowed_upper = {r.upper() for r in ALLOWED_ROLES}
    return bool(member_roles & allowed_upper)


class KoordDeleteView(discord.ui.View):
    def __init__(self, coord_name: str):
        super().__init__(timeout=300)
        self.coord_name = coord_name

    @discord.ui.button(label="🗑️ Löschen / Supprimer / Apagar", style=discord.ButtonStyle.danger)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or
                any(r.name.upper() in {"R5", "R4", "DEV"} for r in interaction.user.roles)):
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        try:
            deleted = delete_koord(self.coord_name)
            if deleted:
                add_log("Koordinate gelöscht", interaction.user.display_name, self.coord_name)
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


class KoordinatenCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            init_db()
            existing = get_all_koordinaten()
            if not existing:
                for k in INITIAL_KOORDINATEN:
                    add_koord(k["name"], k["r"], k["x"], k["y"])
                log.info("Initiale Koordinaten eingefügt!")
        except Exception as e:
            log.error(f"Fehler beim Initialisieren der Koordinaten: {e}")

    @commands.group(name="koordinaten", aliases=["coord", "coords", "koordinate", "coordonnees", "coordonnée", "coordonnées", "coordenadas"], invoke_without_command=True)
    async def koordinaten(self, ctx):
        try:
            data = get_all_koordinaten()
        except Exception:
            await ctx.send("❌ Fehler beim Laden der Koordinaten.")
            return

        if not data:
            await ctx.send("📭 Keine Koordinaten gespeichert. / Aucune coordonnée. / Nenhuma coordenada.")
            return

        embed = discord.Embed(title="📍 Koordinaten • Mecha Fire", color=0x2ECC71)

        lines = []
        for k in data:
            lines.append(f"`{k['name']:<6}` R:{k['r']}, X:{k['x']}, Y:{k['y']}")

        chunk = ""
        field_num = 1
        for line in lines:
            if len(chunk) + len(line) + 1 > 1000:
                embed.add_field(
                    name="Allianzen / Alliances" if field_num == 1 else f"... {field_num}",
                    value=chunk,
                    inline=False
                )
                chunk = line + "\n"
                field_num += 1
            else:
                chunk += line + "\n"

        if chunk:
            embed.add_field(
                name="Allianzen / Alliances" if field_num == 1 else f"... {field_num}",
                value=chunk,
                inline=False
            )

        embed.set_footer(text=f"Gesamt / Total: {len(data)} • !koordinaten add")
        await ctx.send(embed=embed)

        await ctx.send("🗑️ **Koordinaten löschen / Supprimer / Apagar:**")
        for k in data:
            e = discord.Embed(color=0x2ECC71)
            e.add_field(name=f"📍 {k['name']}", value=f"R:{k['r']}, X:{k['x']}, Y:{k['y']}", inline=False)
            view = KoordDeleteView(k["name"])
            await ctx.send(embed=e, view=view)

    @koordinaten.command(name="add", aliases=["hinzufügen", "ajouter", "adicionar"])
    async def koordinaten_add(self, ctx, name: str, r: int, x: int, y: int):
        if not has_permission(ctx.author):
            embed = discord.Embed(
                title="❌ Keine Berechtigung / Pas d'autorisation / Sem permissão",
                description="Nur **Administrator**, **R5** und **R4** dürfen Koordinaten hinzufügen.",
                color=0xED4245
            )
            await ctx.send(embed=embed)
            return

        if koord_exists(name):
            await ctx.send(f"⚠️ `{name}` existiert bereits. Zuerst löschen mit `!koordinaten delete {name}`")
            return

        try:
            add_koord(name, r, x, y)
        except Exception:
            await ctx.send("❌ Fehler beim Speichern.")
            return

        embed = discord.Embed(title="✅ Koordinate hinzugefügt / Coordonnée ajoutée / Coordenada adicionada", color=0x57F287)
        embed.add_field(name=name, value=f"R:{r}, X:{x}, Y:{y}", inline=False)
        add_log("Koordinate hinzugefügt", ctx.author.display_name, f"{name} R:{r} X:{x} Y:{y}")
        embed.set_footer(text=f"Hinzugefügt von / Ajouté par / Adicionado por {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @koordinaten_add.error
    async def add_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❓ Nutzung: `!koordinaten add NAME R X Y`\nBeispiel: `!koordinaten add VHA 75 217 802`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ R, X und Y müssen Zahlen sein.")

    @koordinaten.command(name="delete", aliases=["löschen", "supprimer", "del", "remove", "apagar"])
    async def koordinaten_delete(self, ctx, *, name: str):
        if not has_permission(ctx.author):
            embed = discord.Embed(
                title="❌ Keine Berechtigung / Pas d'autorisation / Sem permissão",
                color=0xED4245
            )
            await ctx.send(embed=embed)
            return

        try:
            deleted = delete_koord(name)
        except Exception:
            await ctx.send("❌ Fehler beim Löschen.")
            return

        if deleted == 0:
            await ctx.send(f"⚠️ `{name}` nicht gefunden. / `{name}` introuvable. / `{name}` não encontrado.")
            return

        embed = discord.Embed(
            title=f"🗑️ Koordinate gelöscht / Coordonnée supprimée / Coordenada apagada • {name}",
            color=0xED4245
        )
        add_log("Koordinate gelöscht", ctx.author.display_name, name)
        embed.set_footer(text=f"Gelöscht von / Supprimé par / Apagado por {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @koordinaten.command(name="help", aliases=["hilfe", "aide", "ajuda"])
    async def koordinaten_help(self, ctx):
        embed = discord.Embed(title="📍 Koordinaten – Hilfe / Aide / Ajuda", color=0x3498DB)
        embed.add_field(
            name="🇩🇪 Befehle",
            value=(
                "`!koordinaten` – Alle anzeigen\n"
                "`!koordinaten add NAME R X Y` – Hinzufügen\n"
                "`!koordinaten delete NAME` – Löschen\n"
                "**Beispiel:** `!koordinaten add VHA 75 217 802`"
            ),
            inline=False
        )
        embed.add_field(
            name="🇫🇷 Commandes",
            value=(
                "`!coordonnees` – Afficher\n"
                "`!coordonnees ajouter NOM R X Y` – Ajouter\n"
                "`!coordonnees supprimer NOM` – Supprimer"
            ),
            inline=False
        )
        embed.add_field(
            name="🇧🇷 Comandos",
            value=(
                "`!coordenadas` – Ver todas\n"
                "`!koordinaten adicionar NOME R X Y` – Adicionar\n"
                "`!koordinaten apagar NOME` – Apagar"
            ),
            inline=False
        )
        embed.add_field(name="🔐 Berechtigung / Permission", value="Administrator, R5, R4", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(KoordinatenCog(bot))
