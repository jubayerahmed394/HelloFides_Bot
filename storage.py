"""
Small shared helpers for reading config and the two JSON "databases":
- content_log.json : history of every post ever generated (for duplicate-topic checks)
- data/bot_state.json : bot runtime state (Telegram update offset, pillar rotation, pending posts)

Both JSON files are committed back to the GitHub repo by the workflow after each run,
so state survives between GitHub Actions runs (there is no server keeping memory).
"""
import json
import os
import yaml
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["content_log_path"] = os.path.join(BASE_DIR, config["content_log_path"])
    config["state_path"] = os.path.join(BASE_DIR, config["state_path"])
    return config


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
        if not text:
            return default
        return json.loads(text)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def load_content_log(config):
    return _read_json(config["content_log_path"], {"posts": []})


def save_content_log(config, log):
    _write_json(config["content_log_path"], log)


def load_state(config):
    default = {
        "last_update_id": 0,
        "pillar_index": {"personal": 0, "company": 0},
        "pending_posts": {},
        "pending_topics": {},
    }
    state = _read_json(config["state_path"], default)
    for key, value in default.items():
        state.setdefault(key, value)
    return state


def save_state(config, state):
    _write_json(config["state_path"], state)


def now_dhaka(config):
    return datetime.now(ZoneInfo(config["timezone"]))


def recent_entries(log, mode, cooldown_days, now):
    cutoff = now - timedelta(days=cooldown_days)
    out = []
    for post in log.get("posts", []):
        if post.get("mode") != mode:
            continue
        try:
            posted_at = datetime.fromisoformat(post["created_at"])
        except (KeyError, ValueError):
            continue
        if posted_at >= cutoff:
            out.append(post)
    return out


def append_log_entry(config, log, entry):
    log.setdefault("posts", []).append(entry)
    save_content_log(config, log)
    return log
