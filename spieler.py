# ════════════════════════════════════════════════
#  Spieler-Cog  •  VHA Alliance  •  Mecha Fire
#  SQLite statt MongoDB
#  Mit Delete-Buttons in der Liste
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import logging
from db_helper import get_all_spieler, add_spieler, delete_spieler, spieler_exists, spieler_id_exists, init_db
from log import add_log

log = logging.getLogger("VHABot.Spieler")

ALLOWED_ROLES = {"R5", "R4"}

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)


def has_permission(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    member_roles = {r.name.upper() for r in member.roles}
    return bool(member_roles & {r.upper() for r in ALLOWED_ROLES})


def make_list_embed(data: list) -> discord.Embed:
    embed = discord.Embed(title="👥 Spieler-IDs • Mecha Fire", color=0x2ECC71)
    lines = [f"`{s['name']:<15}` ID: `{s['id']}`" for s in data]

    chunk = ""
    field_num = 1
    for line in lines:
        if len(chunk) + len(line) + 1 > 1000:
            embed.add_field(
                name="Spieler / Joueurs / Jogadores" if field_num == 1 else f"... {field_num}",
                value=chunk,
                inline=False
            )
            chunk = line + "\n"
            field_num += 1
        else:
            chunk += line + "\n"

    if chunk:
        embed.add_field(
            name="Spieler / Joueurs / Jogadores" if field_num == 1 else f"... {field_num}",
            value=chunk,
            inline=False
        )

    embed.set_footer(text=f"Gesamt / Total: {len(data)} • Buttons zum Löschen • !spieler add NAME ID")
    return embed


class SpielerDeleteView(discord.ui.View):
    def __init__(self, author: discord.Member, data: list, page: int = 0):
        super().__init__(timeout=120)
        self.author = author
        self.data = data
        self.page = page
        self.per_page = 16
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        start = self.page * self.per_page
        end = start + self.per_page
        page_data = self.data[start:end]

        for i, spieler in enumerate(page_data):
            btn = discord.ui.Button(
                label=f"🗑️ {spieler['name']}",
                style=discord.ButtonStyle.danger,
                custom_id=f"del_{spieler['name']}",
                row=i // 4
            )
            btn.callback = self._make_delete_callback(spieler["name"])
            self.add_item(btn)

        total_pages = (len(self.data) - 1) // self.per_page + 1
        if total_pages > 1:
            if self.page > 0:
                prev_btn = discord.ui.Button(
                    label="◀️ Zurück",
                    style=discord.ButtonStyle.secondary,
                    custom_id="prev_page",
                    row=4
                )
                prev_btn.callback = self._prev_page
                self.add_item(prev_btn)

            page_info = discord.ui.Button(
                label=f"Seite {self.page + 1}/{total_pages}",
                style=discord.ButtonStyle.secondary,
                custom_id="page_info",
                disabled=True,
                row=4
            )
            self.add_item(page_info)

            if self.page < total_pages - 1:
                next_btn = discord.ui.Button(
                    label="Weiter ▶️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="next_page",
                    row=4
                )
                next_btn.callback = self._next_page
                self.add_item(next_btn)

    def _make_delete_callback(self, name: str):
        async def callback(interaction: discord.Interaction):
            if not has_permission(interaction.user):
                await interaction.response.send_message(
                    "❌ Keine Berechtigung / Pas d'autorisation / Sem permissão",
                    ephemeral=True
                )
                return

            try:
                deleted = delete_spieler(name)
            except Exception as e:
                await interaction.response.send_message(f"❌ Fehler beim Löschen: {e}", ephemeral=True)
                return

            if deleted == 0:
                await interaction.response.send_message(
                    f"⚠️ `{name}` nicht gefunden.",
                    ephemeral=True
                )
                return

            add_log("Spieler gelöscht", interaction.user.display_name, name)

            try:
                self.data = get_all_spieler()
            except Exception:
                pass

            if not self.data:
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="👥 Spieler-IDs • Mecha Fire",
                        description="📭 Keine Spieler gespeichert.",
                        color=0x2ECC71
                    ),
                    view=None
                )
                await interaction.followup.send(f"🗑️ **{name}** gelöscht.", ephemeral=True)
                return

            total_pages = (len(self.data) - 1) // self.per_page + 1
            if self.page >= total_pages:
                self.page = total_pages - 1

            self._build_buttons()
            await interaction.response.edit_message(
                embed=make_list_embed(self.data),
                view=self
            )
            await interaction.followup.send(f"🗑️ **{name}** gelöscht.", ephemeral=True)

        return callback

    async def _prev_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Nur der Aufrufer kann blättern.", ephemeral=True)
            return
        self.page -= 1
        self._build_buttons()
        await interaction.response.edit_message(embed=make_list_embed(self.data), view=self)

    async def _next_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Nur der Aufrufer kann blättern.", ephemeral=True)
            return
        self.page += 1
        self._build_buttons()
        await interaction.response.edit_message(embed=make_list_embed(self.data), view=self)


class SpielerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            init_db()
        except Exception as e:
            log.error(f"Spieler DB Init: {e}")

    @commands.group(name="spieler", aliases=["joueur", "joueurs", "player", "players", "ids"], invoke_without_command=True)
    async def spieler(self, ctx):
        try:
            data = get_all_spieler()
        except Exception:
            await ctx.send("❌ Fehler beim Laden der Spieler.")
            return

        if not data:
            await ctx.send(
                "📭 Keine Spieler gespeichert.\n"
                "Aucun joueur enregistré.\n"
                "Nenhum jogador registrado."
            )
            return

        embed = make_list_embed(data)

        if has_permission(ctx.author):
            view = SpielerDeleteView(ctx.author, data)
            await ctx.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed)

    @spieler.command(name="add", aliases=["hinzufügen", "ajouter", "adicionar"])
    async def spieler_add(self, ctx, name: str, spieler_id: str):
        if not has_permission(ctx.author):
            embed = discord.Embed(
                title="❌ Keine Berechtigung / Pas d'autorisation / Sem permissão",
                description="Nur **Administrator**, **R5** und **R4** dürfen Spieler hinzufügen.",
                color=0xED4245
            )
            await ctx.send(embed=embed)
            return

        if not spieler_id.isdigit():
            await ctx.send("❌ Die ID muss eine Zahl sein. / L'ID doit être un nombre. / O ID deve ser um número.")
            return

        if spieler_exists(name):
            await ctx.send(f"⚠️ `{name}` existiert bereits. Zuerst löschen mit `!spieler delete {name}`")
            return

        existing = spieler_id_exists(spieler_id)
        if existing:
            await ctx.send(f"⚠️ ID `{spieler_id}` ist bereits **{existing}** zugeordnet.")
            return

        try:
            add_spieler(name, spieler_id)
        except Exception:
            await ctx.send("❌ Fehler beim Speichern.")
            return

        embed = discord.Embed(title="✅ Spieler hinzugefügt / Joueur ajouté / Jogador ajouté", color=0x57F287)
        embed.add_field(name="👤 Name", value=name, inline=True)
        embed.add_field(name="🆔 ID", value=spieler_id, inline=True)
        add_log("Spieler hinzugefügt", ctx.author.display_name, f"{name} (ID: {spieler_id})")
        embed.set_footer(text=f"Hinzugefügt von / Ajouté par / Adicionado por {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @spieler_add.error
    async def add_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❓ Nutzung: `!spieler add NAME ID`\nBeispiel: `!spieler add Noxxi 3881385`")

    @spieler.command(name="delete", aliases=["löschen", "supprimer", "del", "remove", "apagar"])
    async def spieler_delete(self, ctx, *, name: str):
        if not has_permission(ctx.author):
            embed = discord.Embed(
                title="❌ Keine Berechtigung / Pas d'autorisation / Sem permissão",
                color=0xED4245
            )
            await ctx.send(embed=embed)
            return

        try:
            deleted = delete_spieler(name)
        except Exception:
            await ctx.send("❌ Fehler beim Löschen.")
            return

        if deleted == 0:
            await ctx.send(f"⚠️ `{name}` nicht gefunden.")
            return

        embed = discord.Embed(
            title=f"🗑️ Spieler gelöscht / Joueur supprimé / Jogador apagado • {name}",
            color=0xED4245
        )
        add_log("Spieler gelöscht", ctx.author.display_name, name)
        embed.set_footer(text=f"Gelöscht von {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @spieler.command(name="suche", aliases=["search", "chercher", "find", "id", "pesquisar"])
    async def spieler_suche(self, ctx, *, suche: str):
        try:
            from db_helper import find_spieler
            gefunden = find_spieler(suche)
        except Exception:
            await ctx.send("❌ Fehler bei der Suche.")
            return

        if not gefunden:
            await ctx.send(f"🔍 Kein Spieler mit `{suche}` gefunden.")
            return

        embed = discord.Embed(title=f"🔍 Suchergebnis • {suche}", color=0x3498DB)
        for s in gefunden:
            embed.add_field(name=f"👤 {s['name']}", value=f"🆔 `{s['id']}`", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(SpielerCog(bot))
