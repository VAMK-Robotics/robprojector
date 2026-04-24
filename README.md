# RobProjector

A Python tool that reads RAPID robtarget and wobjdata variables from an ABB
robot controller via Robot Web Services (RWS) and displays them in real time
on a 2-D web-based projection canvas.  The web view is designed to be
projected onto a physical work table so that programmed robot positions are
visible directly on the surface.

Path visualization (move sequence, zone circles) is loaded on demand from the
Home page: the program acquires RAPID mastership, reads the module source, and
draws the path.  If the robot is in manual mode the operator must accept the
RMMP grant on the FlexPendant.

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

Dependencies: `requests`, `flask`, `cryptography`

---

## Files

| File | Description |
|---|---|
| `robot_monitor.py` | Main script: RWS client, monitor loop, Flask web server (HTTP + HTTPS) |
| `config.ini` | Configuration (IP, port, module, routine, credentials, web ports) |
| `projector_settings.json` | Display settings saved by the Settings page (created on first save) |
| `requirements.txt` | Python package dependencies |
| `generate_cert.py` | Standalone script to regenerate the self-signed SSL certificate |
| `templates/home.html` | Home page: path control, module/routine settings, navigation (served at `/home`) |
| `templates/index.html` | Fullscreen 2-D projection canvas (served at `/`) |
| `templates/settings.html` | Image adjustment controls (served at `/settings`) |
| `templates/xr.html` | 3-D WebXR visualiser for Meta Quest 3 (served at `/xr` over HTTPS) |
| `cert.pem` / `key.pem` | Auto-generated self-signed SSL certificate and private key |
| `static/three/` | Three.js module (auto-downloaded on first run) |

---

## Configuration

All parameters are set in `config.ini`.  The `module` and `routine` keys can
also be changed at runtime from the Home page without restarting the program.

```ini
[robot]
ip            = 192.168.125.1   # IP address of the robot controller
port          = 80              # RWS HTTP port (default 80)
rws_version   = 1               # 1 for RWS 1.0, 2 for RWS 2.0
task          = T_ROB1          # RAPID task name
module        = MainModule      # RAPID module to monitor
routine       = main            # RAPID routine to parse for path visualization
poll_interval = 2.0             # Polling interval in seconds
username      = Default User    # RWS login username
password      = robotics        # RWS login password
web_port      = 5000            # Port for the local web visualiser
https_port    = 5443            # Port for the HTTPS / WebXR server
```

| Key | Default | Description |
|---|---|---|
| `ip` | `192.168.125.1` | IP address of the robot controller |
| `port` | `80` | RWS HTTP port |
| `rws_version` | `1` | Robot Web Services version: `1` for RWS 1.0, `2` for RWS 2.0 |
| `task` | `T_ROB1` | RAPID task name |
| `module` | `MainModule` | RAPID module to monitor |
| `routine` | `main` | RAPID routine to parse for MoveL/MoveJ/MoveC path visualization |
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
5. Opens the Home page (`http://localhost:5000/home`) in the default browser.

The terminal prints the current lists of robtargets and wobjdata every time
the lists change.  The web pages are available at:

```
http://localhost:5000/home       (Home – path control, module/routine)
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

The lists are reprinted to the terminal and broadcast to all connected web
clients whenever:

- A new robtarget variable is found.
- An existing robtarget is removed.
- The value of any robtarget changes.

Wobjdata variables are re-read on every update but are not individually
tracked for changes.

---

## Home page (`/home`)

The Home page is opened automatically in the default browser when the program
starts.  It provides three sections:

### Path & Zone Values

| Button | Effect |
|---|---|
| **Show / Update Path and Z-values** | Acquires RAPID mastership, reads the module source, parses the configured routine, and draws the path and zone circles on the projection canvas. In manual mode a status message instructs the operator to accept the RMMP grant on the FlexPendant. Mastership is released automatically after loading. |
| **Hide Path and Z-values** | Removes path lines and zone circles from the canvas. Robtargets continue to be shown. |

A colour-coded status card shows the current state:

| Colour | State | Meaning |
|---|---|---|
| Grey | Idle | No path loaded yet, or path was hidden |
| Blue | Requesting / Loading | Mastership is being acquired or module is being read |
| Green | Done | Path loaded successfully |
| Red | Error | Mastership failed or module could not be read |

### Module & Routine

Displays the currently active RAPID module name and routine name read from
`config.ini`.  Both fields are editable.  Pressing **Save to config.ini**
updates the running monitor immediately (the next poll uses the new module)
and writes the new values back to `config.ini`.

### Navigation

Quick links to the Projected Image (`/`) and the Settings page (`/settings`).

---

## Path visualization

Path visualization is **on demand**, triggered from the Home page.  It is
not part of the continuous polling loop.

When "Show / Update Path and Z-values" is pressed:

1. The program requests RAPID mastership from the controller.
2. **RWS 1.0 only:** if the robot is in manual mode, an RMMP (Request Manual
   Mode Privilege) grant is requested and the operator must accept it on the
   FlexPendant.  The program waits up to 30 seconds.
3. The module source is downloaded (via File Service for RWS 1.0, or via the
   `/rw/rapid/tasks/{task}/modules/{module}/text` endpoint for RWS 2.0).
4. RAPID mastership is released and the RMMP grant is cancelled.
5. The routine is parsed for `MoveL`, `MoveJ`, and `MoveC` instructions.
6. The path is broadcast to all connected canvas clients via SSE.

Only named robtargets (simple identifiers) are included; inline position
values are skipped.

The path is drawn as:

- **Solid black line** for MoveL (linear motion).
- **Dashed black line** for MoveJ (joint motion).
- **Circular arc** for MoveC (circular motion), passing through the CirPoint.
- **Grey ellipse** around each target showing the zone radius.
- **Smooth corner arc** within the zone ellipse at each intermediate target,
  representing the blended corner path.

The loaded path persists across robtarget polling updates.  Use "Hide Path
and Z-values" to clear it from the display.

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
- All robtargets are always shown regardless of whether a path is loaded.
- The Z coordinate is only used by the Z-filter; it is not used for 2-D
  positioning.

### Panning

Click and drag anywhere on the canvas to reposition the view.  The wobj
origin moves with the drag.  The position is never reset automatically by
new data arriving.

On resize the origin position is scaled proportionally so that it stays at
the same relative location on screen.

For precise repositioning, use the Position Adjustment arrows on the Settings
page (see below).

### Initial scaling

The first time a non-empty target list arrives, the view is scaled and centred
to fit all targets and the origin within the canvas with padding.  After that
the scale does not change automatically; it can only be changed with the Zoom
sliders in Settings.

---

## Settings page (`/settings`)

Open in a separate browser tab or window alongside the projection canvas.
Changes take effect on the canvas instantly without any page reload.

Settings are stored in **`projector_settings.json`** on disk (next to the
executable / script).  This means they survive browser cache clears and can
be backed up or copied to another machine by copying that file.  The
projection canvas reads settings from the server file on every page load so
the correct values are always applied even in a fresh browser session.

### Image Transform

| Setting | Range | Description |
|---|---|---|
| Rotation | 0 – 360 ° | Rotates the coordinate system (origin, targets, calibration marks) around the wobj origin. The canvas aspect ratio and keystone correction are not affected. |
| Vertical Keystone | −45 – +45 ° | Corrects vertical trapezoidal distortion caused by the projector being angled toward or away from the table |
| Horizontal Keystone | −45 – +45 ° | Corrects horizontal trapezoidal distortion caused by the projector being angled sideways |
| Zoom X | 10 – 500 % | Scales the image horizontally around the wobj origin |
| Zoom Y | 10 – 500 % | Scales the image vertically around the wobj origin |

### Position Adjustment

Shifts the entire coordinate system (origin and all targets) by a precise
offset in robot mm.  This is independent of the drag pan on the canvas;
both adjustments are additive.

| Control | Description |
|---|---|
| **Step size** | Amount in mm by which each arrow press moves the image (default 1 mm) |
| **↑ ↓ ← →** arrows | Move the image up/down/left/right by one step. ↑ increases Y offset; → increases X offset. |
| **X Offset** slider / field | Current horizontal offset in mm (range ±2000 mm on slider; wider range in number field) |
| **Y Offset** slider / field | Current vertical offset in mm |

Position offsets are saved to `projector_settings.json` along with all other
settings and are restored automatically on the next program start.

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

- Python package `cryptography` (included in `requirements.txt`).
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

The rotation, keystone, zoom, position offset, calibration marks, and Z-filter
controls on the `/settings` page apply **only** to the 2-D projection canvas
(`/`).  They have no effect on the XR view.

---

## API reference

The following HTTP endpoints are served alongside the web pages:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/server-info` | Returns local IP and port numbers as JSON |
| `GET` | `/events` | SSE stream of robtarget, wobjdata, path, and path_visible updates |
| `GET` | `/api/path-status` | Current status of the path-loading operation (`idle`, `requesting`, `loading`, `done`, `error`) |
| `POST` | `/api/load-path` | Trigger on-demand path loading (acquire mastership, read module, parse, release) |
| `POST` | `/api/hide-path` | Hide path and zone circles from the canvas |
| `GET` | `/api/projector-settings` | Read display settings from `projector_settings.json` |
| `POST` | `/api/projector-settings` | Write display settings to `projector_settings.json` |
| `GET` | `/api/robot-config` | Return active module and routine |
| `POST` | `/api/robot-config` | Update module and routine at runtime and persist to `config.ini` |

---

## Known limitations

- **RWS 1.0:** Only CONST robtargets and PERS wobjdata are read.  VAR
  declarations are not queried.
- **RWS 2.0:** Both VAR and CONST robtargets are read; wobjdata searches
  for PERS declarations.
- Only the first wobjdata variable found is shown on the canvas.  Its name
  is displayed next to the origin.
- The Z coordinate is not used for 2-D positioning; depth information is
  intentionally ignored in the projection canvas.
- The web server uses Werkzeug's threaded server, which is suitable for
  single-user local network use.  It is not intended for production or
  multi-user deployments.
- Path visualization reads the module source at the moment the button is
  pressed.  If the RAPID program is modified on the controller, press
  "Show / Update Path and Z-values" again to refresh.
