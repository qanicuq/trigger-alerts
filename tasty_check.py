"""Manual check: shows tastytrade's raw answers (status + start of body), never the keys."""
import os
import requests

H = {"User-Agent": "trigger-alerts/1.0"}
API = "https://api.tastyworks.com"
for scope in ("read", None):
    body = {"grant_type": "refresh_token", "refresh_token": os.environ["TT_REFRESH_TOKEN"],
            "client_id": os.environ["TT_CLIENT_ID"], "client_secret": os.environ["TT_CLIENT_SECRET"]}
    if scope:
        body["scope"] = scope
    r = requests.post(f"{API}/oauth/token", headers=H, timeout=10, json=body)
    print(f"[scope={scope}] sign-in:", r.status_code, "" if r.ok else r.text[:300])
    if not r.ok:
        continue
    h = {**H, "Authorization": f"Bearer {r.json()['access_token']}"}
    q = requests.get(f"{API}/market-data/by-type", params={"equity": "CRWV"}, headers=h, timeout=10)
    print(f"[scope={scope}] stock quote:", q.status_code, q.text[:200])
    a = requests.get(f"{API}/api-quote-tokens", headers=h, timeout=10)
    print(f"[scope={scope}] quote-token:", a.status_code, (a.json().get("data", {}).get("level") if a.ok else a.text[:200]))
