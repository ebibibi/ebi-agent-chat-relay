"""A software WebAuthn authenticator, so passkey ceremonies run for real in tests.

It produces the same bytes a browser hands the console: a ``none`` attestation
on registration and an ES256 signature on sign-in. Nothing here is mocked on the
verifying side — ``webauthn`` checks these exactly as it checks a phone's.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_AT = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(self, *, user_verified: bool = True) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.sign_count = 0
        self.flags = FLAG_UP | (FLAG_UV if user_verified else 0)

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}
        ).encode()

    def register(self, options: dict, origin: str, rp_id: str | None = None) -> dict:
        rp_id = rp_id or options["rp"]["id"]
        attested = (
            bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id
        ) + self._cose_key()
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self.flags | FLAG_AT])
            + struct.pack(">I", self.sign_count)
            + attested
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
            },
        }

    def login(self, options: dict, origin: str, rp_id: str | None = None) -> dict:
        rp_id = rp_id or options["rpId"]
        self.sign_count += 1
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self.flags])
            + struct.pack(">I", self.sign_count)
        )
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(b"ccdb-relay-console-owner"),
            },
        }
