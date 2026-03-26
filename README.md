# RobProjector

A Python tool that reads RAPID robtarget and wobjdata variables from an ABB
robot controller via Robot Web Services (RWS) and displays them in real time
on a 2-D web-based projection canvas.  The web view is designed to be
projected onto a physical work table so that programmed robot positions are
visible directly on the surface.

Supported controllers:

| Controller | RWS version | `rws_version` |
|---|---|---|
| IRC5 (RobotWare 6) | RWS 1.0 | `1` |
| IRC5 (RobotWare 7) | RWS 2.0 | `2` |
| OmniCore (RobotWare 7) | RWS 2.0 | `2` |

---

## Requirements

- Python 3.10 or newer
- ABB IRC5 or OmniCore controller with RWS enabled (RWS 1.0 or 2.0)
- Network connection to the controller

Install Python dependencies:

```
pip install -r requirements.txt
```

Dependencies: `requests`, `flask`

---

## Files

| File | Description |
|---|---|
| `robot_monitor.py` | Main script: RWS client, monitor loop, Flask web server (HTTP + HTTPS) |
| `config.ini` | Configuration (IP, port, module name, credentials, web port, HTTPS port) |
| `requirements.txt` | Python package dependencies |
| `generate_cert.py` | Standalone script to regenerate the self-signed SSL certificate |
| `templates/index.html` | Fullscreen 2-D projection canvas (served at `/`) |
| `templates/settings.html` | Image adjustment controls (served at `/settings`) |
| `templates/xr.html` | 3-D WebXR visualiser for Meta Quest 3 (served at `/xr` over HTTPS) |
| `cert.pem` / `key.pem` | Auto-generated self-signed SSL certificate and private key |
| `static/three/` | Three.js module (auto-downloaded on first run) |

---

## Configuration

All parameters are set in `config.ini`:

```ini
[robot]
ip           = 192.168.125.1   # IP address of the IRC5 controller
port         = 80              # RWS HTTP port (default 80)
rws_version  = 1               # 1 for RWS 1.0, 2 for RWS 2.0
task         = T_ROB1          # RAPID task name
module       = MainModule      # RAPID module to monitor
poll_interval = 2.0            # Polling interval in seconds
username     = Default User    # RWS login username
password     = robotics        # RWS login password
web_port     = 5000            # Port for the local web visualiser
https_port   = 5443            # Port for the HTTPS / WebXR server
```

| Key | Default | Description |
|---|---|---|
| `ip` | `192.168.125.1` | IP address of the IRC5 controller |
| `port` | `80` | RWS HTTP port |
| `rws_version` | `1` | Robot Web Services version: `1` for RWS 1.0, `2` for RWS 2.0 |
| `task` | `T_ROB1` | RAPID task name |
| `module` | `MainModule` | RAPID module to monitor |
| `poll_interval` | `2.0` | Polling interval in seconds |
| `username` | `Default User` | RWS login username |
| `password` | `robotics` | RWS login password |
| `web_port` | `5000` | Port for the local web visualiser |
| `https_port` | `5443` | Port for the HTTPS / WebXR server |

---

## Running

```
python robot_monitor.py
```

On startup the script:

1. Reads `config.ini`.
2. Verifies that the controller is reachable.
3. Starts the robot monitor in a background thread.
4. Starts the Flask web server.

The terminal prints the current lists of robtargets and wobjdata every time
the lists change.  The web visualiser is available at:

```
http://localhost:5000/           (2-D projection canvas)
http://localhost:5000/settings   (image adjustment controls)
https://localhost:5443/xr        (3-D XR visualiser – Meta Quest 3)
https://<local-ip>:5443/xr       (same, accessible from Quest on LAN)
```

### Probe mode

If the RWS endpoint paths are uncertain, run:

```
python robot_monitor.py --probe
```

This sends test requests to a set of candidate URL paths and prints the HTTP
status code and response preview for each, without starting the monitor or
web server.

---

## How the monitor works

The monitor polls the controller every `poll_interval` seconds.  The API
endpoints differ depending on the selected RWS version:

### RWS 1.0 (`rws_version = 1`)

- `POST /rw/rapid/symbols?action=search-symbols` — searches for CONST
  robtarget variables and PERS wobjdata variables in the configured module.
- `GET /rw/rapid/symbol/data/RAPID/<task>/<module>/<variable>` — retrieves
  the current value of each variable.

### RWS 2.0 (`rws_version = 2`)

- `POST /rw/rapid/symbols/search` — searches for VAR/CONST robtarget
  variables and PERS wobjdata variables in the configured module.
- `GET /rw/rapid/symbol/RAPID/<task>/<module>/<variable>/data` — retrieves
  the current value of each variable.

RWS 2.0 requests include `Accept: application/xhtml+xml;v=2.0` and
`Content-Type: application/x-www-form-urlencoded;v=2.0` headers.

---

The lists are reprinted to the terminal and broadcast to all connected web
clients whenever:

- A new robtarget variable is found.
- An existing robtarget is removed.
- The value of any robtarget changes.

Wobjdata variables are re-read on every update but are not individually
tracked for changes.

---

## Projection canvas (`/`)

The canvas is a fullscreen white page intended to be used in browser
fullscreen mode (F11) and projected onto a table.

### Coordinate system

- The wobj frame origin (0, 0) is drawn with a dark-red X-axis arrow and a
  dark-green Y-axis arrow, matching the ABB RobotStudio colour convention.
- Each robtarget is drawn at its (X, Y) position with:
  - A red arrow showing the tool X-axis direction (derived from the
    orientation quaternion).
  - A green arrow showing the tool Y-axis direction.
  - A filled dot at the position.
  - The variable name as a text label.
- The Z coordinate of each robtarget is not used for positioning; it is only
  used by the Z-filter.
- Only targets whose canvas position falls within the visible area are drawn.
  Targets outside the canvas are silently skipped.

### Panning

Click and drag anywhere on the canvas to reposition the view.  The wobj
origin moves with the drag.  Releasing the mouse (or touch) locks the new
position.  The position is never reset automatically by new data arriving.

On resize the origin position is scaled proportionally so that it stays at
the same relative location on screen.

### Initial scaling

The first time a non-empty target list arrives, the view is scaled and centred
to fit all targets and the origin within the canvas with padding.  After that
the scale does not change automatically; it can only be changed with the Zoom
slider in Settings.

---

## Settings page (`/settings`)

Open in a separate browser tab or window.  Changes take effect on the
projection canvas instantly without any page reload.  Settings are stored in
the browser's `localStorage` and survive page refreshes.

### Image Transform

| Setting | Range | Description |
|---|---|---|
| Rotation | 0 - 360 deg | Rotates the entire canvas image |
| Vertical Keystone | -45 - +45 deg | Corrects vertical trapezoidal distortion caused by the projector being angled toward or away from the table |
| Horizontal Keystone | -45 - +45 deg | Corrects horizontal trapezoidal distortion caused by the projector being angled sideways |
| Zoom | 10 - 500 % | Scales the image around the wobj origin |

All transform controls have both a slider and a number input field that are
kept in sync.  The number field accepts values typed directly.

### Calibration Marks

When enabled, two reference marks are drawn on the canvas:

- One mark at robot position (calX, 0) on the X-axis.
- One mark at robot position (0, calY) on the Y-axis.

Each mark is rendered as a dashed crosshair with a circle and a coordinate
label in dark blue.  The purpose is to help physically align the projected
image with the real table by placing known reference points at measurable
distances from the wobj origin.

| Setting | Default | Description |
|---|---|---|
| Show Calibration Marks | Off | Enables or disables the marks |
| X-axis mark distance | 500 mm | Distance along the X-axis for the X mark |
| Y-axis mark distance | 500 mm | Distance along the Y-axis for the Y mark |

### Z-filter

When enabled, robtargets are hidden from the canvas if their Z coordinate
falls outside the symmetric range defined by the limit:

```
Target is drawn  if  -limit <= Z <= +limit
Target is hidden if   Z > +limit  or  Z < -limit
```

This is useful when the robot module contains targets at multiple Z heights
and only the targets lying approximately in the table plane should be shown.

| Setting | Default | Description |
|---|---|---|
| Filter Robtargets by Z-value | Off | Enables or disables the filter |
| Z limit | 20 mm | Half-range around Z = 0 within which targets are shown |

---

## Authentication

The authentication method depends on the RWS version:

- **RWS 1.0** — HTTP Digest authentication (the default for RWS 1.0
  controllers).
- **RWS 2.0** — HTTP Basic authentication.

Credentials are taken from `config.ini` in both cases.  The default ABB
factory credentials are `Default User` / `robotics`.

---

## XR visualiser (`/xr`)  –  Meta Quest 3

A second, independent visualiser that renders robtargets in full 3-D using
WebXR and Three.js, designed for use in the Meta Quest 3 browser.

### Requirements

- Python package `cryptography` (added to `requirements.txt`).
- The server PC must have internet access on the **first run** so that
  Three.js can be downloaded and cached in `static/three/`.  Subsequent
  runs work without internet.
- The Meta Quest 3 must be on the same Wi-Fi network as the server PC.

### Setup and access

1. Install dependencies (if not already done):

```
pip install -r requirements.txt
```

2. Start the server normally:

```
python robot_monitor.py
```

On first startup the script will:
- Download Three.js from the jsDelivr CDN and save it to `static/three/`.
- Generate a self-signed SSL certificate (`cert.pem` / `key.pem`) covering
  `localhost`, `127.0.0.1`, and all detected local network IPs.

The terminal will print the HTTPS address, for example:

```
XR (WebXR/VR)  →  https://192.168.1.10:5443/xr  ← open on Meta Quest 3
```

3. Open that URL in the Meta Quest 3 browser.

4. The browser will warn about the self-signed certificate.  Tap
   **Advanced → Proceed** to continue (the connection is local-only; there
   is no third-party security risk).

5. Tap **ENTER VR** on the page.  The scene starts in immersive VR mode.

### Accepting the certificate permanently (optional)

To avoid the warning on every visit:

1. Copy `cert.pem` to the Quest (e.g. via the Meta Quest Developer Hub or
   `adb push cert.pem /sdcard/`).
2. On the Quest go to **Settings → Security → Install from storage** and
   install the certificate as a "CA certificate".

### Regenerating the certificate

If the server's IP address changes, regenerate the certificate:

```
python generate_cert.py
```

This replaces `cert.pem` and `key.pem` with a new certificate that covers
the current network addresses.

### 3-D scene

| Element | Description |
|---|---|
| Coordinate-system axes | Large X (red) / Y (green) / Z (blue) arrows at the wobj origin |
| Wobj label | Name of the first wobjdata variable, shown near the origin |
| Robtarget markers | Orange sphere at each position with smaller X/Y/Z axis arrows showing tool orientation |
| Name labels | Floating text label above each robtarget sphere |
| Floor grid | Reference grid at the VR floor level (Y = 0) |

The coordinate mapping is:  ABB X → VR right,  ABB Y → VR forward,
ABB Z → VR up.  Positions are converted from mm to metres (÷ 1000).

### Controller interaction

| Action | Effect |
|---|---|
| Hold **A** (right) / **X** (left) | Translate the coordinate system along its own local axes |
| Hold **B** (right) / **Y** (left) | Rotate the coordinate system around its own local axes |
| Hold both A+B / X+Y | Translate and rotate simultaneously |
| Press **Grip** (either controller) | Open / close the settings menu |
| Point + press **Trigger** (in menu) | Click a button in the settings menu |

Individual robtargets cannot be moved; only the whole coordinate system
moves and rotations are applied around the local-axis pivot.

### Settings menu

Opened by pressing the **Grip** button on either controller.  Point the
controller at a button and press **Trigger** to activate it.  The menu has
two tabs.

#### Position tab

Fine-tune the position and orientation of the coordinate system with precise
numeric values.

| Row | Unit | Description |
|---|---|---|
| **x / y / z** | mm | Translate the origin along the system's local X, Y, Z axes |
| **rx / ry / rz** | ° | Rotate around the system's local X, Y, Z axes |

Each field has **−** and **+** buttons.  The step increment is shown and
adjustable separately for mm and degrees (cycle through presets:
0.1, 0.5, 1, 5, 10, 50, 100).

#### Appearance tab

| Control | Description |
|---|---|
| **Origin axes → Length** | Length of the X/Y/Z arrows at the coordinate-system origin (50 %–200 % of default 120 mm) |
| **Robtarget axes → Length** | Length of the orientation arrows on each robtarget marker (50 %–200 % of default 60 mm) |
| **step** | Increment for the length controls (same presets as position tab) |
| **Plane ON / OFF** | Toggle a semi-transparent calibration plane in the ABB XY plane.  Its corner is at the origin; sides run along +X and +Y |
| **Marks ON / OFF** | Toggle two crosshair markers — one on the X-axis at distance *Size*, one on the Y-axis at distance *Size* |
| **Size** | Side length of the calibration plane and distance of the crosshair marks (100–2000 mm, default 500 mm) |
| **step** (calib) | Increment for the Size control (default 50 mm) |

Plane and marks can be used independently or together.

### Image settings

The rotation, keystone, zoom, calibration marks, and Z-filter controls on
the `/settings` page apply **only** to the 2-D projection canvas (`/`).
They have no effect on the XR view.

---

## Known limitations

- **RWS 1.0:** Only CONST robtargets and PERS wobjdata are read.  VAR
  declarations are not queried.
- **RWS 2.0:** Both VAR and CONST robtargets are read; wobjdata searches
  for PERS declarations.
- Only the first wobjdata variable found is shown on the canvas.  Its name
  is displayed next to the origin.
- The Z coordinate is not used for 2-D positioning; depth information is
  intentionally ignored.
- The web server uses Flask's built-in development server, which is suitable
  for single-user local network use.  It is not intended for production or
  multi-user deployments.
