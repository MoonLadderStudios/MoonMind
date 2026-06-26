#!/bin/bash
set -e

echo "Verifying @google/gemini-cli installation..."

# Check if npm is available
if command -v npm >/dev/null 2>&1; then
    echo "npm is available. Checking via npm list..."
    if npm list -g @google/gemini-cli > /dev/null 2>&1; then
        echo "@google/gemini-cli is installed globally (verified via npm)."
    else
        echo "Warning: npm list failed to find @google/gemini-cli (possibly due to prefix mismatch or missing npm)."
    fi
else
    echo "npm is NOT available in PATH."
fi

# Check if the binary is executable and works
echo "Checking gemini CLI version..."
if command -v gemini >/dev/null 2>&1; then
    gemini --version
    echo "gemini CLI is executable and operational."
else
    echo "Error: gemini command not found in PATH."
    exit 1
fi

# Check if the package files exist in node_modules
echo "Checking node_modules..."
if [ -d "/usr/local/lib/node_modules/@google/gemini-cli" ]; then
    echo "@google/gemini-cli directory exists in /usr/local/lib/node_modules."
else
    echo "Error: @google/gemini-cli directory missing from /usr/local/lib/node_modules."
    exit 1
fi

exit 0
