#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ -f "env/bin/python" ]; then
    env/bin/python scripts/make_release.py
else
    python3 scripts/make_release.py 2>/dev/null || python scripts/make_release.py
fi
