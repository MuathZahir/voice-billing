import requests
import json
import time

BASE_URL = "http://127.0.0.1:5001" # Your Flask app's local URL

test_cases = [
    {
        "name": "Valid Text Transfer",
        "payload": { # Copy the JSON payload structure from curl example 1
          "object": "whatsapp_business_account", # ... etc ...
           "entry": [ { "changes": [ { "value": {
                "messaging_product": "whatsapp",
                "metadata": { "phone_number_id": "1234567890" },
                "contacts": [ { "wa_id": "962790000001" } ],
                "messages": [ {
                    "from": "962790000001", "id": "wamid.PYTEST_1", "timestamp": str(int(time.time())),
                    "text": { "body": "تحويل 350 دينار من فرع الصويفية الى فرع السلالم" }, "type": "text"
                  } ] }, "field": "messages" } ] } ]
        }
        # Optional: Add expected console output snippets or DB state checks here
    },
    # Add more test cases...
]

print("Starting tests...")

for case in test_cases:
    print(f"\n--- Running Test Case: {case['name']} ---")
    try:
        response = requests.post(f"{BASE_URL}/webhook", json=case['payload'], timeout=30) # Increased timeout for NLU
        response.raise_for_status() # Check for HTTP errors 4xx/5xx
        print(f"Response Status Code: {response.status_code}")
        # print(f"Response Body: {response.text}") # Usually just {"status": "received"}
        print("Test request sent successfully. Check Flask console logs and database.")

    except requests.exceptions.RequestException as e:
        print(f"!!! Test Failed: Request error - {e} !!!")
    except Exception as e:
        print(f"!!! Test Failed: Unexpected error - {e} !!!")

    time.sleep(2) # Pause slightly between tests

print("\n--- Testing Complete ---")