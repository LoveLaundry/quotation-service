import requests
import json

BASE_URL = "http://localhost:8000"

print("🧪 Testing Quotation Service API\n")

print("1️⃣ Testing root endpoint...")
response = requests.get(f"{BASE_URL}/")
print(f"   Status: {response.status_code}")
print(f"   Response: {response.json()}\n")

print("2️⃣ Creating a test quotation...")
test_quotation = {
    "client_name": "Nilawin Hotel",
    "quotation_title": "2026 Price List",
    "line_items": [
        {
            "item_name": "Bed Sheet",
            "category": "Bed Linen",
            "unit_price": 125.00,
            "notes": "(S, D)"
        },
        {
            "item_name": "Bath Towel",
            "category": "Towels",
            "unit_price": 75.00,
            "notes": None
        },
        {
            "item_name": "Pillow Case",
            "category": "Bed Linen",
            "unit_price": 50.00,
            "notes": "White"
        }
    ],
    "status": "draft"
}

response = requests.post(f"{BASE_URL}/quotations", json=test_quotation)
print(f"   Status: {response.status_code}")
if response.status_code == 201:
    created = response.json()
    quotation_id = created["id"]
    print(f"   Created quotation ID: {quotation_id}")
    print(f"   Client: {created['client_name']}")
    print(f"   Line Items: {len(created['line_items'])}")
    print(f"   Status: {created['status']}\n")
else:
    print(f"   Error: {response.text}\n")
    exit(1)

print("3️⃣ Fetching all quotations...")
response = requests.get(f"{BASE_URL}/quotations")
print(f"   Status: {response.status_code}")
quotations = response.json()
print(f"   Total quotations: {len(quotations)}\n")

print("4️⃣ Fetching single quotation...")
response = requests.get(f"{BASE_URL}/quotations/{quotation_id}")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    quotation = response.json()
    print(f"   Client: {quotation['client_name']}")
    print(f"   Line Items:")
    for item in quotation['line_items']:
        print(f"     - {item['item_name']}: LKR {item['unit_price']}")
    print()

print("5️⃣ Updating quotation...")
update_data = {
    "quotation_title": "Updated 2026 Price List",
    "status": "sent"
}
response = requests.put(f"{BASE_URL}/quotations/{quotation_id}", json=update_data)
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    updated = response.json()
    print(f"   New title: {updated['quotation_title']}")
    print(f"   New status: {updated['status']}\n")

print("6️⃣ Deleting quotation...")
response = requests.delete(f"{BASE_URL}/quotations/{quotation_id}")
print(f"   Status: {response.status_code}")
print(f"   Response: {response.json()}\n")

print("✅ All tests completed!")
