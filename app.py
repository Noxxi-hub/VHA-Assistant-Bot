import discord
from discord.ext import commands
import os
import re
import time
import asyncio
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv

from logger import log
from flask_api import run_flask
import db_helper

# ── Geteilte Konstanten & Laufzeit-Flags (Circular-Import-vermeidend) ──
from state import LOGO_URL, NOXXI_ID
import state

# ── Übersetzungs-/Sprachlogik ──
from translate import (
    detect_language_llm,
    translate_all,
    translate_text,
    LANG_FLAGS,
    LANG_NAMES,
    get_active_languages,
    _get_room_langs_safe,
)

load_dotenv()
print("✅ .env geladen | Gemini Key vorhanden:", bool(os.getenv("GEMINI_API_KEY")))
print("🔄 Initialisiere SQLite DB...")
db_helper.init_db()
print("✅ SQLite DB bereit")

# ────────────────────────────────────────────────
# KONFIGURATION
# ────────────────────────────────────────────────

BOT_LOG_CHANNEL_ID = 1498221186025259108

# ── GLOBALS & DEDUP ──

# ── Persistent Message Dedup (SQLite) ──────────────────
import sqlite3 as _sqlite3
_gmsg_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed_msgs.db")
_gmsg_db = _sqlite3.connect(_gmsg_db_path, check_same_thread=False)
_gmsg_db.execute("CREATE TABLE IF NOT EXISTS processed (msg_id INTEGER PRIMARY KEY)")
_gmsg_db.execute("CREATE INDEX IF NOT EXISTS idx_processed_id ON processed(msg_id)")
_gmsg_db.commit()

# Beim Start: IDs laden + alte Einträge (>24h) aufräumen
_gcutoff = time.time() - 86400
_grows = _gmsg_db.execute(
    "SELECT msg_id FROM processed WHERE msg_id > ? ORDER BY msg_id DESC LIMIT 500",
    (_gcutoff,)
).fetchall()
processed_messages_set: set[int] = {r[0] for r in _grows}
_gmsg_db.execute("DELETE FROM processed WHERE msg_id <= ?", (_gcutoff,))
_gmsg_db.commit()
log.info(f"💾 SQLite Dedup geladen: {len(processed_messages_set)} IDs cached")

# Cooldown pro User & Erstzeitpunkt
user_last_translation: dict[int, float] = {}
TRANSLATION_COOLDOWN = 2.0  # reduziert von 8.0 für Gemini

# ────────────────────────────────────────────────
# BOT SETUP
# ────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    case_insensitive=True
)


bot_ready = False  # Verhindert doppeltes Laden


@bot.event
async def on_ready():
    global bot_ready
    if bot_ready:
        return  # Bereits initialisiert → überspringen
    bot_ready = True
    errors = []

    try:
        await bot.load_extension("koordinaten")
    except Exception as e:
        errors.append(f"❌ koordinaten: {e}")

    try:
        from gemini_core import gemini_call
        from bilduebersetzer import setup as setup_bild
        await setup_bild(bot, gemini_call)
    except Exception as e:
        errors.append(f"❌ bilduebersetzer: {e}")

    try:
        await bot.load_extension("spieler")
    except Exception as e:
        errors.append(f"❌ spieler: {e}")

    try:
        await bot.load_extension("log")
    except Exception as e:
        errors.append(f"❌ log: {e}")

    try:
        await bot.load_extension("raumsprachen")
    except Exception as e:
        errors.append(f"❌ raumsprachen: {e}")

    try:
        await bot.load_extension("sprachen")
    except Exception as e:
        errors.append(f"❌ sprachen: {e}")

    try:
        await bot.load_extension("svs")
    except Exception as e:
        errors.append(f"❌ svs: {e}")

    try:
        await bot.load_extension("server")
    except Exception as e:
        errors.append(f"❌ server: {e}")

    # ── Neue modulare Cogs (ai_commands / commands) ──
    try:
        from ai_commands import setup as setup_ai
        await setup_ai(bot)
    except Exception as e:
        errors.append(f"❌ ai_commands: {e}")

    try:
        from commands import setup as setup_commands
        await setup_commands(bot)
    except Exception as e:
        errors.append(f"❌ commands: {e}")

    log.info(f"→ {bot.user}  •  ONLINE  •  {discord.utils.utcnow():%Y-%m-%d %H:%M UTC}")

    if BOT_LOG_CHANNEL_ID:
        channel = bot.get_channel(BOT_LOG_CHANNEL_ID)
        if channel:
            if errors:
                msg = "⚠️ **Bot gestartet mit Fehlern:**\n" + "\n".join(errors)
            else:
                msg = (
                    "✅ **Bot erfolgreich gestartet!**\n"
                    "🔧 koordinaten.py • geladen\n"
                    "🔧 bilduebersetzer.py • geladen\n"
                    "🔧 spieler.py • geladen\n"
                    "🔧 log.py • geladen\n"
                    "🔧 raumsprachen.py • geladen\n"
                    "🔧 sprachen.py • geladen\n"
                    "🔧 svs.py • geladen\n"
                    "🔧 server.py • geladen\n"
                    "🔧 gemini_core.py • geladen\n"
                    "🔧 translate.py • geladen\n"
                    "🔧 ai_commands.py • geladen\n"
                    "🔧 commands.py • geladen\n"
                    "🔧 flask_api.py • geladen"
                )
            await channel.send(msg)


# ────────────────────────────────────────────────
# AUTOMATISCHE ÜBERSETZUNG
# ────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    # Geteilte Runtime-Flags über state-Modul (nicht app-eigenes Global)
    global processed_messages_set
    translate_active = state.translate_active

    if message.author.bot:
        return

    # Forum-Raum: Haupt-Bot schweigt hier komplett
    FORUM_CHANNEL_ID = 1478065008960077866
    channel_id = message.channel.id
    parent_id = getattr(message.channel, 'parent_id', None)
    if channel_id == FORUM_CHANNEL_ID or parent_id == FORUM_CHANNEL_ID:
        return

    # ── RAUM-SONDERKONFIGURATION: Groq-BOT übersetzt NICHT in diesen Raum ─────────────────────────────
    _GROQ_DISABLED_ROOMS = {
        1535662492364308480: set(),  # Keine Übersetzung für diesen Raum
    }
    if channel_id in _GROQ_DISABLED_ROOMS:
        log.info(f"🚫 Groq Bot Übersetzer deaktiviert für Raum #{getattr(message.channel, 'name', channel_id)}")
        return

    # ── GIF & YOUTUBE SPERRE (nur ignorieren, keine API-Calls) ────────────────
    _SKIP_URL_PATTERN = re.compile(
        r'https?://\S*(?:tenor\.com|giphy\.com|youtube\.com|youtu\.be|youtube-nocookie\.com|yt\.be)\S*',
        re.IGNORECASE
    )
    if (
        any(a.filename.lower().endswith(".gif") or (a.content_type and "gif" in a.content_type.lower())
            for a in message.attachments)
        or _SKIP_URL_PATTERN.search(message.content)
        or message.stickers
    ):
        return
    # ── ENDE GIF & YOUTUBE SPERRE ────────────────────────────────────────────

    # Dedup: RAM-Set (schnell) + SQLite (persistent)
    if message.id in processed_messages_set:
        return
    processed_messages_set.add(message.id)
    try:
        _gmsg_db.execute("INSERT OR IGNORE INTO processed (msg_id) VALUES (?)", (message.id,))
        _gmsg_db.commit()
    except Exception:
        pass

    # Befehle (!...) niemals übersetzen — sofort und direkt skippen
    msg_stripped = message.content.strip()
    if msg_stripped and msg_stripped[0] == "!":
        await bot.process_commands(message)
        return

    if not translate_active:
        return

    content = message.content.strip()

    # Kein Text-Inhalt (nur Anhänge, GIFs, Sticker, Embeds) → skip
    if not content:
        return

    # Zu kurz → skip
    if len(content) < 2:
        return

    # Nur ein Link → skip (inkl. Tenor/Giphy GIFs)
    if re.match(r'^https?://\S+$', content):
        return

    # Tenor / Giphy GIF-Links rausfiltern (auch wenn Text dabei)
    content_cleaned = re.sub(r'https?://\S+', '', content).strip()
    if not content_cleaned or len(content_cleaned) < 2:
        return
    content = content_cleaned

    # Cooldown pro User
    now = time.time()
    last = user_last_translation.get(message.author.id, 0)
    if now - last < TRANSLATION_COOLDOWN:
        return
    user_last_translation[message.author.id] = now

    # Sprache erkennen
    lang = await detect_language_llm(content)
    if lang == "OTHER":
        return

    # Reply-Ziel Sprache prüfen
    reply_target_lang = None
    if message.reference and message.reference.resolved:
        ref = message.reference.resolved
        if isinstance(ref, discord.Message) and not ref.author.bot:
            ref_lang = await detect_language_llm(ref.content.strip())
            if ref_lang not in ("DE", "FR", "PT", "EN", "OTHER"):
                reply_target_lang = ref_lang

    author_name = message.author.display_name

    def make_multi_embed(fields: list, color: int = 0x3498DB) -> discord.Embed:
        embed = discord.Embed(title=f"💬 • {author_name}", color=color)
        for flag, text in fields:
            # Discord Embed Felder max. 1024 Zeichen - aufteilen wenn nötig
            if len(text) <= 1000:
                embed.add_field(name=flag, value=text, inline=False)
            else:
                # Ersten Teil mit Flagge, Rest als Fortsetzung
                chunks = [text[i:i+1000] for i in range(0, len(text), 1000)]
                embed.add_field(name=flag, value=chunks[0], inline=False)
                for chunk in chunks[1:]:
                    embed.add_field(name="↳", value=chunk, inline=False)
        embed.set_footer(text="Noxxi's Übersetzer", icon_url=LOGO_URL)
        return embed

    try:
        fields = []

        # ── Raum-spezifische Sprachen prüfen ──
        # HARDCODE: Raum 1495892175974830213 immer DE+FR+EN — kein MongoDB
        HARDCODED_ROOMS = {
            1495892175974830213: {"DE", "FR", "EN"},
        }
        if message.channel.id in HARDCODED_ROOMS:
            active_langs = HARDCODED_ROOMS[message.channel.id]
        else:
            room_langs = _get_room_langs_safe(message.channel.id, message.guild.id if message.guild else None)
            if room_langs is not None:
                if len(room_langs) == 0:
                    return  # Explizit deaktiviert
                active_langs = set(room_langs)
            else:
                # Keine Raum-Einstellung → globale Einstellungen + FIXED_LANGS als Default
                active_langs = set(get_active_languages())
                FIXED_LANGS = {"DE", "FR"}
                active_langs = active_langs | FIXED_LANGS

        # Haupt-Bot: feste Zielsprachen DE+FR, Rest zuschaltbar
        ALL_LANGS = [
            ("DE", "German",               "🇩🇪 Deutsch"),
            ("FR", "French",               "🇫🇷 Français"),
            ("PT", "Brazilian Portuguese", "🇧🇷 Português"),
            ("EN", "English",              "🇬🇧 English"),
            ("JA", "Japanese",             "🇯🇵 日本語"),
            ("ZH", "Chinese",              "🇨🇳 中文"),
            ("KO", "Korean",               "🇰🇷 한국어"),
            ("ES", "Spanish",              "🇪🇸 Español"),
            ("RU", "Russian",              "🇷🇺 Русский"),
            ("TR", "Turkish",              "🇹🇷 Türkçe"),
        ]

        # Nachrichten in der erkannten Sprache nicht zurückübersetzen
        target_langs = [
            t for t in ALL_LANGS
            if t[0] != lang and t[0] in active_langs
        ]

        # Wenn keine Zielsprachen → skip (Übersetzer-Bot übernimmt)
        if not target_langs:
            return

        # Kontext: letzte 4 Nachrichten aus dem Kanal laden
        context_lines = []
        try:
            async for ctx_msg in message.channel.history(limit=5):
                if ctx_msg.id == message.id:
                    continue
                if ctx_msg.author.bot:
                    continue
                if ctx_msg.content and len(ctx_msg.content.strip()) > 1:
                    context_lines.append(f"{ctx_msg.author.display_name}: {ctx_msg.content.strip()[:150]}")
                if len(context_lines) >= 4:
                    break
            context_lines.reverse()  # Älteste zuerst
        except Exception:
            pass
        context_str = "\n".join(context_lines)

        # Ein einziger API-Call für alle Sprachen → spart 80% der Requests
        translations = await translate_all(content, target_langs, context=context_str)
        for code, lang_name, label in target_langs:
            translation = translations.get(code, "")
            if translation:
                fields.append((label, translation))

        # Reply auf Gast → auch in Gastsprache übersetzen
        if reply_target_lang and reply_target_lang not in active_langs:
            guest_text = await translate_text(content, LANG_NAMES.get(reply_target_lang, reply_target_lang))
            guest_flag = LANG_FLAGS.get(reply_target_lang, "🌐")
            if guest_text and guest_text.lower() != content.lower():
                fields.append((guest_flag, guest_text))

        if fields:
            color = 0x9B59B6 if lang not in ("DE", "FR", "PT", "EN", "JA", "ZH", "KO") else 0x3498DB
            await message.reply(embed=make_multi_embed(fields, color), mention_author=False)

    except Exception as e:
        log.error(f"Übersetzungsfehler: {type(e).__name__} - {str(e)}")
        try:
            await message.add_reaction("⚠️")
        except Exception:
            pass


# ────────────────────────────────────────────────
# START
# ────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True, name="Flask-KeepAlive").start()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN fehlt!")
        exit(1)

    bot.run(token)