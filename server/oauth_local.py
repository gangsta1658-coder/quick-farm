#!/usr/bin/env python3
"""Local AWS Quick OAuth (PKCE) implementation.

Ported from the quick2api gateway's oauth-client so the registration
backend can mint gateway credential JSONs by itself, without depending
on an external gateway instance.

Protocol:
  1. build_discovery_url() -> browser opens it, account is identified,
     browser lands on http://localhost:18080/callback?nonce&account&
     issuerUrl&tenantUrl&idcInstanceId&idcRegion
  2. start_flow(discovery_callback_url) -> registers a fresh OIDC public
     client (90d secret), builds the authorize URL (PKCE S256)
  3. Browser opens authUrl, user consents, browser lands on
     http://127.0.0.1:<port>/?code&state
  4. complete_flow(flow_id, callback_url) -> exchanges the code for
     access/refresh tokens, returns the credential dict
  5. serialize_credential(cred) -> "oauth:<base64url(json)>" (gateway
     import format); make_export_file() wraps it in the export envelope.
"""
import base64, hashlib, http.client, json, re, secrets, ssl, time
from urllib.parse import urlparse, parse_qs

QUICK_APP_VERSION = "1.2.4660"
WS_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)+(?:plato\.ai\.aws\.dev|quick\.aws\.com|"
    r"quicksight\.aws\.amazon\.com)$", re.I)
REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d+$")

_flows = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


# ---------------------------------------------------------------- HTTP

def https_post_json(url, body, proxy_url=None, timeout=60):
    """POST JSON via an optional HTTP proxy (CONNECT tunnel). Stdlib only."""
    u = urlparse(url)
    payload = json.dumps(body).encode()
    headers = {"Content-Type": "application/json",
               "Content-Length": str(len(payload))}
    ctx = ssl.create_default_context()
    if proxy_url:
        p = urlparse(proxy_url)
        conn = http.client.HTTPSConnection(
            p.hostname, p.port or 80, timeout=timeout, context=ctx)
        if p.username:
            import base64 as b64
            auth = b64.b64encode(
                f"{p.username}:{p.password or ''}".encode()).decode()
            conn.set_tunnel(u.hostname, u.port or 443,
                            headers={"Proxy-Authorization": "Basic " + auth})
        else:
            conn.set_tunnel(u.hostname, u.port or 443)
    else:
        conn = http.client.HTTPSConnection(
            u.hostname, u.port or 443, timeout=timeout, context=ctx)
    path = u.path + ("?" + u.query if u.query else "")
    conn.request("POST", path, body=payload, headers=headers)
    resp = conn.getresponse()
    text = resp.read().decode("utf-8", "replace")
    conn.close()
    if resp.status >= 400:
        raise RuntimeError(f"POST {u.hostname}{u.path} -> {resp.status}: "
                           f"{text[:240]}")
    return json.loads(text)


# ------------------------------------------------------------ discovery

def build_discovery_url(port=18080):
    nonce = _b64url(secrets.token_bytes(24))
    surface = f"desktop:{nonce}:{port}:{QUICK_APP_VERSION}"
    from urllib.parse import quote
    return nonce, "https://quick.aws.com/sn?qwSurface=" + quote(surface, safe="")


def sanitize_ws_host(raw):
    host = re.sub(r"^https?://", "", raw.strip(), flags=re.I)
    host = host.split("/")[0].replace(":443", "").strip()
    if not host or not WS_HOST_RE.match(host):
        raise ValueError(f"Invalid AWS Quick host: {host!r}")
    return host


def parse_discovery_callback(raw_url):
    parsed = urlparse(str(raw_url).strip())
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    idc_instance = q.get("idcInstanceId", "").strip()
    issuer = q.get("issuerUrl", "").strip() or (
        f"https://identitycenter.amazonaws.com/{idc_instance}"
        if idc_instance else "")
    tenant = q.get("tenantUrl", "").strip() or q.get("wsHost", "").strip()
    ws_host = sanitize_ws_host(tenant) if tenant else ""
    region = q.get("idcRegion", "").strip() or q.get("region", "").strip() \
        or "us-east-1"
    account = q.get("account", "").strip() or None
    if not issuer or not ws_host:
        raise ValueError("Discovery callback missing issuerUrl or tenantUrl")
    return {"issuerUrl": issuer, "wsHost": ws_host, "region": region,
            "accountId": account}


# ---------------------------------------------------------------- flow

def oidc_base(region):
    if not REGION_RE.match(region.strip()):
        raise ValueError(f"Invalid AWS region: {region}")
    return f"https://oidc.{region.strip()}.amazonaws.com"


def start_flow(discovery_callback_url=None, issuer_url=None, ws_host=None,
               region=None, account_id=None, callback_port=18080,
               proxy_url=None):
    """Register an OIDC client and build the authorize URL.

    The gateway binds a local listener for the callback; here the caller
    (browser driver) captures the redirect itself, so callback_port only
    shapes the redirect_uri. Port 0 in the gateway meant "ephemeral";
    for a headless driver any fixed port works because nothing needs to
    listen: the navigation is observed, not served.
    """
    disc = parse_discovery_callback(discovery_callback_url) \
        if discovery_callback_url else {}
    region = (region or disc.get("region") or "us-east-1").strip()
    issuer = (issuer_url or disc.get("issuerUrl") or "").strip()
    ws_raw = (ws_host or disc.get("wsHost") or "").strip()
    account = (account_id or disc.get("accountId") or "").strip() or None
    if not issuer or not ws_raw:
        raise ValueError("discovery callback URL (or issuerUrl & wsHost) "
                         "is required")
    ws = sanitize_ws_host(ws_raw)
    redirect_uri = f"http://127.0.0.1:{callback_port}"

    reg = https_post_json(oidc_base(region) + "/client/register", {
        "clientName": "AmazonQuick-Desktop",
        "clientType": "public",
        "scopes": ["quicksight:read_write"],
        "grantTypes": ["authorization_code", "refresh_token"],
        "redirectUris": [redirect_uri],
        "issuerUrl": issuer,
    }, proxy_url=proxy_url)
    if not reg.get("clientId") or not reg.get("clientSecret"):
        raise RuntimeError("client/register returned no clientId/clientSecret")

    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(32))
    flow_id = "oauth_" + secrets.token_hex(6)
    from urllib.parse import urlencode
    auth_url = oidc_base(region) + "/authorize?" + urlencode({
        "response_type": "code",
        "client_id": reg["clientId"],
        "redirect_uri": redirect_uri,
        "scopes": "quicksight:read_write",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    _flows[flow_id] = {
        "region": region, "issuerUrl": issuer, "wsHost": ws,
        "accountId": account, "clientId": reg["clientId"],
        "clientSecret": reg["clientSecret"], "redirectUri": redirect_uri,
        "codeVerifier": verifier, "state": state,
        "proxyUrl": proxy_url, "createdAt": time.time(),
    }
    return {"flowId": flow_id, "authUrl": auth_url,
            "redirectUri": redirect_uri, "issuerUrl": issuer,
            "wsHost": ws, "region": region, "accountId": account}


def _email_from_id_token(id_token):
    if not id_token:
        return None
    parts = id_token.split(".")
    if len(parts) != 3:
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return None
    for k in ("email", "preferred_username", "name"):
        v = claims.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def complete_flow(flow_id, callback_url):
    flow = _flows.get(flow_id)
    if not flow:
        raise ValueError(f"Unknown or expired flow: {flow_id}")
    q = {k: v[0] for k, v in parse_qs(urlparse(callback_url).query).items()}
    code = q.get("code")
    if not code:
        raise ValueError("Callback URL has no code")
    if q.get("state") and q["state"] != flow["state"]:
        raise ValueError("OAuth state mismatch")
    data = https_post_json(oidc_base(flow["region"]) + "/token", {
        "clientId": flow["clientId"],
        "clientSecret": flow["clientSecret"],
        "grantType": "authorization_code",
        "code": code,
        "redirectUri": flow["redirectUri"],
        "codeVerifier": flow["codeVerifier"],
    }, proxy_url=flow.get("proxyUrl"))
    if not data.get("accessToken") or not data.get("refreshToken"):
        raise RuntimeError("Token response missing accessToken/refreshToken")
    cred = {
        "region": flow["region"],
        "issuerUrl": flow["issuerUrl"],
        "wsHost": flow["wsHost"],
        "clientId": flow["clientId"],
        "clientSecret": flow["clientSecret"],
        "refreshToken": data["refreshToken"],
        "accessToken": data["accessToken"],
        "expiresAt": int(time.time() * 1000)
                     + max(300, int(data.get("expiresIn") or 3600)) * 1000,
    }
    if flow.get("accountId"):
        cred["accountId"] = flow["accountId"]
    email = _email_from_id_token(data.get("idToken"))
    if email:
        cred["email"] = email
    _flows.pop(flow_id, None)
    return cred


def refresh_credential(cred, proxy_url=None):
    """Exchange a refresh token for a fresh access token (rotation-safe:
    returns the possibly-rotated refresh token too)."""
    data = https_post_json(oidc_base(cred["region"]) + "/token", {
        "clientId": cred["clientId"],
        "clientSecret": cred["clientSecret"],
        "grantType": "refresh_token",
        "refreshToken": cred["refreshToken"],
    }, proxy_url=proxy_url)
    if not data.get("accessToken"):
        raise RuntimeError("Refresh response missing accessToken")
    out = dict(cred)
    out["accessToken"] = data["accessToken"]
    if data.get("refreshToken"):
        out["refreshToken"] = data["refreshToken"]
    out["expiresAt"] = int(time.time() * 1000) \
        + max(300, int(data.get("expiresIn") or 3600)) * 1000
    return out


# ---------------------------------------------------------- serialization

def serialize_credential(cred):
    return "oauth:" + _b64url(json.dumps(cred, ensure_ascii=False)
                              .encode("utf-8"))


def parse_credential(raw):
    raw = raw.strip()
    if not raw.startswith("oauth:"):
        return None
    try:
        pad = raw[6:] + "=" * (-len(raw[6:]) % 4)
        obj = json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return None
    if not obj.get("clientId") or not obj.get("clientSecret") \
            or not obj.get("refreshToken") or not obj.get("wsHost"):
        return None
    return obj


def make_export_file(cred, label):
    from datetime import datetime, timezone
    return {
        "ok": True,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "version": 1,
        "credentials": [{
            "apiKey": serialize_credential(cred),
            "enabled": True,
            "label": label,
        }],
    }
