# Geteilte Konstanten & Laufzeit-Flags zwischen Modulen (vermeidet Circular-Imports in app.py).

# Bot-Logo (geteilt: commands.py, ai_commands.py, app.py)
LOGO_URL = (
    "https://cdn.discordapp.com/attachments/1498221186025259108/"
    "1516400553645834472/Picsart_26-06-16_13-04-08-364.png"
    "?ex=6a328191&is=6a313011&hm=72f5b3e3960a3ad8637eeb59e07cca15bc4ce08d9f506e8b72a61d5297cc9bb7&"
)

# Wird von !translate on/off gesetzt und von on_message() gelesen.
translate_active = True

# !clean ist nur für Noxxi erlaubt
NOXXI_ID = 1464651603654086748