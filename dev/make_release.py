"""Package Trip Manager into a zip that can be shared with another user.

The zip contains everything needed to set up a fresh copy of Trip Manager
(scripts, HTML, requirements, setup/launch batch files) but excludes
personal data and machine-specific files: the virtual environment, caches,
and any saved Komoot credentials. The recipient just unzips it and runs
setup.bat.
"""

import os
import zipfile

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.dirname(DEV_DIR)              # project root
KOMOOTGPX_DIR = os.path.join(os.path.dirname(DIR), "komootgpx")
OUTPUT_ZIP = os.path.join(DIR, "TripManager_release.zip")

# Folders that should not be included in the shared copy.
EXCLUDE_DIRS = {
    "env",           # Python virtual environment — recipient runs setup.bat to create their own
    "cache",         # runtime cache
    "__pycache__",   # compiled bytecode
    ".git",          # version-control history
    ".claude",       # dev tooling
    ".vs",           # Visual Studio workspace
    ".github",       # CI/CD config, not needed by end users
    "dev",           # developer tools, not needed by end users
    "tests",         # test suite, not needed by end users
    "processed",     # user's processed GPS data
    "raw_gpx",       # user's raw GPX files
    "trip_details",  # processed per-trip detail files
    "dist",          # PyInstaller output
    "build",         # PyInstaller work folder
}

# Files that should not be included in the shared copy.
EXCLUDE_FILES = {
    "komoot_credentials.json",  # personal credentials — never share
    "osm_graph.pkl",
    os.path.basename(OUTPUT_ZIP),
    "TripManager.spec",         # PyInstaller spec, not needed by end users
    "requirements-dev.txt",     # dev dependencies only
    "pytest.ini",
}


def main():
    if os.path.exists(OUTPUT_ZIP):
        os.remove(OUTPUT_ZIP)

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                if name in EXCLUDE_FILES or name.endswith(".pyc"):
                    continue
                full_path = os.path.join(root, name)
                arc_path = os.path.relpath(full_path, DIR)
                zf.write(full_path, arc_path)
                print(f"Added {arc_path}")

        # Bundle the komootgpx package (sibling of this folder in dev) so
        # recipients don't need a separate checkout.
        if os.path.isdir(KOMOOTGPX_DIR):
            for root, dirs, files in os.walk(KOMOOTGPX_DIR):
                dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
                for name in files:
                    if name.endswith(".pyc"):
                        continue
                    full_path = os.path.join(root, name)
                    arc_path = os.path.join("komootgpx", os.path.relpath(full_path, KOMOOTGPX_DIR))
                    zf.write(full_path, arc_path)
                    print(f"Added {arc_path}")
        else:
            print(f"WARNING: komootgpx not found at {KOMOOTGPX_DIR} — import buttons will not work in the release")

    size_mb = os.path.getsize(OUTPUT_ZIP) / (1024 * 1024)
    print(f"\nCreated {OUTPUT_ZIP} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
