#!/usr/bin/env python3
"""
ABB Robot Variable Monitor + Web Visualiser
Reads robtarget and wobjdata variables from an ABB IRC5 controller
via Robot Web Services (RWS 1.0 or 2.0) and polls for changes.
Select the RWS version with the 'rws_version' key in config.ini.

The monitor runs in a background thread while a Flask web server serves:
  http://localhost:<web_port>/           – live fullscreen 2-D canvas
  http://localhost:<web_port>/settings   – rotation / keystone controls

Usage:
  python robot_monitor.py            # normal monitoring + web server
  python robot_monitor.py --probe    # discover which API paths respond, then exit
"""

import sys
import re
import time
import json
import queue
import threading
import argparse
import configparser
import os
import socket
import ipaddress
import datetime
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from flask import Flask, Response, render_template, jsonify
from werkzeug.serving import make_server

# Suppress per-request werkzeug logs (Flask already silences its own logger below)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XHTML_NS     = "http://www.w3.org/1999/xhtml"
CONFIG_PATH  = "config.ini"
THREE_VERSION = "0.168.0"
CERT_FILE     = "cert.pem"
KEY_FILE      = "key.pem"

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

_template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
app = Flask(__name__, template_folder=_template_dir)
app.logger.disabled = True          # suppress Flask request logs in terminal

# ---------------------------------------------------------------------------
# SSE broadcast infrastructure (thread-safe)
# ---------------------------------------------------------------------------

_shared: dict = {"robtargets": [], "wobjdata": [], "path": []}
_shared_lock  = threading.Lock()

_subscribers: list[queue.Queue] = []
_subs_lock    = threading.Lock()


def _broadcast(data: dict) -> None:
    """Push *data* to every connected SSE client."""
    with _shared_lock:
        _shared.update(data)
    with _subs_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/xr")
def xr_page():
    return render_template(
        "xr.html",
        server_ip=_get_local_ip(),
        https_port=_cfg_cache.get("https_port", 5443),
    )


@app.route("/api/server-info")
def api_server_info():
    return jsonify({
        "ip":         _get_local_ip(),
        "https_port": _cfg_cache.get("https_port", 5443),
        "web_port":   _cfg_cache.get("web_port",   5000),
    })


@app.route("/events")
def sse_stream():
    """Server-Sent Events endpoint – pushes JSON updates to the browser."""
    def generate():
        q: queue.Queue = queue.Queue(maxsize=8)
        with _subs_lock:
            _subscribers.append(q)
        try:
            # Send the current state immediately on connect
            with _shared_lock:
                yield f"data: {json.dumps(dict(_shared))}\n\n"
            while True:
                try:
                    payload = q.get(timeout=25)
                    yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"   # keep connection alive
        finally:
            with _subs_lock:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Config cache (populated in main, used by XR route before cfg is local)
# ---------------------------------------------------------------------------

_cfg_cache: dict = {}


# ---------------------------------------------------------------------------
# Local-IP helper
# ---------------------------------------------------------------------------

def _get_local_ip() -> str:
    """Return the primary local network IP of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# SSL certificate generation
# ---------------------------------------------------------------------------

def _ensure_ssl_certs() -> bool:
    """Generate self-signed cert+key if they do not already exist."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        print(f"  SSL certs found: {CERT_FILE}, {KEY_FILE}")
        return True

    print("  Generating self-signed SSL certificate …")
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        local_ips: set[str] = {"127.0.0.1"}
        try:
            local_ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except Exception:
            pass
        local_ips.add(_get_local_ip())

        san_list: list = [x509.DNSName("localhost")]
        for ip_str in sorted(local_ips):
            try:
                san_list.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
            except ValueError:
                pass

        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME,       "RobProjector XR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RobProjector"),
        ])

        now  = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )

        with open(KEY_FILE, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
        with open(CERT_FILE, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        san_strs = [str(s.value) for s in san_list]
        print(f"  Certificate written (valid 10 years)")
        print(f"  SANs: {', '.join(san_strs)}")
        return True

    except ImportError:
        print(
            "[WARN] Package 'cryptography' not installed – HTTPS/XR server disabled.\n"
            "       Install with:  pip install cryptography",
            file=sys.stderr,
        )
        return False
    except Exception as exc:
        print(f"[WARN] Certificate generation failed: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Three.js static asset download
# ---------------------------------------------------------------------------

def _download_three_js() -> None:
    """Download Three.js from CDN if not already present (server needs internet)."""
    base_dir  = os.path.dirname(os.path.abspath(__file__))
    three_dir = os.path.join(base_dir, "static", "three")
    os.makedirs(three_dir, exist_ok=True)

    dest = os.path.join(three_dir, "three.module.min.js")
    if os.path.exists(dest):
        return

    url = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.min.js"
    print(f"  Downloading Three.js r{THREE_VERSION} …")
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(dest, "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(f"  ✓ Saved ({len(resp.content) // 1024} KB) → static/three/")
    except Exception as exc:
        print(f"  ✗ Download failed: {exc}", file=sys.stderr)
        print(    "    The /xr page requires Three.js.  Re-run the server with internet access,",
              file=sys.stderr)
        print(    "    or place three.module.min.js manually in static/three/.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RapidVariable:
    name: str
    value: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RapidVariable):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(path: str = CONFIG_PATH) -> dict:
    cfg = configparser.ConfigParser()
    if not cfg.read(path):
        print(f"[WARN] Config file '{path}' not found – using defaults.", file=sys.stderr)

    section = cfg["robot"] if "robot" in cfg else {}
    return {
        "ip":            section.get("ip",            "192.168.125.1"),
        "port":          section.get("port",          "80"),
        "task":          section.get("task",          "T_ROB1"),
        "module":        section.get("module",        "MainModule"),
        "poll_interval": float(section.get("poll_interval", "2.0")),
        "username":      section.get("username",      "Default User"),
        "password":      section.get("password",      "robotics"),
        "web_port":      int(section.get("web_port",  "5000")),
        "https_port":    int(section.get("https_port", "5443")),
        "rws_version":   int(section.get("rws_version", "1")),
        "routine":       section.get("routine",       "main"),
    }


# ---------------------------------------------------------------------------
# RWS 1.0 client
# ---------------------------------------------------------------------------

class RWSClient:
    """Thin wrapper around the ABB RWS 1.0 REST API."""

    def __init__(self, cfg: dict) -> None:
        self.base_url = f"http://{cfg['ip']}:{cfg['port']}"
        self.task     = cfg["task"]
        self.module   = cfg["module"]

        self.session = requests.Session()
        self.session.auth = HTTPDigestAuth(cfg["username"], cfg["password"])
        self.session.headers.update({"Accept": "application/xhtml+xml"})

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _parse_response(self, resp: requests.Response, url: str) -> Optional[ET.Element]:
        try:
            resp.raise_for_status()
            return ET.fromstring(resp.text)
        except requests.HTTPError:
            print(f"[ERROR] HTTP {resp.status_code} – {url}", file=sys.stderr)
            body = resp.text.strip()
            if body:
                print(f"        Body: {body[:400]}", file=sys.stderr)
        except ET.ParseError as exc:
            print(f"[ERROR] XML parse error ({exc}) – {url}", file=sys.stderr)
        return None

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> Optional[ET.Element]:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.request(method, url, params=params, data=data, timeout=10)
            return self._parse_response(resp, url)
        except requests.ConnectionError:
            print(f"[ERROR] Cannot connect to {self.base_url}", file=sys.stderr)
        except requests.Timeout:
            print(f"[ERROR] Request timed out – {url}", file=sys.stderr)
        return None

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[ET.Element]:
        return self._request("GET", path, params=params)

    # ------------------------------------------------------------------
    # Connectivity check
    # ------------------------------------------------------------------

    def check_connection(self) -> bool:
        """Returns True if the server answers at all (any HTTP response)."""
        url = f"{self.base_url}/"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 401:
                print(
                    "[WARN] HTTP 401 on initial probe – credentials may be wrong.\n"
                    "       Continuing; the API calls will confirm.",
                    file=sys.stderr,
                )
            else:
                print(f"  Server responded HTTP {resp.status_code} – controller is reachable.")
            return True
        except requests.ConnectionError:
            print(
                f"[ERROR] Cannot reach {self.base_url}\n"
                f"        Check the controller is powered on and IP/port are correct.",
                file=sys.stderr,
            )
            return False
        except requests.Timeout:
            print(f"[ERROR] Connection timed out – {url}", file=sys.stderr)
            return False

    # ------------------------------------------------------------------
    # Endpoint discovery (--probe mode)
    # ------------------------------------------------------------------

    def probe_endpoints(self, task: str, module: str) -> None:
        candidates = [
            ("GET",  "/rw/rapid/symbols"),
            ("POST", "/rw/rapid/symbols"),
            ("GET",  f"/rw/rapid/symbol/data/RAPID/{task}/{module}"),
            ("GET",  "/rw/rapid"),
            ("GET",  "/rw/rapid/tasks"),
            ("GET",  f"/rw/rapid/tasks/{task}"),
            ("GET",  "/rws/rapid/symbols"),
            ("POST", "/rws/rapid/symbol/search"),
            ("GET",  "/"),
        ]
        post_body = {
            "view": "block", "blockurl": f"RAPID/{task}/{module}",
            "recursive": "false", "onlyused": "false", "skipshared": "false",
            "regexp": ".*", "symtyp": "con", "dattyp": "robtarget",
        }
        print(f"\n{'─' * 70}")
        print(f"  ENDPOINT PROBE  –  {self.base_url}")
        print(f"{'─' * 70}")
        print(f"  {'METHOD':<6}  {'PATH':<45}  STATUS  BODY PREVIEW")
        print(f"  {'──────':<6}  {'────────────────────────────────────────────':<45}  ──────  ────────────")
        for method, path in candidates:
            url = f"{self.base_url}{path}"
            try:
                if method == "POST":
                    resp = self.session.request(
                        method, url, params={"action": "search-symbols"},
                        data=post_body, timeout=10,
                    )
                else:
                    resp = self.session.request(method, url, timeout=10)
                preview = resp.text.strip().replace("\n", " ")[:60]
                print(f"  {method:<6}  {path:<45}  {resp.status_code:<6}  {preview}")
            except requests.ConnectionError:
                print(f"  {method:<6}  {path:<45}  CONNECTION ERROR")
            except requests.Timeout:
                print(f"  {method:<6}  {path:<45}  TIMEOUT")
        print(f"{'─' * 70}")
        print("\nRows with status 200 or 201 are working endpoints.")

    # ------------------------------------------------------------------
    # Symbol search
    # ------------------------------------------------------------------

    def search_symbols(self, type_name: str, symtyp: str = "con") -> list[str]:
        """
        POST /rw/rapid/symbols?action=search-symbols
        Returns variable names of *type_name* in the configured module.
        """
        root = self._request(
            "POST",
            "/rw/rapid/symbols",
            params={"action": "search-symbols"},
            data={
                "view": "block",
                "blockurl": f"RAPID/{self.task}/{self.module}",
                "recursive": "false",
                "onlyused": "false",
                "skipshared": "false",
                "regexp": ".*",
                "symtyp": symtyp,
                "dattyp": type_name,
            },
        )
        if root is None:
            return []
        names: list[str] = []
        for a in root.iter(f"{{{XHTML_NS}}}a"):
            href = a.get("href", "")
            if href and "/RAPID/" in href:
                name = href.rstrip("/").split("/")[-1].split("?")[0]
                if name:
                    names.append(name)
        return names

    # ------------------------------------------------------------------
    # Symbol value
    # ------------------------------------------------------------------

    def get_value(self, var_name: str) -> str:
        """
        GET /rw/rapid/symbol/data/RAPID/<task>/<module>/<var>
        Value is in: <li class="rap-data"><span class="value">…</span></li>
        """
        root = self._get(
            f"/rw/rapid/symbol/data/RAPID/{self.task}/{self.module}/{var_name}"
        )
        if root is None:
            return "<unavailable>"
        for li in root.iter(f"{{{XHTML_NS}}}li"):
            if li.get("class") == "rap-data":
                span = li.find(f"{{{XHTML_NS}}}span[@class='value']")
                if span is not None:
                    return (span.text or "").strip() or "<empty>"
        return "<unavailable>"

    # ------------------------------------------------------------------
    # Compound fetch
    # ------------------------------------------------------------------

    def fetch_variables(self, type_name: str, symtyp: str = "con") -> list[RapidVariable]:
        names = self.search_symbols(type_name, symtyp=symtyp)
        return [RapidVariable(name=n, value=self.get_value(n)) for n in names]

    # ------------------------------------------------------------------
    # Module source download (save to $TEMP, then fetch via File Service)
    # ------------------------------------------------------------------

    def get_module_text(self) -> str:
        """Save the active module to $TEMP via the RAPID save endpoint,
        then download the file via the RWS File Service."""
        save_url = f"{self.base_url}/rw/rapid/modules/{self.module}"
        try:
            resp = self.session.post(
                save_url,
                params={"task": self.task, "action": "save"},
                data={"name": self.module, "path": "$TEMP"},
                timeout=15,
            )
            if resp.status_code not in (200, 204):
                print(f"[ERROR] HTTP {resp.status_code} saving module – {save_url}",
                      file=sys.stderr)
                return ""
        except requests.ConnectionError:
            print(f"[ERROR] Cannot connect to {self.base_url}", file=sys.stderr)
            return ""
        except requests.Timeout:
            print(f"[ERROR] Timeout saving module – {save_url}", file=sys.stderr)
            return ""

        dl_url = f"{self.base_url}/fileservice/$TEMP/{self.module}.mod"
        try:
            resp = self.session.get(dl_url, headers={"Accept": "*/*"}, timeout=15)
            resp.raise_for_status()
            return resp.text
        except requests.HTTPError:
            print(f"[ERROR] HTTP {resp.status_code} downloading module – {dl_url}",
                  file=sys.stderr)
        except requests.ConnectionError:
            print(f"[ERROR] Cannot connect to {self.base_url}", file=sys.stderr)
        except requests.Timeout:
            print(f"[ERROR] Timeout downloading module – {dl_url}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# RWS 2.0 client
# ---------------------------------------------------------------------------

class RWS2Client(RWSClient):
    """Thin wrapper around the ABB RWS 2.0 REST API.

    Inherits connection handling and XML parsing from RWSClient;
    overrides endpoints, headers, and response parsing for RWS 2.0.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.session.auth = HTTPBasicAuth(cfg["username"], cfg["password"])
        self.session.headers.update({"Accept": "application/xhtml+xml;v=2.0"})

    # ------------------------------------------------------------------
    # Endpoint discovery (--probe mode)
    # ------------------------------------------------------------------

    def probe_endpoints(self, task: str, module: str) -> None:
        candidates = [
            ("POST", "/rw/rapid/symbols/search"),
            ("POST", "/rw/rapid/symbols"),
            ("GET",  f"/rw/rapid/symbol/RAPID/{task}/{module}"),
            ("GET",  "/rw/rapid"),
            ("GET",  "/rw/rapid/tasks"),
            ("GET",  f"/rw/rapid/tasks/{task}"),
            ("GET",  "/"),
        ]
        post_body = urlencode([
            ("view", "block"), ("vartyp", "any"),
            ("blockurl", f"RAPID/{task}/{module}"),
            ("symtyp", "var"), ("symtyp", "con"),
            ("recursive", "true"), ("dattyp", "robtarget"),
            ("skipshared", "FALSE"), ("onlyused", "FALSE"),
            ("stack", "0"), ("posl", "0"), ("posc", "0"),
        ])
        post_headers = {"Content-Type": "application/x-www-form-urlencoded;v=2.0"}

        print(f"\n{'─' * 70}")
        print(f"  ENDPOINT PROBE (RWS 2.0)  –  {self.base_url}")
        print(f"{'─' * 70}")
        print(f"  {'METHOD':<6}  {'PATH':<45}  STATUS  BODY PREVIEW")
        print(f"  {'──────':<6}  {'────────────────────────────────────────────':<45}  ──────  ────────────")
        for method, path in candidates:
            url = f"{self.base_url}{path}"
            try:
                if method == "POST":
                    resp = self.session.request(
                        method, url, data=post_body,
                        headers=post_headers, timeout=10,
                    )
                else:
                    resp = self.session.request(method, url, timeout=10)
                preview = resp.text.strip().replace("\n", " ")[:60]
                print(f"  {method:<6}  {path:<45}  {resp.status_code:<6}  {preview}")
            except requests.ConnectionError:
                print(f"  {method:<6}  {path:<45}  CONNECTION ERROR")
            except requests.Timeout:
                print(f"  {method:<6}  {path:<45}  TIMEOUT")
        print(f"{'─' * 70}")
        print("\nRows with status 200 or 201 are working endpoints.")

    # ------------------------------------------------------------------
    # Symbol search
    # ------------------------------------------------------------------

    def search_symbols(self, type_name: str, symtyp: str = "con") -> list[str]:
        """
        POST /rw/rapid/symbols/search
        Returns variable names of *type_name* in the configured module.
        """
        symtyp_values = ["var", "con"] if symtyp == "con" else [symtyp]

        body_params: list[tuple[str, str]] = [
            ("view", "block"),
            ("vartyp", "any"),
            ("blockurl", f"RAPID/{self.task}/{self.module}"),
        ]
        for sv in symtyp_values:
            body_params.append(("symtyp", sv))
        body_params.extend([
            ("recursive", "true"),
            ("dattyp", type_name),
            ("skipshared", "FALSE"),
            ("onlyused", "FALSE"),
            ("stack", "0"),
            ("posl", "0"),
            ("posc", "0"),
        ])

        url = f"{self.base_url}/rw/rapid/symbols/search"
        try:
            resp = self.session.post(
                url,
                data=urlencode(body_params),
                headers={"Content-Type": "application/x-www-form-urlencoded;v=2.0"},
                timeout=10,
            )
            root = self._parse_response(resp, url)
        except requests.ConnectionError:
            print(f"[ERROR] Cannot connect to {self.base_url}", file=sys.stderr)
            return []
        except requests.Timeout:
            print(f"[ERROR] Request timed out – {url}", file=sys.stderr)
            return []

        if root is None:
            return []

        names: list[str] = []

        # Strategy 1: <li class="rap-sympropvar-li"> with <span class="name">
        for li in root.iter(f"{{{XHTML_NS}}}li"):
            if "rap-sympropvar-li" not in (li.get("class") or ""):
                continue
            span = li.find(f"{{{XHTML_NS}}}span[@class='name']")
            if span is not None and span.text:
                names.append(span.text.strip())
                continue
            title = li.get("title", "")
            if title and ("RAPID/" in title):
                name = title.rstrip("/").split("/")[-1]
                if name:
                    names.append(name)

        # Strategy 2: all <a href> tags containing /RAPID/ (different response layouts)
        if not names:
            for a in root.iter(f"{{{XHTML_NS}}}a"):
                href = a.get("href", "")
                if not href or "/RAPID/" not in href:
                    continue
                parts = href.rstrip("/").split("/")
                while parts and parts[-1] in ("properties", "data"):
                    parts.pop()
                if parts:
                    name = parts[-1].split("?")[0]
                    if name:
                        names.append(name)

        if not names:
            raw = ET.tostring(root, encoding="unicode")[:1000]
            print(f"[DEBUG] RWS2 search returned no symbols. Response:\n{raw}",
                  file=sys.stderr)
        return names

    # ------------------------------------------------------------------
    # Symbol value
    # ------------------------------------------------------------------

    def get_value(self, var_name: str) -> str:
        """
        GET /rw/rapid/symbol/RAPID/<task>/<module>/<var>/data
        Value is in: <li class="rap-data"><span class="value">…</span></li>
        """
        root = self._get(
            f"/rw/rapid/symbol/RAPID/{self.task}/{self.module}/{var_name}/data"
        )
        if root is None:
            return "<unavailable>"
        for li in root.iter(f"{{{XHTML_NS}}}li"):
            if li.get("class") == "rap-data":
                span = li.find(f"{{{XHTML_NS}}}span[@class='value']")
                if span is not None:
                    return (span.text or "").strip() or "<empty>"
        return "<unavailable>"

    def get_module_text(self) -> str:
        """RWS 2.0 override — save POST needs Content-Type with v=2.0."""
        save_url = f"{self.base_url}/rw/rapid/modules/{self.module}"
        try:
            resp = self.session.post(
                save_url,
                params={"task": self.task, "action": "save"},
                data=urlencode([("name", self.module), ("path", "$TEMP")]),
                headers={"Content-Type": "application/x-www-form-urlencoded;v=2.0"},
                timeout=15,
            )
            if resp.status_code not in (200, 204):
                print(f"[ERROR] HTTP {resp.status_code} saving module – {save_url}",
                      file=sys.stderr)
                return ""
        except requests.ConnectionError:
            print(f"[ERROR] Cannot connect to {self.base_url}", file=sys.stderr)
            return ""
        except requests.Timeout:
            print(f"[ERROR] Timeout saving module – {save_url}", file=sys.stderr)
            return ""

        dl_url = f"{self.base_url}/fileservice/$TEMP/{self.module}.mod"
        try:
            resp = self.session.get(dl_url, headers={"Accept": "*/*"}, timeout=15)
            resp.raise_for_status()
            return resp.text
        except requests.HTTPError:
            print(f"[ERROR] HTTP {resp.status_code} downloading module – {dl_url}",
                  file=sys.stderr)
        except requests.ConnectionError:
            print(f"[ERROR] Cannot connect to {self.base_url}", file=sys.stderr)
        except requests.Timeout:
            print(f"[ERROR] Timeout downloading module – {dl_url}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# RAPID source parsing
# ---------------------------------------------------------------------------

_ZONE_MAP = {
    "fine": 0, "z0": 0.3, "z1": 1, "z5": 5, "z10": 10, "z15": 15,
    "z20": 20, "z30": 30, "z40": 40, "z50": 50, "z60": 60, "z80": 80,
    "z100": 100, "z150": 150, "z200": 200,
}

_MOVE_RE = re.compile(r"\b(MoveL|MoveJ|MoveC)\b(.*?);", re.IGNORECASE | re.DOTALL)
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def _split_rapid_args(s: str) -> list[str]:
    """Split RAPID arguments by comma, respecting nested brackets."""
    args: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        args.append("".join(buf).strip())
    return args


def _parse_zone(zone_str: str) -> float:
    """Convert a RAPID zone token (e.g. 'z50', 'fine') to a radius in mm."""
    val = _ZONE_MAP.get(zone_str.lower(), -1)
    if val >= 0:
        return val
    z_match = re.match(r"^z(\d+)$", zone_str, re.IGNORECASE)
    return float(z_match.group(1)) if z_match else 0


def parse_routine_moves(source: str, routine: str) -> list[dict]:
    """Extract MoveL/MoveJ/MoveC instructions from a named PROC in RAPID source.

    Returns a list of dicts:
      MoveL/MoveJ: {"target": str, "move_type": "L"|"J", "zone": float}
      MoveC:       {"target": str, "move_type": "C", "zone": float[, "via": str]}
    Only named targets (simple identifiers) are included; inline values are skipped.
    For MoveC the "via" (CirPoint) is included only when it is a named identifier.
    """
    proc_re = re.compile(
        rf"\bPROC\s+{re.escape(routine)}\s*\(.*?\)(.*?)\bENDPROC\b",
        re.DOTALL | re.IGNORECASE,
    )
    m = proc_re.search(source)
    if not m:
        return []

    body = m.group(1)
    moves: list[dict] = []

    for mm in _MOVE_RE.finditer(body):
        line_start = body.rfind('\n', 0, mm.start()) + 1
        if body[line_start:mm.start()].lstrip().startswith('!'):
            continue

        instr = mm.group(1).upper()               # 'MOVEL', 'MOVEJ', 'MOVEC'
        raw_args = mm.group(2).strip()
        args = _split_rapid_args(raw_args)
        positional = [a for a in args if not a.lstrip().startswith("\\")]

        if instr == "MOVEC":
            # MoveC CirPoint, ToPoint, Speed, Zone, Tool [\WObj]
            if len(positional) < 4:
                continue
            cir_point = positional[0].strip()
            to_point  = positional[1].strip()
            zone_str  = positional[3].strip()

            if not _IDENT_RE.match(to_point):
                continue

            entry: dict = {
                "target": to_point,
                "move_type": "C",
                "zone": _parse_zone(zone_str),
            }
            if _IDENT_RE.match(cir_point):
                entry["via"] = cir_point
            moves.append(entry)
        else:
            # MoveL / MoveJ  ToPoint, Speed, Zone, Tool [\WObj]
            if len(positional) < 3:
                continue
            target   = positional[0].strip()
            zone_str = positional[2].strip()

            if not _IDENT_RE.match(target):
                continue

            moves.append({
                "target": target,
                "move_type": instr[-1],            # 'L' or 'J'
                "zone": _parse_zone(zone_str),
            })

    return moves


# ---------------------------------------------------------------------------
# Console formatting helpers
# ---------------------------------------------------------------------------

def _fmt_var(var: RapidVariable) -> str:
    return f"  {var.name:<30} = {var.value}"


def print_robtargets(variables: list[RapidVariable]) -> None:
    print(f"\n{'=' * 60}")
    print(f"  ROBTARGETS  ({len(variables)} found in module)")
    print(f"{'=' * 60}")
    print("  <none>" if not variables else "\n".join(_fmt_var(v) for v in variables))
    print(f"{'=' * 60}")


def print_wobjdata(variables: list[RapidVariable]) -> None:
    print(f"\n{'─' * 60}")
    print(f"  WOBJDATA    ({len(variables)} found in module)")
    print(f"{'─' * 60}")
    print("  <none>" if not variables else "\n".join(_fmt_var(v) for v in variables))
    print(f"{'─' * 60}")


# ---------------------------------------------------------------------------
# Monitor loop  (runs in a background daemon thread)
# ---------------------------------------------------------------------------

def monitor(client: RWSClient, poll_interval: float, routine: str) -> None:
    known: dict[str, str] = {}
    known_path: list[dict] = []
    first_run = True

    print(f"\nMonitoring module '{client.module}' on task '{client.task}', routine '{routine}'.")
    print(f"Polling every {poll_interval:.1f} s.  Press Ctrl+C to stop.\n")

    while True:
        robtargets   = client.fetch_variables("robtarget", symtyp="con")
        current      = {v.name: v.value for v in robtargets}
        current_names = set(current)
        known_names   = set(known)

        new_names     = current_names - known_names
        removed_names = known_names   - current_names
        changed_names = {
            n for n in current_names & known_names if current[n] != known[n]
        }

        source = client.get_module_text()
        path   = parse_routine_moves(source, routine) if source else []
        path_changed = path != known_path

        if first_run or new_names or removed_names or changed_names or path_changed:
            if not first_run:
                if new_names:
                    print(f"\n[INFO] Robtarget(s) added:   {', '.join(sorted(new_names))}")
                if removed_names:
                    print(f"\n[INFO] Robtarget(s) removed: {', '.join(sorted(removed_names))}")
                if changed_names:
                    print(f"\n[INFO] Robtarget(s) changed: {', '.join(sorted(changed_names))}")
                if path_changed:
                    targets_in_path = [s["target"] for s in path]
                    print(f"\n[INFO] Path updated ({len(path)} moves): {' → '.join(targets_in_path)}")

            wobjdata = client.fetch_variables("wobjdata", symtyp="per")
            print_robtargets(robtargets)
            print_wobjdata(wobjdata)

            if path:
                print(f"\n  Path in '{routine}' ({len(path)} moves):")
                for step in path:
                    via = f"  via {step['via']}" if step.get('via') else ""
                    print(f"    Move{step['move_type']} {step['target']:<25} zone={step['zone']}{via}")

            _broadcast({
                "robtargets": [{"name": r.name, "value": r.value} for r in robtargets],
                "wobjdata":   [{"name": w.name, "value": w.value} for w in wobjdata],
                "path":       path,
            })

            known      = current
            known_path = path
            first_run  = False

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor RAPID robtarget/wobjdata variables via RWS (1.0 or 2.0) and serve a live web visualiser."
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Probe the controller to discover which API paths respond, then exit.",
    )
    args = parser.parse_args()

    cfg = load_config(CONFIG_PATH)
    _cfg_cache.update(cfg)

    rws_ver = cfg["rws_version"]

    print("ABB Robot Variable Monitor")
    print(f"  Controller  : {cfg['ip']}:{cfg['port']}")
    print(f"  Task        : {cfg['task']}")
    print(f"  Module      : {cfg['module']}")
    print(f"  User        : {cfg['username']}")
    print(f"  RWS version : {'2.0' if rws_ver == 2 else '1.0'}")
    print(f"  Routine     : {cfg['routine']}")

    if rws_ver == 2:
        client = RWS2Client(cfg)
    else:
        client = RWSClient(cfg)

    print("\nChecking connection …")
    if not client.check_connection():
        sys.exit(1)
    print("Connection OK.")

    if args.probe:
        client.probe_endpoints(cfg["task"], cfg["module"])
        return

    # Start the robot monitor in a background thread
    t = threading.Thread(
        target=monitor, args=(client, cfg["poll_interval"], cfg["routine"]), daemon=True
    )
    t.start()

    # Prepare XR assets
    print("\nXR setup …")
    _download_three_js()
    https_ok = _ensure_ssl_certs()

    local_ip   = _get_local_ip()
    web_port   = cfg["web_port"]
    https_port = cfg["https_port"]

    # HTTP server (2-D projection) runs in a background thread
    http_srv = make_server("0.0.0.0", web_port, app, threaded=True)
    http_thread = threading.Thread(target=http_srv.serve_forever, daemon=True)
    http_thread.start()

    print(f"\n2-D projection →  http://localhost:{web_port}/")
    print(f"                  http://{local_ip}:{web_port}/")
    print(f"Settings       →  http://localhost:{web_port}/settings")

    if https_ok:
        print(f"\nXR (WebXR/VR)  →  https://localhost:{https_port}/xr")
        print(f"                  https://{local_ip}:{https_port}/xr  ← open on Meta Quest 3")
        print(f"\nNote: accept the self-signed certificate warning in the Quest browser.")
        print(f"      To install the cert permanently, see README.md.\n")
        https_srv = make_server(
            "0.0.0.0", https_port, app, threaded=True,
            ssl_context=(CERT_FILE, KEY_FILE),
        )
        https_srv.serve_forever()
    else:
        print("\n[WARN] HTTPS server not started (see warnings above).\n")
        http_thread.join()


if __name__ == "__main__":
    main()
