import discord
from discord.ext import commands
import os
import re
import time
import asyncio
import threading
import logging
from collections import deque
from datetime import datetime, timezone
from flask import Flask
from google import genai
from google.genai import types
from dotenv import load_dotenv
import db_helper

load_dotenv()
print("✅ .env geladen | Gemini Key vorhanden:", bool(os.getenv("GEMINI_API_KEY")))
print("🔄 Initialisiere SQLite DB...")
db_helper.init_db()
print("✅ SQLite DB bereit")

# ────────────────────────────────────────────────
# LOGGING
# ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("gemini_usage.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("VHABot")

# ────────────────────────────────────────────────
# KONFIGURATION
# ────────────────────────────────────────────────

LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1498221186025259108/"
    "1516400553645834472/Picsart_26-06-16_13-04-08-364.png"
    "?ex=6a328191&is=6a313011&hm=72f5b3e3960a3ad8637eeb59e07cca15bc4ce08d9f506e8b72a61d5297cc9bb7&"
)

# ── Modell-Priorität: Primär (günstig) → Notfall (teurer) ──
# 3.1-flash-lite ist NOTFALLSCHALTER nur bei 503 der Primär-Modelle
GEMINI_MODELS = [
    "gemini-2.5-flash",          # Primär
    "gemini-2.5-flash-lite",     # Primär (leichter)
]
GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"  # Notfall (teurer, nur bei 503)

GEMINI_MODEL = GEMINI_MODELS[0]  # für Kompatibilität

# ── Fallback-Tracking ──
fallback_active = False
fallback_since: float = 0.0
_fallback_check_interval = 600  # 10 Minuten
_last_fallback_check: float = 0.0

# ── Modell-Nutzungs-Statistik ──
model_usage: dict[str, int] = {}  # model_name → anzahl_calls

# ── Preis-Tabelle (pro 1M Tokens) ──
MODEL_PRICING = {
    "gemini-2.5-flash":       {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-3.1-flash-lite": {"input": 0.15, "output": 0.60},
}
BOT_LOG_CHANNEL_ID = 1498221186025259108

# ────────────────────────────────────────────────
# GLOBALS & FLASK
# ────────────────────────────────────────────────

app = Flask(__name__)

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

translate_active = True

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Semaphore: max. 4 gleichzeitige Gemini-Calls
gemini_semaphore = asyncio.Semaphore(8)

# Globale Rate-Limit-Pause
_gemini_rate_limit_until: float = 0.0

user_last_translation: dict[int, float] = {}
TRANSLATION_COOLDOWN = 2.0  # reduziert von 8.0 für Gemini

# Token-Zähler für den Tag
token_counter = {"prompt": 0, "completion": 0, "total": 0}


def run_flask():
    port = int(os.environ.get("PORT", 10001))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


@app.route("/")
def home():
    return "VHA Translator • Online"

@app.route("/ping")
def ping():
    return "pong"


# ────────────────────────────────────────────────
# GEMINI ASYNC WRAPPER mit priorisiertem Fallback
# ────────────────────────────────────────────────

async def _try_model(model_name: str, messages: list, temperature: float,
                      max_tokens: int, retries: int = 3) -> str:
    """Versucht ein einzelnes Modell mit Retries. Returns text bei Erfolg, raises bei Fehler."""
    loop = asyncio.get_event_loop()

    system_text = None
    contents = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            system_text = content
        elif role == "user":
            if isinstance(content, str):
                contents.append(types.Content(role="user", parts=[types.Part(text=content)]))
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if item.get("type") == "text":
                        parts.append(types.Part(text=item["text"]))
                    elif item.get("type") == "image_url":
                        url = item["image_url"]["url"]
                        if url.startswith("data:"):
                            header, b64data = url.split(",", 1)
                            mime = header.split(":")[1].split(";")[0]
                            import base64 as _b64
                            raw = _b64.b64decode(b64data)
                            parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=raw)))
                        else:
                            parts.append(types.Part(text=f"[Image URL: {url}]"))
                contents.append(types.Content(role="user", parts=parts))

    use_thinking = "2.5" in model_name
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        system_instruction=system_text,
        thinking_config=types.ThinkingConfig(thinking_budget=0) if use_thinking else None,
    )

    wait = 1
    for attempt in range(retries):
        async with gemini_semaphore:
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda m=model_name, c=contents, cfg=config: gemini_client.models.generate_content(
                        model=m, contents=c, config=cfg,
                    )
                )
                if resp.usage_metadata:
                    total = (resp.usage_metadata.prompt_token_count or 0) + (resp.usage_metadata.candidates_token_count or 0)
                    token_counter["prompt"]     += resp.usage_metadata.prompt_token_count or 0
                    token_counter["completion"] += resp.usage_metadata.candidates_token_count or 0
                    token_counter["total"]      += total
                    log.info(f"Tokens: +{total} (heute gesamt: {token_counter['total']})")

                model_usage[model_name] = model_usage.get(model_name, 0) + 1
                return resp.text.strip()

            except Exception as e:
                err = str(e).lower()
                if "429" in err or "quota" in err or "resource_exhausted" in err or "rate" in err:
                    log.warning(f"⚠️  RATE-LIMIT {model_name} (Versuch {attempt+1}/{retries}) — warte {wait}s...")
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, 10)
                elif "503" in err or "500" in err or "502" in err or "unavailable" in err or "server" in err:
                    raise  # 503 direkt weiter oben behandeln
                else:
                    log.error(f"❌ GEMINI-FEHLER {model_name}: {type(e).__name__}: {e}")
                    raise

    raise Exception(f"{model_name} nach {retries} Versuchen fehlgeschlagen")


async def _check_fallback_reset():
    """Prüft ob Primär-Modelle wieder verfügbar sind (alle 10 Min)."""
    global fallback_active, fallback_since, _last_fallback_check

    now = time.time()
    if not fallback_active:
        return
    if now - _last_fallback_check < _fallback_check_interval:
        return

    _last_fallback_check = now
    log.info("🔍 PRÜFE: Primär-Modelle wieder verfügbar?")

    try:
        test_messages = [
            {"role": "system", "content": "Reply with exactly: OK"},
            {"role": "user", "content": "test"}
        ]
        result = await _try_model("gemini-2.5-flash-lite", test_messages, 0.1, 50, retries=1)
        if result:
            fallback_active = False
            fallback_since = 0.0
            log.info("✅ PRIMÄR-MODELLE WIEDER VERFÜGABAR — zurück zu günstigen Modellen")
    except Exception:
        fallback_since = now
        log.info("⏳ Primär-Modelle immer noch nicht verfügbar — bleibe auf 3.1er")


async def gemini_call(model: str, messages: list, temperature: float = 0.1,
                      max_tokens: int = 500, retries: int = 3) -> str:
    """Priorisiertes Fallback-System: Primär (günstig) → Notfall (teurer) → Fehler."""
    global fallback_active, fallback_since

    # 1. Versuch Primär-Modelle
    last_error = None
    for model_name in GEMINI_MODELS:
        try:
            result = await _try_model(model_name, messages, temperature, max_tokens, retries=2)
            if model_name != GEMINI_MODELS[0]:
                log.info(f"FALLBACK OK → {model_name}")

            if fallback_active:
                await _check_fallback_reset()

            return result
        except Exception as e:
            err = str(e).lower()
            last_error = str(e)
            if "503" in err or "500" in err or "502" in err or "unavailable" in err:
                log.warning(f"⚠️  SERVER-FEHLER {model_name} — 503 erkannt")
            else:
                log.warning(f"⚠️  {model_name} fehlgeschlagen: {type(e).__name__}")

    # 2. Alle Primär-Modelle gescheitert → Fallback auf 3.1-flash-lite
    if not fallback_active:
        fallback_active = True
        fallback_since = time.time()
        log.warning(f"🚨 ALLE PRIMÄR-MODELLE DOWN → NOTFALL: {GEMINI_FALLBACK_MODEL}")

    try:
        result = await _try_model(GEMINI_FALLBACK_MODEL, messages, temperature, max_tokens, retries)
        log.info(f"✅ NOTFALL OK → {GEMINI_FALLBACK_MODEL}")

        await _check_fallback_reset()
        return result
    except Exception as e:
        last_error = str(e)
        log.error(f"❌ AUCH NOTFALL DOWN: {GEMINI_FALLBACK_MODEL}: {last_error}")

        usage_31 = model_usage.get(GEMINI_FALLBACK_MODEL, 0)
        total_usage = sum(model_usage.values()) or 1
        pct_31 = (usage_31 / total_usage) * 100
        if pct_31 > 20 and usage_31 > 10:
            log.error(f"🚨 ALARM: 3.1-Flash-Lite Anteil {pct_31:.0f}% — Google-Problem andauert!")

        raise Exception(f"Alle Gemini-Modelle down (inkl. Notfall). Letzter Fehler: {last_error}")


async def gemini_call_thinking(model: str, messages: list, temperature: float = 0.7,
                               max_tokens: int = 1000, retries: int = 3) -> str:
    """
    Gemini-Call MIT aktiviertem Thinking — nur für !ai verwendet.
    Für Übersetzungen → gemini_call() mit thinking_budget=0 verwenden.
    Enthält Fallback-Kette und Retry bei 503/429/Überlastung.
    """
    loop = asyncio.get_event_loop()

    system_text = None
    contents = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            system_text = content
        elif role == "user":
            if isinstance(content, str):
                contents.append(types.Content(role="user", parts=[types.Part(text=content)]))

    last_error = None
    for model_name in GEMINI_MODELS:
        # Thinking nur bei 2.5-Modellen aktivieren (3.x hat es eingebaut)
        use_thinking = "2.5" in model_name
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_text,
            thinking_config=types.ThinkingConfig(thinking_budget=512) if use_thinking else None,
        )

        wait = 4
        for attempt in range(retries):
            async with gemini_semaphore:
                try:
                    resp = await loop.run_in_executor(
                        None,
                        lambda m=model_name, c=contents, cfg=config: gemini_client.models.generate_content(
                            model=m,
                            contents=c,
                            config=cfg,
                        )
                    )
                    if model_name != GEMINI_MODELS[0]:
                        log.info(f"!ai FALLBACK OK → {model_name}")
                    return resp.text.strip()

                except Exception as e:
                    err = str(e)
                    last_error = err
                    if "429" in err or "quota" in err.lower() or "rate" in err.lower():
                        log.warning(f"!ai {model_name} Rate-Limit (Versuch {attempt+1}/{retries}) – warte {wait}s")
                        await asyncio.sleep(wait)
                        wait = min(wait * 2, 60)
                    elif "503" in err or "500" in err or "502" in err or "unavailable" in err.lower() or "server" in err.lower():
                        log.warning(f"!ai {model_name} überlastet ({err[:60]}), versuche nächstes Modell...")
                        break  # sofort nächstes Modell
                    else:
                        log.error(f"!ai Gemini-Fehler {model_name}: {e}")
                        break

        log.warning(f"!ai Modell {model_name} fehlgeschlagen, fallback...")

    raise Exception(f"Alle Gemini-Modelle down. Letzter Fehler: {last_error}")


# ────────────────────────────────────────────────
# SPRACHE ERKENNEN — regelbasiert (kein API-Call)
# ────────────────────────────────────────────────

# Cache (auch für LLM-Fallback)
lang_cache: dict[str, str] = {}

# Neutrale Wörter die keine Spracherkennung auslösen sollen
_NEUTRAL = {
    "ok","okay","lol","gg","wp","xd","haha","hahaha","😂","👍","👋","gn","gm",
    "afk","brb","thx","ty","np","omg","wtf","irl","imo","btw","fyi","asap",
}

def _script_detect(text: str) -> str | None:
    """Erkennt Sprache anhand von Unicode-Blöcken — kein API-Call nötig."""
    cjk    = sum(1 for c in text if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf")
    hira   = sum(1 for c in text if "\u3040" <= c <= "\u309f")
    kata   = sum(1 for c in text if "\u30a0" <= c <= "\u30ff")
    hangul = sum(1 for c in text if "\uac00" <= c <= "\ud7a3")
    arabic = sum(1 for c in text if "\u0600" <= c <= "\u06ff")
    cyril  = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    total  = max(len(text), 1)

    if (hira + kata) / total > 0.15:  return "JA"
    if hangul / total > 0.15:         return "KO"
    if cjk / total > 0.15:            return "ZH"
    if arabic / total > 0.15:         return "AR"
    if cyril / total > 0.15:          return "RU"
    return None  # Lateinische Schrift → LLM nötig

# Nur für lateinische Texte deren Sprache unklar ist
async def detect_language_llm(text: str) -> str:
    """Lokale Erkennung – kein LLM Call, optimiert für kurze DE/FR Sätze."""
    stripped = text.strip()
    if not stripped or len(stripped) < 2:
        return "OTHER"
    
    # Neutral / Emojis → ignorieren
    if stripped.lower() in _NEUTRAL:
        return "OTHER"

    # 1. Script-Erkennung für nicht-lateinische Sprachen
    script = _script_detect(stripped)
    if script:
        return script

    t = f" {stripped.lower()} "  # Padding für Wortgrenzen

    # 2. Kurze Texte (<20 Zeichen): harte Heuristik für DE/FR
    # Das fixt "Ne bin da", "Was sagst du nicht", etc.
    if len(stripped) < 20:
        de_markers = [' ich ', ' bin ', ' da ', ' ne ', ' ja ', ' nein ', ' was ', ' du ', ' nicht ', ' mal ', ' hab ', ' habe ', ' ist ', ' ein ', ' der ', ' die ', ' das ', ' und ']
        fr_markers = [' je ', ' suis ', ' pas ', ' oui ', ' non ', ' tu ', ' vous ', ' est ', ' le ', ' la ', ' et ', ' pour ', ' quoi ']
        
        # Zähle Treffer
        de_hits = sum(1 for w in de_markers if w in t)
        fr_hits = sum(1 for w in fr_markers if w in t)
        
        if de_hits > 0 and de_hits >= fr_hits:
            return "DE"
        if fr_hits > 0 and fr_hits > de_hits:
            return "FR"
        # Wenn nichts passt, aber Text sieht deutsch aus (Umlaute)
        # NICHT return — als Fallback merken, erst nach langdetect verwenden
        has_umlaut = any(c in stripped for c in 'äöüßÄÖÜ')

    else:
        has_umlaut = False

    # 3. Normale Heuristik für längere Texte
    if any(w in t for w in [' der ', ' die ', ' das ', ' und ', ' ich ', ' nicht ', ' ist ', ' ein ', ' zu ']):
        return "DE"
    if any(w in t for w in [' le ', ' la ', ' les ', ' et ', ' vous ', ' je ', ' suis ', ' pas ']):
        return "FR"
    if any(w in t for w in [' o ', ' que ', ' para ', ' com ', ' você ', ' voce ', ' não ', ' nao ']):
        return "PT"
    if any(w in t for w in [' el ', ' la ', ' y ', ' que ', ' para ', ' con ']):
        return "ES"
    if any(w in t for w in [' the ', ' and ', ' you ', ' is ', ' are ', ' i am ', ' not ']):
        return "EN"
    if any(w in t for w in [' merhaba ', ' teşekkür ', ' evet ', ' hayır ', ' nasılsın ', ' naber ', ' selam ', ' sağol ']):
        return "TR"

    # 4. Fallback: Gemini API zur sicheren Spracherkennung nutzen
    try:
        result = await gemini_call(
            model=GEMINI_MODEL,
            temperature=0.0,
            max_tokens=5,
            messages=[
                {"role": "system", "content": "Detect the language of the text. Reply with ONLY the 2-letter code: DE, FR, EN, PT, ES, RU, JA, ZH, KO, TR, or OTHER. Nothing else."},
                {"role": "user", "content": stripped[:200]}
            ]
        )
        detected = result.strip().upper()[:2]
        if detected in {"DE", "FR", "EN", "PT", "ES", "RU", "JA", "ZH", "KO", "TR"}:
            return detected
    except Exception:
        pass

    # 5. Letzter Fallback: Umlaut-Heuristik (nur wenn nichts anderes gefunden)
    if has_umlaut:
        return "DE"

    return "OTHER"


# ────────────────────────────────────────────────
# ÜBERSETZEN — ALLE SPRACHEN IN EINEM CALL
# ────────────────────────────────────────────────

async def translate_all(text: str, target_langs: list, context: str = "") -> dict:
    """
    Übersetzt text in ALLE Zielsprachen in einem einzigen API-Call.
    context: Die letzten paar Nachrichten aus dem Kanal als Gesprächskontext.
    Gibt dict zurück: {code: übersetzter_text}
    """
    if not target_langs:
        return {}

    codes_str  = ", ".join(f"{code}={lang_name}" for code, lang_name, _ in target_langs)
    codes      = [code for code, _, _ in target_langs]
    json_keys  = ", ".join(f'"{code}": "..."' for code in codes)

    # Token-Limit dynamisch: ~1.5 Tokens/Zeichen x Anzahl Sprachen, mind. 1500, max. 6000
    estimated = max(1500, min(6000, int(len(text) * 1.5 * len(target_langs))))

    try:
        result = await gemini_call(
            model=GEMINI_MODEL,
            temperature=0.1,
            max_tokens=estimated,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Du bist ein intelligenter Übersetzer für eine internationale Gaming-Community (Discord).\n"
                        f"Übersetze den Text in diese {len(codes)} Sprachen: {codes_str}.\n\n"
                        + (f"GESPRÄCHSKONTEXT (letzte Nachrichten im Kanal — NUR zum Verstehen, NICHT übersetzen):\n{context}\n\n" if context else "")
                        + f"DEINE MISSION:\n"
                        f"1. ANALYSE: Erkenne den Tonfall — ist es ein privates/liebevolles Gespräch oder geht es um Spiel/Allianz-Organisation? Übersetze entsprechend.\n"
                        f"2. NATÜRLICHKEIT: Übersetze den SINN. Klinge wie ein Muttersprachler im Chat, nicht wie ein Lexikon.\n"
                        f"3. TON: Wenn ein Satz witzig, frech, emotional oder liebevoll ist, übersetze ihn genauso — nicht steif.\n"
                        f"4. DU-FORM: Verwende IMMER 'Du' (Deutsch), 'Tu/Toi' (Französisch) — niemals 'Sie' oder 'Vous'.\n"
                        f"5. KOSENAMEN: 'schatz'→chéri/chérie (FR), honey/darling (EN); 'süße/süßer'→ma chérie/mon chéri (FR), sweetie (EN).\n"
                        f"5b. Diese Kosenamen NIE übersetzen: baby, babe, bby — bleiben in allen Sprachen gleich.\n"
                        f"6. NO-GO: Spielernamen, @mentions, R1/R2/R3/R4/R5, Koordinaten, Allianz-Namen NIEMALS übersetzen.\n"
                        f"7. Emojis bleiben exakt unverändert.\n"
                        f"8. Jedes Sprachfeld MUSS in der richtigen Zielsprache sein — DE=Deutsch, FR=Französisch, EN=Englisch, PT=Portugiesisch, TR=Türkisch.\n"
                        f"9. WICHTIG: Alle {len(codes)} Sprachfelder MÜSSEN befüllt sein — auch bei sehr kurzen Sätzen.\n"
                        f"10. Antworte NUR mit diesem JSON, kein Markdown, kein Extra-Text:\n"
                        f"{{{json_keys}}}"
                    )
                },
                {"role": "user", "content": text}
            ]
        )

        import json as _json
        clean = result.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()

        parsed = _json.loads(clean)
        translations = {}
        max_len = max(len(text) * 6, 500)
        original_words = set(re.sub(r'[^\w\s]', '', text.lower()).split())

        # Englische Marker-Wörter — wenn diese in DE/FR/PT/ES auftauchen ist es falsch
        en_markers = {"the","is","are","she","he","they","needs","want","yeah","most","of","all",
                      "need","sleep","and","but","for","with","that","this","have","has","was","were"}

        for code in codes:
            val = parsed.get(code, "").strip()
            if not val:
                continue

            # Identisch mit Original
            if val.lower() == text.lower():
                log.warning(f"Übersetzung identisch mit Original ({code}) — verworfen")
                continue

            # Zu ähnlich zum Original — nur bei 5+ Wörtern und nicht für EN
            # EN-Nachrichten enthalten oft englische Wörter aus dem Original (Namen, Begriffe)
            if code != "EN" and len(original_words) >= 5:
                val_words = set(re.sub(r'[^\w\s]', '', val.lower()).split())
                if len(original_words) > 0:
                    overlap = len(original_words & val_words) / len(original_words)
                    if overlap > 0.80:
                        log.warning(f"Übersetzung zu ähnlich ({code}): {overlap:.0%} — verworfen")
                        continue

            # Englischen Text in nicht-englischen Feldern erkennen
            if code in ("DE", "FR", "PT", "ES", "RU", "JA", "ZH", "KO", "TR"):
                val_word_set = set(val.lower().split())
                en_hits = len(val_word_set & en_markers)
                total_words = len(val_word_set)
                if total_words > 0 and en_hits / total_words > 0.35:
                    log.warning(f"Englischer Text im {code}-Feld erkannt ({en_hits}/{total_words} EN-Wörter) — verworfen")
                    continue

            # Loop-Erkennung
            words = val.split()
            if words:
                most_common = max(set(words), key=words.count)
                if words.count(most_common) > 15:
                    log.warning(f"Loop erkannt ({code}): '{most_common}' x{words.count(most_common)} — verworfen")
                    continue

            # Längen-Check
            if len(val) > max_len:
                val = val[:max_len]

            translations[code] = val
        return translations

    except Exception as e:
        log.error(f"Übersetzungsfehler (multi): {e}")
        return {}


async def translate_text(text: str, target_lang_name: str) -> str:
    """Einzelübersetzung — nur noch für Reply-Gast-Sprachen verwendet."""
    try:
        return await gemini_call(
            model=GEMINI_MODEL,
            temperature=0.1,
            max_tokens=600,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a professional translator. Translate the text into {target_lang_name} accurately. Keep the exact meaning and tone. Never translate: player names, @mentions, R1-R5, coordinates. Keep emojis as-is. Output ONLY the translation, nothing else."
                    )
                },
                {"role": "user", "content": text}
            ]
        )
    except Exception as e:
        log.error(f"Übersetzungsfehler ({target_lang_name}): {e}")
        return ""


# ────────────────────────────────────────────────
# FLAGGEN & SPRACHNAMEN
# ────────────────────────────────────────────────
# Import einmalig beim Start — nicht bei jedem on_message neu
def get_active_languages() -> set:
    try:
        from sprachen import get_active_langs
        return get_active_langs()
    except Exception:
        return {"DE", "FR"}  # Fallback Haupt-Bot


# Einmalig beim Modulstart importieren — nicht bei jeder Nachricht
try:
    from sprachen import get_active_langs as _sprachen_get_active
    from raumsprachen import get_room_langs as _raumsprachen_get_room
    def get_active_languages() -> set:
        try:
            return _sprachen_get_active()
        except Exception:
            return {"DE", "FR"}
    def _get_room_langs_safe(channel_id: int, guild_id: int = None):
        try:
            result = _raumsprachen_get_room(channel_id)
            if result is None:
                return None
            if not result.get("enabled", True):
                return []
            return result.get("langs", [])
        except Exception as e:
            log.info(f"[raum] error for {channel_id}: {e}")
            return None
except Exception:
    def get_active_languages() -> set:
        return {"DE", "FR"}
    def _get_room_langs_safe(channel_id: int):
        return None

LANG_FLAGS = {
    "DE": "🇩🇪", "FR": "🇫🇷", "PT": "🇧🇷", "EN": "🇬🇧",
    "JA": "🇯🇵", "ES": "🇪🇸", "IT": "🇮🇹", "RU": "🇷🇺",
    "ZH": "🇨🇳", "AR": "🇸🇦", "KO": "🇰🇷", "TR": "🇹🇷",
    "PL": "🇵🇱", "NL": "🇳🇱",
}

LANG_NAMES = {
    "DE": "German", "FR": "French", "PT": "Brazilian Portuguese",
    "EN": "English", "JA": "Japanese", "ES": "Spanish",
    "IT": "Italian", "RU": "Russian", "ZH": "Chinese",
    "AR": "Arabic", "KO": "Korean", "TR": "Turkish",
    "PL": "Polish", "NL": "Dutch",
}

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
                    "🔧 server.py • geladen"
                )
            await channel.send(msg)

# ────────────────────────────────────────────────
# BEFEHLE
# ────────────────────────────────────────────────

@bot.command(name="help")
async def cmd_help(ctx):
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


@bot.command(name="ping")
async def cmd_ping(ctx):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        color=0x57F287 if latency < 200 else 0xF39C12
    )
    embed.add_field(name="📡 Latenz / Latence", value=f"`{latency}ms`", inline=True)
    embed.add_field(name="📊 Tokens heute / Today", value=f"`{token_counter['total']}`", inline=True)
    embed.set_footer(text="VHA Bot • Online", icon_url=LOGO_URL)
    await ctx.send(embed=embed)


@bot.command(name="translate")
@commands.has_permissions(manage_messages=True)
async def cmd_translate(ctx, action: str = None):
    global translate_active

    if action is None:
        await ctx.send(
            "❓ Benutzung: `!translate on` / `!translate off` / `!translate status`\n"
            "Usage: `!translate on` / `!translate off` / `!translate status`"
        )
        return

    action = action.lower()

    if action == "on":
        translate_active = True
        embed = discord.Embed(title="VHA System • Übersetzung", color=0x57F287)
        embed.add_field(name="Deutsch ↔ Français ↔ Português", value="Aktiviert / Activée / Ativada", inline=False)
        await ctx.send(embed=embed)

    elif action == "off":
        translate_active = False
        embed = discord.Embed(title="VHA System • Übersetzung", color=0xED4245)
        embed.add_field(name="Deutsch ↔ Français ↔ Português", value="Deaktiviert / Désactivée / Desativada", inline=False)
        await ctx.send(embed=embed)

    elif action == "status":
        if translate_active:
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


@cmd_translate.error
async def translate_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Du hast keine Berechtigung dafür. / Tu n'as pas la permission.")


@bot.command(name="ai")
@commands.cooldown(1, 12, commands.BucketType.user)
async def cmd_ai(ctx, *, question: str = None):
    if not question or not question.strip():
        await ctx.send("Beispiel: `!ai Qui est la VHA ?`  oder  `!ai Was ist die VHA?`")
        return

    thinking = await ctx.send("**Denke nach …** 🧠")

    lang = await detect_language_llm(question)
    flag = LANG_FLAGS.get(lang, "🌐")
    footer = f"Antwort in {lang}"

    system_prompt = (
        "Du bist ein freundlicher VHA-Alliance Assistent. "
        "Antworte IMMER in derselben Sprache wie die Frage. "
        "Natürlich und direkt."
    )
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question.strip()}]

    try:
        answer = await gemini_call_thinking(
            model=GEMINI_MODEL,
            temperature=0.7,
            max_tokens=1000,
            messages=messages
        )
        color = 0x5865F2
    except Exception as e:
        answer = f"Fehler: {str(e)}"
        color = 0xFF0000
        footer = "Fehler"

    embed = discord.Embed(title=f"VHA KI • Antwort {flag}", description=answer, color=color)
    embed.set_author(name="VHA ALLIANCE", icon_url=LOGO_URL)
    embed.add_field(name="→ Deine Frage", value=question[:900], inline=False)
    embed.set_footer(text=f"VHA • Gemini • {GEMINI_MODEL} • {footer}", icon_url=LOGO_URL)
    await thinking.edit(embed=embed)


@bot.command(name="aipm")
@commands.cooldown(1, 12, commands.BucketType.user)
async def cmd_aipm(ctx, *, question: str = None):
    """Wie !ai — Antwort wird nur per DM an den Fragesteller geschickt."""
    if not question or not question.strip():
        await ctx.send("Beispiel: `!aipm Qui est la VHA ?`  oder  `!aipm Was ist die VHA?`", delete_after=10)
        return

    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass

    confirm = await ctx.send(f"📬 {ctx.author.mention} Ich schicke dir die Antwort per DM!")

    lang = await detect_language_llm(question)
    flag = LANG_FLAGS.get(lang, "🌐")
    footer = f"Antwort in {lang}"

    system_prompt = (
        "Du bist ein freundlicher VHA-Alliance Assistent. "
        "Antworte IMMER in derselben Sprache wie die Frage. "
        "Natürlich und direkt."
    )
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question.strip()}]

    try:
        answer = await gemini_call_thinking(
            model=GEMINI_MODEL,
            temperature=0.7,
            max_tokens=1000,
            messages=messages
        )
        color = 0x5865F2
    except Exception as e:
        answer = f"Fehler: {str(e)}"
        color = 0xFF0000
        footer = "Fehler"

    embed = discord.Embed(title=f"VHA KI • Antwort {flag}", description=answer, color=color)
    embed.set_author(name="VHA ALLIANCE", icon_url=LOGO_URL)
    embed.add_field(name="→ Deine Frage", value=question[:900], inline=False)
    embed.set_footer(text=f"VHA • Gemini • {GEMINI_MODEL} • {footer} • Privat", icon_url=LOGO_URL)

    try:
        await ctx.author.send(embed=embed)
        try:
            await confirm.delete()
        except discord.NotFound:
            pass
    except discord.Forbidden:
        # User hat DMs deaktiviert → Bestätigung löschen, Fehlermeldung zeigen
        try:
            await confirm.delete()
        except discord.NotFound:
            pass
        await ctx.send(
            f"❌ {ctx.author.mention} Ich konnte dir keine DM schicken. "
            "Bitte aktiviere DMs von Servermitgliedern in deinen Discord-Einstellungen.",
            delete_after=15
        )


@cmd_aipm.error
async def aipm_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(
            f"⏳ {ctx.author.mention} Bitte warte noch {error.retry_after:.0f}s.",
            delete_after=5
        )


# ────────────────────────────────────────────────
# KANAL-IDs ANZEIGEN
# ────────────────────────────────────────────────

@bot.command(name="kanalid", aliases=["channelid", "kanalids"])
async def cmd_kanalid(ctx):
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


# ────────────────────────────────────────────────
# KANAL LEEREN
# ────────────────────────────────────────────────

NOXXI_ID = 1464651603654086748

@bot.command(name="clean", aliases=["clear", "purge", "löschen"])
async def cmd_clean(ctx, *args):
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
            reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=check)
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


@cmd_clean.error
async def clean_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ Ungültige Eingabe.\n"
            "Beispiele: `!clean` · `!clean 50` · `!clean 1234567890` · `!clean 1234567890 50`",
            delete_after=8
        )


# ────────────────────────────────────────────────
# AUTOMATISCHE ÜBERSETZUNG
# ────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    global processed_messages, processed_messages_set, translate_active

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
