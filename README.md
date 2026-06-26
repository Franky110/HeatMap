# Trip Manager

A personal GPS activity heatmap and dashboard for cyclists, runners, hikers, and skiers.
Import trips from Komoot, Strava, or Garmin, visualize them all on a single map, and explore
each trip's elevation, speed, heart rate, and cadence in an interactive dashboard.

![Heatmap](docs/HeatMapHTML.png)

## Features
- **Import** — download activities directly from Komoot, Strava, and Garmin, or add any GPX file
![Import window](docs/HeatMapMainWindow.png)
- **Heatmap** — all your trips overlaid on an interactive map (Leaflet + OpenStreetMap)
![Heatmap](docs/HeatMapHTML.png)
- **Trips** — see all your trips passing through a certain location
![Trips](docs/TripsSelector.png)
- **Dashboard** — per-trip charts for elevation, speed, heart rate, cadence, and power with a flexible layout (stack or combine any series, dual Y-axes)
- **Segments** — define named stretches of road/trail and see a leaderboard of all trips through them, ranked fastest to slowest; compare two efforts side by side
- **Compare mode** — overlay two trips (or two segment efforts) to compare pace, elevation, power, and more
- **X-axis toggle** — switch between distance and time on all charts at once
- **Color modes** — line, gradient fill, or value-based coloring (green → red by speed)
- **Data Wizard** — inspect and clean your trip library: filter by sport/date/source/sensor, detect color-coded duplicate pairs, delete raw or processed files
- **Android app** — view your heatmap on your phone via the companion Flutter app (`android_app/`)

## Requirements

- Windows 10/11, macOS 12+, or Linux (any modern distro)
- Python 3.9+ ([python.org](https://www.python.org/downloads/))
- tkinter (included with the python.org installer; on Linux: `sudo apt install python3-tk`)

## Getting started

### Windows
1. Download and unzip the [latest release](../../releases/latest)
2. **Right-click the zip → Properties → check Unblock → OK** before extracting (avoids Windows security warnings)
3. Run `setup.bat` once to create the Python environment
4. Double-click **Trip Manager.bat** to launch

### macOS / Linux
1. Clone or download and unzip the repository
2. Run `./setup.sh` once to create the Python environment
3. Run `./trip_manager.sh` to launch

> **Credential security:** On Windows, saved passwords are encrypted with DPAPI and bound to your user account. On macOS and Linux, they are stored as base64 in the credential file with permissions set to `600` (owner read/write only). Keep your data folder private.

## Importing trips

| Source | How |
|--------|-----|
| **Komoot** | Click *Import from Komoot* — enter your credentials once, then select activities to download |
| **Strava** | Click *Import from Strava* — follow the OAuth flow |
| **Garmin** | Click *Import from Garmin* — enter credentials and select a date range |
| **Any GPX** | Click *Add files…* or *Add folder…* in the trip list |

After a download completes you will be prompted to process and visualize the new trips immediately.

## Processing and viewing

1. Select trips in the list (or use the date/sport/distance filters)
2. Click **Run processing** — this parses the GPX files and builds the map data
3. Click **Open visualization** — your browser opens the heatmap

## Dashboard layout

Click a trip on the map to open its dashboard. The **Layout ▾** button lets you:
- Assign any series (speed, elevation, heart rate, cadence, power) to any chart slot
- Combine two series in one slot (dual Y-axes) or split them into separate charts
- Choose line, gradient fill, or value-gradient color per series
- Toggle the X-axis between distance and time

## Project structure

```
Trip Manager.bat        — launch the app (Windows)
trip_manager.sh         — launch the app (macOS / Linux)
setup.bat               — first-time setup (Windows)
setup.sh                — first-time setup (macOS / Linux)
combined.html           — self-contained map + dashboard viewer (shared by desktop and Android)
help.html               — in-app help page
README.md               — this file
LICENSE

scripts/                — Python source (desktop app, all platforms)
  trip_manager.py       — main GUI (tkinter)
  combine_trips.py      — GPX parser and data builder
  komoot_import.py      — Komoot downloader
  strava_import.py      — Strava downloader
  garmin_import.py      — Garmin downloader
  data_wizard.py        — advanced data management
  dpapi_utils.py        — cross-platform credential encryption
  trip_utils.py         — shared GPX utilities
  activity_import_base.py — shared import base class

android_app/            — Flutter Android companion app
  lib/                  — Dart source
  android_patch/        — AndroidManifest + network security config
  scripts/              — prepare_assets.bat / prepare_assets.sh
  pubspec.yaml
  README.md             — Android-specific setup guide

docs/                   — screenshots and documentation assets

dev/                    — developer tools (not needed for normal use)
  Build Exe.bat         — build TripManager.exe via PyInstaller (Windows)
  Make Release.bat      — package a distributable zip (Windows)
  make_release.sh       — package a distributable zip (macOS / Linux)
  build_exe.py          — PyInstaller build script
  make_release.py       — release packaging script

tests/                  — automated test suite (pytest)
```

## Data and privacy

Your personal data (GPX files, processed maps, credentials) is stored in a folder **you choose**,
separate from the program folder. It is never uploaded anywhere. Credentials are encrypted locally
(DPAPI on Windows, `chmod 600` on macOS/Linux) and are excluded from any release zip.

## Android companion app

See [`android_app/README.md`](android_app/README.md) for setup instructions.
The app serves your processed trip data over a local HTTP server and displays the full heatmap
in an embedded WebView — the same map and dashboard as the desktop browser version.

## Contributing

Pull requests welcome. Please open an issue first for significant changes.

## License

[MIT](LICENSE)
