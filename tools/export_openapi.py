#!/usr/bin/env python3
import json
import logging
import sys
import warnings
import os

# Optionally disable logging and warnings to reduce noise during schema export.
# Set EXPORT_OPENAPI_QUIET=1 (or "true"/"yes") to enable suppression.
if os.getenv("EXPORT_OPENAPI_QUIET", "").lower() in ("1", "true", "yes"):
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")

# add root dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_service.main import app

def main():
    print(json.dumps(app.openapi(), indent=2))

if __name__ == "__main__":
    main()
