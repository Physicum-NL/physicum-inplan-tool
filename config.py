# Introductie Inplan Tool — Configuratie

import os

# Trainin API
# Business domain (company dashboard, auth API, Slack links)
TRAININ_BASE = "https://physicum-company.trainin.app"
# Public client API still lives on the old domain
TRAININ_PUBLIC_BASE = "https://physicum.trainin.app"
TRAININ_API_PUBLIC = f"{TRAININ_PUBLIC_BASE}/api/v2/AR7DJ/client"

# De 3 introductie-listings in Trainin
LISTINGS = [
    {"hid": "L93X95", "id": 49005, "name": "1e Introductie personal training", "step": 1},
    {"hid": "LBVJN9", "id": 49003, "name": "2e Introductie personal training", "step": 2},
    {"hid": "LNVLKO", "id": 49004, "name": "3e Introductie personal training", "step": 3},
]

# Via Via Cadeau listing in Trainin
VIAVIA_LISTING = {"hid": "L2YKN", "id": 7117, "name": "Via via cadeau personal training"}

# Telefonisch Kennismaken listing in Trainin
KENNISMAKEN_LISTING = {"hid": "LNV3K9", "id": 51876, "name": "Telefonisch kennismaken"}

# Locatie
DEFAULT_LOCATION_ID = 908  # Physicum, Emmaplein 2 (legacy numeric)
DEFAULT_LOCATION_PID = "NDLBZ"  # Physicum, Emmaplein 2 (new PID)

# Listing numeric ID → activity PID mapping (new platform uses PIDs)
LISTING_ID_TO_PID = {
    49005: "L93X95",   # 1e Introductie personal training
    49003: "LBVJN9",   # 2e Introductie personal training
    49004: "LNVLKO",   # 3e Introductie personal training
    7117:  "L2YKN",    # Via via cadeau personal training
    51876: "LNV3K9",   # Telefonisch kennismaken
}

# Inertia version for new Trainin platform (may change on Trainin deploys)
INERTIA_VERSION = "e739c1b93267de9a613d71e6ebf1f008"

# Studio capacity — max simultaneous PT sessions at Emmaplein
STUDIO_CAPACITY = 6

# Online-only trainers — not in studio, don't count toward capacity
ONLINE_ONLY_TRAINERS = {"Rochelle", "Romy Schmidt"}

# Prijs (alleen weergave, geen betaling)
INTRO_PRICE = "99"
INTRO_PRICE_LABEL = "\u20ac99"

# Server
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "5001"))
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# Publieke URL van deze tool (voor links in Slack meldingen)
SERVER_URL = os.environ.get("SERVER_URL", "http://128.140.43.68:5001")

# Slack Webhook — MOET via environment variable gezet worden
# Maak aan via: Slack > Apps > Incoming Webhooks
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# Healthchecks.io ping URL — optioneel, voor monitoring van de monitoring
# Maak gratis aan op https://healthchecks.io en plak de ping URL hier
HEALTHCHECK_PING_URL = os.environ.get("HEALTHCHECK_PING_URL", "")

# Communicatie Portaal webhook — meldt aan het portaal als iemand zich heeft ingepland
# Portaal endpoint: POST /api/webhooks/inplan-booking
COMM_PORTAAL_WEBHOOK_URL    = os.environ.get("COMM_PORTAAL_WEBHOOK_URL", "https://physicum-communicatie-portaal.vercel.app/api/webhooks/inplan-booking")
COMM_PORTAAL_WEBHOOK_SECRET = os.environ.get("COMM_PORTAAL_WEBHOOK_SECRET", "")
