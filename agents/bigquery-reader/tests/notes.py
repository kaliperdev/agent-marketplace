"""The router's access note, written the way the router writes it (the format is the contract between the two repos)."""

import base64
import hashlib
import hmac
import json

SECRET = "test-note-key"
AT = 1764115200


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def payload(**over) -> dict:
    return {"v": 1, "agent": "finance", "login": "3", "project": "finance-1234",
            "datasets": ["finance-1234.ledger", "finance-1234.payroll_summary"], "at": AT, **over}


def sign(body: dict, secret: str) -> str:
    text = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    return f"{text}.{_b64(hmac.new(secret.encode(), text.encode('ascii'), hashlib.sha256).digest())}"


NOTE = sign(payload(), SECRET)
