<h1 align="center">
  🤖 VHA Assistant Bot
</h1>

<p align="center">
  AI-powered Discord bot for the VHA Alliance — translation, image OCR, server management, and more.<br>
  Self-hosted on Linux with Google Gemini AI.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Discord-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord" />
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/AI-Google%20Gemini-4285F4?style=flat&logo=google&logoColor=white" alt="Gemini" />
</p>

---

## 📋 Overview

The VHA Alliance is a multi-language Discord community. This bot provides automatic message translation between DE/FR/FR/EN (and more), AI assistance via Gemini Vision, and powerful server administration tools.

---

## ⚙️ Tech Stack

| Component | Details |
|-----------|---------|
| **AI Model** | Google Gemini 2.5 Flash (primary) |
| **Fallback** | Gemini 2.5 Flash Lite → Gemini 3 Flash Preview |
| **Database** | MongoDB Atlas (`vhabot`) + SQLite (`processed_msgs.db`, `vhabot.db`) |
| **Discord Library** | discord.py |
| **Hosting** | Self-hosted on Linux (systemd) |

---

## 🌍 Translation

### How it works
The bot auto-detects the language of each message and translates into active target languages. Translation is done via Google Gemini — natural and human-like, not word-by-word.

### Fixed languages (always active)
- 🇩🇪 Deutsch
- 🇫🇷 Français
- 🇬🇧 English

### Optional languages (toggle via `!sprachen`)
- 🇧🇷 Português
- 🇯🇵 日本語
- 🇨🇳 中文
- 🇰🇷 한국어
- 🇪🇸 Español
- 🇷🇺 Русский

### Translation rules
- Always **du-form** — never "Sie" or "Vous"
- Terms of endearment translated correctly (süße → chérie/honey, schatz → chéri/darling)
- Game terms are **never** translated: R1–R5, coordinates, player names, @mentions
- Emojis stay unchanged

### Quality assurance
Each translation is automatically checked for:
- Identical text (not translated) → discarded
- Wrong language in field → discarded
- Repetition loops → discarded
- Excessive length → truncated

---

## 📁 File Structure

```
app.py              — Main bot logic, Gemini calls, on_message handler
sprachen.py         — Global language settings (MongoDB)
raumsprachen.py     — Room-specific language settings (MongoDB)
server.py           — Server structure export/import
bilduebersetzer.py  — Screenshot translation (!übersetze)
db_helper.py        — Database helper utilities
spieler.py          — Player management
svs.py              — SVS (Spielerverwaltung) management
koordinaten.py      — Coordinate utilities
log.py              — Logging configuration
requirements.txt    — Python dependencies
```

---

## 🔑 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token from [Discord Developer Portal](https://discord.com/developers/applications) |
| `GEMINI_API_KEY` | ✅ | API key from [Google AI Studio](https://aistudio.google.com/apikey) |
| `MONGODB_URI` | ✅ | MongoDB Atlas connection string |

---

## 💬 Commands

### 🌐 Translation
| Command | Description | Permission |
|---------|-------------|------------|
| `!sprachen` | Toggle target languages | R4, R5, DEV |
| `!raumsprachen [channel-ID]` | Set languages for specific channel | R5, DEV |
| `!kanalid` | List all channel IDs as DM | Everyone |
| `!translate [text]` | Manually translate text | Manage Messages |

### 🤖 AI Assistant
| Command | Description | Permission |
|---------|-------------|------------|
| `!ai [question]` | Ask AI (Gemini with Thinking) | Everyone |

### 🗑️ Channel Management
| Command | Description | Permission |
|---------|-------------|------------|
| `!clean` | Delete all messages (with confirmation) | Bot DEV only |
| `!clean [number]` | Delete specific number of messages | Bot DEV only |

### 🏗️ Server Structure
| Command | Description | Permission |
|---------|-------------|------------|
| `!server export` | Save current server structure to MongoDB | Bot DEV only |
| `!server preview` | Show saved structure | Bot DEV only |
| `!server import` | Recreate structure on new server | Bot DEV only |

### 📊 Status
| Command | Description | Permission |
|---------|-------------|------------|
| `!ping` | Bot status and latency | Everyone |
| `!help` | Show all commands | Everyone |

---

## 🖼️ Image Translation

Use `!übersetze` (or reply to an image) to translate screenshots from Mecha Fire or Discord:
- OCR text detection via Gemini Vision
- Auto-detects duplicate text (original + game translation) — keeps original only
- Detects player names and shows them **bold** before the message
- Translates into 4 languages: DE, FR, EN, PT

---

## 🄾 Model Fallback

```
gemini-2.5-flash          ← primary (best quality)
    ↓ on 503/429
gemini-2.5-flash-lite     ← faster, lighter
    ↓ on 503/429
gemini-3-flash-preview    ← last resort
```

---

## 📦 Installation

```bash
pip install -r requirements.txt
```

### Requirements

```
discord.py
flask
google-genai
pymongo
aiohttp
```

---

## 🚀 Setup & Start

```bash
# 1. Copy environment template
cp .env.example .env
# Edit .env and fill in your tokens

# 2. Start the bot
python app.py
```

For production, run as systemd service for auto-restart and persistence.

---

## ⚠️ Known Limitations

- Discord bulk-delete only works for messages younger than 14 days
- Gemini can be slower under heavy load (Google-side)
- MongoDB Atlas free tier has limited storage (512 MB)
