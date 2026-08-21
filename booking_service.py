"""Booking Service — Verwerkt introductie-training aanvragen.

Flow:
1. Zoek client in Trainin op e-mailadres
2. Als de client niet bestaat → maak automatisch aan via staff API
3. Maak sessies aan in Trainin (met client + trainer gekoppeld)
4. Log de aanvraag
5. Stuur Slack melding met deep links naar Trainin

Sessies worden aangemaakt via POST /sessions. Dit blokkeert
het tijdslot in de agenda en koppelt de client + trainer direct.
"""

import json
import logging
import os
import re
import threading
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from config import (
    SLACK_WEBHOOK_URL, TRAININ_BASE, DEFAULT_LOCATION_ID,
    DEFAULT_LOCATION_PID, LISTING_ID_TO_PID,
    STUDIO_CAPACITY, ONLINE_ONLY_TRAINERS,
    COMM_PORTAAL_WEBHOOK_URL, COMM_PORTAAL_WEBHOOK_SECRET,
)
from trainin_client import TraininClient

logger = logging.getLogger(__name__)

PENDING_LOG = Path(__file__).parent / "pending_bookings.jsonl"


def notify_portaal(client_data: dict, sessions: list[dict], booking_type: str = "introductie") -> None:
    """Stuurt een webhook naar het communicatie portaal zodat de boeking
    zichtbaar wordt in de CRM-extensie voor medewerkers."""
    if not COMM_PORTAAL_WEBHOOK_SECRET:
        logger.debug("COMM_PORTAAL_WEBHOOK_SECRET niet geconfigureerd, portaal webhook overgeslagen")
        return

    session_list = [
        {
            "sessie_naam": s.get("key", s.get("listing_id", "")),
            "datum":       s.get("date", ""),
            "tijd":        s.get("start", "")[:5] if s.get("start") else "",
            "trainer":     s.get("instructor", ""),
        }
        for s in sessions
    ]

    payload = {
        "naam":         f"{client_data.get('first_name', '')} {client_data.get('last_name', '')}".strip(),
        "email":        client_data.get("email", ""),
        "phone":        client_data.get("phone", ""),
        "booking_type": booking_type,
        "sessions":     session_list,
    }

    try:
        resp = httpx.post(
            COMM_PORTAAL_WEBHOOK_URL,
            json=payload,
            headers={"x-webhook-secret": COMM_PORTAAL_WEBHOOK_SECRET},
            timeout=8,
        )
        if resp.status_code == 200:
            logger.info("Portaal webhook verstuurd voor %s", payload["naam"])
        else:
            logger.warning("Portaal webhook fout %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Portaal webhook mislukt: %s", e)
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


# ─── Form config cache (instructor/activity PID mapping) ──

_form_config = None
_form_config_lock = threading.Lock()


def _load_form_config():
    """Lazy-load session creation form config from Trainin. Caches result."""
    global _form_config
    if _form_config is not None:
        return _form_config

    with _form_config_lock:
        if _form_config is not None:
            return _form_config

        try:
            api = get_trainin_client()
            data = api.get("/calendar/sessions/create")
            _form_config = data.get("formConfig", data)
            logger.info("Form config geladen: %d instructors, %d activities",
                        len(_form_config.get("instructor", {}).get("options", [])),
                        len(_form_config.get("activity", {}).get("options", [])))
        except Exception as e:
            logger.warning("Form config laden mislukt: %s", e)
            _form_config = {}

    return _form_config


def _get_instructor_pid(name: str) -> Optional[str]:
    """Look up instructor PID by name using the form config."""
    config = _load_form_config()
    options = config.get("instructor", {}).get("options", [])
    name_lower = name.lower().strip()
    for opt in options:
        if opt.get("label", "").lower().strip() == name_lower:
            return opt.get("value")
    logger.warning("Instructor PID niet gevonden voor: %s", name)
    return None


def _get_location_pid(key: str) -> str:
    """Extract location PID from a slot key or return default."""
    if key and "_" in key:
        loc_id_str = key.split("_")[0]
        try:
            loc_id = int(loc_id_str)
            if loc_id == DEFAULT_LOCATION_ID:
                return DEFAULT_LOCATION_PID
        except ValueError:
            if len(loc_id_str) >= 4 and loc_id_str[0].isalpha():
                return loc_id_str
    return DEFAULT_LOCATION_PID


# ─── Client zoeken ────────────────────────────────────────

def find_client_by_email(email: str) -> Optional[dict]:
    """Zoek een bestaande client in Trainin op email.

    Uses the new search endpoint + Inertia detail fetch.
    Returns a dict compatible with old JSON:API format for backward compat:
        {"id": PID, "attributes": {"hid": PID, "name": ..., "email": ...}}
    """
    try:
        api = get_trainin_client()
        result = api.get("/clients/search", params={"search": email})
        candidates = result.get("clients", [])

        for c in candidates:
            pid = c.get("pid")
            if not pid:
                continue
            try:
                detail = api.get_inertia(f"/clients/{pid}")
                client_data = detail.get("client", {})
                if client_data.get("email", "").lower() == email.lower():
                    logger.info("Client gevonden: %s (PID: %s)", email, pid)
                    return {
                        "id": pid,
                        "attributes": {
                            "hid": pid,
                            "name": client_data.get("name", ""),
                            "email": client_data.get("email", ""),
                        },
                    }
            except Exception as e:
                logger.debug("Client detail ophalen mislukt voor %s: %s", pid, e)
                continue
    except Exception as e:
        logger.warning("Client zoeken mislukt: %s", e)
    return None


# ─── Client aanmaken ─────────────────────────────────────

def create_client_in_trainin(client_data: dict) -> Optional[dict]:
    """Maak een nieuwe client aan in Trainin via de staff API.

    Endpoint: POST /clients/new
    Required: lastName, email. Optional: firstName, phone, etc.

    Args:
        client_data: {"first_name": ..., "last_name": ..., "email": ..., "phone": ...}

    Returns:
        Dict compatible with old format: {"id": PID, "attributes": {"hid": PID, ...}}
    """
    try:
        api = get_trainin_client()

        first_name = client_data.get("first_name", "")
        last_name = client_data.get("last_name", "")
        email = client_data.get("email", "")
        phone = client_data.get("phone", "")

        payload = {
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "phone": phone,
        }

        data = api.post("/clients/new", data=payload)

        # After creation, search for the client to get their PID
        created_client = find_client_by_email(email)
        if created_client:
            logger.info(
                "Client aangemaakt in Trainin: %s %s (PID: %s)",
                first_name, last_name, created_client.get("id"),
            )
            return created_client

        # Fallback: return minimal dict if search fails
        name = f"{first_name} {last_name}".strip()
        logger.warning("Client aangemaakt maar niet gevonden bij zoeken: %s", email)
        return {
            "id": "unknown",
            "attributes": {"hid": "", "name": name, "email": email},
        }

    except Exception as e:
        logger.warning("Client aanmaken mislukt: %s", e)
        return None


# ─── Sessie aanmaken in Trainin ───────────────────────────

def create_session_in_trainin(
    session_data: dict,
    client_id: Optional[str] = None,
) -> Optional[dict]:
    """Maak een sessie aan in Trainin via de staff API.

    Endpoint: POST /calendar/sessions
    Uses PIDs for activity, location, instructor, and client.

    Args:
        session_data: {"listing_id": ..., "date": ..., "start": ..., "end": ...,
                       "instructor_id": ..., "instructor": ..., "key": ...}
        client_id: Trainin client PID om direct als booking te koppelen.

    Returns:
        {"session_id": None, "booking_id": None, "status": "created"} of None bij fout.
    """
    try:
        api = get_trainin_client()

        date = session_data["date"]
        start_time = session_data["start"]
        start_dt = f"{date} {start_time}"

        # Map listing numeric ID → activity PID
        listing_id = session_data["listing_id"]
        activity_pid = LISTING_ID_TO_PID.get(listing_id)
        if not activity_pid:
            logger.error("Geen activity PID gevonden voor listing_id=%s", listing_id)
            return None

        # Map location
        key = session_data.get("key", "")
        location_pid = _get_location_pid(key)

        payload = {
            "activityPid": activity_pid,
            "start": start_dt,
            "locationPid": location_pid,
        }

        # Map instructor by name (public API provides numeric ID + name)
        instructor_name = session_data.get("instructor", "")
        if instructor_name:
            instructor_pid = _get_instructor_pid(instructor_name)
            if instructor_pid:
                payload["instructorPid"] = instructor_pid

        # Koppel client
        if client_id:
            payload["clientPids"] = [client_id]

        logger.info(
            "Sessie aanmaken: activity=%s, %s %s, location=%s, client=%s, instructor=%s",
            activity_pid, date, start_time, location_pid,
            client_id, instructor_name,
        )

        data = api.post("/calendar/sessions", data=payload)

        logger.info("Sessie aangemaakt: response=%s", data)

        return {
            "session_id": None,
            "booking_id": None,
            "status": "created",
            "trainin_status": "accepted",
        }

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

        client_link = f"{TRAININ_BASE}/clients/{client_hid}"
    else:
        status_emoji = ":warning:"
        status_text = "Niet aangemaakt (handmatig toevoegen)"
        client_link = f"{TRAININ_BASE}/clients/new"
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
            "url": f"{TRAININ_BASE}/clients/new",
            "style": "primary",
        })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":calendar: Open agenda", "emoji": True},
        "url": f"{TRAININ_BASE}/calendar",
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


# ─── NS1/VZ detection + Studio capacity ──────────────────

_ns1vz_cache = {}  # key -> {"data": ..., "ts": float}
_ns1vz_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 60  # 1 minute — short so new bookings are reflected quickly


def _get_cached(key: str):
    """Get a value from the NS1/VZ cache if not expired."""
    with _ns1vz_cache_lock:
        entry = _ns1vz_cache.get(key)
        if entry and (_time.time() - entry["ts"]) < CACHE_TTL_SECONDS:
            return entry["data"]
    return None


def _set_cached(key: str, data):
    """Store a value in the NS1/VZ cache."""
    with _ns1vz_cache_lock:
        _ns1vz_cache[key] = {"data": data, "ts": _time.time()}
        # Cleanup old entries (older than 10 min)
        cutoff = _time.time() - 600
        expired = [k for k, v in _ns1vz_cache.items() if v["ts"] < cutoff]
        for k in expired:
            del _ns1vz_cache[k]


def fetch_sessions_for_date(date_str: str) -> list[dict]:
    """Fetch all sessions for a given date from the Trainin business API.

    Uses Inertia partial reload to get calendar sessions, then filters
    client-side to only the requested date.

    Returns list of parsed session dicts with:
        id, title, start, end, instructor_id, instructor_names,
        location_id, status, listing_id
    """
    cache_key = f"sessions_{date_str}"
    cached = _get_cached(cache_key)
    if cached is not None:
        logger.debug("Cache hit for sessions %s (%d sessions)", date_str, len(cached))
        return cached

    api = get_trainin_client()
    sessions = []

    try:
        result = api.get_inertia(
            "/calendar",
            partial_data="sessions",
            partial_component="calendar/CalendarPage",
        )

        all_sessions = result.get("sessions", [])
        for s in all_sessions:
            if s.get("type") != "session":
                continue

            start = s.get("start", "")
            session_date = start[:10] if len(start) >= 10 else ""

            if session_date != date_str:
                continue

            status_obj = s.get("status", {})
            status_val = status_obj.get("value", "") if isinstance(status_obj, dict) else str(status_obj)

            instructors = s.get("instructors", [])
            instructor_names = [i.get("name", "") for i in instructors]
            instructor_pid = instructors[0].get("pid") if instructors else None

            location = s.get("location", {}) or {}
            location_pid = location.get("pid")

            # Parse time from ISO format (e.g., "2026-08-21T10:00:00+02:00")
            time_str = ""
            if len(start) >= 16:
                time_str = start[11:16]

            sessions.append({
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "start": start,
                "end": s.get("end", ""),
                "time": time_str,
                "status": status_val,
                "instructor_id": instructor_pid,
                "instructor_names": instructor_names,
                "location_id": location_pid,
                "listing_id": s.get("activityPid"),
            })
    except Exception as e:
        logger.warning("Inertia calendar fetch mislukt: %s", e)

    logger.info("Fetched %d sessions for %s from calendar", len(sessions), date_str)
    _set_cached(cache_key, sessions)
    return sessions


def _is_ns1(title: str) -> bool:
    """Check if a session title indicates NS1 (No Show 1)."""
    return "NS1" in title.upper()


def _is_vz(title: str) -> bool:
    """Check if a session title indicates VZ (Verzet/late cancel)."""
    t = title.upper()
    return t.startswith("VZ ") or " VZ " in t


def _is_pt_session(title: str) -> bool:
    """Check if a session uses a studio PT spot (for capacity counting)."""
    t = title.lower()
    return "personal training" in t or "proeftraining" in t


def detect_ns1_vz_slots(sessions: list[dict], date_str: str) -> list[dict]:
    """Detect NS1/VZ slots from sessions, with overlap filtering.

    NS1 = No Show 1 (client didn't show, slot physically free)
    VZ = Verzet (client cancelled late, slot rebookable)

    Overlap fix: a VZ/NS1 booking stays in Trainin even after a new regular
    booking is made for the same trainer+time. We build an "occupied" set
    of (trainer, time) from regular bookings and skip any VZ/NS1 where the
    trainer is already occupied — they're not actually free anymore.

    Returns list of slot dicts that can be merged with regular available slots.
    Labels are stripped for client-facing view (just shown as normal available time).
    """
    now = datetime.now()

    # STEP 1: Build occupied set — trainers with a regular (non-VZ/NS1)
    # booking at a specific time are NOT available for VZ/NS1 slots
    occupied = set()  # {(trainer_name_lower, time_str)}
    for s in sessions:
        status = (s.get("status") or "").lower()
        if status in ("cancelled", "canceled", "declined"):
            continue
        title = s.get("title", "")
        if _is_ns1(title) or _is_vz(title):
            continue  # skip VZ/NS1 themselves when building occupied set
        instructor_names = s.get("instructor_names", [])
        time_str = s.get("time", "")
        if instructor_names and time_str:
            occupied.add((instructor_names[0].lower(), time_str))

    # STEP 2: Detect VZ/NS1 slots, skipping occupied ones
    slots = []
    skipped = 0

    for s in sessions:
        # Skip cancelled sessions
        status = (s.get("status") or "").lower()
        if status in ("cancelled", "canceled", "declined"):
            continue

        title = s.get("title", "")
        slot_type = None
        if _is_ns1(title):
            slot_type = "ns1"
        elif _is_vz(title):
            slot_type = "vz"

        if not slot_type:
            continue

        # Skip slots in the past
        time_str = s.get("time", "")
        if time_str:
            try:
                slot_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                if slot_dt < now:
                    continue
            except ValueError:
                pass

        # Need instructor_id to create a bookable slot
        instructor_id = s.get("instructor_id")
        if not instructor_id:
            continue

        # Get instructor name
        instructor_names = s.get("instructor_names", [])
        instructor_name = instructor_names[0] if instructor_names else ""

        # OVERLAP CHECK: skip if trainer already has a regular booking at this time
        if instructor_name and (instructor_name.lower(), time_str) in occupied:
            skipped += 1
            logger.debug(
                "VZ/NS1 overlap: %s at %s already has regular booking, skipping",
                instructor_name, time_str,
            )
            continue

        # Calculate end time
        end_time = ""
        if s.get("end") and len(s["end"]) >= 16:
            end_time = s["end"][11:16]

        # Build slot in same format as regular schedule slots
        # Client-facing: no NS1/VZ labels, just show as normal available time
        slots.append({
            "start": time_str,
            "end": end_time,
            "start_full": s.get("start", ""),
            "instructor": instructor_name,
            "instructor_id": instructor_id,
            "key": f"{DEFAULT_LOCATION_PID}_{instructor_id}",
            "type": slot_type,  # internal tracking only, not shown to client
        })

    if skipped:
        logger.info("Detected %d NS1/VZ slots for %s (%d skipped due to overlap)", len(slots), date_str, skipped)
    else:
        logger.info("Detected %d NS1/VZ slots for %s", len(slots), date_str)
    return slots


def get_occupancy_for_date(sessions: list[dict]) -> dict[str, int]:
    """Calculate studio occupancy per timeslot.

    Counts active PT sessions at each time, excluding:
    - NS1/VZ slots (those spots are actually free)
    - Online-only trainers (not in studio)
    - Cancelled sessions
    - Non-PT sessions (voedingscoaching, shifts, etc.)

    Returns dict like {"10:00": 4, "10:30": 6, ...}
    """
    occupancy = {}

    for s in sessions:
        # Skip cancelled
        status = (s.get("status") or "").lower()
        if status in ("cancelled", "canceled", "declined"):
            continue

        title = s.get("title", "")

        # Skip NS1/VZ (those slots are free)
        if _is_ns1(title) or _is_vz(title):
            continue

        # Only count PT sessions (uses studio spot)
        if not _is_pt_session(title):
            continue

        # Skip online-only trainers
        instructor_names = s.get("instructor_names", [])
        if instructor_names and instructor_names[0] in ONLINE_ONLY_TRAINERS:
            continue

        # Count this session at its timeslot
        time_str = s.get("time", "")
        if time_str:
            occupancy[time_str] = occupancy.get(time_str, 0) + 1

    return occupancy


def get_availability_with_ns1vz(date_str: str, listing_id: int) -> dict:
    """Get availability data enhanced with NS1/VZ slots and capacity filtering.

    Returns dict with:
        ns1_vz_slots: list of extra available slots from NS1/VZ
        occupancy: dict of timeslot -> count
        capacity: int (max PT sessions)
    """
    cache_key = f"availability_{date_str}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        sessions = fetch_sessions_for_date(date_str)
        ns1_vz_slots = detect_ns1_vz_slots(sessions, date_str)
        occupancy = get_occupancy_for_date(sessions)

        result = {
            "ns1_vz_slots": ns1_vz_slots,
            "occupancy": occupancy,
            "capacity": STUDIO_CAPACITY,
        }

        _set_cached(cache_key, result)
        return result

    except Exception as e:
        logger.warning("NS1/VZ detection failed for %s: %s", date_str, e)
        return {
            "ns1_vz_slots": [],
            "occupancy": {},
            "capacity": STUDIO_CAPACITY,
        }


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

    # ── Stap 6: Meld aan communicatie portaal ──
    notify_portaal(client_data, sessions, booking_type="introductie")

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

    # ── Stap 6: Meld aan communicatie portaal ──
    notify_portaal(client_data, sessions, booking_type="viavia")

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

        client_link = f"{TRAININ_BASE}/clients/{client_hid}"
    else:
        status_emoji = ":warning:"
        status_text = "Niet aangemaakt (handmatig toevoegen)"
        client_link = f"{TRAININ_BASE}/clients/new"
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
            "url": f"{TRAININ_BASE}/clients/new",
            "style": "primary",
        })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":calendar: Open agenda", "emoji": True},
        "url": f"{TRAININ_BASE}/calendar",
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

    # ── Stap 6: Meld aan communicatie portaal ──
    notify_portaal(client_data, [session_data], booking_type="kennismaken")

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

        client_link = f"{TRAININ_BASE}/clients/{client_hid}"
    else:
        status_emoji = ":warning:"
        status_text = "Niet aangemaakt (handmatig toevoegen)"
        client_link = f"{TRAININ_BASE}/clients/new"

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
            "url": f"{TRAININ_BASE}/clients/new",
            "style": "primary",
        })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":calendar: Open agenda", "emoji": True},
        "url": f"{TRAININ_BASE}/calendar",
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
