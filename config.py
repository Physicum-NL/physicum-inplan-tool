# Introductie Inplan Tool — Configuratie

import os

# Trainin API
TRAININ_BASE = "https://physicum.trainin.app"
TRAININ_API_PUBLIC = f"{TRAININ_BASE}/api/v2/AR7DJ/client"
TRAININ_API_BUSINESS = f"{TRAININ_BASE}/api/v2/AR7DJ/business"

# De 3 introductie-listings in Trainin
LISTINGS = [
    {"hid": "L93X95", "id": 49005, "name": "1e Introductie personal training", "step": 1},
    {"hid": "LBVJN9", "id": 49003, "name": "2e Introductie personal training", "step": 2},
    {"hid": "LNVLKO", "id": 49004, "name": "3e Introductie personal training", "step": 3},
]

# Locatie
DEFAULT_LOCATION_ID = 908  # Physicum, Emmaplein 2

# Prijs (alleen weergave, geen betaling)
INTRO_PRICE = "99"
INTRO_PRICE_LABEL = "\u20ac99"

# Server
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "5001"))
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# Iframe-embedding
ALLOWED_FRAME_ANCESTORS = ["https://physicum.nl", "https://www.physicum.nl", "*"]

# Meta (Facebook/Instagram) Pixel — vul je Pixel ID in voor conversie tracking
# Laat leeg om uit te schakelen
META_PIXEL_ID = os.environ.get("META_PIXEL_ID", "")

# Slack Webhook — vul je webhook URL in voor notificaties bij nieuwe aanvragen
# Maak aan via: Slack > Apps > Incoming Webhooks
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
