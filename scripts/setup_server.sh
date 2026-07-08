#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip minisat cryptominisat
else
    echo "apt-get not found; install python3-venv, python3-pip, minisat, and cryptominisat manually."
fi

python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install -r requirements.txt

if command -v minisat >/dev/null 2>&1; then
    minisat --help 2>&1 | head -1 || true
else
    echo "Warning: minisat not found on PATH (default DIMACS solver)."
    exit 1
fi
if command -v cryptominisat5 >/dev/null 2>&1; then
    cryptominisat5 --version
else
    echo "Note: cryptominisat5 not found (optional; set DIMACS_SOLVER_BIN=cryptominisat5 to use)."
fi

echo "Setup complete. Activate with: source venv/bin/activate"
