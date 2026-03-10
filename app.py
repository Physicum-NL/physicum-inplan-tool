#!/usr/bin/env python3
"""Introductie Inplan Tool — Flask server.

Proxy voor Trainin schedule API + booking creatie.
"""
import logging
from datetime import datetime

import httpx
from flask import Flask, jsonify, render_template, request

from booking_service import book_intro_sessions
from config import (
    TRAININ_API_PUBLIC,
    TRAININ_API_BUSINESS,
    LISTINGS,
    HOST,
    PORT,
    DEBUG,
    META_PIXEL_ID,
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
def index():
    """Serveer de wizard-pagina."""
    return render_template(
        "index.html",
        listings=LISTINGS,
        meta_pixel_id=META_PIXEL_ID,
        name=request.args.get("name", ""),
        email=request.args.get("email", ""),
        phone=request.args.get("phone", ""),
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

    try:
        result = book_intro_sessions(client_data, sessions)
        return jsonify(result)
    except Exception as e:
        logger.error("Onverwachte fout bij boeken: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "message": "Er ging iets mis. Probeer het opnieuw of neem contact op.",
        }), 500


@app.after_request
def add_headers(response):
    """Voeg iframe-compatibele headers toe."""
    response.headers["X-Frame-Options"] = "ALLOWALL"
    response.headers["Content-Security-Policy"] = "frame-ancestors *"
    return response


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG)
