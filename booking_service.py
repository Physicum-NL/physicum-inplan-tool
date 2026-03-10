"""Booking Service — Verwerkt introductie-training aanvragen.

Zoekt clients op in Trainin (als ze bestaan) en logt booking-aanvragen
voor handmatige verwerking door staff. Directe API-booking is niet mogelijk
omdat Trainin geen POST endpoints voor booking-creatie biedt via de staff API.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from config import SLACK_WEBHOOK_URL
from trainin_client import TraininClient

logger = logging.getLogger(__name__)

PENDING_LOG = Path(__file__).parent / "pending_bookings.jsonl"

# ─── TraininClient singleton ─────────────────────────────

_client_lock = threading.Lock()
_client = None  # type: Optional[TraininClient]


def get_trainin_client() -> TraininClient:
    """Lazy singleton — alleen geïnitialiseerd bij eerste gebruik."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                logger.info("TraininClient initialiseren...")
                _client = TraininClient()
                _client._ensure_authenticated()
                logger.info("TraininClient geauthenticeerd")
    return _client


def reset_client():
    """Reset client na auth-failure. Volgende call maakt nieuwe aan."""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


# ─── Client zoeken ────────────────────────────────────────

def find_client_by_email(email: str) -> Optional[dict]:
    """Zoek een bestaande client in Trainin op email.

    Returns het client JSON:API object, of None als niet gevonden.
    """
    try:
        api = get_trainin_client()
        data = api.get("/clients", params={"filter[search]": email})
        for c in data.get("data", []):
            if c.get("attributes", {}).get("email", "").lower() == email.lower():
                logger.info("Client gevonden: %s (ID: %s)", email, c["id"])
                return c
    except Exception as e:
        logger.warning("Client zoeken mislukt: %s", e)
    return None


# ─── Booking aanvraag loggen ──────────────────────────────

def log_booking_request(client_data: dict, sessions: list[dict], trainin_client: Optional[dict] = None):
    """Log een booking-aanvraag naar pending_bookings.jsonl.

    Dit bestand kan door staff worden uitgelezen om bookings
    handmatig in Trainin aan te maken.
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "status": "pending",
        "client": client_data,
        "trainin_client_id": trainin_client["id"] if trainin_client else None,
        "trainin_client_name": trainin_client["attributes"]["name"] if trainin_client else None,
        "sessions": sessions,
    }

    try:
        with open(PENDING_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Booking-aanvraag gelogd: %s", client_data.get("email"))
    except Exception as e:
        logger.error("Kon booking niet loggen: %s", e)


# ─── Slack notificatie ─────────────────────────────────────

DAYS_NL = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag']
MONTHS_NL = ['januari', 'februari', 'maart', 'april', 'mei', 'juni',
             'juli', 'augustus', 'september', 'oktober', 'november', 'december']


def send_slack_notification(client_data: dict, sessions: list, trainin_client: Optional[dict] = None):
    """Stuur een Slack melding bij een nieuwe booking-aanvraag."""
    if not SLACK_WEBHOOK_URL:
        return

    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()
    email = client_data.get("email", "")
    phone = client_data.get("phone", "")

    # Bouw sessie-overzicht
    session_lines = []
    for i, s in enumerate(sessions, 1):
        d = datetime.strptime(s["date"], "%Y-%m-%d")
        day_name = DAYS_NL[d.weekday()]
        month_name = MONTHS_NL[d.month - 1]
        session_lines.append(f"  {i}. {day_name} {d.day} {month_name} om {s['start']}")

    client_info = f"Bestaand in Trainin (ID: {trainin_client['id']})" if trainin_client else "Nieuwe klant"

    text = (
        f"*Nieuwe introductie-aanvraag*\n"
        f"*{name}* ({email})\n"
        f"Tel: {phone}\n"
        f"Status: {client_info}\n\n"
        f"*Sessies:*\n" + "\n".join(session_lines)
    )

    try:
        httpx.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
        logger.info("Slack notificatie verstuurd")
    except Exception as e:
        logger.warning("Slack notificatie mislukt: %s", e)


# ─── Hoofdfunctie ─────────────────────────────────────────

def book_intro_sessions(client_data: dict, sessions: list[dict]) -> dict:
    """Verwerk een introductie-training aanvraag.

    1. Zoek client in Trainin (optioneel, voor staff-context)
    2. Log de aanvraag voor handmatige verwerking
    3. Return succes naar de gebruiker

    Args:
        client_data: {"first_name": ..., "last_name": ..., "email": ..., "phone": ...}
        sessions: [{"listing_id": ..., "date": ..., "start": ..., "key": ...}, ...]

    Returns:
        {"success": bool, "message": str, ...}
    """
    email = client_data.get("email", "")
    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()

    # Probeer client op te zoeken in Trainin (niet-blokkerend)
    trainin_client = None
    try:
        if email:
            trainin_client = find_client_by_email(email)
    except Exception as e:
        logger.warning("Client lookup overgeslagen: %s", e)

    # Log de aanvraag + stuur Slack notificatie
    log_booking_request(client_data, sessions, trainin_client)
    send_slack_notification(client_data, sessions, trainin_client)

    # Bouw session-overzicht voor logging
    session_strs = [f"{s['date']} {s['start']}" for s in sessions]
    client_status = f"bestaand (ID: {trainin_client['id']})" if trainin_client else "nieuw"

    logger.info(
        "BOOKING AANVRAAG: %s (%s) — Client: %s — Sessies: %s",
        name, email, client_status, ", ".join(session_strs),
    )

    return {
        "success": True,
        "message": "Je trainingen zijn aangevraagd! We nemen zo snel mogelijk contact met je op ter bevestiging.",
        "client_found": trainin_client is not None,
        "client_id": trainin_client["id"] if trainin_client else None,
        "sessions_logged": len(sessions),
    }
