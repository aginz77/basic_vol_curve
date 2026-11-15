"""Cloudflare Access verification.

Cloudflare Access (GitHub login + email allowlist) sits in front of the app and
adds a signed JWT to every request it lets through. This module verifies that
JWT, so requests that bypass Cloudflare (or forge the header) are rejected.

Env vars (set on the server):
    CF_ACCESS_TEAM_DOMAIN  e.g. "yourteam.cloudflareaccess.com"
    CF_ACCESS_AUD          the Access application's "Application Audience (AUD) Tag"

Fails closed: if these are not set, every request is refused, unless the app
config has AUTH_DISABLED=True (only set by the local dev server in app.py).
"""

import os

import jwt
from flask import abort, current_app, g, request

_jwks_client = None


def _jwks(team_domain: str) -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs",
                                       cache_keys=True, lifespan=3600)
    return _jwks_client


def verify_access():
    """Flask before_request hook: require a valid Cloudflare Access JWT."""
    if current_app.config.get("AUTH_DISABLED"):
        g.user_email = None
        return
    team = os.environ.get("CF_ACCESS_TEAM_DOMAIN")
    aud = os.environ.get("CF_ACCESS_AUD")
    if not team or not aud:
        abort(500, "Auth is not configured (CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD).")

    token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
    if not token:
        abort(403)
    try:
        key = _jwks(team).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=aud,
                            issuer=f"https://{team}")
    except jwt.PyJWTError:
        abort(403)
    g.user_email = claims.get("email")
