#!/usr/bin/env python3
"""
model-fallback.py - Test backup models and switch to one with available quota.

Reads ANTHROPIC_* env vars from .claude/settings.json,
tests the current model, then iterates through BACKUP_MODELS
until one responds successfully. Updates settings.json on success.
"""

import json
import os
import sys
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Edit this list to change fallback order
# ---------------------------------------------------------------------------
BACKUP_MODELS = [
    "qwen3.5-plus-2026-04-20",
    "qwen3.6-max-preview",
]
# ---------------------------------------------------------------------------

HOME = os.path.expanduser("~")
SETTINGS_PATH = os.path.join(HOME, ".claude", "settings.json")

SYSTEM_PROMPT = "Reply with OK."
MAX_TOKENS = 5


def load_settings():
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_settings(settings):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_api_config(settings):
    """Extract API connection info from settings."""
    env = settings.get("env", {})
    return {
        "base_url": env.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        "api_key": env.get("ANTHROPIC_AUTH_TOKEN", env.get("ANTHROPIC_API_KEY", "")),
        "api_version": env.get("ANTHROPIC_API_VERSION", "2023-06-01"),
    }


def test_model(model, api_config, timeout=15):
    """
    Send a minimal messages API request to test if the model has quota.
    Returns (True, None) on success, (False, error_string) on failure.
    """
    url = f"{api_config['base_url']}/v1/messages"
    payload = json.dumps({
        "model": model,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": MAX_TOKENS,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", api_config["api_key"])
    req.add_header("anthropic-version", api_config["api_version"])

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if resp.status == 200 and data.get("content"):
                return True, None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        # Check for quota/balance/insufficient errors
        error_lower = error_body.lower()
        if any(kw in error_lower for kw in [
            "quota", "credit", "balance", "insufficient",
            "overdue", "expired", "rate limit", "429"
        ]):
            return False, f"quota error ({e.code}): {error_body[:200]}"
        return False, f"HTTP {e.code}: {error_body[:200]}"
    except Exception as e:
        return False, f"exception: {type(e).__name__}: {e}"

    return False, "unknown failure"


def is_quota_error(model, api_config):
    """Check if the current model is experiencing a quota error."""
    ok, err = test_model(model, api_config)
    if ok:
        return False, None
    return True, err


def update_settings_model(settings, new_model):
    """只切换当前生效的模型，不覆盖 ANTHROPIC_DEFAULT_* 角色配置（保留 cc switch 设置）。"""
    env = settings.setdefault("env", {})
    env["ANTHROPIC_MODEL"] = new_model
    settings["model"] = new_model
    return settings


def main():
    settings = load_settings()
    api_config = get_api_config(settings)

    if not api_config["api_key"]:
        print("ERROR: No API key found in settings.json env.ANTHROPIC_AUTH_TOKEN")
        sys.exit(1)

    current_model = settings.get("env", {}).get("ANTHROPIC_MODEL", settings.get("model", "unknown"))
    print(f"Current model: {current_model}")
    print(f"API endpoint: {api_config['base_url']}")
    print()

    # Step 1: Confirm current model is broken
    print(f"Testing current model: {current_model}...")
    ok, err = test_model(current_model, api_config, timeout=15)
    if ok:
        print(f"  -> {current_model} is working. No switch needed.")
        sys.exit(0)
    print(f"  -> CONFIRMED error: {err}")
    print()

    # Step 2: Try backup models
    print("Testing backup models...")
    switched = False
    unavailable = []

    for model in BACKUP_MODELS:
        print(f"  Testing {model}...")
        ok, err = test_model(model, api_config, timeout=15)
        if ok:
            print(f"  -> {model} is available! Switching...")
            settings = update_settings_model(settings, model)
            save_settings(settings)
            print(f"  -> Switched to {model}. Updated settings.json.")
            switched = True
            break
        else:
            print(f"  -> Unavailable: {err}")
            unavailable.append(model)
    print()

    # Step 3: Report
    if switched:
        print("=" * 50)
        print(f"SUCCESS: Switched from {current_model} to {model}")
        if unavailable:
            print(f"Unavailable models (skipped): {', '.join(unavailable)}")
        print(f"New model is active. Start a new session or run /model to verify.")
        print("=" * 50)
    else:
        print("=" * 50)
        print("FAILURE: All backup models are also unavailable.")
        print(f"Tried: {', '.join(BACKUP_MODELS)}")
        print("You may need to top up your account or change API endpoint.")
        print("=" * 50)
        sys.exit(1)


if __name__ == "__main__":
    main()
