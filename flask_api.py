import os

from flask import Flask

from logger import log

# ────────────────────────────────────────────────
# FLASK WEB (KeepAlive)
# ────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def home():
    return "VHA Translator • Online"


@app.route("/ping")
def ping():
    return "pong"


def run_flask():
    port = int(os.environ.get("PORT", 10001))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)