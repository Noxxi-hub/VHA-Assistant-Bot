import asyncio
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from logger import log

load_dotenv()

# ────────────────────────────────────────────────
# KONFIGURATION / MODELLE
# ────────────────────────────────────────────────

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

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Semaphore: max. 4 gleichzeitige Gemini-Calls
gemini_semaphore = asyncio.Semaphore(8)

# Globale Rate-Limit-Pause
_gemini_rate_limit_until: float = 0.0

# Token-Zähler für den Tag
token_counter = {"prompt": 0, "completion": 0, "total": 0}


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