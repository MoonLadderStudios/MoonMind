#!/usr/bin/env bash
set -e

# Clear conflicting API keys
unset GEMINI_API_KEY
unset GOOGLE_API_KEY

echo "=========================================================="
echo "      MoonMind - Gemini CLI OAuth Login Session"
echo "=========================================================="
echo ""
echo "Provider: Google / Gemini"
echo "Target volume: /var/lib/gemini-auth"
echo ""
echo "Instructions:"
echo "1. We will launch the Gemini CLI in login mode."
echo "2. Follow the on-screen instructions."
echo "3. Once you complete the login and see 'Success', type 'exit' or close this window."
echo "=========================================================="
echo ""

# The actual login command will go here. Assuming it is something like `gemini login` or similar.
# Since we might be running inside a docker container that has gemini cli installed.
gemini login

echo ""
echo "=========================================================="
echo "Login process finished."
echo "You can now safely type 'exit' to close this terminal."
echo "=========================================================="

# Drop into a shell to let the user review before exiting, or just give them a prompt.
exec bash
