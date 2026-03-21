#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "      MoonMind - Codex CLI OAuth Login Session"
echo "=========================================================="
echo ""
echo "Provider: Codex"
echo "Target volume: /home/app/.codex"
echo ""
echo "Instructions:"
echo "1. We will launch the Codex CLI in login mode."
echo "2. Follow the on-screen instructions."
echo "3. Once you complete the login and see 'Success', type 'exit' or close this window."
echo "=========================================================="
echo ""

codex login

echo ""
echo "=========================================================="
echo "Login process finished."
echo "You can now safely type 'exit' to close this terminal."
echo "=========================================================="

exec bash
