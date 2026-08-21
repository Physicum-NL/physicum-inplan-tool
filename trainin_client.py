#!/usr/bin/env python3
"""
Trainin API Client — Starterskit voor Python projecten met Trainin.

Gebruik:
    from trainin_client import TraininClient

    client = TraininClient()
    orders = client.get("/orders/outstanding", params={"page": 1})
    clients = client.get("/clients/123")
    client.put("/orders/456/send-payment-reminder")

Authenticatie gaat automatisch:
    1. Probeert bestaande cookie uit macOS Keychain
    2. Als verlopen → login via Playwright (echte browser)
    3. Slaat verse cookie op voor volgende keer

Setup (eenmalig):
    python3 setup.py
"""

import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote

import httpx


# ─── Standaard configuratie ───

DEFAULT_LOGIN_URL = "https://trainin.app/login"
DEFAULT_SUBDOMAIN = "physicum-company"
DEFAULT_BASE_URL = f"https://{DEFAULT_SUBDOMAIN}.trainin.app"
DEFAULT_API_BASE = f"{DEFAULT_BASE_URL}/api/v2/AR7DJ/business"
KEYCHAIN_SERVICE = "trainin-reminders"
IS_MACOS = platform.system() == "Darwin"
COOKIE_FILE = Path(__file__).parent / ".cookie_cache.json"


class TraininClient:
    """Authenticated HTTP client voor de Trainin API.

    Handelt automatisch cookie-management, XSRF tokens, en
    Playwright browser-login af.

    Args:
        subdomain: Je Trainin subdomain (bijv. "physicum")
        api_path: API pad na het subdomein (bijv. "/api/v2/AR7DJ/business")
        login_url: URL van de login pagina
        keychain_service: macOS Keychain service naam
    """

    def __init__(
        self,
        subdomain=DEFAULT_SUBDOMAIN,
        api_path="/api/v2/AR7DJ/business",
        login_url=DEFAULT_LOGIN_URL,
        keychain_service=KEYCHAIN_SERVICE,
    ):
        self.subdomain = subdomain
        self.base_url = f"https://{subdomain}.trainin.app"
        self.api_base = f"{self.base_url}{api_path}"
        self.login_url = login_url
        self.keychain_service = keychain_service

        self._cookie = None
        self._xsrf = None
        self._http = httpx.Client(follow_redirects=True, timeout=30)
        self._authenticated = False
        self._auth_attempts = 0
        self._last_auth_attempt = 0

    # ─── Public API ───

    def get(self, path, params=None):
        """GET request naar de Trainin API. Retourneert JSON response."""
        self._ensure_authenticated()
        url = self._url(path)
        resp = self._http.get(url, headers=self._headers(), params=params)

        # Auto-retry bij verlopen sessie
        if resp.status_code in (401, 419):
            self._re_authenticate()
            resp = self._http.get(url, headers=self._headers(), params=params)

        resp.raise_for_status()
        return resp.json()

    def put(self, path, data=None):
        """PUT request naar de Trainin API. Retourneert JSON response."""
        self._ensure_authenticated()
        url = self._url(path)
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        resp = self._http.put(url, headers=headers, json=data)

        if resp.status_code in (401, 419):
            self._re_authenticate()
            headers = self._headers()
            headers["Content-Type"] = "application/json"
            resp = self._http.put(url, headers=headers, json=data)

        resp.raise_for_status()
        return resp.json() if resp.text else None

    def post(self, path, data=None):
        """POST request naar de Trainin API. Retourneert JSON response."""
        self._ensure_authenticated()
        url = self._url(path)
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        resp = self._http.post(url, headers=headers, json=data)

        if resp.status_code in (401, 419):
            self._re_authenticate()
            headers = self._headers()
            headers["Content-Type"] = "application/json"
            resp = self._http.post(url, headers=headers, json=data)

        resp.raise_for_status()
        return resp.json() if resp.text else None

    def delete(self, path):
        """DELETE request naar de Trainin API."""
        self._ensure_authenticated()
        url = self._url(path)
        resp = self._http.delete(url, headers=self._headers())

        if resp.status_code in (401, 419):
            self._re_authenticate()
            resp = self._http.delete(url, headers=self._headers())

        resp.raise_for_status()
        return resp.json() if resp.text else None

    # ─── Handige shortcuts ───

    def outstanding_orders(self, page=1):
        """Haal alle openstaande orders op."""
        return self.get("/orders/outstanding", params={"page": page})

    def client_detail(self, client_id):
        """Haal klantgegevens op (incl. telefoon)."""
        return self.get(f"/clients/{client_id}")

    def send_payment_reminder(self, order_id):
        """Stuur een betalingsherinnering voor een order."""
        return self.put(f"/orders/{order_id}/send-payment-reminder")

    # ─── Authenticatie ───

    def _re_authenticate(self):
        """Reset auth state en log opnieuw in. Wordt aangeroepen bij 401/419.

        Heeft backoff om te voorkomen dat we Trainin spammen bij herhaalde failures.
        """
        now = time.time()
        # Voorkom meer dan 3 pogingen per 5 minuten
        if self._auth_attempts >= 3 and (now - self._last_auth_attempt) < 300:
            raise RuntimeError(
                f"Te veel auth pogingen ({self._auth_attempts}x in 5 min). "
                "Wacht even of check credentials."
            )

        self._auth_attempts += 1
        self._last_auth_attempt = now

        # Backoff: wacht langer bij meer pogingen (0s, 2s, 5s)
        wait = min(self._auth_attempts * 2, 10)
        if wait > 0:
            print(f"[TraininClient] Wacht {wait}s voor re-auth (poging {self._auth_attempts})...")
            time.sleep(wait)

        print("[TraininClient] Sessie verlopen, opnieuw inloggen...")
        self._authenticated = False
        self._cookie = None
        self._xsrf = None
        self._ensure_authenticated()

        # Reset counter bij succes
        self._auth_attempts = 0

    def _ensure_authenticated(self):
        """Zorg dat we een geldige sessie hebben."""
        if self._authenticated:
            return

        # Stap 1: probeer bestaande cookie
        cookie = self._get_from_keychain("cookie")
        if cookie and cookie != "OPGESLAGEN_IN_KEYCHAIN":
            if self._try_cookie(cookie):
                self._authenticated = True
                return

        # Stap 2: probeer login via httpx (geen browser nodig — werkt op VPS)
        email = self._get_from_keychain("email")
        password = self._get_from_keychain("password")
        if email and password:
            if self._login_with_httpx(email, password):
                self._authenticated = True
                return

        # Stap 3: login via Playwright (macOS met browser)
        self._login_with_playwright()
        self._authenticated = True

    def _try_cookie(self, cookie):
        """Test of een bestaande cookie nog werkt."""
        # Parse cookie
        parts = {}
        for part in cookie.split("; "):
            if "=" in part:
                k, v = part.split("=", 1)
                parts[k] = v

        # Bezoek pagina voor verse session tokens
        client = httpx.Client(follow_redirects=True, timeout=30)
        client.get(
            f"{self.base_url}/business/finances/outstanding",
            headers={
                "Cookie": cookie,
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0",
            },
        )

        jar = {k: v for k, v in client.cookies.items()}
        parts.update(jar)
        cookie_str = "; ".join(f"{k}={v}" for k, v in parts.items())
        xsrf = unquote(jar.get("XSRF-TOKEN", ""))

        if not xsrf:
            return False

        # Quick API test
        resp = client.get(
            f"{self.api_base}/orders/outstanding",
            headers={
                "Accept": "application/json",
                "Cookie": cookie_str,
                "X-XSRF-TOKEN": xsrf,
                "X-Auth-Guard": "staff",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/business/finances/outstanding",
            },
            params={"page": 1},
        )

        if resp.status_code == 200:
            self._cookie = cookie_str
            self._xsrf = xsrf
            self._http = httpx.Client(follow_redirects=True, timeout=30)
            client.close()
            return True
        client.close()
        return False

    def _login_with_httpx(self, email, password):
        """Login via directe HTTP requests (geen browser nodig — werkt op VPS).

        Gebruikt het Trainin SPA login endpoint:
        1. GET /login → haal session cookies (XSRF-TOKEN + trainin_session)
        2. POST /api/v2/login → authenticeer met username/password
        3. GET /business/... → haal subdomain cookies voor API calls
        """
        try:
            client = httpx.Client(follow_redirects=False, timeout=30)

            # Stap 1: GET login pagina voor session cookies
            resp = client.get(self.login_url, headers={
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            })

            login_cookies = {k: v for k, v in client.cookies.items()}
            cookie_str = "; ".join(f"{k}={v}" for k, v in login_cookies.items())
            xsrf = unquote(login_cookies.get("XSRF-TOKEN", ""))

            if not xsrf:
                print("[TraininClient] Geen XSRF token van login pagina")
                client.close()
                return False

            # Stap 2: POST /api/v2/login (SPA endpoint)
            resp = client.post(
                "https://trainin.app/api/v2/login",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Cookie": cookie_str,
                    "Origin": "https://trainin.app",
                    "Referer": self.login_url,
                    "X-XSRF-TOKEN": xsrf,
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                },
                json={
                    "username": email,
                    "password": password,
                    "device_name": "InplanTool",
                },
            )

            if resp.status_code != 200:
                print(f"[TraininClient] Login mislukt: status {resp.status_code}")
                client.close()
                return False

            # Merge cookies van login response
            for k, v in resp.cookies.items():
                login_cookies[k] = v

            # Stap 3: Bezoek business pagina voor subdomain cookies
            cookie_str = "; ".join(f"{k}={v}" for k, v in login_cookies.items())
            resp2 = client.get(
                f"{self.base_url}/business/finances/outstanding",
                headers={
                    "Accept": "text/html",
                    "Cookie": cookie_str,
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                },
            )

            for k, v in resp2.cookies.items():
                login_cookies[k] = v

            # Volg redirect als nodig
            if resp2.status_code in (302, 303):
                redirect_url = resp2.headers.get("location", "")
                if redirect_url:
                    cookie_str = "; ".join(f"{k}={v}" for k, v in login_cookies.items())
                    resp3 = client.get(redirect_url, headers={
                        "Accept": "text/html",
                        "Cookie": cookie_str,
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    })
                    for k, v in resp3.cookies.items():
                        login_cookies[k] = v

            # Bouw finale cookie string
            self._cookie = "; ".join(f"{k}={v}" for k, v in login_cookies.items())
            self._xsrf = unquote(login_cookies.get("XSRF-TOKEN", ""))

            if not self._xsrf:
                print("[TraininClient] Geen XSRF token na login")
                self._cookie = None
                client.close()
                return False

            # Verifieer met een API call
            test_resp = client.get(
                f"{self.api_base}/orders/outstanding",
                headers={
                    "Accept": "application/json",
                    "Cookie": self._cookie,
                    "X-XSRF-TOKEN": self._xsrf,
                    "X-Auth-Guard": "staff",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.base_url}/business/finances/outstanding",
                },
                params={"page": 1},
            )

            if test_resp.status_code == 200:
                print("[TraininClient] Httpx login succesvol!")
                self._save_to_keychain("cookie", self._cookie)
                self._save_to_keychain("xsrf", self._xsrf)
                self._http = httpx.Client(follow_redirects=True, timeout=30)
                client.close()
                return True
            else:
                print(f"[TraininClient] API test na login mislukt: {test_resp.status_code}")
                self._cookie = None
                self._xsrf = None
                client.close()
                return False

        except Exception as e:
            print(f"[TraininClient] Httpx login fout: {e}")
            self._cookie = None
            self._xsrf = None
            return False

    def _login_with_playwright(self):
        """Login via een echte Chromium browser."""
        from playwright.sync_api import sync_playwright

        email = self._get_from_keychain("email")
        password = self._get_from_keychain("password")
        if not email or not password:
            raise RuntimeError(
                "Geen login-gegevens in Keychain. Voer uit: python3 setup.py"
            )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                page = context.new_page()

                page.goto(self.login_url, wait_until="networkidle")

                page.fill('input[name="email"]', email)
                page.fill('input[name="password"]', password)
                if page.is_visible('input[name="remember"]'):
                    page.check('input[name="remember"]')

                page.click('button:has-text("Login")')

                try:
                    page.wait_for_url(f"**{self.subdomain}**", timeout=15000)
                except Exception:
                    raise RuntimeError(
                        f"Login mislukt — pagina bleef op {page.url}. "
                        "Check je gegevens via: python3 setup.py"
                    )

                cookies = context.cookies(self.base_url)
                if not cookies:
                    cookies = context.cookies()
            finally:
                browser.close()

        cookie_dict = {c["name"]: c["value"] for c in cookies}
        self._cookie = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
        self._xsrf = unquote(cookie_dict.get("XSRF-TOKEN", ""))

        if not self._xsrf:
            raise RuntimeError("Login: geen XSRF token in cookies.")

        # Bewaar voor volgende keer
        self._save_to_keychain("cookie", self._cookie)
        self._http = httpx.Client(follow_redirects=True, timeout=30)

    # ─── Helpers ───

    def _url(self, path):
        """Bouw volledige API URL."""
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.api_base}{path}"

    def _headers(self):
        """Bouw geauthenticeerde headers."""
        return {
            "Accept": "application/json",
            "Cookie": self._cookie,
            "X-XSRF-TOKEN": self._xsrf,
            "X-Auth-Guard": "staff",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/business/finances/outstanding",
        }

    def _get_from_keychain(self, key):
        """Lees een waarde — macOS Keychain of environment/file fallback."""
        if IS_MACOS:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-s", self.keychain_service, "-a", key, "-w"],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

        # Linux/VPS: lees uit environment variabelen
        env_map = {
            "email": "TRAININ_EMAIL",
            "password": "TRAININ_PASSWORD",
            "cookie": "TRAININ_COOKIE",
        }
        env_key = env_map.get(key)
        if env_key and os.environ.get(env_key):
            return os.environ[env_key]

        # Fallback: cookie cache bestand
        if key in ("cookie", "xsrf") and COOKIE_FILE.exists():
            try:
                data = json.loads(COOKIE_FILE.read_text())
                return data.get(key)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Corrupt cache — verwijder zodat we een verse login forceren
                print(f"[TraininClient] Cookie cache corrupt, wordt verwijderd")
                try:
                    COOKIE_FILE.unlink()
                except Exception:
                    pass
            except Exception:
                pass

        return None

    def _save_to_keychain(self, key, value):
        """Sla een waarde op — macOS Keychain of file fallback."""
        if IS_MACOS:
            subprocess.run(
                ["security", "delete-generic-password",
                 "-s", self.keychain_service, "-a", key],
                capture_output=True,
            )
            subprocess.run(
                ["security", "add-generic-password",
                 "-s", self.keychain_service, "-a", key, "-w", value, "-U"],
                capture_output=True,
            )
        else:
            # Linux/VPS: sla op in cache bestand (atomic write om corruptie te voorkomen)
            if key in ("cookie", "xsrf"):
                try:
                    existing = {}
                    if COOKIE_FILE.exists():
                        try:
                            existing = json.loads(COOKIE_FILE.read_text())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            existing = {}  # Start vers bij corrupt bestand
                    existing[key] = value

                    # Schrijf eerst naar temp bestand, dan rename (atomic op Linux)
                    tmp_path = str(COOKIE_FILE) + ".tmp"
                    with open(tmp_path, "w") as f:
                        json.dump(existing, f)
                    os.chmod(tmp_path, 0o600)
                    os.replace(tmp_path, str(COOKIE_FILE))
                except Exception as e:
                    print(f"[TraininClient] Cookie opslaan mislukt: {e}")

    def close(self):
        """Sluit de HTTP client."""
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
