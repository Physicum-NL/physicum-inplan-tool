"""Booking Service — Verwerkt introductie-training aanvragen.

Flow:
1. Zoek client in Trainin op e-mailadres
2. Als de client niet bestaat → maak automatisch aan via staff API
3. Maak sessies aan in Trainin (met client + trainer gekoppeld)
4. Log de aanvraag
5. Stuur Slack melding met deep links naar Trainin

Sessies worden aangemaakt via POST /business/sessions. Dit blokkeert
het tijdslot in de agenda en koppelt de client + trainer direct.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from config import SLACK_WEBHOOK_URL, TRAININ_BASE, DEFAULT_LOCATION_ID
from trainin_client import TraininClient

logger = logging.getLogger(__name__)

PENDING_LOG = Path(__file__).parent / "pending_bookings.jsonl"
MAX_LOG_SIZE_MB = 10  # Roteer log als het groter wordt dan 10 MB


def _rotate_log_if_needed():
    """Roteer pending_bookings.jsonl als het te groot wordt."""
    try:
        if PENDING_LOG.exists() and PENDING_LOG.stat().st_size > MAX_LOG_SIZE_MB * 1024 * 1024:
            rotated = PENDING_LOG.with_suffix(f".{datetime.now().strftime('%Y%m%d')}.jsonl")
            os.replace(str(PENDING_LOG), str(rotated))
            logger.info("Log geroteerd naar: %s", rotated.name)
    except Exception as e:
        logger.warning("Log rotatie mislukt: %s", e)


# ─── Input validatie ──────────────────────────────────────

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def validate_session_data(session_data: dict) -> Optional[str]:
    """Valideer session data. Returns foutmelding of None als alles ok is."""
    date = session_data.get("date", "")
    start = session_data.get("start", "")

    if not DATE_RE.match(date):
        return f"Ongeldige datum format: {date!r} (verwacht YYYY-MM-DD)"
    if not TIME_RE.match(start):
        return f"Ongeldige tijd format: {start!r} (verwacht HH:MM)"

    # Check dat datum in de toekomst is
    try:
        session_date = datetime.strptime(date, "%Y-%m-%d").date()
        if session_date < datetime.now().date():
            return f"Datum {date} ligt in het verleden"
    except ValueError:
        return f"Ongeldige datum: {date}"

    return None


# ─── Dubbele boeking preventie ───────────────────────────

_recent_bookings = {}  # email -> timestamp
_recent_lock = threading.Lock()
DUPLICATE_WINDOW_SECONDS = 60  # Blokkeer dubbele boekingen binnen 60 seconden


def is_duplicate_booking(email: str) -> bool:
    """Check of dit een dubbele boeking is (zelfde email binnen 60 sec)."""
    now = datetime.now().timestamp()
    with _recent_lock:
        last = _recent_bookings.get(email)
        if last and (now - last) < DUPLICATE_WINDOW_SECONDS:
            return True
        _recent_bookings[email] = now
        # Cleanup oude entries (ouder dan 5 min)
        cutoff = now - 300
        expired = [k for k, v in _recent_bookings.items() if v < cutoff]
        for k in expired:
            del _recent_bookings[k]
        return False


# ─── TraininClient singleton ─────────────────────────────

_client_lock = threading.Lock()
_client = None  # type: Optional[TraininClient]


def get_trainin_client() -> TraininClient:
    """Lazy singleton — alleen geïnitialiseerd bij eerste gebruik.

    Belangrijk: zet _client pas na succesvolle authenticatie.
    Anders wordt een kapotte client gecached en falen alle volgende calls.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                logger.info("TraininClient initialiseren...")
                try:
                    client = TraininClient()
                    client._ensure_authenticated()
                    _client = client  # Pas toewijzen NA succesvolle auth
                    logger.info("TraininClient geauthenticeerd")
                except Exception as e:
                    logger.error("TraininClient authenticatie mislukt: %s", e)
                    raise  # Propageer zodat callers weten dat auth faalde
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


# ─── Client aanmaken ─────────────────────────────────────

def create_client_in_trainin(client_data: dict) -> Optional[dict]:
    """Maak een nieuwe client aan in Trainin via de staff API.

    Endpoint: POST /business/clients/new/invite
    Dit is het endpoint dat het Trainin staff dashboard zelf gebruikt.

    Args:
        client_data: {"first_name": ..., "last_name": ..., "email": ..., "phone": ...}

    Returns:
        JSON:API client object met id en attributes, of None bij fout.
    """
    try:
        api = get_trainin_client()

        payload = {
            "client": {
                "first_name": client_data.get("first_name", ""),
                "last_name": client_data.get("last_name", ""),
                "email": client_data.get("email", ""),
                "phone": client_data.get("phone", ""),
                "phone_ice": "",
                "address": "",
                "postal_code": "",
                "city": "",
                "has_company": False,
                "company_name": "",
                "company_contact_name": "",
                "company_vat_no": "",
                "company_address": "",
                "company_postal_code": "",
                "company_city": "",
                "company_order_email": "",
                "needs_registration": True,
                "create_user": True,
                "can_notify": True,
                "location": None,
                "child": {"first_name": "", "last_name": ""},
            },
            "has_product": False,
            "product": {
                "quantity": 1,
                "has_payment": True,
                "payment_option": "send",
                "payment_request_method": "link",
                "payment_method": None,
                "add_registration_product": True,
                "has_remarks": False,
                "orders": [],
            },
        }

        headers = api._headers()
        headers["Content-Type"] = "application/json"

        resp = api._http.post(
            f"{api.api_base}/clients/new/invite",
            headers=headers,
            json=payload,
            timeout=30,
        )

        if resp.status_code == 201:
            data = resp.json()
            client = data.get("data", {})
            client_id = client.get("id")
            client_hid = client.get("attributes", {}).get("hid", "")
            logger.info(
                "Client aangemaakt in Trainin: %s %s (ID: %s, HID: %s)",
                client_data.get("first_name"), client_data.get("last_name"),
                client_id, client_hid,
            )
            return client
        else:
            body = resp.text[:300]
            logger.warning(
                "Client aanmaken mislukt (%d): %s", resp.status_code, body
            )
            return None

    except Exception as e:
        logger.warning("Client aanmaken mislukt: %s", e)
        return None


# ─── Sessie aanmaken in Trainin ───────────────────────────

def create_session_in_trainin(
    session_data: dict,
    client_id: Optional[str] = None,
) -> Optional[dict]:
    """Maak een sessie aan in Trainin via de staff API.

    Endpoint: POST /business/sessions
    Maakt een sessie in de agenda, koppelt de client (booking) en trainer.

    Args:
        session_data: {"listing_id": ..., "date": ..., "start": ..., "end": ..., "instructor_id": ...}
        client_id: Trainin client ID om direct als booking te koppelen.

    Returns:
        {"session_id": ..., "booking_id": ..., "status": "created"} of None bij fout.
    """
    try:
        api = get_trainin_client()

        # Bouw volledige datetime strings: "2026-04-10 14:30:00"
        date = session_data["date"]
        start_time = session_data["start"]
        end_time = session_data.get("end", "")

        # Start datetime
        start_dt = f"{date} {start_time}:00" if len(start_time) == 5 else f"{date} {start_time}"

        # End datetime: als er een eindtijd is, gebruik die; anders +60 min
        if end_time:
            end_dt = f"{date} {end_time}:00" if len(end_time) == 5 else f"{date} {end_time}"
        else:
            # Fallback: 60 minuten na start
            from datetime import timedelta
            start_obj = datetime.strptime(start_dt, "%Y-%m-%d %H:%M:%S")
            end_obj = start_obj + timedelta(minutes=60)
            end_dt = end_obj.strftime("%Y-%m-%d %H:%M:%S")

        # Use location from the slot key if available (format: "{location_id}_{instructor_id}")
        location_id = DEFAULT_LOCATION_ID
        key = session_data.get("key", "")
        if key and "_" in key:
            try:
                location_id = int(key.split("_")[0])
            except ValueError:
                pass

        payload = {
            "listing": session_data["listing_id"],
            "location": location_id,
            "start": start_dt,
            "end": end_dt,
        }

        # Koppel client als booking
        if client_id:
            payload["client"] = int(client_id)

        # Koppel instructor
        instructor_id = session_data.get("instructor_id")
        if instructor_id:
            payload["instructor"] = int(instructor_id)

        headers = api._headers()
        headers["Content-Type"] = "application/json"

        logger.info(
            "Sessie aanmaken: listing=%s, %s %s-%s, client=%s, instructor=%s",
            session_data["listing_id"], date, start_time, end_time,
            client_id, instructor_id,
        )

        resp = api._http.post(
            f"{api.api_base}/sessions",
            headers=headers,
            json=payload,
            timeout=30,
        )

        if resp.status_code == 201:
            data = resp.json()
            session = data.get("data", {})
            session_id = session.get("id")

            # Zoek booking ID in included data
            booking_id = None
            for inc in data.get("included", []):
                if inc.get("type") == "bookings":
                    booking_id = inc.get("id")
                    break

            logger.info(
                "Sessie aangemaakt: ID %s, booking ID %s, status %s",
                session_id, booking_id,
                session.get("attributes", {}).get("status", "?"),
            )

            return {
                "session_id": session_id,
                "booking_id": booking_id,
                "status": "created",
                "trainin_status": session.get("attributes", {}).get("status"),
            }
        else:
            body = resp.text[:500]
            logger.warning(
                "Sessie aanmaken mislukt (%d): %s", resp.status_code, body
            )
            return None

    except Exception as e:
        logger.warning("Sessie aanmaken mislukt: %s", e, exc_info=True)
        return None


# ─── Booking aanvraag loggen ──────────────────────────────

def log_booking_request(
    client_data: dict,
    sessions: list[dict],
    trainin_client: Optional[dict] = None,
    session_results: Optional[list] = None,
):
    """Log een booking-aanvraag naar pending_bookings.jsonl."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "status": "booked" if session_results else "pending",
        "client": client_data,
        "trainin_client_id": trainin_client["id"] if trainin_client else None,
        "trainin_client_name": trainin_client.get("attributes", {}).get("name") if trainin_client else None,
        "sessions": sessions,
        "session_results": session_results,
    }

    try:
        _rotate_log_if_needed()
        with open(PENDING_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Booking-aanvraag gelogd: %s", client_data.get("email"))
    except Exception as e:
        logger.error("Kon booking niet loggen: %s", e)


# ─── Slack notificatie ─────────────────────────────────────

DAYS_NL = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag']
MONTHS_NL = ['januari', 'februari', 'maart', 'april', 'mei', 'juni',
             'juli', 'augustus', 'september', 'oktober', 'november', 'december']

LISTING_NAMES = {
    49005: "1e Introductie PT",
    49003: "2e Introductie PT",
    49004: "3e Introductie PT",
    7117: "Via Via Cadeau PT",
    51876: "Telefonisch kennismaken",
}


def send_slack_notification(
    client_data: dict,
    sessions: list,
    trainin_client: Optional[dict] = None,
    client_created: bool = False,
    session_results: Optional[list] = None,
):
    """Stuur een Slack Block Kit melding met deep links naar Trainin."""
    if not SLACK_WEBHOOK_URL:
        return

    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()
    email = client_data.get("email", "")
    phone = client_data.get("phone", "")

    # Bepaal client status en link
    if trainin_client:
        client_hid = trainin_client.get("attributes", {}).get("hid", "")
        client_id = trainin_client.get("id", "")

        if client_created:
            status_emoji = ":white_check_mark:"
            status_text = "Nieuw aangemaakt in Trainin"
        else:
            status_emoji = ":bust_in_silhouette:"
            status_text = f"Bestaande klant (ID: {client_id})"

        client_link = f"{TRAININ_BASE}/business/clients/{client_hid}"
    else:
        status_emoji = ":warning:"
        status_text = "Niet aangemaakt (handmatig toevoegen)"
        client_link = f"{TRAININ_BASE}/business/clients/new"
        client_hid = ""

    # Bepaal booking status
    sessions_created = 0
    sessions_failed = 0
    if session_results:
        for r in session_results:
            if r and r.get("status") == "created":
                sessions_created += 1
            else:
                sessions_failed += 1

    if sessions_created == len(sessions):
        booking_emoji = ":white_check_mark:"
        booking_status = f"Alle {sessions_created} sessies ingepland in agenda"
    elif sessions_created > 0:
        booking_emoji = ":warning:"
        booking_status = f"{sessions_created}/{len(sessions)} sessies ingepland ({sessions_failed} mislukt)"
    else:
        booking_emoji = ":x:"
        booking_status = "Sessies niet ingepland (handmatig toevoegen)"

    # Bouw sessie-overzicht
    session_lines = []
    for i, s in enumerate(sessions):
        d = datetime.strptime(s["date"], "%Y-%m-%d")
        day_name = DAYS_NL[d.weekday()]
        month_name = MONTHS_NL[d.month - 1]
        listing_name = LISTING_NAMES.get(s.get("listing_id"), f"Sessie {i + 1}")
        instructor = s.get("instructor", "")
        instructor_str = f" ({instructor})" if instructor else ""

        # Status per sessie
        result = session_results[i] if session_results and i < len(session_results) else None
        if result and result.get("status") == "created":
            line_emoji = ":white_check_mark:"
        else:
            line_emoji = ":x:"

        session_lines.append(
            f"    {line_emoji} *{listing_name}*: {day_name} {d.day} {month_name} om {s['start']}{instructor_str}"
        )

    # Slack Block Kit payload
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":calendar: Nieuwe introductie-boeking",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Naam:*\n<{client_link}|{name}>"},
                {"type": "mrkdwn", "text": f"*Klant:*\n{status_emoji} {status_text}"},
                {"type": "mrkdwn", "text": f"*E-mail:*\n{email}"},
                {"type": "mrkdwn", "text": f"*Telefoon:*\n{phone}"},
            ]
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Sessies:* {booking_emoji} {booking_status}\n" + "\n".join(session_lines),
            }
        },
        {"type": "divider"},
    ]

    # Actie-knoppen
    actions = []
    if trainin_client:
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":bust_in_silhouette: Open klant", "emoji": True},
            "url": client_link,
            "style": "primary",
        })
    else:
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":heavy_plus_sign: Maak klant aan", "emoji": True},
            "url": f"{TRAININ_BASE}/business/clients/new",
            "style": "primary",
        })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":calendar: Open agenda", "emoji": True},
        "url": f"{TRAININ_BASE}/business/calendar",
    })

    blocks.append({
        "type": "actions",
        "elements": actions,
    })

    # Context met timestamp
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Geboekt op {datetime.now().strftime('%d %b %Y om %H:%M')} via Introductie Inplan Tool",
            }
        ],
    })

    # Verstuur
    fallback_text = (
        f"Nieuwe introductie-boeking: {name} ({email})\n"
        f"Klant: {status_text}\n"
        f"Sessies: {booking_status}\n" + "\n".join(session_lines)
    )

    try:
        httpx.post(
            SLACK_WEBHOOK_URL,
            json={"text": fallback_text, "blocks": blocks},
            timeout=10,
        )
        logger.info("Slack notificatie verstuurd")
    except Exception as e:
        logger.warning("Slack notificatie mislukt: %s", e)


# ─── Hoofdfunctie ─────────────────────────────────────────

def book_intro_sessions(client_data: dict, sessions: list[dict]) -> dict:
    """Verwerk een introductie-training aanvraag — volledig geautomatiseerd.

    Flow:
    1. Zoek client in Trainin op e-mail
    2. Als niet gevonden → maak client automatisch aan
    3. Maak sessies aan in Trainin (met client + trainer)
    4. Log de aanvraag
    5. Stuur Slack melding
    6. Return resultaat naar de gebruiker

    Args:
        client_data: {"first_name": ..., "last_name": ..., "email": ..., "phone": ...}
        sessions: [{"listing_id": ..., "date": ..., "start": ..., "end": ..., "instructor_id": ..., "key": ...}, ...]

    Returns:
        {"success": bool, "message": str, ...}
    """
    email = client_data.get("email", "")
    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()

    # ── Stap 0: Valideer input ──
    for i, s in enumerate(sessions):
        error = validate_session_data(s)
        if error:
            logger.warning("Sessie %d validatie mislukt: %s", i + 1, error)
            return {"success": False, "message": f"Ongeldige sessie data: {error}"}

    # ── Stap 1: Zoek client in Trainin ──
    trainin_client = None
    client_created = False

    try:
        if email:
            trainin_client = find_client_by_email(email)
    except Exception as e:
        logger.warning("Client lookup overgeslagen: %s", e)

    # ── Stap 2: Als niet gevonden, maak client aan ──
    if trainin_client is None and email:
        logger.info("Client niet gevonden, aanmaken in Trainin: %s", email)
        trainin_client = create_client_in_trainin(client_data)
        if trainin_client:
            client_created = True
            logger.info("Client succesvol aangemaakt: ID %s", trainin_client.get("id"))
        else:
            logger.warning("Client aanmaken mislukt voor: %s", email)

    # ── Stap 3: Maak sessies aan in Trainin ──
    client_id = trainin_client.get("id") if trainin_client else None
    session_results = []

    for i, session_data in enumerate(sessions):
        logger.info("Sessie %d/%d aanmaken...", i + 1, len(sessions))
        result = create_session_in_trainin(session_data, client_id=client_id)
        session_results.append(result)

    sessions_created = sum(1 for r in session_results if r and r.get("status") == "created")

    # ── Stap 4: Log de aanvraag ──
    log_booking_request(client_data, sessions, trainin_client, session_results)

    # ── Stap 5: Stuur Slack notificatie ──
    send_slack_notification(
        client_data, sessions, trainin_client, client_created, session_results
    )

    # ── Log samenvatting ──
    session_strs = [f"{s['date']} {s['start']}" for s in sessions]
    if trainin_client:
        client_status = f"aangemaakt (ID: {trainin_client['id']})" if client_created else f"bestaand (ID: {trainin_client['id']})"
    else:
        client_status = "niet aangemaakt (fout)"

    logger.info(
        "BOOKING COMPLEET: %s (%s) — Client: %s — Sessies: %d/%d ingepland — %s",
        name, email, client_status, sessions_created, len(sessions),
        ", ".join(session_strs),
    )

    # ── Return resultaat ──
    if sessions_created == len(sessions):
        message = "Je trainingen zijn ingepland! Je ontvangt een bevestiging per e-mail."
    elif sessions_created > 0:
        message = f"{sessions_created} van {len(sessions)} trainingen zijn ingepland. We nemen contact op over de overige."
    else:
        message = "Je trainingen zijn aangevraagd! We nemen zo snel mogelijk contact met je op ter bevestiging."

    return {
        "success": True,
        "message": message,
        "client_found": trainin_client is not None,
        "client_created": client_created,
        "client_id": client_id,
        "sessions_created": sessions_created,
        "sessions_total": len(sessions),
    }


# ─── Via Via Cadeau booking ────────────────────────────────

def book_viavia_sessions(client_data: dict, sessions: list[dict]) -> dict:
    """Verwerk een Via Via Cadeau training aanvraag — volledig geautomatiseerd.

    Identieke flow als book_intro_sessions, maar voor 2 sessies
    en met Via Via Cadeau labeling.

    Args:
        client_data: {"first_name": ..., "last_name": ..., "email": ..., "phone": ...}
        sessions: [{"listing_id": ..., "date": ..., "start": ..., "end": ..., "instructor_id": ..., "key": ...}, ...]

    Returns:
        {"success": bool, "message": str, ...}
    """
    email = client_data.get("email", "")
    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()

    # ── Stap 0: Valideer input ──
    for i, s in enumerate(sessions):
        error = validate_session_data(s)
        if error:
            logger.warning("Via Via sessie %d validatie mislukt: %s", i + 1, error)
            return {"success": False, "message": f"Ongeldige sessie data: {error}"}

    # ── Stap 1: Zoek client in Trainin ──
    trainin_client = None
    client_created = False

    try:
        if email:
            trainin_client = find_client_by_email(email)
    except Exception as e:
        logger.warning("Client lookup overgeslagen: %s", e)

    # ── Stap 2: Als niet gevonden, maak client aan ──
    if trainin_client is None and email:
        logger.info("Client niet gevonden, aanmaken in Trainin: %s", email)
        trainin_client = create_client_in_trainin(client_data)
        if trainin_client:
            client_created = True
            logger.info("Client succesvol aangemaakt: ID %s", trainin_client.get("id"))
        else:
            logger.warning("Client aanmaken mislukt voor: %s", email)

    # ── Stap 3: Maak sessies aan in Trainin ──
    client_id = trainin_client.get("id") if trainin_client else None
    session_results = []

    for i, session_data in enumerate(sessions):
        logger.info("Via Via sessie %d/%d aanmaken...", i + 1, len(sessions))
        result = create_session_in_trainin(session_data, client_id=client_id)
        session_results.append(result)

    sessions_created = sum(1 for r in session_results if r and r.get("status") == "created")

    # ── Stap 4: Log de aanvraag ──
    log_booking_request(client_data, sessions, trainin_client, session_results)

    # ── Stap 5: Stuur Slack notificatie ──
    send_viavia_slack_notification(
        client_data, sessions, trainin_client, client_created, session_results
    )

    # ── Log samenvatting ──
    session_strs = [f"{s['date']} {s['start']}" for s in sessions]
    if trainin_client:
        client_status = f"aangemaakt (ID: {trainin_client['id']})" if client_created else f"bestaand (ID: {trainin_client['id']})"
    else:
        client_status = "niet aangemaakt (fout)"

    logger.info(
        "VIA VIA BOOKING COMPLEET: %s (%s) — Client: %s — Sessies: %d/%d ingepland — %s",
        name, email, client_status, sessions_created, len(sessions),
        ", ".join(session_strs),
    )

    # ── Return resultaat ──
    if sessions_created == len(sessions):
        message = "Je gratis trainingen zijn ingepland! Je ontvangt een bevestiging per e-mail."
    elif sessions_created > 0:
        message = f"{sessions_created} van {len(sessions)} trainingen zijn ingepland. We nemen contact op over de overige."
    else:
        message = "Je trainingen zijn aangevraagd! We nemen zo snel mogelijk contact met je op ter bevestiging."

    return {
        "success": True,
        "message": message,
        "client_found": trainin_client is not None,
        "client_created": client_created,
        "client_id": client_id,
        "sessions_created": sessions_created,
        "sessions_total": len(sessions),
    }


def send_viavia_slack_notification(
    client_data: dict,
    sessions: list,
    trainin_client: Optional[dict] = None,
    client_created: bool = False,
    session_results: Optional[list] = None,
):
    """Stuur een Slack Block Kit melding voor Via Via Cadeau boeking."""
    if not SLACK_WEBHOOK_URL:
        return

    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()
    email = client_data.get("email", "")
    phone = client_data.get("phone", "")

    # Bepaal client status en link
    if trainin_client:
        client_hid = trainin_client.get("attributes", {}).get("hid", "")
        client_id = trainin_client.get("id", "")

        if client_created:
            status_emoji = ":white_check_mark:"
            status_text = "Nieuw aangemaakt in Trainin"
        else:
            status_emoji = ":bust_in_silhouette:"
            status_text = f"Bestaande klant (ID: {client_id})"

        client_link = f"{TRAININ_BASE}/business/clients/{client_hid}"
    else:
        status_emoji = ":warning:"
        status_text = "Niet aangemaakt (handmatig toevoegen)"
        client_link = f"{TRAININ_BASE}/business/clients/new"
        client_hid = ""

    # Bepaal booking status
    sessions_created = 0
    sessions_failed = 0
    if session_results:
        for r in session_results:
            if r and r.get("status") == "created":
                sessions_created += 1
            else:
                sessions_failed += 1

    if sessions_created == len(sessions):
        booking_emoji = ":white_check_mark:"
        booking_status = f"Alle {sessions_created} sessies ingepland in agenda"
    elif sessions_created > 0:
        booking_emoji = ":warning:"
        booking_status = f"{sessions_created}/{len(sessions)} sessies ingepland ({sessions_failed} mislukt)"
    else:
        booking_emoji = ":x:"
        booking_status = "Sessies niet ingepland (handmatig toevoegen)"

    # Bouw sessie-overzicht
    session_lines = []
    for i, s in enumerate(sessions):
        d = datetime.strptime(s["date"], "%Y-%m-%d")
        day_name = DAYS_NL[d.weekday()]
        month_name = MONTHS_NL[d.month - 1]
        listing_name = LISTING_NAMES.get(s.get("listing_id"), f"Via Via Cadeau PT {i + 1}")
        instructor = s.get("instructor", "")
        instructor_str = f" ({instructor})" if instructor else ""

        # Status per sessie
        result = session_results[i] if session_results and i < len(session_results) else None
        if result and result.get("status") == "created":
            line_emoji = ":white_check_mark:"
        else:
            line_emoji = ":x:"

        session_lines.append(
            f"    {line_emoji} *{listing_name} ({i + 1}e)*: {day_name} {d.day} {month_name} om {s['start']}{instructor_str}"
        )

    # Slack Block Kit payload
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":gift: Nieuwe Via Via Cadeau boeking",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Naam:*\n<{client_link}|{name}>"},
                {"type": "mrkdwn", "text": f"*Klant:*\n{status_emoji} {status_text}"},
                {"type": "mrkdwn", "text": f"*E-mail:*\n{email}"},
                {"type": "mrkdwn", "text": f"*Telefoon:*\n{phone}"},
            ]
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Sessies:* {booking_emoji} {booking_status}\n" + "\n".join(session_lines),
            }
        },
        {"type": "divider"},
    ]

    # Actie-knoppen
    actions = []
    if trainin_client:
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":bust_in_silhouette: Open klant", "emoji": True},
            "url": client_link,
            "style": "primary",
        })
    else:
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":heavy_plus_sign: Maak klant aan", "emoji": True},
            "url": f"{TRAININ_BASE}/business/clients/new",
            "style": "primary",
        })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":calendar: Open agenda", "emoji": True},
        "url": f"{TRAININ_BASE}/business/calendar",
    })

    blocks.append({
        "type": "actions",
        "elements": actions,
    })

    # Context met timestamp
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Geboekt op {datetime.now().strftime('%d %b %Y om %H:%M')} via Via Via Cadeau Inplan Tool",
            }
        ],
    })

    # Verstuur
    fallback_text = (
        f"Nieuwe Via Via Cadeau boeking: {name} ({email})\n"
        f"Klant: {status_text}\n"
        f"Sessies: {booking_status}\n" + "\n".join(session_lines)
    )

    try:
        httpx.post(
            SLACK_WEBHOOK_URL,
            json={"text": fallback_text, "blocks": blocks},
            timeout=10,
        )
        logger.info("Via Via Slack notificatie verstuurd")
    except Exception as e:
        logger.warning("Via Via Slack notificatie mislukt: %s", e)


# ─── Telefonisch Kennismaken booking ─────────────────────

def book_kennismaken_session(client_data: dict, session_data: dict) -> dict:
    """Verwerk een telefonisch kennismakingsgesprek aanvraag.

    Identieke client-flow als intro/viavia, maar voor 1 sessie.

    Args:
        client_data: {"first_name": ..., "last_name": ..., "email": ..., "phone": ...}
        session_data: {"listing_id": ..., "date": ..., "start": ..., "end": ..., "instructor_id": ..., "key": ...}

    Returns:
        {"success": bool, "message": str, ...}
    """
    email = client_data.get("email", "")
    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()

    # ── Stap 0: Valideer input ──
    error = validate_session_data(session_data)
    if error:
        logger.warning("Kennismaken sessie validatie mislukt: %s", error)
        return {"success": False, "message": f"Ongeldige sessie data: {error}"}

    # ── Stap 1: Zoek client in Trainin ──
    trainin_client = None
    client_created = False

    try:
        if email:
            trainin_client = find_client_by_email(email)
    except Exception as e:
        logger.warning("Client lookup overgeslagen: %s", e)

    # ── Stap 2: Als niet gevonden, maak client aan ──
    if trainin_client is None and email:
        logger.info("Client niet gevonden, aanmaken in Trainin: %s", email)
        trainin_client = create_client_in_trainin(client_data)
        if trainin_client:
            client_created = True
            logger.info("Client succesvol aangemaakt: ID %s", trainin_client.get("id"))
        else:
            logger.warning("Client aanmaken mislukt voor: %s", email)

    # ── Stap 3: Maak sessie aan in Trainin ──
    client_id = trainin_client.get("id") if trainin_client else None
    logger.info("Kennismaken sessie aanmaken...")
    session_result = create_session_in_trainin(session_data, client_id=client_id)
    session_created = session_result is not None and session_result.get("status") == "created"

    # ── Stap 4: Log de aanvraag ──
    log_booking_request(
        client_data, [session_data], trainin_client,
        [session_result] if session_result else None,
    )

    # ── Stap 5: Stuur Slack notificatie ──
    send_kennismaken_slack_notification(
        client_data, session_data, trainin_client, client_created, session_result
    )

    # ── Log samenvatting ──
    if trainin_client:
        client_status = f"aangemaakt (ID: {trainin_client['id']})" if client_created else f"bestaand (ID: {trainin_client['id']})"
    else:
        client_status = "niet aangemaakt (fout)"

    logger.info(
        "KENNISMAKEN BOOKING COMPLEET: %s (%s) — Client: %s — Sessie: %s — %s %s",
        name, email, client_status,
        "ingepland" if session_created else "MISLUKT",
        session_data.get("date", ""), session_data.get("start", ""),
    )

    # ── Return resultaat ──
    if session_created:
        message = "Je kennismakingsgesprek is ingepland! Je ontvangt een bevestiging per e-mail."
    else:
        message = "Je kennismakingsgesprek is aangevraagd! We nemen zo snel mogelijk contact met je op ter bevestiging."

    return {
        "success": True,
        "message": message,
        "client_found": trainin_client is not None,
        "client_created": client_created,
        "client_id": client_id,
        "sessions_created": 1 if session_created else 0,
        "sessions_total": 1,
    }


def send_kennismaken_slack_notification(
    client_data: dict,
    session_data: dict,
    trainin_client: Optional[dict] = None,
    client_created: bool = False,
    session_result: Optional[dict] = None,
):
    """Stuur een Slack Block Kit melding voor telefonisch kennismaken."""
    if not SLACK_WEBHOOK_URL:
        return

    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()
    email = client_data.get("email", "")
    phone = client_data.get("phone", "")

    if trainin_client:
        client_hid = trainin_client.get("attributes", {}).get("hid", "")
        client_id = trainin_client.get("id", "")

        if client_created:
            status_emoji = ":white_check_mark:"
            status_text = "Nieuw aangemaakt in Trainin"
        else:
            status_emoji = ":bust_in_silhouette:"
            status_text = f"Bestaande klant (ID: {client_id})"

        client_link = f"{TRAININ_BASE}/business/clients/{client_hid}"
    else:
        status_emoji = ":warning:"
        status_text = "Niet aangemaakt (handmatig toevoegen)"
        client_link = f"{TRAININ_BASE}/business/clients/new"

    # Sessie details
    session_created = session_result is not None and session_result.get("status") == "created"

    d = datetime.strptime(session_data["date"], "%Y-%m-%d")
    day_name = DAYS_NL[d.weekday()]
    month_name = MONTHS_NL[d.month - 1]
    instructor = session_data.get("instructor", "")
    instructor_str = f" ({instructor})" if instructor else ""

    if session_created:
        booking_emoji = ":white_check_mark:"
        booking_status = "Gesprek ingepland in agenda"
        line_emoji = ":white_check_mark:"
    else:
        booking_emoji = ":x:"
        booking_status = "Gesprek niet ingepland (handmatig toevoegen)"
        line_emoji = ":x:"

    session_line = f"    {line_emoji} *Telefonisch kennismaken*: {day_name} {d.day} {month_name} om {session_data['start']}{instructor_str}"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":telephone_receiver: Nieuw kennismakingsgesprek",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Naam:*\n<{client_link}|{name}>"},
                {"type": "mrkdwn", "text": f"*Klant:*\n{status_emoji} {status_text}"},
                {"type": "mrkdwn", "text": f"*E-mail:*\n{email}"},
                {"type": "mrkdwn", "text": f"*Telefoon:*\n{phone}"},
            ]
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Gesprek:* {booking_emoji} {booking_status}\n{session_line}",
            }
        },
        {"type": "divider"},
    ]

    actions = []
    if trainin_client:
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":bust_in_silhouette: Open klant", "emoji": True},
            "url": client_link,
            "style": "primary",
        })
    else:
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":heavy_plus_sign: Maak klant aan", "emoji": True},
            "url": f"{TRAININ_BASE}/business/clients/new",
            "style": "primary",
        })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":calendar: Open agenda", "emoji": True},
        "url": f"{TRAININ_BASE}/business/calendar",
    })

    blocks.append({"type": "actions", "elements": actions})

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Geboekt op {datetime.now().strftime('%d %b %Y om %H:%M')} via Kennismaken Inplan Tool",
            }
        ],
    })

    fallback_text = (
        f"Nieuw kennismakingsgesprek: {name} ({email})\n"
        f"Klant: {status_text}\n"
        f"Gesprek: {booking_status}\n{session_line}"
    )

    try:
        httpx.post(
            SLACK_WEBHOOK_URL,
            json={"text": fallback_text, "blocks": blocks},
            timeout=10,
        )
        logger.info("Kennismaken Slack notificatie verstuurd")
    except Exception as e:
        logger.warning("Kennismaken Slack notificatie mislukt: %s", e)


# ─── Evaluatie voorkeur ───────────────────────────────────

def send_evaluation_preference(client_data: dict, preferred_day: str, preferred_time: str):
    """Stuur een Slack melding met de evaluatie terugbel-voorkeur."""
    if not SLACK_WEBHOOK_URL:
        return

    name = f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip()
    email = client_data.get("email", "")
    phone = client_data.get("phone", "")

    preference = ""
    if preferred_day:
        preference += f"*Dag:* {preferred_day}"
    if preferred_time:
        if preference:
            preference += "\n"
        preference += f"*Tijdstip:* {preferred_time}"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":phone: Evaluatie terugbelverzoek",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Naam:*\n{name}"},
                {"type": "mrkdwn", "text": f"*Telefoon:*\n{phone}"},
                {"type": "mrkdwn", "text": f"*E-mail:*\n{email}"},
                {"type": "mrkdwn", "text": f"*Voorkeur:*\n{preference}"},
            ]
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Verstuurd op {datetime.now().strftime('%d %b %Y om %H:%M')} via Introductie Inplan Tool",
                }
            ],
        },
    ]

    fallback = f"Evaluatie terugbelverzoek: {name} ({phone}) — {preference}"

    try:
        httpx.post(
            SLACK_WEBHOOK_URL,
            json={"text": fallback, "blocks": blocks},
            timeout=10,
        )
        logger.info("Evaluatie voorkeur Slack melding verstuurd voor: %s", email)
    except Exception as e:
        logger.warning("Evaluatie voorkeur Slack melding mislukt: %s", e)
