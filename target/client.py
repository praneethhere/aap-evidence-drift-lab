"""Local-only command-line client for the synthetic target; never prints a token."""
import argparse
import json
import os
import urllib.error
import urllib.request


def request(base: str, token: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": "Bearer " + token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as err:
        return err.code, json.load(err)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["state", "reset", "inject-healthy", "inject-degraded"])
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    token = os.environ.get("EDLAB_TOKEN", "")
    if not token:
        ap.error("EDLAB_TOKEN is not set")
    commands = {
        "state": ("/v1/state", None),
        "reset": ("/v1/reset", {}),
        "inject-healthy": ("/v1/inject", {"status": "healthy"}),
        "inject-degraded": ("/v1/inject", {"status": "degraded"}),
    }
    endpoint, payload = commands[args.command]
    status, output = request(args.url, token, endpoint, payload)
    print(json.dumps({"http_status": status, **output}, indent=2, sort_keys=True))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
