#!/bin/bash
cd "$(dirname "$0")"
echo "Starting Money..."
if command -v python3 >/dev/null 2>&1; then
  python3 money.py
else
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ and run this again."
  read -p "Press Enter to close."
fi
