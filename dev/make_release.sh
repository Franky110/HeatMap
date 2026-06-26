#!/usr/bin/env bash
cd "$(dirname "$0")/.."

if [ -f "env/bin/python" ]; then
    env/bin/python dev/make_release.py
else
    python3 dev/make_release.py 2>/dev/null || python dev/make_release.py
fi
