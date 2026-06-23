# ════════════════════════════════════════════════
#  Sprachen-Cog  •  VHA Alliance
#  Globale Sprachen per Button ein/ausschalten
#  DE + FR immer aktiv
#  PT, EN, JA per Button steuerbar
#  SQLite statt MongoDB
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import logging
from db_helper import get_active_langs, set_active_langs, init_db

log = logging.getLogger("VHABot.Sprachen")

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)

FIXED_LANGS = {"DE", "FR"}

OPTIONAL_LANGS = {
    "PT": {"flag": "🇧🇷", "name": "Português"},
    "EN": {"flag": "🇬🇧", "name": "English"},
    "JA": {"flag": "🇯🇵", "name": "日本語"},
    "TR": {"flag": "🇹🇷", "name": "Türkçe"},
}

ALL_LANGS = {}
ALL_LANGS.update({k: {"flag": "🇩🇪" if k == "DE" else "🇫🇷", "name": "Deutsch" if k == "DE" else "Français"} for k in FIXED_LANGS})
ALL_LANGS.update(OPTIONAL_LANGS)


class SprachenView(discord.ui.View):
    def __init__(self, active_langs: set):
        super().__init__(timeout=120)
        self.active_langs = set(active_langs)
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for code, info in ALL_LANGS.items():
            is_active = code in self.active_langs
            is_fixed = code in FIXED_LANGS
            btn = discord.ui.Button(
                label=f"{info['flag']} {info['name']}",
                style=discord.ButtonStyle.success if is_active else discord.ButtonStyle.secondary,
                emoji="✅" if is_active else "❌",
                custom_id=f"sp_{code}",
                disabled=is_fixed
            )
            if not is_fixed:
                btn.callback = self._make_callback(code)
            self.add_item(btn)

        save_btn = discord.ui.Button(
            label="✅ Speichern",
            style=discord.ButtonStyle.primary,
            custom_id="sp_save",
            row=1
        )
        save_btn.callback = self._save
        self.add_item(save_btn)

    def _make_callback(self, code: str):
        async def callback(interaction: discord.Interaction):
            if code in self.active_langs:
                self.active_langs.discard(code)
            else:
                self.active_langs.add(code)
            self._build_buttons()
            await interaction.response.edit_message(view=self)
        return callback

    async def _save(self, interaction: discord.Interaction):
        set_active_langs(list(self.active_langs))
        await interaction.response.send_message("✅ Sprachen gespeichert!", ephemeral=True)


class SprachenCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            init_db()
        except Exception as e:
            log.error(f"Sprachen DB Init: {e}")

    @commands.group(name="sprachen", aliases=["languages", "langs", "idiomas", "langues"], invoke_without_command=True)
    async def sprachen(self, ctx):
        active = set(get_active_langs())
        active.update(FIXED_LANGS)

        embed = discord.Embed(
            title="🌐 Sprachen • Globale Einstellung",
            color=0x5865F2
        )
        embed.add_field(
            name="Aktive Sprachen",
            value="\n".join([f"{ALL_LANGS[c]['flag']} {ALL_LANGS[c]['name']}" for c in sorted(active) if c in ALL_LANGS]),
            inline=False
        )
        embed.set_footer(text="DE + FR sind immer aktiv • Optional: PT, EN, JA, TR")

        view = SprachenView(active)
        await ctx.send(embed=embed, view=view)

    @sprachen.command(name="help", aliases=["hilfe", "aide"])
    async def sprachen_help(self, ctx):
        embed = discord.Embed(title="🌐 Sprachen – Hilfe", color=0x5865F2)
        embed.add_field(
            name="Befehle",
            value=(
                "`!sprachen` – Globale Sprachen ein/ausschalten\n"
                "`!raumsprachen` – Raumspezifische Sprachen"
            ),
            inline=False
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(SprachenCog(bot))
