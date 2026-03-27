#!/usr/bin/env python3
import json
import logging
import sys
import warnings
import os

# Disable logging and warnings
logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")

# add root dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_service.main import app

def main():
    print(json.dumps(app.openapi(), indent=2))

if __name__ == "__main__":
    main()
