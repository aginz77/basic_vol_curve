"""Manage the GitHub-email allowlist for the IV surface site.

The allowlist is a Cloudflare Zero Trust list (type EMAIL) that the Access
policy references, so changes take effect without redeploying anything.

Env vars:
    CF_API_TOKEN      API token with "Account > Zero Trust > Edit"
    CF_ACCOUNT_ID     Cloudflare account ID
    CF_ALLOWLIST_ID   list ID (printed by `create`)

Usage:
    python allowlist.py create you@example.com      # one-time; prints the list ID
    python allowlist.py list
    python allowlist.py add friend@example.com [...]
    python allowlist.py remove friend@example.com [...]
"""

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Set {name}")
    return value


def _call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {_env('CF_API_TOKEN')}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Cloudflare API error {e.code}: {e.read().decode(errors='replace')[:500]}")
    if not payload.get("success", False):
        sys.exit(f"Cloudflare API error: {payload.get('errors')}")
    return payload


def _list_path() -> str:
    return f"/accounts/{_env('CF_ACCOUNT_ID')}/gateway/lists/{_env('CF_ALLOWLIST_ID')}"


def _clean(emails: list[str]) -> list[str]:
    out = [e.strip().lower() for e in emails if e.strip()]
    bad = [e for e in out if "@" not in e]
    if bad:
        sys.exit(f"Not email addresses: {', '.join(bad)}")
    return out


def create(emails):
    payload = _call("POST", f"/accounts/{_env('CF_ACCOUNT_ID')}/gateway/lists", {
        "name": "ivsurface-allowlist", "type": "EMAIL",
        "description": "GitHub emails allowed into the IV surface site",
        "items": [{"value": e} for e in _clean(emails)]})
    print(f"Created list. Set CF_ALLOWLIST_ID={payload['result']['id']}")


def show():
    result = _call("GET", f"{_list_path()}/items")["result"]
    # The API nests items one level deep ([[...]]); flatten either shape.
    items = [i for group in result for i in (group if isinstance(group, list) else [group])]
    for e in sorted(i["value"] for i in items):
        print(e)
    print(f"({len(items)} allowed)")


def add(emails):
    _call("PATCH", _list_path(), {"append": [{"value": e} for e in _clean(emails)]})
    print(f"Added: {', '.join(_clean(emails))}")


def remove(emails):
    _call("PATCH", _list_path(), {"remove": _clean(emails)})
    print(f"Removed: {', '.join(_clean(emails))}")


if __name__ == "__main__":
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if cmd == "list":
        show()
    elif cmd in ("add", "remove", "create") and args:
        {"add": add, "remove": remove, "create": create}[cmd](args)
    else:
        sys.exit(__doc__)
