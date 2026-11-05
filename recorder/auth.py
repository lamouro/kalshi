"""Kalshi RSA-PSS request signing (ported from razorbillaws/kalshi.py).
Key id and key path come from env: KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH. Never commit keys."""
from __future__ import annotations

import base64
import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class Signer:
    def __init__(self, key_id: str | None = None, key_path: str | None = None):
        self.key_id = key_id or os.environ["KALSHI_KEY_ID"]
        path = key_path or os.environ["KALSHI_PRIVATE_KEY_PATH"]
        with open(path, "rb") as f:
            self.key = serialization.load_pem_private_key(f.read(), password=None)

    def headers(self, method: str, path: str) -> dict:
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path.split("?")[0]).encode()
        sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                             salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}
