#!/usr/bin/env python3
import json
import logging
import sys
import warnings
import os
import io

# add root dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress ALL output during import to prevent logging pollution into our JSON stream
old_stdout = sys.stdout
old_stderr = sys.stderr
sys.stdout = io.StringIO()
sys.stderr = io.StringIO()

os.environ["LOG_LEVEL"] = "CRITICAL"
logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")

try:
    from api_service.main import app
finally:
    # Restore stdout/stderr so we can print the JSON
    sys.stdout = old_stdout
    sys.stderr = old_stderr

def main():
    print(json.dumps(app.openapi(), indent=2))

if __name__ == "__main__":
    main()
