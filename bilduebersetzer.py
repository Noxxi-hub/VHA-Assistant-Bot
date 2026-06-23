# ════════════════════════════════════════════════
#  Bild-Übersetzer Cog  •  VHA Alliance
#  Komplett überarbeitet — besserer Prompt
#  Behandelt zweisprachige Screenshots (Spiel-Übersetzer)
#  Discord-Screenshots, Mecha Fire Chats, etc.
# ════════════════════════════════════════════════

import discord
from discord.ext import commands
import aiohttp
import base64
import json
import logging
import time

log = logging.getLogger("VHABot.Bild")

VISION_MODEL = "gemini-2.5-flash-lite"

IMAGE_COOLDOWN = 15.0
user_last_image: dict[int, float] = {}


async def image_to_base64(url: str) -> tuple:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None, None
            data = await resp.read()
            content_type = resp.content_type or "image/png"
            b64 = base64.b64encode(data).decode("utf-8")
            return b64, content_type


async def extract_and_translate(gemini_call_fn, image_b64: str, content_type: str) -> dict | None:
    """
    Liest Text aus dem Bild und übersetzt ihn.
    Speziell optimiert für:
    - Mecha Fire in-game Chat (mit eingebautem Übersetzer → 2 Sprachen pro Nachricht)
    - Discord Screenshots
    - Alliance Info / Events / Profile
    """

    result_str = await gemini_call_fn(
        model=VISION_MODEL,
        temperature=0.1,
        max_tokens=1800,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{content_type};base64,{image_b64}"}
                    },
                    {
                        "type": "text",
                        "text": (
                            "You are analyzing a game chat screenshot from Mecha Fire (mobile strategy game) or Discord.\\n\\n"

                            "STEP 1 — EXTRACT TEXT:\\n"
                            "- If a message appears TWICE (original + game auto-translation below it), keep ONLY the ORIGINAL (first version)\\n"
                            "- Extract each message as a separate line\\n"
                            "- If a player name/username is visible before a message, prefix that line with: @NAME:PlayerName| then the message\\n"
                            "  Example: @NAME:SaintBrewski| They seem to be falling out okay after that hack kick thing.\\n"
                            "  Example: @NAME:Mochisaurus| Hack kick thing?\\n"
                            "- If no player name is visible for a message, write the message directly with NO prefix\\n"
                            "- Do NOT repeat the same sentence twice\\n"
                            "- Ignore UI labels, buttons, timestamps, rank badges\\n\\n"

                            "STEP 2 — TRANSLATE:\\n"
                            "Translate ALL extracted messages into all 5 languages below.\\n"
                            "Keep the @NAME:PlayerName| prefix on each line in every translation — this is critical.\\n"
                            "Keep game terms untranslated: R1/R2/R3/R4/R5, coordinates, server numbers, @mentions, player names.\\n"
                            "Translate naturally like a real player would write.\\n"
                            "You MUST provide all 5 translations. Never leave a field empty or write 'no translation available'.\\n\\n"

                            "OUTPUT FORMAT — reply with VALID JSON ONLY, no markdown, no explanation:\\n"
                            "{\\n"
                            "  \"lang\": \"ISO 639-1 code of the original text (DE/FR/EN/PT/ZH/KO/etc)\",\\n"
                            "  \"de\": \"German translation, one message per line, @NAME:X| prefix where applicable\",\\n"
                            "  \"fr\": \"French translation, one message per line, @NAME:X| prefix where applicable\",\\n"
                            "  \"en\": \"English translation, one message per line, @NAME:X| prefix where applicable\",\\n"
                            "  \"pt\": \"Brazilian Portuguese translation, one message per line, @NAME:X| prefix where applicable\",\\n"
                            "  \"tr\": \"Turkish translation, one message per line, @NAME:X| prefix where applicable\"\\n"
                            "}\\n\\n"
                            "If there is truly no readable text, return {\"lang\": \"?\", \"de\": \"NOTEXT\", \"fr\": \"NOTEXT\", \"en\": \"NOTEXT\", \"pt\": \"NOTEXT\", \"tr\": \"NOTEXT\"}"
                        )
                    }
                ]
            }
        ]
    )

    try:
        clean = result_str.strip()

        # Markdown-Backticks entfernen
        if "```" in clean:
            parts = clean.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{") or part.startswith("["):
                    clean = part
                    break
        clean = clean.strip()

        # Falls Modell ein Array zurückgibt → erstes Element nehmen
        if clean.startswith("["):
            arr = json.loads(clean)
            parsed = arr[0] if (isinstance(arr, list) and arr) else None
            if not parsed:
                return None
        else:
            parsed = json.loads(clean)

        # Kein Text erkannt
        if all(str(parsed.get(k, "")).upper().strip() in ("NOTEXT", "", "[]", "{}") for k in ["de", "fr", "en", "pt", "tr"]):
            return None

        # Sicherstellen dass alle Felder Strings sind (nicht Listen/Dicts)
        for key in ["de", "fr", "en", "pt", "tr", "lang"]:
            val = parsed.get(key, "")
            if isinstance(val, list):
                parsed[key] = "\n".join(str(v) for v in val)
            elif not isinstance(val, str):
                parsed[key] = str(val)

        return parsed

    except Exception as e:
        log.warning(f"JSON-Parse fehlgeschlagen ({e}): {result_str[:300]}")

        # Fallback: Regex-Extraktion
        import re
        parsed = {"lang": "?", "de": "", "fr": "", "pt": "", "en": "", "tr": ""}
        for key in ["lang", "de", "fr", "en", "pt", "tr"]:
            pattern = rf'"{key}"\\s*:\\s*"(.*?)(?<!\\\\)"(?=\\s*[,}}])'
            m = re.search(pattern, result_str, re.DOTALL)
            if m:
                parsed[key] = m.group(1).replace('\\n', '\n').replace('\\"', '"')
        return parsed if any(parsed.get(k) for k in ["de", "fr", "en", "pt", "tr"]) else None


def clean_text(text: str) -> str:
    """
    Bereinigt den übersetzten Text:
    - Wandelt literal \\n in echte Zeilenumbrüche um
    - Konvertiert [NAME: PlayerName] in **PlayerName:** (Discord fett)
    - Entfernt doppelte Zeilen
    """
    if not text:
        return ""

    import re

    # Literal \n (als zwei Zeichen) → echter Zeilenumbruch
    text = text.replace("\\n", "\n")

    # Zeilen aufteilen und bereinigen
    lines = text.split("\n")
    seen = []
    result_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in seen:
            continue
        seen.append(stripped)

        # @NAME:PlayerName| prefix → **PlayerName:** fett
        name_match = re.match(r'^@NAME:([^|]+)\|\s*(.*)', stripped)
        if name_match:
            player_name = name_match.group(1).strip()
            message = name_match.group(2).strip()
            if message:
                result_lines.append(f"**{player_name}:** {message}")
            else:
                result_lines.append(f"**{player_name}:**")
        else:
            result_lines.append(stripped)

    return "\n".join(result_lines)


LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1484252260614537247/"
    "1484253018533662740/Picsart_26-03-18_13-55-24-994.png"
    "?ex=69bd8dd7&is=69bc3c57&hm=de6fea399dd30f97d2a14e1515c9e7f91d81d0d9ea111f13e0757d42eb12a0e5&"
)


class BildUebersetzerCog(commands.Cog):
    def __init__(self, bot, gemini_call_fn):
        self.bot = bot
        self.gemini_call = gemini_call_fn

    @commands.command(name="übersetze", aliases=["uebersetze", "traduire", "traduzir", "ocr", "lese", "lire"])
    async def uebersetze_bild(self, ctx):
        """Liest Text aus einem Bild und übersetzt ihn."""

        # Cooldown
        now = time.time()
        last = user_last_image.get(ctx.author.id, 0)
        if now - last < IMAGE_COOLDOWN:
            remaining = int(IMAGE_COOLDOWN - (now - last))
            await ctx.send(
                f"⏳ Bitte warte noch **{remaining}s**. / Attends encore **{remaining}s**.",
                delete_after=remaining
            )
            return
        user_last_image[ctx.author.id] = now

        # Bilder sammeln — eigene Nachricht + Reply
        image_urls = []

        if ctx.message.attachments:
            for att in ctx.message.attachments:
                if att.content_type and att.content_type.startswith("image"):
                    image_urls.append(att.url)

        if ctx.message.reference:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message):
                for att in ref.attachments:
                    if att.content_type and att.content_type.startswith("image"):
                        image_urls.append(att.url)
                # Auch Embeds mit Bild-URL prüfen (z.B. Discord-Screenshots als Link)
                for embed in ref.embeds:
                    if embed.image and embed.image.url:
                        image_urls.append(embed.image.url)

        if not image_urls:
            embed = discord.Embed(
                title="❓ Kein Bild gefunden / Aucune image",
                description=(
                    "**So verwenden / Comment utiliser:**\n"
                    "1️⃣ Bild hochladen und `!übersetze` dazu tippen\n"
                    "2️⃣ Auf eine Nachricht mit Bild antworten und `!übersetze` tippen\n\n"
                    "💡 Funktioniert mit: Spielscreenshots, Discord-Screenshots, Fotos"
                ),
                color=0xF39C12
            )
            await ctx.send(embed=embed)
            return

        total = len(image_urls)
        thinking = await ctx.send(
            f"🔍 **Analysiere {total} Bild{'er' if total > 1 else ''}...** "
            f"/ **Analyse en cours...**"
        )

        import asyncio as _asyncio

        async def process_single(url: str, index: int) -> discord.Embed | None:
            try:
                image_b64, content_type = await image_to_base64(url)
                if not image_b64:
                    return None

                result = await extract_and_translate(self.gemini_call, image_b64, content_type)
                if not result:
                    return None

                lang = (result.get("lang") or "?").upper().strip()

                title = (
                    f"🖼️ Bild {index}/{total} • Bildübersetzung"
                    if total > 1
                    else "🖼️ Bildübersetzung / Traduction d'image"
                )
                embed = discord.Embed(title=title, color=0x9B59B6)

                # Immer alle 5 Sprachen — Original wird nicht mehr angezeigt
                lang_map = [
                    ("DE", "🇩🇪 Deutsch",     result.get("de", "")),
                    ("FR", "🇫🇷 Français",    result.get("fr", "")),
                    ("EN", "🇬🇧 English",     result.get("en", "")),
                    ("PT", "🇧🇷 Português",   result.get("pt", "")),
                    ("TR", "🇹🇷 Türkçe",      result.get("tr", "")),
                ]

                has_any = False
                for code, label, text in lang_map:
                    cleaned = clean_text(text)
                    if cleaned:
                        has_any = True
                        embed.add_field(name=label, value=cleaned[:1000], inline=False)

                if not has_any:
                    return None

                embed.set_footer(text="VHA Bild-Übersetzer • Mecha Fire", icon_url=LOGO_URL)
                return embed

            except Exception as e:
                log.error(f"Bildübersetzungs-Fehler Bild {index}: {e}")
                return None

        try:
            tasks = [process_single(url, i + 1) for i, url in enumerate(image_urls)]
            results = await _asyncio.gather(*tasks)
            embeds = [r for r in results if r is not None]

            if not embeds:
                embed = discord.Embed(
                    title="🖼️ Kein Text erkannt / Aucun texte détecté",
                    description=(
                        "Im Bild wurde kein lesbarer Text gefunden.\n"
                        "Aucun texte lisible n'a été trouvé dans l'image.\n\n"
                        "💡 Tipp: Bild muss scharf und gut lesbar sein."
                    ),
                    color=0xF39C12
                )
                await thinking.edit(content=None, embed=embed)
                return

            await thinking.edit(content=None, embed=embeds[0])
            for embed in embeds[1:]:
                await ctx.send(embed=embed)

        except Exception as e:
            log.error(f"Bildübersetzungs-Fehler: {type(e).__name__} - {str(e)}")
            embed = discord.Embed(
                title="⚠️ Fehler / Erreur",
                description=(
                    "Bild konnte nicht verarbeitet werden — bitte nochmal versuchen."
                    "Impossible de traiter l'image — réessaie."
                ),
                color=0xED4245
            )
            await thinking.edit(content=None, embed=embed)


async def setup(bot, gemini_call_fn):
    await bot.add_cog(BildUebersetzerCog(bot, gemini_call_fn))
