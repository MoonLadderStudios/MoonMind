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
    openapi_json = json.dumps(app.openapi(), indent=2)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as f:
            f.write(openapi_json)
    else:
        print(openapi_json)

if __name__ == "__main__":
    main()
