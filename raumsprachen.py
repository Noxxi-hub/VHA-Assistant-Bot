# ════════════════════════════════════════════════
#  Raumsprachen-Cog  •  VHA Alliance
#  Raum-spezifische Sprachen per Button steuern
#  Funktioniert auf jedem Server • Nur R5 / Dev
#  SQLite statt MongoDB
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import logging
from db_helper import get_room_langs, set_room_langs, delete_room_langs, get_active_langs, init_db

log = logging.getLogger("VHABot.Raumsprachen")

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)

ALLOWED_ROLES = {"R5", "DEV"}

ALL_ROOM_LANGS = {
    "DE": {"flag": "🇩🇪", "name": "Deutsch"},
    "FR": {"flag": "🇫🇷", "name": "Français"},
    "PT": {"flag": "🇧🇷", "name": "Português"},
    "EN": {"flag": "🇬🇧", "name": "English"},
    "JA": {"flag": "🇯🇵", "name": "日本語"},
    "TR": {"flag": "🇹🇷", "name": "Türkçe"},
}


def has_permission(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.name.upper() in ALLOWED_ROLES for r in member.roles)


class RoomLangView(discord.ui.View):
    def __init__(self, channel_id: str, current_langs: list, enabled: bool):
        super().__init__(timeout=120)
        self.channel_id = str(channel_id)
        self.selected_langs = set(current_langs)
        self.enabled = enabled
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for code, info in ALL_ROOM_LANGS.items():
            is_sel = code in self.selected_langs
            btn = discord.ui.Button(
                label=f"{info['flag']} {info['name']}",
                style=discord.ButtonStyle.success if is_sel else discord.ButtonStyle.secondary,
                emoji="✅" if is_sel else "❌",
                custom_id=f"rl_{code}"
            )
            btn.callback = self._make_callback(code)
            self.add_item(btn)

        toggle_label = "🔔 An" if not self.enabled else "🔕 Aus"
        toggle_btn = discord.ui.Button(
            label=toggle_label,
            style=discord.ButtonStyle.primary if not self.enabled else discord.ButtonStyle.danger,
            custom_id="rl_toggle",
            row=1
        )
        toggle_btn.callback = self._toggle_callback
        self.add_item(toggle_btn)

        save_btn = discord.ui.Button(
            label="✅ Speichern",
            style=discord.ButtonStyle.success,
            custom_id="rl_save",
            row=1
        )
        save_btn.callback = self._save_callback
        self.add_item(save_btn)

        del_btn = discord.ui.Button(
            label="🗑️ Reset",
            style=discord.ButtonStyle.danger,
            custom_id="rl_reset",
            row=1
        )
        del_btn.callback = self._reset_callback
        self.add_item(del_btn)

    def _make_callback(self, code: str):
        async def callback(interaction: discord.Interaction):
            if code in self.selected_langs:
                self.selected_langs.discard(code)
            else:
                self.selected_langs.add(code)
            self._build_buttons()
            await interaction.response.edit_message(view=self)
        return callback

    async def _toggle_callback(self, interaction: discord.Interaction):
        self.enabled = not self.enabled
        self._build_buttons()
        await interaction.response.edit_message(view=self)

    async def _save_callback(self, interaction: discord.Interaction):
        set_room_langs(self.channel_id, list(self.selected_langs), self.enabled)
        await interaction.response.send_message("✅ Raumsprachen gespeichert!", ephemeral=True)

    async def _reset_callback(self, interaction: discord.Interaction):
        delete_room_langs(self.channel_id)
        await interaction.response.send_message("🗑️ Raumsprachen zurückgesetzt (nutzt jetzt globale Einstellung).", ephemeral=True)


class RaumsprachenCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            init_db()
        except Exception as e:
            log.error(f"Raumsprachen DB Init: {e}")

    @commands.command(name="raumsprachen", aliases=["raumsprache", "roomlang", "roomlangs"])
    async def raumsprachen(self, ctx, channel_id: str = None):
        if not has_permission(ctx.author):
            await ctx.send("❌ Keine Berechtigung. Nur R5 / Dev.", ephemeral=True)
            return

        if channel_id:
            try:
                cid = int(channel_id.replace("<#", "").replace(">", ""))
            except ValueError:
                await ctx.send("❌ Ungültige Kanal-ID.")
                return
        else:
            cid = ctx.channel.id

        ch = self.bot.get_channel(cid)
        ch_name = ch.mention if ch else f"#{cid}"

        settings = get_room_langs(cid)
        current_langs = set(settings["langs"]) if settings else set()
        enabled = settings["enabled"] if settings else True
        active_langs = set(get_active_langs())

        # Altes Raumsprachen-Embed löschen (edit-first statt spam)
        try:
            async for msg in ctx.channel.history(limit=20):
                if msg.author == ctx.guild.me and msg.embeds:
                    if "Raumsprachen" in (msg.embeds[0].title or ""):
                        await msg.delete()
        except Exception:
            pass

        embed = discord.Embed(
            title=f"🌐 Raumsprachen • {ch_name}",
            color=0x5865F2
        )
        embed.add_field(name="Status", value="🔔 Aktiv" if enabled else "🔕 Deaktiviert", inline=True)
        embed.add_field(
            name="Sprachen",
            value=", ".join([f"{ALL_ROOM_LANGS[c]['flag']} {ALL_ROOM_LANGS[c]['name']}" for c in current_langs if c in ALL_ROOM_LANGS]) or "Keine (nutzt globale)",
            inline=True
        )

        view = RoomLangView(cid, current_langs, enabled)
        await ctx.send(embed=embed, view=view)

    @commands.command(name="raumsprachenhelp", aliases=["raumsprachenhilfe", "raumsprachenraide", "roomlanghelp"])
    async def raumsprachen_help(self, ctx):
        embed = discord.Embed(title="🌐 Raumsprachen – Hilfe", color=0x5865F2)
        embed.add_field(
            name="Befehle",
            value=(
                "`!raumsprachen` – Aktueller Raum konfigurieren\n"
                "`!raumsprachen <Kanal-ID>` – Bestimmten Raum konfigurieren\n"
                "## Buttons:\n"
                "- Sprache klicken: ein/aus\n"
                "- 🔔/🔕: Übersetzung für diesen Raum an/aus\n"
                "- ✅ Speichern: übernehmen\n"
                "- 🗑️ Reset: auf global zurücksetzen"
            ),
            inline=False
        )
        embed.add_field(name="Berechtigung", value="Nur R5 / Dev", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(RaumsprachenCog(bot))
