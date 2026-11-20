"""Manual check (Test tastytrade quote workflow): shows tastytrade's answers, never the keys.
"quote level" must say something other than "demo" before live quotes work."""
import os
import requests

H = {"User-Agent": "trigger-alerts/1.0"}
API = "https://api.tastyworks.com"
r = requests.post(f"{API}/oauth/token", headers=H, timeout=10, json={
    "grant_type": "refresh_token", "refresh_token": os.environ["TT_REFRESH_TOKEN"],
    "client_id": os.environ["TT_CLIENT_ID"], "client_secret": os.environ["TT_CLIENT_SECRET"], "scope": "read"})
print("sign-in:", r.status_code, "" if r.ok else r.text[:300])
h = {**H, "Authorization": f"Bearer {r.json()['access_token']}"}
a = requests.get(f"{API}/api-quote-tokens", headers=h, timeout=10)
print("quote level:", a.json().get("data", {}).get("level") if a.ok else a.text[:200])
q = requests.get(f"{API}/market-data/by-type", params={"equity-option": "CRWV  270115P00055000"}, headers=h, timeout=10)
print("option quote:", q.status_code, q.text[:300])
