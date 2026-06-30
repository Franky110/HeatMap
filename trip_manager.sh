#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ -f "env/bin/python" ]; then
    exec env/bin/python scripts/trip_manager.py
else
    echo "Setup has not been run yet. Please run ./setup.sh first."
    exit 1
fi
