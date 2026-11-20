"""Manual check: shows tastytrade's raw answers (status + start of body), never the keys."""
import os
import requests

H = {"User-Agent": "trigger-alerts/1.0"}
API = "https://api.tastyworks.com"
r = requests.post(f"{API}/oauth/token", headers=H, timeout=10, json={
    "grant_type": "refresh_token", "refresh_token": os.environ["TT_REFRESH_TOKEN"],
    "client_id": os.environ["TT_CLIENT_ID"], "client_secret": os.environ["TT_CLIENT_SECRET"], "scope": "read"})
print("sign-in:", r.status_code, "scope:", r.json().get("scope") if r.ok else r.text[:300])
h = {**H, "Authorization": f"Bearer {r.json()['access_token']}"}
for name, params in (("stock", {"equity": "CRWV"}), ("option", {"equity-option": "CRWV  270115P00055000"})):
    q = requests.get(f"{API}/market-data/by-type", params=params, headers=h, timeout=10)
    print(f"{name} quote:", q.status_code, q.headers.get("content-type"), q.text[:300])
