import re

from logger import log
from gemini_core import gemini_call, GEMINI_MODEL


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