# ════════════════════════════════════════════════
#  db_helper.py  •  VHA Alliance
#  SQLite-Ersatz für MongoDB — persistente Speicherung
#  Ersetzt alle pymongo/MongoClient Aufrufe
# ════════════════════════════════════════════════

import sqlite3
import json
import os
import logging
from datetime import datetime, timezone

log = logging.getLogger("VHABot.DB")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vhabot.db")


def get_db():
    """Gibt eine SQLite-Verbindung zurück (thread-safe per connection)."""
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    return db


def _row_to_doc(row):
    """Konvertiert eine SQLite Row in ein MongoDB-ähnliches Dict."""
    if row is None:
        return None
    d = dict(row)
    if "_id" in d and d["_id"] is not None:
        d["_id"] = str(d["_id"])
    return d


def _rows_to_list(rows):
    """Konvertiert SQLite Rows in Liste von Dicts."""
    return [dict(r) for r in rows]


def init_db():
    """Initialisiert alle Tabellen. Beim ersten Aufruf erstellt."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sprachen (
            _id TEXT PRIMARY KEY,
            active_json TEXT NOT NULL DEFAULT '["DE","FR"]'
        );

        CREATE TABLE IF NOT EXISTS raumsprachen (
            _id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL UNIQUE,
            langs_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS tsprachen (
            _id TEXT PRIMARY KEY,
            active_json TEXT NOT NULL DEFAULT '["DE","FR"]'
        );

        CREATE TABLE IF NOT EXISTS tsprachen_rooms (
            _id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL UNIQUE,
            langs_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS spieler (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE,
            id TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS spieler_name_idx ON spieler(name);

        CREATE TABLE IF NOT EXISTS logs (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            date TEXT NOT NULL,
            action TEXT NOT NULL,
            user TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS logs_ts_idx ON logs(timestamp);

        CREATE TABLE IF NOT EXISTS koordinaten (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            r INTEGER NOT NULL DEFAULT 75,
            x INTEGER NOT NULL DEFAULT 0,
            y INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS koord_name_idx ON koordinaten(name);

        CREATE TABLE IF NOT EXISTS svs (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            server TEXT NOT NULL,
            name TEXT NOT NULL,
            r INTEGER NOT NULL DEFAULT 75,
            x INTEGER NOT NULL DEFAULT 0,
            y INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS svs_server_idx ON svs(server);
        CREATE INDEX IF NOT EXISTS svs_name_idx ON svs(name);

        CREATE TABLE IF NOT EXISTS server_struktur (
            _id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL DEFAULT '{}'
        );
    """)

    # Import data from JSON files if tables are empty
    _import_if_empty(db, "sprachen", "sprachen.json")
    _import_if_empty(db, "raumsprachen", "raumsprachen.json")
    _import_if_empty(db, "tsprachen", "tsprachen.json")
    _import_if_empty(db, "tsprachen_rooms", "tsprachen_rooms.json")
    _import_if_empty(db, "spieler", "spieler.json")
    _import_if_empty(db, "logs", "logs.json")
    _import_if_empty(db, "koordinaten", "koordinaten.json")
    _import_if_empty(db, "svs", "svs.json")

    db.commit()
    db.close()
    log.info("✅ SQLite DB initialisiert")


def _import_if_empty(db, table, json_file):
    """Importiert JSON-Daten wenn Tabelle leer ist."""
    base = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base, json_file)

    # Prüfen ob Tabelle leer
    cur = db.execute(f"SELECT COUNT(*) FROM {table}")
    if cur.fetchone()[0] > 0:
        return

    if not os.path.exists(json_path):
        log.info(f"Kein {json_file} gefunden — überspringe Import für {table}")
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            data = [data]

        for doc in data:
            if table == "sprachen":
                _id = doc.get("_id", "settings")
                active = json.dumps(doc.get("active", ["DE", "FR"]))
                db.execute("INSERT OR IGNORE INTO sprachen (_id, active_json) VALUES (?, ?)", (_id, active))

            elif table == "raumsprachen":
                ch_id = str(doc.get("channel_id", ""))
                langs = json.dumps(doc.get("langs", []))
                enabled = 1 if doc.get("enabled", True) else 0
                db.execute("INSERT OR IGNORE INTO raumsprachen (channel_id, langs_json, enabled) VALUES (?, ?, ?)",
                           (ch_id, langs, enabled))

            elif table == "tsprachen":
                _id = doc.get("_id", "settings")
                active = json.dumps(doc.get("active", ["DE", "FR"]))
                db.execute("INSERT OR IGNORE INTO tsprachen (_id, active_json) VALUES (?, ?)", (_id, active))

            elif table == "tsprachen_rooms":
                ch_id = str(doc.get("channel_id", ""))
                langs = json.dumps(doc.get("langs", []))
                enabled = 1 if doc.get("enabled", True) else 0
                db.execute("INSERT OR IGNORE INTO tsprachen_rooms (channel_id, langs_json, enabled) VALUES (?, ?, ?)",
                           (ch_id, langs, enabled))

            elif table == "spieler":
                name = doc.get("name", "")
                pid = str(doc.get("id", ""))
                if name and pid:
                    db.execute("INSERT OR IGNORE INTO spieler (name, id) VALUES (?, ?)", (name, pid))

            elif table == "logs":
                ts = doc.get("timestamp", datetime.now(timezone.utc).timestamp())
                date = doc.get("date", datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"))
                action = doc.get("action", "")
                user = doc.get("user", "")
                details = doc.get("details", "")
                db.execute("INSERT INTO logs (timestamp, date, action, user, details) VALUES (?, ?, ?, ?, ?)",
                           (ts, date, action, user, details))

            elif table == "koordinaten":
                name = doc.get("name", "")
                r = doc.get("r", 75)
                x = doc.get("x", 0)
                y = doc.get("y", 0)
                if name:
                    db.execute("INSERT OR IGNORE INTO koordinaten (name, r, x, y) VALUES (?, ?, ?, ?)",
                               (name, r, x, y))

            elif table == "svs":
                server = doc.get("server", "")
                name = doc.get("name", "")
                r = doc.get("r", 75)
                x = doc.get("x", 0)
                y = doc.get("y", 0)
                if server and name:
                    db.execute("INSERT INTO svs (server, name, r, x, y) VALUES (?, ?, ?, ?, ?)",
                               (server, name, r, x, y))

            elif table == "server_struktur":
                _id = doc.get("_id", "export")
                data_str = json.dumps(doc)
                db.execute("INSERT OR REPLACE INTO server_struktur (_id, data_json) VALUES (?, ?)", (_id, data_str))

        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log.info(f"✅ {table}: {count} Einträge importiert")
    except Exception as e:
        log.error(f"Fehler beim Import von {json_file}: {e}")


# ════════════════════════════════════════════════
#  Sprachen-Funktionen (ersetzt MongoDB sprachen/raumsprachen)
# ════════════════════════════════════════════════

def get_active_langs() -> list:
    """Gibt aktive Sprachcodes zurück. Ersetzt MongoDB sprachen collection."""
    db = get_db()
    row = db.execute("SELECT active_json FROM sprachen WHERE _id = 'settings'").fetchone()
    db.close()
    if row:
        return json.loads(row[0])
    return ["DE", "FR"]


def set_active_langs(langs: list):
    """Setzt aktive Sprachen."""
    db = get_db()
    db.execute("INSERT OR REPLACE INTO sprachen (_id, active_json) VALUES ('settings', ?)",
               (json.dumps(langs),))
    db.commit()
    db.close()


def get_room_langs(channel_id: str) -> dict:
    """Gibt Raumeinstellungen zurück. Ersetzt MongoDB raumsprachen collection."""
    db = get_db()
    row = db.execute("SELECT langs_json, enabled FROM raumsprachen WHERE channel_id = ?", (str(channel_id),)).fetchone()
    db.close()
    if row:
        return {"langs": json.loads(row[0]), "enabled": bool(row[1])}
    return None


def set_room_langs(channel_id: str, langs: list, enabled: bool = True):
    """Setzt Raumeinstellungen."""
    db = get_db()
    db.execute("INSERT OR REPLACE INTO raumsprachen (channel_id, langs_json, enabled) VALUES (?, ?, ?)",
               (str(channel_id), json.dumps(langs), 1 if enabled else 0))
    db.commit()
    db.close()


def delete_room_langs(channel_id: str):
    """Löscht Raumeinstellungen."""
    db = get_db()
    db.execute("DELETE FROM raumsprachen WHERE channel_id = ?", (str(channel_id),))
    db.commit()
    db.close()


def get_all_room_langs() -> list:
    """Gibt alle Raumeinstellungen zurück."""
    db = get_db()
    rows = db.execute("SELECT channel_id, langs_json, enabled FROM raumsprachen").fetchall()
    db.close()
    return [{"channel_id": r[0], "langs": json.loads(r[1]), "enabled": bool(r[2])} for r in rows]


# ════════════════════════════════════════════════
#  Spieler-Funktionen
# ════════════════════════════════════════════════

def get_all_spieler() -> list:
    db = get_db()
    rows = db.execute("SELECT name, id FROM spieler ORDER BY name COLLATE NOCASE").fetchall()
    db.close()
    return [{"name": r[0], "id": r[1]} for r in rows]


def find_spieler(name_or_id: str) -> list:
    db = get_db()
    rows = db.execute(
        "SELECT name, id FROM spieler WHERE name LIKE ? OR id = ?",
        (f"%{name_or_id}%", name_or_id)
    ).fetchall()
    db.close()
    return [{"name": r[0], "id": r[1]} for r in rows]


def add_spieler(name: str, spieler_id: str) -> bool:
    db = get_db()
    try:
        db.execute("INSERT INTO spieler (name, id) VALUES (?, ?)", (name, spieler_id))
        db.commit()
        db.close()
        return True
    except sqlite3.IntegrityError:
        db.close()
        return False


def delete_spieler(name: str) -> int:
    db = get_db()
    cur = db.execute("DELETE FROM spieler WHERE name = ? COLLATE NOCASE", (name,))
    db.commit()
    count = cur.rowcount
    db.close()
    return count


def spieler_exists(name: str) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM spieler WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    db.close()
    return row is not None


def spieler_id_exists(spieler_id: str) -> bool:
    db = get_db()
    row = db.execute("SELECT name FROM spieler WHERE id = ?", (spieler_id,)).fetchone()
    db.close()
    return row


# ════════════════════════════════════════════════
#  Log-Funktionen
# ════════════════════════════════════════════════

def add_log(action: str, user: str, details: str):
    db = get_db()
    now = datetime.now(timezone.utc)
    db.execute("INSERT INTO logs (timestamp, date, action, user, details) VALUES (?, ?, ?, ?, ?)",
               (now.timestamp(), now.strftime("%d.%m.%Y %H:%M UTC"), action, user, details))
    # Nur letzten 500 behalten
    count = db.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    if count > 500:
        db.execute("DELETE FROM logs WHERE _id IN (SELECT _id FROM logs ORDER BY timestamp ASC LIMIT ?)",
                   (count - 500,))
    db.commit()
    db.close()


def get_logs(limit: int = 20) -> list:
    db = get_db()
    rows = db.execute("SELECT timestamp, date, action, user, details FROM logs ORDER BY timestamp DESC LIMIT ?",
                      (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def clear_logs():
    db = get_db()
    db.execute("DELETE FROM logs")
    db.commit()
    db.close()


def count_logs() -> int:
    db = get_db()
    row = db.execute("SELECT COUNT(*) FROM logs").fetchone()
    db.close()
    return row[0]


# ════════════════════════════════════════════════
#  Koordinaten-Funktionen
# ════════════════════════════════════════════════

def get_all_koordinaten() -> list:
    db = get_db()
    rows = db.execute("SELECT name, r, x, y FROM koordinaten ORDER BY name COLLATE NOCASE").fetchall()
    db.close()
    return [{"name": r[0], "r": r[1], "x": r[2], "y": r[3]} for r in rows]


def add_koord(name: str, r: int, x: int, y: int) -> bool:
    db = get_db()
    try:
        db.execute("INSERT INTO koordinaten (name, r, x, y) VALUES (?, ?, ?, ?)", (name, r, x, y))
        db.commit()
        db.close()
        return True
    except sqlite3.IntegrityError:
        db.close()
        return False


def delete_koord(name: str) -> int:
    db = get_db()
    cur = db.execute("DELETE FROM koordinaten WHERE name = ? COLLATE NOCASE", (name,))
    db.commit()
    count = cur.rowcount
    db.close()
    return count


def koord_exists(name: str) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM koordinaten WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    db.close()
    return row is not None


# ════════════════════════════════════════════════
#  SVS-Funktionen
# ════════════════════════════════════════════════

def get_all_svs(server: str = None) -> list:
    db = get_db()
    if server:
        rows = db.execute("SELECT _id, server, name, r, x, y FROM svs WHERE server = ? COLLATE NOCASE ORDER BY name",
                          (server,)).fetchall()
    else:
        rows = db.execute("SELECT _id, server, name, r, x, y FROM svs ORDER BY server, name").fetchall()
    db.close()
    return [{"_id": str(r[0]), "server": r[1], "name": r[2], "r": r[3], "x": r[4], "y": r[5]} for r in rows]


def add_svs(server: str, name: str, r: int, x: int, y: int):
    db = get_db()
    db.execute("INSERT INTO svs (server, name, r, x, y) VALUES (?, ?, ?, ?, ?)",
               (server.upper(), name, r, x, y))
    db.commit()
    db.close()


def delete_svs_by_id(svs_id: int) -> int:
    db = get_db()
    cur = db.execute("DELETE FROM svs WHERE _id = ?", (svs_id,))
    db.commit()
    count = cur.rowcount
    db.close()
    return count


def get_distinct_servers() -> list:
    db = get_db()
    rows = db.execute("SELECT DISTINCT server FROM svs ORDER BY server").fetchall()
    db.close()
    return [r[0] for r in rows]


def count_svs() -> int:
    db = get_db()
    row = db.execute("SELECT COUNT(*) FROM svs").fetchone()
    db.close()
    return row[0]


# ════════════════════════════════════════════════
#  Server-Struktur-Funktionen
# ════════════════════════════════════════════════

def save_server_export(data: dict):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO server_struktur (_id, data_json) VALUES ('export', ?)",
               (json.dumps(data, ensure_ascii=False),))
    db.commit()
    db.close()


def get_server_export() -> dict:
    db = get_db()
    row = db.execute("SELECT data_json FROM server_struktur WHERE _id = 'export'").fetchone()
    db.close()
    if row:
        return json.loads(row[0])
    return None
