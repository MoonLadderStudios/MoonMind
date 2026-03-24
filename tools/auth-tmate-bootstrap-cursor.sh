#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "      MoonMind - Cursor CLI OAuth Login Session"
echo "=========================================================="
echo ""
echo "Provider: Cursor"
echo "Target volume: /home/app/.cursor"
echo ""
echo "Instructions:"
echo "1. We will launch the Cursor CLI in login mode."
echo "2. Follow the on-screen instructions."
echo "3. Once you complete the login and see 'Success', type 'exit' or close this window."
echo "=========================================================="
echo ""

cursor login

echo ""
echo "=========================================================="
echo "Login process finished."
echo "You can now safely type 'exit' to close this terminal."
echo "=========================================================="

exec bash
