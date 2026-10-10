---
type: adr
id: ADR-0012
title: Sign in to the console with passkeys by default
decision: Make passkeys (WebAuthn, user verification required) the default Relay Console sign-in, gate the first passkey with a single-use setup code written to ccdb's own log, keep sessions server-side as hashes, and keep Cloudflare Access and the bearer token as optional additions.
status: accepted
date: 2026-10-10
deciders: [Masahiko Ebi, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0012: Sign in to the console with passkeys by default

## Context

ADR-0010 made every console request authenticated, through either a Cloudflare Access JWT or a
static bearer token. In an open-source project both are awkward defaults. Access needs a
Cloudflare account and a zone on Cloudflare. The token is a single shared secret: whoever holds
it is in, and the browser keeps it in local storage. Operators asked for real multi-factor
sign-in that any deployment gets without signing up for a third-party service.

## Options considered

1. **Keep Access as the recommended path and script its setup.** Still needs a Cloudflare
   account and a domain, so it cannot be the default for everyone.
2. **Built-in OpenID Connect (Google, Entra ID, …).** Strong, but each operator must register an
   OAuth client first, and whether MFA is enforced depends on the provider account's settings.
   Worth having as an option (#894), not as the default.
3. **TOTP after the token.** Adds a second factor but stays phishable, and a shared secret
   still has to be provisioned.
4. **Passkeys (WebAuthn) built in.** Chosen. Possession of the device plus a fingerprint, face or
   PIN in a single gesture; phishing-resistant because the browser signs the origin it is on;
   no external account; works on `localhost` and on any HTTPS origin (tailscale serve,
   Cloudflare Tunnel, a reverse proxy).

## Decision

- Passkeys are on unless `CCDB_CONSOLE_PASSKEYS=0`. With nothing else configured, setting
  `CCDB_CONSOLE_PORT` is enough to run the console safely.
- Registration and sign-in require **user verification**, and credentials are discoverable, so
  sign-in needs no user name. Verification uses `py_webauthn` (Duo Labs); ccdb does not parse
  WebAuthn structures itself.
- **The first passkey needs a setup code** that ccdb writes to its own log while no passkey is
  registered. Reading the server's log is the trust the operator already has, so the bootstrap
  adds no new secret to distribute. Codes are single use and expire after 15 minutes. Wrong guesses
  do not burn them (that would let anyone break the owner's code); the ~50-bit code and the
  rate limit bound guessing instead. Further devices need a code created by someone already signed in.
  `CCDB_CONSOLE_ENROLL=1` prints a fresh code on start for recovery.
- A sign-in creates a **server-side session**: the cookie is `HttpOnly`, `SameSite=Strict`,
  `Secure` on https, and only its SHA-256 is stored. Removing a passkey ends every session it
  opened. The last passkey cannot be removed unless another way in is configured.
- Pre-sign-in routes (`/console/api/auth/*`) still need `X-Console-Request: 1`. The routes
  that check a setup code share one global rate limit, because behind a tunnel every caller
  arrives from loopback. Passkey sign-in is deliberately not rate limited and evicts the oldest
  pending ceremony instead of refusing: a signature cannot be guessed, and a shared limit there
  would let anyone lock the owner out. Only one logged setup code is live at a time.
- The expected origin comes from `CCDB_CONSOLE_ORIGIN` when set, otherwise from the request's
  `Host` (https unless the host is loopback, or `X-Forwarded-Proto`). Trusting `Host` only picks
  which origin to expect: the browser signs the origin it is really on and a passkey is bound to
  its own site, so a forged header gets nothing a browser would sign.
- Cloudflare Access and the bearer token keep working unchanged. If the `webauthn` package is
  missing but another way in is configured, passkeys are switched off with a warning rather than
  taking a working deployment down.

## Consequences

- New deployments get phishing-resistant MFA with no account anywhere.
- WebAuthn refuses IP addresses as a site, so the console must be opened by host name
  (`http://localhost:8100`, not `http://127.0.0.1:8100`).
- A passkey is bound to one host name. Moving the console to a new name means registering again
  there (sign in with the old name or a setup code).
- Sessions live in the session database; a copy of it cannot be replayed as a login.
