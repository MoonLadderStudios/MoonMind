#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "      MoonMind - Claude Code OAuth Login Session"
echo "=========================================================="
echo ""
echo "Provider: Anthropic / Claude"
echo "Target volume: /home/app/.claude"
echo ""
echo "Instructions:"
echo "1. We will launch Claude Code in login mode."
echo "2. Follow the on-screen instructions."
echo "3. Once you complete the login and see 'Success', type 'exit' or close this window."
echo "=========================================================="
echo ""

claude login

echo ""
echo "=========================================================="
echo "Login process finished."
echo "You can now safely type 'exit' to close this terminal."
echo "=========================================================="

exec bash
