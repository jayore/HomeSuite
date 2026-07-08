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

response = requests.get(f"{HA_URL}/api/services", headers=HEADERS)
services = response.json()

# Find Wyoming-related services
print("\nWyoming-related services:")
for domain_info in services:
    domain = domain_info.get('domain', '')
    if "wyoming" in domain.lower():
        print(f"Domain: {domain}")
        services_dict = domain_info.get('services', {})
        for service_name, service_info in services_dict.items():
            print(f"  Service: {service_name}")
            print(f"    Description: {service_info.get('description', 'No description')}")
            print(f"    Fields: {json.dumps(service_info.get('fields', {}), indent=2)}")
            print()
