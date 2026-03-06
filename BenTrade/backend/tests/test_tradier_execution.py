import requests
import json
import os

TOKEN = os.getenv("TRADIER_API_KEY_PAPER", "aXqnJPB73yHBBHiQgoAAdOfjtAtk")
ACCT = os.getenv("TRADIER_ACCOUNT_ID_PAPER", "VA74095461")

url = f"https://sandbox.tradier.com/v1/accounts/{ACCT}/orders"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json"
}

payload = {
    "class": "multileg",
    "symbol": "QQQ",
    "type": "credit",
    "duration": "day",
    "price": "0.10",
    "side[0]": "buy_to_open",
    "option_symbol[0]": "QQQ260320P00600000",
    "quantity[0]": "1",
    "side[1]": "sell_to_open",
    "option_symbol[1]": "QQQ260320P00590000",
    "quantity[1]": "1"
}

print("Submitting payload (form-encoded via data=):")
print(json.dumps(payload, indent=2))
print()

r = requests.post(url, headers=headers, data=payload)

print("Status:", r.status_code)
print("Response:")
print(r.text)
