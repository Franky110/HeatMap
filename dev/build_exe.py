"""Build TripManager.exe using PyInstaller.

Produces dist/TripManager.exe — a single self-contained Windows executable.
Run via dev/Build Exe.bat (or: python dev/build_exe.py from the project root).
"""
import os
import subprocess
import sys

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.dirname(DEV_DIR)              # project root
SCRIPTS_DIR = os.path.join(DIR, "scripts")
KOMOOTGPX_DIR = os.path.join(os.path.dirname(DIR), "komootgpx")
DIST_DIR = os.path.join(DIR, "dist")
BUILD_DIR = os.path.join(DIR, "build")

# Data files to bundle (src_path;dest_folder, Windows PyInstaller separator)
DATA_FILES = []
for fname in ("combined.html", "help.html"):
    fpath = os.path.join(DIR, fname)
    if os.path.exists(fpath):
        DATA_FILES.append(f"{fpath};.")

HIDDEN_IMPORTS = [
    "combine_trips",
    "trip_manager",
    "komoot_import",
    "strava_import",
    "garmin_import",
    "data_wizard",
    "dpapi_utils",
    "activity_import_base",
]

args = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--noconsole",
    "--name", "TripManager",
    "--distpath", DIST_DIR,
    "--workpath", BUILD_DIR,
    "--specpath", DIR,
    "--paths", SCRIPTS_DIR,
]

for data in DATA_FILES:
    args += ["--add-data", data]

for imp in HIDDEN_IMPORTS:
    args += ["--hidden-import", imp]

if os.path.isdir(KOMOOTGPX_DIR):
    args += ["--paths", KOMOOTGPX_DIR, "--collect-all", "komootgpx"]
    print(f"Including komootgpx from {KOMOOTGPX_DIR}")
else:
    print(f"WARNING: komootgpx not found at {KOMOOTGPX_DIR} — Komoot import will not work in the exe")

args.append(os.path.join(DIR, "scripts", "main.py"))

print("Running PyInstaller...")
result = subprocess.run(args, cwd=DIR)
if result.returncode == 0:
    exe = os.path.join(DIST_DIR, "TripManager.exe")
    size_mb = os.path.getsize(exe) / (1024 * 1024) if os.path.exists(exe) else 0
    print(f"\nBuild complete: {exe} ({size_mb:.0f} MB)")
else:
    print("\nBuild FAILED — see output above.")
    sys.exit(result.returncode)
