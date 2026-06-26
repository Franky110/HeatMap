#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "Trip Manager setup"
echo "=================="
echo

# Find Python 3.9+
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Python 3.9+ was not found on this computer."
    echo
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Install it from https://www.python.org/downloads/ or via Homebrew:"
        echo "  brew install python"
    else
        echo "Install it with your package manager, e.g.:"
        echo "  sudo apt install python3   (Debian / Ubuntu)"
        echo "  sudo dnf install python3   (Fedora)"
    fi
    exit 1
fi

echo "Using: $($PYTHON --version)"
echo

# Create / reuse virtual environment
if [ ! -f "env/bin/python" ]; then
    echo "Creating virtual environment in 'env'..."
    "$PYTHON" -m venv env
else
    echo "Reusing existing virtual environment in 'env'."
fi

echo
echo "Installing required packages..."
env/bin/python -m pip install --upgrade pip --quiet
env/bin/python -m pip install -r requirements.txt

# Warn if tkinter is missing (common on Linux minimal installs)
if ! env/bin/python -c "import tkinter" 2>/dev/null; then
    echo
    echo "WARNING: tkinter is not installed — the GUI will not start."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "Install it with:"
        echo "  sudo apt install python3-tk      (Debian / Ubuntu)"
        echo "  sudo dnf install python3-tkinter (Fedora)"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Use the python.org installer (includes tkinter), or:"
        echo "  brew install python-tk"
    fi
fi

echo
echo "Setup complete!"
echo "Start the program with:  ./trip_manager.sh"
echo
