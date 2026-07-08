#!/usr/bin/env python3
import sys
from pathlib import Path
from private_config import HA_URL, HA_TOKEN
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
import json

# Replace with your actual Home Assistant URL and token
HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

response = requests.get(f"{HA_URL}/api/states", headers=HEADERS)
entities = response.json()

# Find Wyoming-related entities
print("\nWyoming-related entities:")
for entity in entities:
    entity_id = entity.get('entity_id', '')
    if "wyoming" in entity_id.lower() or "pi_wyoming" in entity_id.lower():
        print(f"Entity ID: {entity_id}")
        print(f"  State: {entity.get('state', '')}")
        print(f"  Attributes: {json.dumps(entity.get('attributes', {}), indent=2)}")
        print()
