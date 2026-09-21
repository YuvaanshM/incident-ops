"""Send one synthetic normalized alert with the Phase 8 HMAC contract."""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

PATH = "/api/v1/alerts/webhook"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "alerts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=("firing", "update", "resolved"))
    parser.add_argument("--url", default=f"http://127.0.0.1:8000{PATH}")
    parser.add_argument("--idempotency-key", default=str(uuid4()))
    parser.add_argument("--started-at", help="Override the occurrence RFC 3339 timestamp")
    args = parser.parse_args()
    secret = os.environ.get("ALERT_SIGNING_SECRET")
    key_id = os.environ.get("ALERT_KEY_ID", "synthetic-key")
    if not secret:
        parser.error("ALERT_SIGNING_SECRET must be set in the environment")
    if not args.url.endswith(PATH):
        parser.error(f"URL must end with {PATH}")
    UUID(args.idempotency_key)
    payload = json.loads((FIXTURES / f"{args.fixture}.json").read_text(encoding="utf-8"))
    if args.started_at:
        payload["started_at"] = args.started_at
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    stamp = str(int(time.time()))
    base = (
        f"v1\nPOST\n{PATH}\n{key_id}\n{stamp}\n{args.idempotency_key}\n".encode() + body
    )
    signature = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    request = Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Alert-Key-Id": key_id,
            "X-Alert-Timestamp": stamp,
            "Idempotency-Key": args.idempotency_key,
            "X-Alert-Signature": f"v1={signature}",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            print(response.read().decode())
            return 0
    except HTTPError as error:
        print(f"Alert intake returned HTTP {error.code}", file=sys.stderr)
        return 1
    except URLError:
        print("Alert intake is unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
