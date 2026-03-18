# RobProjector

A Python tool that reads RAPID robtarget and wobjdata variables from an ABB
IRC5 robot controller via Robot Web Services (RWS) 1.0 and displays them in
real time on a 2-D web-based projection canvas.  The web view is designed to
be projected onto a physical work table so that programmed robot positions are
visible directly on the surface.

---

## Requirements

- Python 3.10 or newer
- ABB IRC5 controller with RobotWare 6 and RWS 1.0 enabled
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
| `robot_monitor.py` | Main script: RWS client, monitor loop, Flask web server |
| `config.ini` | Configuration (IP, port, module name, credentials, web port) |
| `requirements.txt` | Python package dependencies |
| `templates/index.html` | Fullscreen projection canvas (served at `/`) |
| `templates/settings.html` | Image adjustment controls (served at `/settings`) |

---

## Configuration

All parameters are set in `config.ini`:

```ini
[robot]
ip           = 192.168.125.1   # IP address of the IRC5 controller
port         = 80              # RWS HTTP port (default 80)
task         = T_ROB1          # RAPID task name
module       = MainModule      # RAPID module to monitor
poll_interval = 2.0            # Polling interval in seconds
username     = Default User    # RWS login username
password     = robotics        # RWS login password
web_port     = 5000            # Port for the local web visualiser
```

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
http://localhost:5000/          (projection canvas)
http://localhost:5000/settings  (image adjustment controls)
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

The monitor polls the controller every `poll_interval` seconds using the
following RWS 1.0 endpoints:

- `POST /rw/rapid/symbols?action=search-symbols` - searches for CONST
  robtarget variables and PERS wobjdata variables in the configured module.
- `GET /rw/rapid/symbol/data/RAPID/<task>/<module>/<variable>` - retrieves
  the current value of each variable.

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

The script uses HTTP Digest authentication, which is the default method for
RWS.  The credentials are taken from `config.ini`.  The default ABB factory
credentials are `Default User` / `robotics`.

---

## Known limitations

- Only CONST robtargets and PERS wobjdata are read.  VAR declarations are
  not queried.
- Only the first wobjdata variable found is shown on the canvas.  Its name
  is displayed next to the origin.
- The Z coordinate is not used for 2-D positioning; depth information is
  intentionally ignored.
- The web server uses Flask's built-in development server, which is suitable
  for single-user local network use.  It is not intended for production or
  multi-user deployments.
