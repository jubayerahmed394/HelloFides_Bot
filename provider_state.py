"""
Tracks per-provider health across runs: last_error, cooldown_until,
requests_today. Persisted to data/provider_state.json (committed back to the
repo by the workflow, same as bot_state.json) so cooldowns survive between
short-lived GitHub Actions runs.

A provider is never permanently disabled - once cooldown_until passes (or
a RateLimited/error record expires), it's eligible again automatically,
and gemini_primary is always tried first on a fresh attempt.
"""
from datetime import datetime, timedelta, timezone

import storage

_DEFAULT_ENTRY = {"last_error": None, "cooldown_until": None, "requests_today": 0, "day": None}


def load(config):
    data = storage._read_json(config["provider_state_path"], {"providers": {}})
    data.setdefault("providers", {})
    return data


def save(config, state):
    storage._write_json(config["provider_state_path"], state)


def _entry(state, provider_id):
    return state["providers"].setdefault(provider_id, dict(_DEFAULT_ENTRY))


def _today_str(now):
    return now.date().isoformat()


def _bump_requests_today(entry, now):
    today = _today_str(now)
    if entry.get("day") != today:
        entry["day"] = today
        entry["requests_today"] = 0
    entry["requests_today"] += 1


def in_cooldown(state, provider_id, now):
    entry = state["providers"].get(provider_id)
    if not entry or not entry.get("cooldown_until"):
        return False
    try:
        until = datetime.fromisoformat(entry["cooldown_until"])
    except ValueError:
        return False
    return now < until


def record_success(state, provider_id, now):
    entry = _entry(state, provider_id)
    _bump_requests_today(entry, now)
    entry["last_error"] = None
    entry["cooldown_until"] = None


def record_rate_limited(state, provider_id, retry_after_seconds, now):
    entry = _entry(state, provider_id)
    _bump_requests_today(entry, now)
    entry["last_error"] = "rate limited (429)"
    entry["cooldown_until"] = (now + timedelta(seconds=max(1, retry_after_seconds))).isoformat()


def record_error(state, provider_id, message, now):
    entry = _entry(state, provider_id)
    _bump_requests_today(entry, now)
    entry["last_error"] = str(message)[:300]
    # Short cooldown on repeated non-429 errors too, so a provider that's
    # fully down (bad key, outage) doesn't get hammered every single call.
    entry["cooldown_until"] = (now + timedelta(seconds=60)).isoformat()
