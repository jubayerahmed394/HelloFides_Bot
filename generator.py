"""
Text generation for HelloFides Bot.

Resilience design (see README.md section "Content generation resilience"):
- One batch call (not one call per post) asks a provider for a whole week
  of posts per mode (default 3), saved to a queue file. Scheduled runs and
  the /post command just pop the next queued item - no API call needed on
  a normal day.
- If every provider in the fallback chain (providers.ORDER) is unavailable,
  a local hand-written post bank (data/fallback_bank.json) is used instead,
  so a batch call never truly fails.
- Never invents statistics, client names, or results. "stats" posts must
  cite a real, named, dated source - the bank deliberately avoids that
  pillar rather than risk a fabricated citation.
"""
import json
import os
import re
import uuid
from datetime import date

import provider_state
import providers
import storage

MODE_INFO = {
    "personal": {
        "platform_label": "LinkedIn - Personal",
        "caption_words": "120-150",
        "min_hashtags": 3,
        "max_hashtags": 6,
    },
    "company": {
        "platform_label": "Facebook - HelloFides",
        "caption_words": "60-100",
        "min_hashtags": 2,
        "max_hashtags": 3,
    },
}

PILLAR_LABELS = {
    "strategy": "Social media strategy",
    "stats": "Facts and stats (must cite a real, named source and year)",
    "howto": "Practical how-to tip",
    "lesson": "Lesson learned / mini case study (general lesson, no fabricated client names or numbers)",
}

REQUIRED_FIELDS = [
    "pillar", "hook", "photocard_text", "caption", "hashtags", "source", "photo_query",
]

ATTEMPTS_PER_PROVIDER = 2  # one real try + one retry, per the "other errors" rule


class GenerationError(Exception):
    pass


def _word_count(text):
    return len(re.findall(r"\S+", text or ""))


def _normalize(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return set(text.split())


def _is_duplicate(hook, recent_hooks, threshold=0.55):
    hook_words = _normalize(hook)
    if not hook_words:
        return False
    for prev_hook in recent_hooks:
        prev_words = _normalize(prev_hook)
        if not prev_words:
            continue
        overlap = len(hook_words & prev_words) / len(hook_words | prev_words)
        if overlap >= threshold:
            return True
    return False


def _system_voice(mode):
    if mode == "personal":
        return (
            "You are ghost-writing LinkedIn posts for Jubayer Ahmed, founder and CEO of HelloFides, "
            "a social media management agency. Write in first person ('I'), as a practitioner sharing "
            "real working experience. Goal: position him as a skilled, trustworthy social media manager "
            "who attracts clients and employers. Tone: direct, no fluff, no cliche openers like 'In "
            "today's digital age', no emoji spam."
        )
    return (
        "You are writing Facebook posts for the HelloFides Facebook Page. HelloFides is a social media "
        "management agency. Write in brand voice ('we'), friendly and professional, for business owners "
        "deciding whether to hire help with their social media. Goal: make it clear HelloFides is a "
        "social media management company and generate inquiries. Every post must end with a soft call "
        "to action pointing to the Page inbox or hellofides.com."
    )


def _audience_rules():
    return (
        "Audience: US-based small business owners, startups, DTC/e-commerce brands, local service "
        "businesses, and marketing hiring managers in the United States. Write in American English - "
        "American spelling and idioms ('color' not 'colour', 'optimize' not 'optimise'). When an "
        "example is useful, use a US-relevant one (a local cafe, a DTC skincare brand, a SaaS startup, "
        "a real estate agent, a fitness studio). If giving posting-time advice, use US time zones (e.g. "
        "\"weekday mornings, 8-10 AM Eastern\").\n"
        "Never mention Dhaka, Bangladesh, South Asia, or any other specific location of the author or "
        "of HelloFides, in any field (hook, photocard_text, caption, hashtags, source, photo_query). Do "
        "NOT claim HelloFides is based in the US either - simply never state a location at all. Use "
        "neutral wording like \"brands\", \"small businesses\", \"founders\"."
    )


BANNED_LOCATION_TERMS = ["dhaka", "bangladesh", "bd ", "south asia", "bengali"]


def _find_banned_location_term(data):
    haystack = " ".join([
        str(data.get("hook", "")), str(data.get("photocard_text", "")), str(data.get("caption", "")),
        " ".join(data.get("hashtags") or []), str(data.get("source", "")), str(data.get("photo_query", "")),
    ]).lower()
    for term in BANNED_LOCATION_TERMS:
        if term in haystack:
            return term
    return None


def _post_schema_block(mode, pillar_placeholder="pillar"):
    info = MODE_INFO[mode]
    return f"""{{
  "pillar": "{pillar_placeholder}",
  "hook": "string, under 12 words",
  "photocard_text": "string, max 25 words",
  "caption": "string, {info['caption_words']} words",
  "hashtags": ["#tag1", "#tag2"],
  "source": "string like 'HubSpot, 2024', or empty string if not a stats post",
  "photo_query": "string, 3-6 words, stock photo search terms"
}}"""


def _build_batch_prompt(mode, pillars, avoid_hooks):
    info = MODE_INFO[mode]
    pillar_lines = "\n".join(f"- posts[{i}].pillar must be exactly \"{p}\" ({PILLAR_LABELS[p]})"
                              for i, p in enumerate(pillars))

    avoid_block = ""
    if avoid_hooks:
        bullet_list = "\n".join(f"- {h}" for h in avoid_hooks)
        avoid_block = (
            "\nDo NOT repeat any of these recently used hooks/topics. Pick clearly different angles:\n"
            f"{bullet_list}\n"
        )

    example = _post_schema_block(mode)

    rules = f"""
{_system_voice(mode)}

{_audience_rules()}

Produce exactly {len(pillars)} posts in ONE response, one per content pillar below:
{pillar_lines}
{avoid_block}
Hard rules for EVERY post:
- Hook: under 12 words, no cliche openers.
- photocard_text: the short text that appears ON the image itself. Maximum 25 words. It should be the single
  clearest takeaway, written to be readable on a phone screen.
- caption: {info['caption_words']} words, matching the voice described above, one clear takeaway, one soft
  call to action at the end.
- hashtags: {info['min_hashtags']} to {info['max_hashtags']} relevant hashtags, each starting with '#'.
- NEVER invent statistics, client names, or results. If a post's pillar is "stats", you MUST cite a real,
  named, verifiable source and year (e.g. "HubSpot, 2024"). If you cannot think of a verifiable statistic,
  write a non-statistical insight instead and set "source" to an empty string.
- If a post's pillar is "lesson", it must read as a general, real-sounding lesson. Do not invent fake client
  names, fake brand names, or fake numeric results.
- photo_query: 3-6 words describing a stock photo search for the background image (e.g. "laptop content
  calendar desk flatlay"). It must describe a scene, object, or setting only - never a specific named
  person, and avoid wording that implies a close-up identifiable face (prefer hands/objects/desks/devices
  over "portrait of a person").
- The {len(pillars)} posts must be about clearly different topics from each other, not variations of the
  same idea.
- Output STRICT JSON only, matching exactly this schema, no markdown fences, no extra commentary:

{{
  "posts": [
{example}
    ... ({len(pillars)} items total, in the same pillar order as listed above)
  ]
}}
"""
    return rules.strip()


def _validate_item(data, mode, pillar):
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in data or data[field] in (None, ""):
            if field == "source":
                continue  # empty source is allowed when there is no statistic
            errors.append(f"missing or empty field '{field}'")

    if errors:
        return errors

    if data.get("pillar") != pillar:
        errors.append(f"pillar must be '{pillar}'")

    hook_words = _word_count(data.get("hook"))
    if hook_words == 0 or hook_words > 12:
        errors.append(f"hook must be under 12 words (got {hook_words})")

    photocard_words = _word_count(data.get("photocard_text"))
    if photocard_words == 0 or photocard_words > 25:
        errors.append(f"photocard_text must be max 25 words (got {photocard_words})")

    caption_words = _word_count(data.get("caption"))
    lo, hi = [int(x) for x in MODE_INFO[mode]["caption_words"].split("-")]
    # allow some slack either side since word-count targets from an LLM are approximate
    if caption_words < lo * 0.7 or caption_words > hi * 1.3:
        errors.append(f"caption should be about {lo}-{hi} words (got {caption_words})")

    hashtags = data.get("hashtags")
    if not isinstance(hashtags, list) or not hashtags:
        errors.append("hashtags must be a non-empty list")
    else:
        min_h, max_h = MODE_INFO[mode]["min_hashtags"], MODE_INFO[mode]["max_hashtags"]
        if not (min_h <= len(hashtags) <= max_h):
            errors.append(f"hashtags should have {min_h}-{max_h} items (got {len(hashtags)})")
        if not all(isinstance(h, str) and h.startswith("#") for h in hashtags):
            errors.append("every hashtag must be a string starting with '#'")

    if pillar == "stats":
        source = data.get("source", "")
        if not source or not re.search(r"(19|20)\d{2}", source):
            errors.append("stats posts must have a 'source' containing a named source and a year")

    photo_query_words = _word_count(data.get("photo_query"))
    if photo_query_words < 2 or photo_query_words > 8:
        errors.append(f"photo_query must be about 3-6 words (got {photo_query_words})")

    banned_term = _find_banned_location_term(data)
    if banned_term:
        errors.append(f"contains banned location term '{banned_term}'")

    return errors


def _parse_and_validate_batch(raw, mode, pillars, recent_hooks):
    """raw is whatever providers.call() returned - expected to be a list of
    `len(pillars)` post dicts. Raises ValueError on any problem. Returns the
    validated list of plain post dicts (no id/timestamp yet)."""
    if not isinstance(raw, list):
        raise ValueError("expected a JSON array of posts")
    if len(raw) != len(pillars):
        raise ValueError(f"expected {len(pillars)} posts, got {len(raw)}")

    seen_hooks = list(recent_hooks)
    items = []
    for data, pillar in zip(raw, pillars):
        if not isinstance(data, dict):
            raise ValueError("each post must be a JSON object")
        errors = _validate_item(data, mode, pillar)
        if errors:
            raise ValueError(f"post for pillar '{pillar}': {'; '.join(errors)}")
        if _is_duplicate(data["hook"], seen_hooks):
            raise ValueError(f"post for pillar '{pillar}': hook too similar to a recent/other post in this batch")
        seen_hooks.append(data["hook"])
        items.append({
            "pillar": pillar,
            "hook": data["hook"],
            "photocard_text": data["photocard_text"],
            "caption": data["caption"],
            "hashtags": data["hashtags"],
            "source": data.get("source", ""),
            "photo_query": data["photo_query"],
        })
    return items


def pick_pillar(config, state, mode):
    pillars = config["pillars"]
    idx = state["pillar_index"].get(mode, 0) % len(pillars)
    state["pillar_index"][mode] = (idx + 1) % len(pillars)
    return pillars[idx]


def _pick_batch_pillars(config, state, mode, count):
    chosen = []
    guard = 0
    while len(chosen) < count and guard < count * 4:
        guard += 1
        pillar = pick_pillar(config, state, mode)
        if pillar not in chosen:
            chosen.append(pillar)
    while len(chosen) < count:  # only possible if count > len(pillars)
        chosen.append(pick_pillar(config, state, mode))
    return chosen


def _load_bank(config):
    path = config["fallback_bank_path"]
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _draw_from_bank(config, state, mode, count, recent_hooks):
    bank = _load_bank(config)[mode]
    state.setdefault("bank_index", {})
    start = state["bank_index"].get(mode, 0) % len(bank)

    seen_hooks = list(recent_hooks)
    items = []
    idx = start
    for _ in range(len(bank)):
        candidate = bank[idx % len(bank)]
        idx += 1
        if _find_banned_location_term(candidate):
            continue  # defensive - the shipped bank is clean, but a future edit might not be
        if not _is_duplicate(candidate["hook"], seen_hooks):
            seen_hooks.append(candidate["hook"])
            items.append(dict(candidate))
            if len(items) == count:
                break
    # If everything collided (bank smaller than needed or fully recently used),
    # just take the next `count` entries anyway - never fail the fallback.
    while len(items) < count:
        candidate = bank[idx % len(bank)]
        idx += 1
        items.append(dict(candidate))
    state["bank_index"][mode] = idx % len(bank)
    return items


def _finalize_entries(config, mode, items, provider_used, now):
    log = storage.load_content_log(config)
    entries = []
    for item in items:
        entry = {
            "id": uuid.uuid4().hex[:10],
            "mode": mode,
            "platform": MODE_INFO[mode]["platform_label"],
            "pillar": item["pillar"],
            "hook": item["hook"],
            "photocard_text": item["photocard_text"],
            "caption": item["caption"],
            "hashtags": item["hashtags"],
            "source": item.get("source", ""),
            "photo_query": item["photo_query"],
            "created_at": now.isoformat(),
            "posted": False,
            "provider_used": provider_used,
        }
        storage.append_log_entry(config, log, entry)
        entries.append(entry)
    return entries


def generate_batch(config, state, mode, count=3):
    """
    Produces `count` validated posts for `mode` via the provider fallback
    chain (one API call, not `count` calls), falling back to the local post
    bank if every provider is unavailable. Returns (entries, provider_used).
    Only raises GenerationError if even the local bank can't produce
    anything, which should not happen in normal operation.
    """
    now = storage.now_dhaka(config)
    log = storage.load_content_log(config)
    recent = storage.recent_entries(log, mode, config["duplicate_topic_cooldown_days"], now)
    avoid_hooks = [p["hook"] for p in recent][-20:]

    pillars = _pick_batch_pillars(config, state, mode, count)

    pstate = provider_state.load(config)
    items = None
    provider_used = None

    for provider_id in config["providers"]["order"]:
        provider_cfg = config["providers"][provider_id]
        if not os.environ.get(provider_cfg["key_env"]):
            continue  # key never configured - skip silently, not an error
        if provider_state.in_cooldown(pstate, provider_id, now):
            continue

        for attempt in range(1, ATTEMPTS_PER_PROVIDER + 1):
            prompt = _build_batch_prompt(mode, pillars, avoid_hooks)
            try:
                raw = providers.call(provider_cfg, prompt)
                candidate = _parse_and_validate_batch(raw, mode, pillars, avoid_hooks)
            except providers.RateLimited as exc:
                provider_state.record_rate_limited(pstate, provider_id, exc.retry_after_seconds, now)
                break  # never retry the same provider after a 429
            except Exception as exc:  # providers.ProviderError or a ValueError from validation
                provider_state.record_error(pstate, provider_id, str(exc), now)
                if attempt >= ATTEMPTS_PER_PROVIDER:
                    break
                continue  # one retry on the same provider
            provider_state.record_success(pstate, provider_id, now)
            items, provider_used = candidate, provider_id
            break

        if items is not None:
            break

    provider_state.save(config, pstate)

    if items is None:
        try:
            items = _draw_from_bank(config, state, mode, count, avoid_hooks)
        except Exception as exc:
            raise GenerationError(f"all providers and the local bank failed: {exc}") from exc
        provider_used = "local_bank"

    return _finalize_entries(config, mode, items, provider_used, now)


# --------------------------------------------------------------------------
# Queue: refill rarely (at most once/day/mode), consume on every post.
# --------------------------------------------------------------------------

def _load_queue(config):
    default = {"company": [], "personal": [], "last_refill_attempt": {"company": None, "personal": None}}
    queue = storage._read_json(config["queue_path"], default)
    for key, value in default.items():
        queue.setdefault(key, value)
    return queue


def _save_queue(config, queue):
    storage._write_json(config["queue_path"], queue)


def ensure_queue(config, state, mode, min_items=3, batch_size=None):
    """Refills the queue for `mode` if it's running low, at most once per
    calendar day (success or failure both count, so a bad day doesn't retry
    every five minutes)."""
    batch_size = batch_size or config["schedule"]["daily"]["batch_size"]
    queue = _load_queue(config)
    today = date.today().isoformat()

    if len(queue[mode]) < min_items and queue["last_refill_attempt"].get(mode) != today:
        queue["last_refill_attempt"][mode] = today
        _save_queue(config, queue)  # record the attempt even if generate_batch raises
        entries = generate_batch(config, state, mode, count=batch_size)
        queue = _load_queue(config)
        queue[mode].extend(entries)
        _save_queue(config, queue)

    return queue


def pop_from_queue(config, state, mode, exclude_ids=None):
    exclude_ids = exclude_ids or set()
    queue = ensure_queue(config, state, mode)
    for i, entry in enumerate(queue[mode]):
        if entry["id"] not in exclude_ids:
            queue[mode].pop(i)
            _save_queue(config, queue)
            return entry
    return None


def next_post(config, state, mode):
    """Used by scheduled runs and the /post command: pop the next queued
    post, refilling first if the queue is running low. Only calls an API if
    today's refill hasn't been attempted yet and the queue needs it."""
    entry = pop_from_queue(config, state, mode)
    if entry is None:
        # Queue + same-day refill both came up empty - fall straight to a
        # single on-demand batch-of-one so the request still succeeds today.
        entries = generate_batch(config, state, mode, count=1)
        entry = entries[0]
    return entry


def regenerate(config, state, old_entry):
    """Used by the 'Regenerate' button: try a different queued item first,
    only call an API (via generate_batch) if the queue has nothing else."""
    mode = old_entry["mode"]
    exclude = {old_entry["id"]}
    for pending in state.get("pending_posts", {}).values():
        if pending["entry"]["mode"] == mode:
            exclude.add(pending["entry"]["id"])

    entry = pop_from_queue(config, state, mode, exclude_ids=exclude)
    if entry is not None:
        return entry
    entries = generate_batch(config, state, mode, count=1)
    return entries[0]


def daily_mode_sequence(config):
    """The mode for each of today's `schedule.daily.count` posts, alternating
    Personal/Company starting with Personal. Callers generate+send one at a
    time (not as one all-or-nothing batch) so a single failure doesn't lose
    the posts that already succeeded."""
    count = config["schedule"]["daily"]["count"]
    modes = ("personal", "company")
    return [modes[i % len(modes)] for i in range(count)]
