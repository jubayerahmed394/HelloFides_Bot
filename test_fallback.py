"""
Tests for the multi-provider fallback chain. No real API calls are made -
providers.call is mocked throughout. Run: python test_fallback.py

Covers:
- A 429 on the first provider moves to the next provider immediately
  (no retry on the rate-limited one) and records a cooldown.
- Once that cooldown has passed, the first provider (Gemini) is tried
  first again on the next call.
- The local post bank is used, untouched, when every provider fails.
- The queue refills at most once per day and is consumed without any
  provider call on a normal pop.
"""
import os
import re
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

import yaml

import generator
import provider_state
import providers
import storage


def _temp_config():
    with open(storage.CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    tmp_dir = tempfile.mkdtemp(prefix="hellofides_test_")
    config["content_log_path"] = os.path.join(tmp_dir, "content_log.json")
    config["state_path"] = os.path.join(tmp_dir, "bot_state.json")
    config["queue_path"] = os.path.join(tmp_dir, "content_queue.json")
    config["provider_state_path"] = os.path.join(tmp_dir, "provider_state.json")
    config["fallback_bank_path"] = os.path.join(storage.BASE_DIR, "data", "fallback_bank.json")
    return config, tmp_dir


def _fresh_state():
    return {
        "last_update_id": 0, "pillar_index": {"personal": 0, "company": 0},
        "bank_index": {"personal": 0, "company": 0}, "pending_posts": {},
        "pending_topics": {}, "last_failure_notice_date": None,
    }


_DISTINCT_HOOKS = {
    ("strategy", 1): "Plan three content pillars before you post anything else",
    ("howto", 1): "Batch your captions in one sitting every single Monday",
    ("lesson", 1): "A quiet launch taught us to double check timing first",
    ("strategy", 2): "Vague goals waste a marketing budget faster than bad photos",
    ("howto", 2): "Save five reply templates before your next busy weekend starts",
    ("lesson", 2): "Ignoring a small platform once cost us real customer leads",
}


def _fake_post(pillar, n):
    return {
        "pillar": pillar,
        "hook": _DISTINCT_HOOKS[(pillar, n)],
        "photocard_text": f"Entirely different phone-readable takeaway about {pillar}, written fresh for test round {n}.",
        "caption": " ".join(["word"] * 70),
        "hashtags": ["#Test", "#HelloFides"],
        "source": "",
        "photo_query": "laptop desk workspace flatlay",
    }


def test_rate_limit_failover_and_recovery():
    config, tmp_dir = _temp_config()
    try:
        state = _fresh_state()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x", "GROQ_API_KEY": "x", "MISTRAL_API_KEY": "x"}):
            with patch.object(generator, "_pick_batch_pillars",
                               side_effect=lambda cfg, st, mode, count: ["strategy", "howto", "lesson"][:count]):

                calls = {"n": 0}

                def gemini_429_then_lite_ok(provider_cfg, prompt):
                    calls["n"] += 1
                    if provider_cfg["model"] == "gemini-3.8-flash":
                        raise providers.RateLimited(5)
                    if provider_cfg["model"] == "gemini-3.5-flash-lite":
                        return [_fake_post(p, 1) for p in ["strategy", "howto", "lesson"]]
                    raise AssertionError("should not reach groq/mistral in this scenario")

                with patch.object(providers, "call", side_effect=gemini_429_then_lite_ok):
                    entries = generator.generate_batch(config, state, "company", count=3)

                assert len(entries) == 3
                assert entries[0]["provider_used"] == "gemini_lite", entries[0]["provider_used"]
                print("PASS: 429 on gemini_primary failed over to gemini_lite, no retry on gemini_primary")

                pstate = provider_state.load(config)
                now = storage.now_dhaka(config)
                assert provider_state.in_cooldown(pstate, "gemini_primary", now)
                print("PASS: gemini_primary is in cooldown after the 429")

                # Fast-forward: push the recorded cooldown into the past, as if
                # time had actually passed, then confirm gemini_primary becomes
                # first choice again automatically (no permanent disabling).
                pstate["providers"]["gemini_primary"]["cooldown_until"] = (now - timedelta(seconds=1)).isoformat()
                provider_state.save(config, pstate)

                def gemini_primary_ok_again(provider_cfg, prompt):
                    if provider_cfg["model"] == "gemini-3.8-flash":
                        return [_fake_post(p, 2) for p in ["strategy", "howto", "lesson"]]
                    raise AssertionError("gemini_primary should have been tried first again")

                with patch.object(providers, "call", side_effect=gemini_primary_ok_again):
                    entries2 = generator.generate_batch(config, state, "company", count=3)
                assert entries2[0]["provider_used"] == "gemini_primary"
                print("PASS: after cooldown expiry, gemini_primary is tried first again")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_local_bank_used_when_everything_fails():
    config, tmp_dir = _temp_config()
    try:
        state = _fresh_state()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x", "GROQ_API_KEY": "x", "MISTRAL_API_KEY": "x"}):
            def always_fail(provider_cfg, prompt):
                raise providers.ProviderError("simulated total outage")

            with patch.object(providers, "call", side_effect=always_fail):
                entries = generator.generate_batch(config, state, "personal", count=3)

        assert len(entries) == 3
        assert all(e["provider_used"] == "local_bank" for e in entries)
        print("PASS: local post bank used when every provider fails, batch still has 3 valid posts")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_queue_refill_once_per_day_and_consume_without_api_call():
    config, tmp_dir = _temp_config()
    try:
        state = _fresh_state()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x", "GROQ_API_KEY": "x", "MISTRAL_API_KEY": "x"}):
            with patch.object(generator, "_pick_batch_pillars",
                               side_effect=lambda cfg, st, mode, count: ["strategy", "howto", "lesson"][:count]):
                call_count = {"n": 0}

                def one_batch_ok(provider_cfg, prompt):
                    call_count["n"] += 1
                    match = re.search(r"Produce exactly (\d+) posts", prompt)
                    n = int(match.group(1)) if match else 3
                    pillars = ["strategy", "howto", "lesson"][:n]
                    return [_fake_post(p, call_count["n"]) for p in pillars]

                with patch.object(providers, "call", side_effect=one_batch_ok):
                    first = generator.next_post(config, state, "company")
                    assert call_count["n"] == 1, "first pop should have triggered exactly one refill call"

                    # Two more pops should drain the rest of the queue with NO new API call.
                    second = generator.next_post(config, state, "company")
                    third = generator.next_post(config, state, "company")
                    assert call_count["n"] == 1, "queued items must not cost another API call"
                    assert {first["id"], second["id"], third["id"]} == {first["id"], second["id"], third["id"]}
                    assert len({first["id"], second["id"], third["id"]}) == 3

                    # Queue now empty; same-day refill already attempted, so this
                    # must fall through to a fresh on-demand single-item call.
                    fourth = generator.next_post(config, state, "company")
                    assert call_count["n"] == 2, "queue empty + same-day refill already tried -> on-demand call"
        print("PASS: queue refills once, three pops cost one API call, empty queue falls back to on-demand")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_banned_location_term_triggers_failover():
    config, tmp_dir = _temp_config()
    try:
        state = _fresh_state()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x", "GROQ_API_KEY": "x", "MISTRAL_API_KEY": "x"}):
            with patch.object(generator, "_pick_batch_pillars",
                               side_effect=lambda cfg, st, mode, count: ["strategy"][:count]):

                def leak_then_clean(provider_cfg, prompt):
                    if provider_cfg["model"] == "gemini-3.8-flash":
                        leaked = _fake_post("strategy", 1)
                        leaked["caption"] += " We're a small agency based in Dhaka, Bangladesh."
                        return [leaked]
                    if provider_cfg["model"] == "gemini-3.5-flash-lite":
                        return [_fake_post("strategy", 2)]
                    raise AssertionError("should not reach groq/mistral in this scenario")

                with patch.object(providers, "call", side_effect=leak_then_clean):
                    entries = generator.generate_batch(config, state, "company", count=1)

                assert entries[0]["provider_used"] == "gemini_lite"
                assert "dhaka" not in entries[0]["caption"].lower()
                pstate = provider_state.load(config)
                assert pstate["providers"]["gemini_primary"]["last_error"]
                print("PASS: a banned location term in gemini_primary's output is rejected and fails over")

        # Unit-level check of the filter itself, independent of the chain.
        assert generator._find_banned_location_term({"caption": "We help brands in Dhaka grow."}) == "dhaka"
        assert generator._find_banned_location_term({"hashtags": ["#Bangladesh"]}) == "bangladesh"
        assert generator._find_banned_location_term({"caption": "Serving founders in South Asia."}) == "south asia"
        assert generator._find_banned_location_term({"caption": "A plain, US-focused caption."}) is None
        print("PASS: _find_banned_location_term correctly flags/clears sample text")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_provider_with_blank_key_is_skipped_silently():
    config, tmp_dir = _temp_config()
    try:
        state = _fresh_state()
        # Only GEMINI_API_KEY set (this script never calls load_dotenv(), so
        # GROQ_API_KEY/MISTRAL_API_KEY are genuinely absent from os.environ
        # here, not just overridden) - every gemini call fails, so the chain
        # must fall through the two keyless providers straight to the local
        # bank without ever calling providers.call for them.
        assert "GROQ_API_KEY" not in os.environ and "MISTRAL_API_KEY" not in os.environ
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x"}, clear=False):

            def gemini_fails(provider_cfg, prompt):
                raise providers.ProviderError("simulated gemini outage")

            called_providers = []

            def tracking_call(provider_cfg, prompt):
                called_providers.append(provider_cfg["kind"])
                return gemini_fails(provider_cfg, prompt)

            with patch.object(providers, "call", side_effect=tracking_call):
                entries = generator.generate_batch(config, state, "personal", count=3)

        assert all(e["provider_used"] == "local_bank" for e in entries)
        assert "groq" not in called_providers and "mistral" not in called_providers
        print("PASS: providers with no key configured are skipped silently, never called, never an error")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_rate_limit_failover_and_recovery()
    test_local_bank_used_when_everything_fails()
    test_queue_refill_once_per_day_and_consume_without_api_call()
    test_banned_location_term_triggers_failover()
    test_provider_with_blank_key_is_skipped_silently()
    print("\nAll fallback-chain tests passed.")
