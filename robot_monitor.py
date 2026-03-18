#!/usr/bin/env python3
"""
ABB Robot Variable Monitor + Web Visualiser
Reads robtarget and wobjdata variables from an ABB IRC5 controller
via Robot Web Services (RWS) 1.0 (base path: /rw/) and polls for changes.

The monitor runs in a background thread while a Flask web server serves:
  http://localhost:<web_port>/           – live fullscreen 2-D canvas
  http://localhost:<web_port>/settings   – rotation / keystone controls

Usage:
  python robot_monitor.py            # normal monitoring + web server
  python robot_monitor.py --probe    # discover which API paths respond, then exit
"""

import sys
import time
import json
import queue
import threading
import argparse
import configparser
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, Response, render_template

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XHTML_NS    = "http://www.w3.org/1999/xhtml"
CONFIG_PATH = "config.ini"

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

_template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
app = Flask(__name__, template_folder=_template_dir)
app.logger.disabled = True          # suppress Flask request logs in terminal

# ---------------------------------------------------------------------------
# SSE broadcast infrastructure (thread-safe)
# ---------------------------------------------------------------------------

_shared: dict = {"robtargets": [], "wobjdata": []}
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

def monitor(client: RWSClient, poll_interval: float) -> None:
    # Stores {name: value} of the previous poll so we detect value changes too
    known: dict[str, str] = {}
    first_run = True

    print(f"\nMonitoring module '{client.module}' on task '{client.task}'.")
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

        if first_run or new_names or removed_names or changed_names:
            if not first_run:
                if new_names:
                    print(f"\n[INFO] Robtarget(s) added:   {', '.join(sorted(new_names))}")
                if removed_names:
                    print(f"\n[INFO] Robtarget(s) removed: {', '.join(sorted(removed_names))}")
                if changed_names:
                    print(f"\n[INFO] Robtarget(s) changed: {', '.join(sorted(changed_names))}")

            wobjdata = client.fetch_variables("wobjdata", symtyp="per")
            print_robtargets(robtargets)
            print_wobjdata(wobjdata)

            _broadcast({
                "robtargets": [{"name": r.name, "value": r.value} for r in robtargets],
                "wobjdata":   [{"name": w.name, "value": w.value} for w in wobjdata],
            })

            known      = current
            first_run  = False

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor RAPID robtarget/wobjdata variables via RWS 1.0 and serve a live web visualiser."
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Probe the controller to discover which API paths respond, then exit.",
    )
    args = parser.parse_args()

    cfg = load_config(CONFIG_PATH)

    print("ABB Robot Variable Monitor")
    print(f"  Controller : {cfg['ip']}:{cfg['port']}")
    print(f"  Task       : {cfg['task']}")
    print(f"  Module     : {cfg['module']}")
    print(f"  User       : {cfg['username']}")

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
        target=monitor, args=(client, cfg["poll_interval"]), daemon=True
    )
    t.start()

    # Start the Flask web server in the main thread
    web_port = cfg["web_port"]
    print(f"\nWeb visualiser →  http://localhost:{web_port}/")
    print(f"Settings       →  http://localhost:{web_port}/settings\n")
    app.run(host="0.0.0.0", port=web_port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
