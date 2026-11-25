#!/usr/bin/env python3
"""Backend config: a single JSON file (mode 600) next to this package.

Secrets (admin token) live only in this file.
GET /api/config masks them; PUT accepts new values.
"""
import json, os, secrets

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.json")

DEFAULTS = {
    "admin_token": "",
    "outlook": {
        # Token / URL-OTP store path relative to this config's dir.
        "token_file": "../outlook-tokens.json",
    },
    "proxy": {
        "url": "",
    },
    "register": {"batch_interval_s": 300},
}


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_proxy(cfg):
    px = cfg.get("proxy")
    if not isinstance(px, dict):
        cfg["proxy"] = {"url": ""}
        return cfg
    url = px.get("url")
    if not url and px.get("mode") == "custom" and px.get("custom_url"):
        url = px.get("custom_url", "")
    cfg["proxy"] = {"url": str(url or "").strip()}
    return cfg


def load_config():
    cfg = _deep_merge(DEFAULTS, {})
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = _deep_merge(cfg, json.load(f))
    cfg.pop("imap", None)
    _normalize_proxy(cfg)
    if not cfg.get("admin_token"):
        cfg["admin_token"] = secrets.token_urlsafe(24)
        save_config(cfg)
    return cfg


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


def public_view(cfg):
    """Config with secrets masked for API responses."""
    out = json.loads(json.dumps(cfg))
    out["admin_token"] = "***" if cfg.get("admin_token") else ""
    return out


def apply_update(cfg, patch):
    """Merge a PUT body into cfg; '***' means 'keep existing secret'."""
    if patch.get("admin_token") in (None, "***"):
        patch.pop("admin_token", None)
    merged = _deep_merge(cfg, patch)
    merged.pop("imap", None)
    _normalize_proxy(merged)
    save_config(merged)
    return merged
