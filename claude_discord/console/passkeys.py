"""Passkey (WebAuthn) sign-in: the default way into the console.

A passkey is two factors in one gesture — the device that holds the private key,
and the fingerprint, face or PIN that unlocks it — and it cannot be phished: the
browser signs the origin it is actually on, so a look-alike site gets nothing
it can replay here. It also needs no third-party account, which is why it is the
default rather than an identity provider.

Who may register a passkey is the part that needs care:

* the **first** one needs a *setup code*. ccdb writes it to its own log while no
  passkey exists, so being able to read the server's log is what makes you the
  owner — the same trust the operator already has.
* every **further** device needs a code created by someone already signed in.

Codes are single use, expire after ``CODE_TTL`` and burn after
``MAX_CODE_ATTEMPTS`` wrong guesses. Challenges live in memory: a ceremony
interrupted by a restart is simply started again.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .passkey_store import Passkey, PasskeyStore

logger = logging.getLogger(__name__)

CODE_TTL = 15 * 60.0
CHALLENGE_TTL = 5 * 60.0
MAX_CODE_ATTEMPTS = 10
MAX_PENDING_CEREMONIES = 256
RP_NAME = "Relay Console"
#: Every passkey belongs to the one owner of this console. A stable handle lets
#: an authenticator replace its own earlier passkey instead of piling up copies.
OWNER_HANDLE = b"ccdb-relay-console-owner"
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


class PasskeyError(Exception):
    """A ceremony that must not succeed. The message is safe to show."""


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _normalise_code(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


@dataclass
class _Code:
    value: str
    expires: float
    attempts: int = 0
    logged: bool = False


@dataclass(frozen=True)
class _Ceremony:
    kind: str  # "register" | "login"
    challenge: bytes
    origin: str
    rp_id: str
    expires: float
    code: str | None = None


def origin_parts(origin: str) -> tuple[str, str]:
    """``(origin, rp_id)`` for an origin such as ``https://host:8443``."""
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PasskeyError(f"not an origin: {origin!r}")
    return f"{parts.scheme}://{parts.netloc}", parts.hostname


class PasskeyService:
    """Registration and sign-in ceremonies, plus the setup codes that gate them."""

    def __init__(self, store: PasskeyStore, *, clock: Any = time.monotonic) -> None:
        self.store = store
        self._clock = clock
        self._codes: list[_Code] = []
        self._ceremonies: dict[str, _Ceremony] = {}

    # -- setup codes -------------------------------------------------------
    def issue_code(self, *, logged: bool = False) -> str:
        """A fresh single-use code that lets one more device register.

        A *logged* code replaces the previous logged one, so asking for setup
        codes again and again never leaves more than one of them live.
        """
        self._prune()
        if logged:
            self._codes = [c for c in self._codes if not c.logged]
        code = _Code(value=_new_code(), expires=self._clock() + CODE_TTL, logged=logged)
        self._codes.append(code)
        return code.value

    async def open_enrollment(self, *, force: bool = False) -> str | None:
        """Log a setup code when no passkey exists yet (or when *force*)."""
        if not force and await self.store.count() > 0:
            return None
        code = self.issue_code(logged=True)
        logger.warning(
            "Relay Console setup code: %s — open the console and enter it to register a "
            "passkey (single use, valid %d minutes)",
            code,
            int(CODE_TTL // 60),
        )
        return code

    def _check_code(self, supplied: str) -> str:
        self._prune()
        wanted = _normalise_code(supplied)
        for code in self._codes:
            if hmac.compare_digest(_normalise_code(code.value).encode(), wanted.encode()):
                return code.value
        # A wrong guess counts against every live code, so guessing is bounded
        # no matter which code the guesser is aiming at.
        for code in self._codes:
            code.attempts += 1
        self._codes = [c for c in self._codes if c.attempts < MAX_CODE_ATTEMPTS]
        raise PasskeyError("that setup code is not valid (or has expired)")

    def _consume_code(self, value: str) -> None:
        before = len(self._codes)
        self._codes = [c for c in self._codes if c.value != value]
        if len(self._codes) == before:
            raise PasskeyError("that setup code has already been used")

    def _prune(self) -> None:
        now = self._clock()
        self._codes = [c for c in self._codes if c.expires > now]
        self._ceremonies = {k: v for k, v in self._ceremonies.items() if v.expires > now}

    def _remember(self, ceremony: _Ceremony) -> str:
        self._prune()
        # Evict rather than refuse: refusing would let anyone keep the table
        # full and lock the owner out. Dicts keep insertion order, oldest first.
        while len(self._ceremonies) >= MAX_PENDING_CEREMONIES:
            del self._ceremonies[next(iter(self._ceremonies))]
        ticket = secrets.token_urlsafe(18)
        self._ceremonies[ticket] = ceremony
        return ticket

    def _take(self, ticket: str, kind: str) -> _Ceremony:
        self._prune()
        ceremony = self._ceremonies.pop(ticket, None)
        if ceremony is None or ceremony.kind != kind:
            raise PasskeyError("this sign-in expired, start again")
        return ceremony

    # -- registration ------------------------------------------------------
    async def registration_options(
        self, origin: str, *, signed_in: bool, code: str | None
    ) -> dict[str, Any]:
        import webauthn
        from webauthn.helpers.structs import (
            AuthenticatorSelectionCriteria,
            PublicKeyCredentialDescriptor,
            ResidentKeyRequirement,
            UserVerificationRequirement,
        )

        origin, rp_id = origin_parts(origin)
        accepted_code = None
        if not signed_in:
            accepted_code = self._check_code(code or "")
        existing = [p for p in await self.store.list_all() if p.rp_id == rp_id]
        options = webauthn.generate_registration_options(
            rp_id=rp_id,
            rp_name=RP_NAME,
            user_id=OWNER_HANDLE,
            user_name="owner",
            user_display_name="Relay Console owner",
            # Discoverable, so signing in needs no user name; user verification
            # required, so the passkey is a second factor and not just a key.
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=p.credential_id) for p in existing
            ],
        )
        ticket = self._remember(
            _Ceremony(
                kind="register",
                challenge=options.challenge,
                origin=origin,
                rp_id=rp_id,
                expires=self._clock() + CHALLENGE_TTL,
                code=accepted_code,
            )
        )
        return {"ticket": ticket, "options": json.loads(webauthn.options_to_json(options))}

    async def finish_registration(
        self, ticket: str, credential: dict[str, Any], name: str
    ) -> Passkey:
        import webauthn

        ceremony = self._take(ticket, "register")
        try:
            verified = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=ceremony.challenge,
                expected_rp_id=ceremony.rp_id,
                expected_origin=ceremony.origin,
                require_user_verification=True,
            )
        except Exception as exc:  # the library raises several types; all mean no
            logger.info("console: passkey registration rejected: %s", exc)
            raise PasskeyError("the passkey could not be verified") from exc
        if ceremony.code is not None:
            self._consume_code(ceremony.code)
        passkey = await self.store.add(
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            rp_id=ceremony.rp_id,
            name=name,
        )
        logger.info("console: passkey %s (%s) registered", passkey.id, passkey.name)
        return passkey

    # -- sign-in -----------------------------------------------------------
    def login_options(self, origin: str) -> dict[str, Any]:
        import webauthn
        from webauthn.helpers.structs import UserVerificationRequirement

        origin, rp_id = origin_parts(origin)
        options = webauthn.generate_authentication_options(
            rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED
        )
        ticket = self._remember(
            _Ceremony(
                kind="login",
                challenge=options.challenge,
                origin=origin,
                rp_id=rp_id,
                expires=self._clock() + CHALLENGE_TTL,
            )
        )
        return {"ticket": ticket, "options": json.loads(webauthn.options_to_json(options))}

    async def finish_login(self, ticket: str, credential: dict[str, Any]) -> Passkey:
        import webauthn
        from webauthn.helpers import base64url_to_bytes

        ceremony = self._take(ticket, "login")
        try:
            raw_id = base64url_to_bytes(str(credential.get("rawId") or credential.get("id")))
        except Exception as exc:
            raise PasskeyError("malformed credential") from exc
        passkey = await self.store.by_credential_id(raw_id)
        if passkey is None or passkey.rp_id != ceremony.rp_id:
            raise PasskeyError("this passkey is not registered here")
        try:
            verified = webauthn.verify_authentication_response(
                credential=credential,
                expected_challenge=ceremony.challenge,
                expected_rp_id=ceremony.rp_id,
                expected_origin=ceremony.origin,
                credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:
            logger.info("console: passkey sign-in rejected: %s", exc)
            raise PasskeyError("the passkey could not be verified") from exc
        await self.store.record_use(passkey.id, verified.new_sign_count)
        return passkey
