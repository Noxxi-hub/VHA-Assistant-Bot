# ════════════════════════════════════════════════
#  server.py  •  VHA Alliance
#  Server-Struktur exportieren & importieren
#  Kanäle + Rollen + Emojis + Einstellungen
#  Nur R5 / Administrator
#  SQLite statt MongoDB
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import logging
import asyncio
import aiohttp
import base64
from db_helper import save_server_export, get_server_export

log = logging.getLogger("VHABot.Server")

NOXXI_ID = 1464651603654086748

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)


def has_permission(member: discord.Member) -> bool:
    return member.id == NOXXI_ID


def channel_type_str(ch) -> str:
    if isinstance(ch, discord.TextChannel): return "text"
    elif isinstance(ch, discord.VoiceChannel): return "voice"
    elif isinstance(ch, discord.ForumChannel): return "forum"
    elif isinstance(ch, discord.StageChannel): return "stage"
    return "text"


async def image_to_base64(url: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return base64.b64encode(data).decode("utf-8")
    except Exception:
        pass
    return None


class ConfirmView(discord.ui.View):
    def __init__(self, author: discord.Member):
        super().__init__(timeout=60)
        self.author = author
        self.confirmed = False

    @discord.ui.button(label="Ja, importieren", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Nur der Aufrufer kann bestaetigen.", ephemeral=True)
            return
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(content="Import laeuft...", embed=None, view=None)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Nur der Aufrufer kann abbrechen.", ephemeral=True)
            return
        self.confirmed = False
        self.stop()
        await interaction.response.edit_message(content="Import abgebrochen.", embed=None, view=None)


class ServerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="server", invoke_without_command=True)
    async def server(self, ctx):
        embed = discord.Embed(title="Server-Backup Befehle", color=0x5865F2)
        embed.add_field(name="Export", value=(
            "`!server export` - Alles (Kanaele + Rollen + Emojis)\n"
            "`!server export kanaele` - Nur Kanaele\n"
            "`!server export rollen` - Nur Rollen\n"
            "`!server export emojis` - Nur Emojis"
        ), inline=False)
        embed.add_field(name="Vorschau", value="`!server preview` - Was ist gespeichert", inline=False)
        embed.add_field(name="Import", value=(
            "`!server import` - Alles importieren\n"
            "`!server import kanaele` - Nur Kanaele\n"
            "`!server import rollen` - Nur Rollen\n"
            "`!server import emojis` - Nur Emojis"
        ), inline=False)
        embed.set_footer(text="Nur R5 / Administrator")
        await ctx.send(embed=embed)

    @server.command(name="export")
    async def server_export(self, ctx, was: str = "alles"):
        if not has_permission(ctx.author):
            await ctx.send("Keine Berechtigung.", delete_after=5)
            return
        was = was.lower()
        msg = await ctx.send(f"Exportiere {was}...")
        try:
            doc = get_server_export() or {}
            doc["guild_name"] = ctx.guild.name
            doc["guild_id"] = str(ctx.guild.id)
            doc["exported_by"] = ctx.author.display_name
            stats = {}

            if was in ("alles", "kanaele", "kanäle"):
                kategorien = []
                for category, channels in ctx.guild.by_category():
                    kat_data = {
                        "name": category.name if category else "Ohne Kategorie",
                        "position": category.position if category else -1,
                        "kanaele": []
                    }
                    for ch in sorted(channels, key=lambda c: c.position):
                        kat_data["kanaele"].append({
                            "name": ch.name,
                            "type": channel_type_str(ch),
                            "position": ch.position,
                            "topic": getattr(ch, "topic", None) or "",
                            "nsfw": getattr(ch, "nsfw", False),
                            "slowmode": getattr(ch, "slowmode_delay", 0),
                        })
                    kategorien.append(kat_data)
                doc["kategorien"] = kategorien
                stats["Kategorien"] = len(kategorien)
                stats["Kanäle"] = sum(len(k["kanaele"]) for k in kategorien)

            if was in ("alles", "rollen"):
                rollen = []
                for role in ctx.guild.roles:
                    if role.is_default():
                        continue
                    rollen.append({
                        "name": role.name,
                        "color": role.color.value,
                        "hoist": role.hoist,
                        "mentionable": role.mentionable,
                        "position": role.position,
                        "permissions": role.permissions.value,
                    })
                rollen.sort(key=lambda r: r["position"])
                doc["rollen"] = rollen
                stats["Rollen"] = len(rollen)

            if was in ("alles", "emojis"):
                await msg.edit(content="Exportiere Emojis (kann dauern)...")
                emojis = []
                for emoji in ctx.guild.emojis:
                    emoji_b64 = await image_to_base64(str(emoji.url))
                    emojis.append({
                        "name": emoji.name,
                        "animated": emoji.animated,
                        "url": str(emoji.url),
                        "data": emoji_b64
                    })
                    await asyncio.sleep(0.2)
                doc["emojis"] = emojis
                stats["Emojis"] = len(emojis)

            if was in ("alles", "einstellungen"):
                guild = ctx.guild
                doc["einstellungen"] = {
                    "name": guild.name,
                    "description": guild.description or "",
                    "verification_level": str(guild.verification_level),
                    "afk_timeout": guild.afk_timeout,
                    "afk_channel": guild.afk_channel.name if guild.afk_channel else None,
                    "icon_url": str(guild.icon.url) if guild.icon else None,
                }

            save_server_export(doc)

            embed = discord.Embed(title="Export abgeschlossen!", color=0x57F287)
            embed.add_field(name="Server", value=ctx.guild.name, inline=False)
            for key, val in stats.items():
                embed.add_field(name=key, value=str(val), inline=True)
            embed.add_field(name="Gespeichert", value="SQLite server_struktur", inline=False)
            embed.set_footer(text=f"Von {ctx.author.display_name}")
            await msg.edit(content=None, embed=embed)
        except Exception as e:
            log.error(f"Export-Fehler: {e}")
            await msg.edit(content=f"Fehler: {e}")

    @server.command(name="preview")
    async def server_preview(self, ctx):
        if not has_permission(ctx.author):
            await ctx.send("Keine Berechtigung.", delete_after=5)
            return
        doc = get_server_export()
        if not doc:
            await ctx.send("Kein Export gefunden. Zuerst !server export ausfuehren.")
            return
        embed = discord.Embed(title=f"Server-Backup: {doc.get('guild_name', '?')}", color=0x3498DB)
        kategorien = doc.get("kategorien", [])
        total_ch = sum(len(k["kanaele"]) for k in kategorien)
        embed.add_field(name="Kanäle", value=f"{len(kategorien)} Kategorien, {total_ch} Kanäle", inline=True)
        rollen = doc.get("rollen", [])
        embed.add_field(name="Rollen", value=f"{len(rollen)} Rollen" if rollen else "Nicht exportiert", inline=True)
        emojis = doc.get("emojis", [])
        embed.add_field(name="Emojis", value=f"{len(emojis)} Emojis" if emojis else "Nicht exportiert", inline=True)
        if rollen:
            rollen_names = ", ".join(r["name"] for r in rollen[-10:])
            embed.add_field(name="Rollen (letzte 10)", value=rollen_names, inline=False)
        if kategorien:
            lines = [f"{kat['name']} ({len(kat['kanaele'])} Kanäle)" for kat in kategorien[:6]]
            if len(kategorien) > 6:
                lines.append(f"... und {len(kategorien)-6} weitere")
            embed.add_field(name="Kategorien", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Exportiert von {doc.get('exported_by', '?')}")
        await ctx.send(embed=embed)

    @server.command(name="import")
    async def server_import(self, ctx, was: str = "alles"):
        if not has_permission(ctx.author):
            await ctx.send("Keine Berechtigung.", delete_after=5)
            return
        doc = get_server_export()
        if not doc:
            await ctx.send("Kein Export gefunden.")
            return
        was = was.lower()
        embed = discord.Embed(title="Import bestaetigen", color=0xF39C12,
            description=f"Wird auf **{ctx.guild.name}** erstellt. Bestehende werden nicht geloescht.")
        embed.add_field(name="Quelle", value=doc.get("guild_name", "?"), inline=True)
        embed.add_field(name="Ziel", value=ctx.guild.name, inline=True)
        view = ConfirmView(ctx.author)
        confirm_msg = await ctx.send(embed=embed, view=view)
        await view.wait()
        if not view.confirmed:
            return
        errors = []
        stats = {}

        if was in ("alles", "rollen"):
            created = 0
            for role_data in doc.get("rollen", []):
                if discord.utils.get(ctx.guild.roles, name=role_data["name"]):
                    continue
                try:
                    await ctx.guild.create_role(
                        name=role_data["name"],
                        color=discord.Color(role_data["color"]),
                        hoist=role_data["hoist"],
                        mentionable=role_data["mentionable"],
                        permissions=discord.Permissions(role_data["permissions"]),
                    )
                    created += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    errors.append(f"Rolle {role_data['name']}: {e}")
            stats["Rollen erstellt"] = created

        if was in ("alles", "kanaele", "kanäle"):
            created_cats = 0
            created_ch = 0
            for kat in sorted(doc.get("kategorien", []), key=lambda k: k.get("position", 0)):
                kat_name = kat["name"]
                if kat_name == "Ohne Kategorie":
                    category_obj = None
                else:
                    category_obj = discord.utils.get(ctx.guild.categories, name=kat_name)
                    if category_obj is None:
                        try:
                            category_obj = await ctx.guild.create_category(name=kat_name, position=kat.get("position", 0))
                            created_cats += 1
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            errors.append(f"Kategorie {kat_name}: {e}")
                            continue
                for ch in sorted(kat.get("kanaele", []), key=lambda c: c.get("position", 0)):
                    if discord.utils.get(ctx.guild.channels, name=ch["name"]):
                        continue
                    try:
                        kwargs = {"name": ch["name"], "category": category_obj, "position": ch.get("position", 0)}
                        ch_type = ch.get("type", "text")
                        if ch_type == "text":
                            if ch.get("topic"): kwargs["topic"] = ch["topic"]
                            if ch.get("nsfw"): kwargs["nsfw"] = ch["nsfw"]
                            if ch.get("slowmode"): kwargs["slowmode_delay"] = ch["slowmode"]
                            await ctx.guild.create_text_channel(**kwargs)
                        elif ch_type == "voice":
                            await ctx.guild.create_voice_channel(**kwargs)
                        elif ch_type == "forum":
                            await ctx.guild.create_forum(**kwargs)
                        elif ch_type == "stage":
                            await ctx.guild.create_stage_channel(**kwargs)
                        created_ch += 1
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        errors.append(f"Kanal {ch['name']}: {e}")
            stats["Kategorien erstellt"] = created_cats
            stats["Kanäle erstellt"] = created_ch

        if was in ("alles", "emojis"):
            created = 0
            for emoji_data in doc.get("emojis", []):
                if discord.utils.get(ctx.guild.emojis, name=emoji_data["name"]):
                    continue
                try:
                    if emoji_data.get("data"):
                        img_bytes = base64.b64decode(emoji_data["data"])
                    else:
                        img_bytes = None
                        async with aiohttp.ClientSession() as session:
                            async with session.get(emoji_data["url"]) as resp:
                                if resp.status == 200:
                                    img_bytes = await resp.read()
                    if img_bytes:
                        await ctx.guild.create_custom_emoji(name=emoji_data["name"], image=img_bytes)
                        created += 1
                        await asyncio.sleep(1.0)
                except Exception as e:
                    errors.append(f"Emoji {emoji_data['name']}: {e}")
            stats["Emojis erstellt"] = created

        embed = discord.Embed(title="Import abgeschlossen!", color=0x57F287 if not errors else 0xF39C12)
        for key, val in stats.items():
            embed.add_field(name=key, value=str(val), inline=True)
        if errors:
            error_text = "\n".join(errors[:8])
            if len(errors) > 8:
                error_text += f"\n... und {len(errors)-8} weitere"
            embed.add_field(name="Fehler", value=error_text, inline=False)
        embed.set_footer(text=f"Von {ctx.author.display_name}")
        await confirm_msg.edit(content=None, embed=embed, view=None)


async def setup(bot):
    await bot.add_cog(ServerCog(bot))
