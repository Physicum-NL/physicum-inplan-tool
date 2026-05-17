#!/usr/bin/env python3
"""Introductie Inplan Tool — Flask server.

Proxy voor Trainin schedule API + booking creatie.
"""
import logging
from datetime import datetime

import httpx
from flask import Flask, jsonify, render_template, request

from booking_service import (
    book_intro_sessions,
    book_viavia_sessions,
    book_kennismaken_session,
    send_evaluation_preference,
    is_duplicate_booking,
    reset_client,
    get_trainin_client,
)
from config import (
    TRAININ_API_PUBLIC,
    TRAININ_API_BUSINESS,
    LISTINGS,
    VIAVIA_LISTING,
    KENNISMAKEN_LISTING,
    HOST,
    PORT,
    DEBUG,
    SERVER_URL,
    HEALTHCHECK_PING_URL,
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Shared HTTP client voor publieke API calls (geen auth nodig)
http = httpx.Client(follow_redirects=True, timeout=30)
PUBLIC_HEADERS = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


# ─── Pagina's ────────────────────────────────────────────

@app.route("/")
@app.route("/en")
def index():
    """Serveer de wizard-pagina (NL of EN)."""
    import time
    lang = "en" if request.path.startswith("/en") else "nl"
    return render_template(
        "index.html",
        listings=LISTINGS,
        lang=lang,
        firstname=request.args.get("firstname", ""),
        lastname=request.args.get("lastname", ""),
        email=request.args.get("email", ""),
        telephone=request.args.get("telephone", ""),
        cache_bust=int(time.time()),
    )


@app.route("/viavia")
@app.route("/en/viavia")
def viavia():
    """Via Via Cadeau inplan wizard (NL of EN)."""
    import time
    lang = "en" if request.path.startswith("/en") else "nl"
    return render_template(
        "viavia.html",
        listing_id=VIAVIA_LISTING["id"],
        lang=lang,
        firstname=request.args.get("firstname", ""),
        lastname=request.args.get("lastname", ""),
        email=request.args.get("email", ""),
        telephone=request.args.get("telephone", ""),
        cache_bust=int(time.time()),
    )


@app.route("/kennismaken")
@app.route("/en/kennismaken")
def kennismaken():
    """Telefonisch kennismaken inplan wizard (NL of EN)."""
    import time
    lang = "en" if request.path.startswith("/en") else "nl"
    return render_template(
        "kennismaken.html",
        listing_id=KENNISMAKEN_LISTING["id"],
        lang=lang,
        firstname=request.args.get("firstname", ""),
        lastname=request.args.get("lastname", ""),
        email=request.args.get("email", ""),
        telephone=request.args.get("telephone", ""),
        cache_bust=int(time.time()),
    )


@app.route("/uitleg")
def uitleg():
    """Interne uitleg-pagina voor collega's."""
    return render_template("uitleg.html")


# ─── API Proxy Endpoints ─────────────────────────────────

@app.route("/api/dates/<int:listing_id>")
def get_dates(listing_id):
    """Haal beschikbare datums op voor een listing in een maand."""
    month = request.args.get("month")
    if not month:
        now = datetime.now()
        month = now.strftime("%Y%m")

    try:
        r = http.get(
            f"{TRAININ_API_PUBLIC}/schedule/dates",
            headers=PUBLIC_HEADERS,
            params={
                "filter[listing]": str(listing_id),
                "filter[month]": month,
                "filter[location]": "all",
                "filter[instructor]": "all",
            },
        )
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        logger.error(f"Fout bij ophalen datums: {e}")
        return jsonify({"error": str(e), "dates": []}), 502


@app.route("/api/slots/<int:listing_id>")
def get_slots(listing_id):
    """Haal beschikbare tijdsloten op voor een listing op een datum."""
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "date parameter verplicht", "slots": []}), 400

    try:
        r = http.get(
            f"{TRAININ_API_PUBLIC}/schedule",
            headers=PUBLIC_HEADERS,
            params={
                "include": "listings,locations,instructors,products",
                "filter[listing]": str(listing_id),
                "filter[date]": date,
                "filter[location]": "all",
            },
        )
        r.raise_for_status()
        data = r.json()

        # Bouw instructor lookup (type is "staff_members" in de API)
        instructors = {}
        for item in data.get("data", []):
            if item.get("type") == "staff_members":
                instructors[int(item["id"])] = item.get("attributes", {}).get("name", "")

        # Transformeer items naar simpele slots
        slots = []
        for item in data.get("items", []):
            if not item.get("available"):
                continue

            start = item.get("start", "")
            end = item.get("end", "")
            start_time = start.split(" ")[1][:5] if " " in start else start
            end_time = end.split(" ")[1][:5] if " " in end else end

            # Zoek instructor naam
            instructor_ids = item.get("available_instructor_ids", [])
            instructor_name = ""
            for iid in instructor_ids:
                if iid in instructors:
                    instructor_name = instructors[iid]
                    break

            slots.append({
                "start": start_time,
                "end": end_time,
                "start_full": start,
                "instructor": instructor_name,
                "instructor_id": instructor_ids[0] if instructor_ids else None,
                "key": item.get("available_keys", [""])[0],
            })

        return jsonify({"slots": slots, "date": date})

    except Exception as e:
        logger.error(f"Fout bij ophalen slots: {e}")
        return jsonify({"error": str(e), "slots": []}), 502


@app.route("/api/book", methods=["POST"])
def book():
    """Boek 3 introductie-trainingen.

    Verwacht JSON body:
    {
        "client": {"first_name": "...", "last_name": "...", "email": "...", "phone": "..."},
        "sessions": [
            {"listing_id": 49005, "date": "2026-03-15", "start": "15:00", "key": "908_6688"},
            {"listing_id": 49003, "date": "2026-03-18", "start": "14:30", "key": "908_6688"},
            {"listing_id": 49004, "date": "2026-03-22", "start": "16:00", "key": "908_6688"}
        ]
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Geen data ontvangen"}), 400

    sessions = data.get("sessions", [])
    if len(sessions) != 3:
        return jsonify({"error": "Precies 3 sessies verwacht"}), 400

    client_data = data.get("client", {})
    if not client_data.get("email"):
        return jsonify({"error": "E-mailadres is verplicht"}), 400

    # Voorkom dubbele boekingen (zelfde email binnen 60 seconden)
    if is_duplicate_booking(client_data["email"].lower()):
        logger.warning("Dubbele boeking geblokkeerd voor: %s", client_data["email"])
        return jsonify({
            "success": True,
            "message": "Je trainingen zijn al aangevraagd! Je ontvangt een bevestiging per e-mail.",
        })

    # Valideer chronologische volgorde
    dates = [s.get("date", "") for s in sessions]
    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            return jsonify({"error": "Sessies moeten in chronologische volgorde staan"}), 400

    try:
        result = book_intro_sessions(client_data, sessions)
        return jsonify(result)
    except Exception as e:
        logger.error("Onverwachte fout bij boeken: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "message": "Er ging iets mis. Probeer het opnieuw of neem contact op.",
        }), 500


@app.route("/api/book-viavia", methods=["POST"])
def book_viavia():
    """Boek 2 Via Via Cadeau trainingen.

    Verwacht JSON body:
    {
        "client": {"first_name": "...", "last_name": "...", "email": "...", "phone": "..."},
        "sessions": [
            {"listing_id": 7117, "date": "2026-03-15", "start": "15:00", "key": "908_6688"},
            {"listing_id": 7117, "date": "2026-03-18", "start": "14:30", "key": "908_6688"}
        ]
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Geen data ontvangen"}), 400

    sessions = data.get("sessions", [])
    if len(sessions) != 2:
        return jsonify({"error": "Precies 2 sessies verwacht"}), 400

    client_data = data.get("client", {})
    if not client_data.get("email"):
        return jsonify({"error": "E-mailadres is verplicht"}), 400

    # Voorkom dubbele boekingen (zelfde email binnen 60 seconden)
    if is_duplicate_booking(client_data["email"].lower()):
        logger.warning("Dubbele Via Via boeking geblokkeerd voor: %s", client_data["email"])
        return jsonify({
            "success": True,
            "message": "Je trainingen zijn al aangevraagd! Je ontvangt een bevestiging per e-mail.",
        })

    # Valideer chronologische volgorde
    dates = [s.get("date", "") for s in sessions]
    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            return jsonify({"error": "Sessies moeten in chronologische volgorde staan"}), 400

    try:
        result = book_viavia_sessions(client_data, sessions)
        return jsonify(result)
    except Exception as e:
        logger.error("Onverwachte fout bij Via Via boeken: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "message": "Er ging iets mis. Probeer het opnieuw of neem contact op.",
        }), 500


@app.route("/api/book-kennismaken", methods=["POST"])
def book_kennismaken():
    """Boek 1 telefonisch kennismakingsgesprek.

    Verwacht JSON body:
    {
        "client": {"first_name": "...", "last_name": "...", "email": "...", "phone": "..."},
        "session": {"listing_id": 51876, "date": "2026-03-15", "start": "10:30", "key": "908_6688"}
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Geen data ontvangen"}), 400

    session = data.get("session")
    if not session:
        return jsonify({"error": "Sessie data ontbreekt"}), 400

    client_data = data.get("client", {})
    if not client_data.get("email"):
        return jsonify({"error": "E-mailadres is verplicht"}), 400

    if is_duplicate_booking(client_data["email"].lower()):
        logger.warning("Dubbele kennismaken boeking geblokkeerd voor: %s", client_data["email"])
        return jsonify({
            "success": True,
            "message": "Je kennismakingsgesprek is al aangevraagd!",
        })

    try:
        result = book_kennismaken_session(client_data, session)
        return jsonify(result)
    except Exception as e:
        logger.error("Onverwachte fout bij kennismaken boeken: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "message": "Er ging iets mis. Probeer het opnieuw of neem contact op.",
        }), 500


@app.route("/api/evaluation-preference", methods=["POST"])
def evaluation_preference():
    """Ontvang voorkeur voor evaluatie terugbelmoment."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Geen data ontvangen"}), 400

    client_data = data.get("client", {})
    preferred_day = data.get("preferred_day", "")
    preferred_time = data.get("preferred_time", "")

    try:
        send_evaluation_preference(client_data, preferred_day, preferred_time)
        return jsonify({"success": True})
    except Exception as e:
        logger.error("Evaluatie voorkeur verwerken mislukt: %s", e)
        return jsonify({"success": True})  # Niet falen richting gebruiker


@app.route("/api/health")
def health_check():
    """Gezondheidscontrole — test of de tool en Trainin API werken.

    Wordt dagelijks om 6:00 aangeroepen door een cron job.
    Stuurt optioneel een Slack melding met de status.
    """
    from config import SLACK_WEBHOOK_URL

    checks = {
        "server": {"ok": True, "detail": "Flask draait"},
        "trainin_public_api": {"ok": False, "detail": "Niet getest"},
        "trainin_auth_api": {"ok": False, "detail": "Niet getest"},
        "listing_dates": {"ok": False, "detail": "Niet getest"},
    }

    # Check 1: Publieke Trainin API (schedule dates)
    try:
        from datetime import datetime as dt
        month = dt.now().strftime("%Y%m")
        r = http.get(
            f"{TRAININ_API_PUBLIC}/schedule/dates",
            headers=PUBLIC_HEADERS,
            params={
                "filter[listing]": "49005",
                "filter[month]": month,
                "filter[location]": "all",
                "filter[instructor]": "all",
            },
        )
        r.raise_for_status()
        dates = r.json().get("dates", [])
        checks["trainin_public_api"] = {"ok": True, "detail": f"OK — {len(dates)} beschikbare datums in {month}"}
        checks["listing_dates"] = {"ok": len(dates) > 0, "detail": f"{len(dates)} datums gevonden" if dates else "Geen datums gevonden"}
    except Exception as e:
        checks["trainin_public_api"] = {"ok": False, "detail": str(e)[:100]}

    # Check 2: Geauthenticeerde Trainin API (staff) — met auto-healing
    try:
        api = get_trainin_client()
        result = api.get("/clients", params={"per_page": 1})
        total = result.get("meta", {}).get("total", 0)
        checks["trainin_auth_api"] = {"ok": True, "detail": f"OK — {total} clients in systeem"}
    except Exception as e:
        # Auto-heal: reset client, forceer nieuwe login, en probeer opnieuw
        logger.warning("Auth API check mislukt, auto-healing: %s", e)
        try:
            reset_client()
            api = get_trainin_client()
            result = api.get("/clients", params={"per_page": 1})
            total = result.get("meta", {}).get("total", 0)
            checks["trainin_auth_api"] = {"ok": True, "detail": f"OK — hersteld na re-login, {total} clients"}
        except Exception as e2:
            checks["trainin_auth_api"] = {"ok": False, "detail": str(e2)[:100]}

    # Samenvatting
    all_ok = all(c["ok"] for c in checks.values())
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M")

    # Stuur Slack melding als ?notify=true
    if request.args.get("notify") == "true" and SLACK_WEBHOOK_URL:
        emoji = ":white_check_mark:" if all_ok else ":rotating_light:"
        status_text = "Alles werkt!" if all_ok else "PROBLEMEN GEVONDEN"

        check_lines = []
        for name, check in checks.items():
            icon = ":white_check_mark:" if check["ok"] else ":x:"
            check_lines.append(f"    {icon} *{name}*: {check['detail']}")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Dagelijkse controle Inplan Tool",
                    "emoji": True,
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Status:* {status_text}\n\n" + "\n".join(check_lines),
                }
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Automatische controle — {timestamp}"}
                ],
            },
        ]

        # Voeg "Repareer" button toe als er problemen zijn
        if not all_ok:
            fix_url = f"{SERVER_URL}/api/fix?notify=true"
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔧 Repareer automatisch", "emoji": True},
                        "url": fix_url,
                        "style": "danger",
                    }
                ],
            })

        try:
            httpx.post(
                SLACK_WEBHOOK_URL,
                json={
                    "text": f"Dagelijkse controle Inplan Tool: {status_text}",
                    "blocks": blocks,
                },
                timeout=10,
            )
        except Exception:
            pass

    # Ping Healthchecks.io zodat we weten dat de health check zelf nog draait
    if HEALTHCHECK_PING_URL:
        try:
            hc_url = HEALTHCHECK_PING_URL if all_ok else f"{HEALTHCHECK_PING_URL}/fail"
            httpx.get(hc_url, timeout=5)
        except Exception:
            pass

    return jsonify({
        "status": "ok" if all_ok else "degraded",
        "timestamp": timestamp,
        "checks": checks,
    }), 200 if all_ok else 503


@app.route("/api/fix")
def fix_tool():
    """Repareer de tool: reset auth, herverbind met Trainin API.

    Kan handmatig aangeroepen worden via de Slack-knop of direct via URL.
    Met ?notify=true stuurt het resultaat naar Slack.
    """
    from config import SLACK_WEBHOOK_URL

    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M")
    steps = []

    # Stap 1: Reset de Trainin client (forceert nieuwe login)
    try:
        reset_client()
        steps.append({"name": "Reset client", "ok": True, "detail": "Client gereset"})
    except Exception as e:
        steps.append({"name": "Reset client", "ok": False, "detail": str(e)[:100]})

    # Stap 2: Maak nieuwe verbinding met Trainin API
    try:
        api = get_trainin_client()
        result = api.get("/clients", params={"per_page": 1})
        total = result.get("meta", {}).get("total", 0)
        steps.append({"name": "Re-authenticatie", "ok": True, "detail": f"Ingelogd, {total} clients zichtbaar"})
    except Exception as e:
        steps.append({"name": "Re-authenticatie", "ok": False, "detail": str(e)[:100]})

    all_ok = all(s["ok"] for s in steps)

    # Stuur resultaat naar Slack
    if request.args.get("notify") == "true" and SLACK_WEBHOOK_URL:
        emoji = ":white_check_mark:" if all_ok else ":x:"
        status_text = "Reparatie gelukt!" if all_ok else "Reparatie mislukt"

        step_lines = []
        for step in steps:
            icon = ":white_check_mark:" if step["ok"] else ":x:"
            step_lines.append(f"    {icon} *{step['name']}*: {step['detail']}")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {status_text}",
                    "emoji": True,
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(step_lines),
                }
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Handmatige reparatie — {timestamp}"}
                ],
            },
        ]

        try:
            httpx.post(
                SLACK_WEBHOOK_URL,
                json={"text": status_text, "blocks": blocks},
                timeout=10,
            )
        except Exception:
            pass

    return jsonify({
        "status": "fixed" if all_ok else "failed",
        "timestamp": timestamp,
        "steps": steps,
    }), 200 if all_ok else 500


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG)
