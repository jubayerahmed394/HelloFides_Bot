"""
Text generation for HelloFides Bot.

Calls the Google Gemini free-tier API, asks for strict JSON, validates the
result, checks it is not a repeat of a topic posted in the last N days, and
retries a few times before giving up. Never invents statistics or results.
"""
import json
import os
import re
import uuid

from google import genai
from google.genai import types

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

REQUIRED_FIELDS = ["mode", "platform", "pillar", "hook", "photocard_text", "caption", "hashtags", "source"]


class GenerationError(Exception):
    pass


def _word_count(text):
    return len(re.findall(r"\S+", text or ""))


def _normalize(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return set(text.split())


def _is_duplicate(hook, recent_posts, threshold=0.55):
    hook_words = _normalize(hook)
    if not hook_words:
        return False
    for post in recent_posts:
        prev_words = _normalize(post.get("hook", ""))
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
            "a social media management agency in Dhaka, Bangladesh. Write in first person ('I'), as a "
            "practitioner sharing real working experience. Goal: position him as a skilled, trustworthy "
            "social media manager who attracts clients and employers. Tone: direct, no fluff, no cliche "
            "openers like 'In today's digital age', no emoji spam."
        )
    return (
        "You are writing Facebook posts for the HelloFides Facebook Page. HelloFides is a social media "
        "management agency. Write in brand voice ('we'), friendly and professional, for small business "
        "owners and startups in Bangladesh and South Asia who are deciding whether to hire help with their "
        "social media. Goal: make it clear HelloFides is a social media management company and generate "
        "inquiries. Every post must end with a soft call to action pointing to the Page inbox or hellofides.com."
    )


def _build_prompt(mode, pillar, avoid_hooks, custom_topic=None, feedback=None):
    info = MODE_INFO[mode]
    pillar_label = PILLAR_LABELS[pillar]

    avoid_block = ""
    if avoid_hooks:
        bullet_list = "\n".join(f"- {h}" for h in avoid_hooks)
        avoid_block = (
            "\nDo NOT repeat any of these recently used hooks/topics. Pick a clearly different angle:\n"
            f"{bullet_list}\n"
        )

    topic_block = ""
    if custom_topic:
        topic_block = f"\nThe post MUST be about this specific topic requested by the user: \"{custom_topic}\"\n"

    feedback_block = ""
    if feedback:
        feedback_block = f"\nYour previous attempt was rejected for this reason, fix it: {feedback}\n"

    rules = f"""
{_system_voice(mode)}

Content pillar for this post: {pillar_label}
{topic_block}{avoid_block}{feedback_block}
Hard rules:
- Hook: under 12 words, no cliche openers.
- photocard_text: the short text that appears ON the image itself. Maximum 25 words. It should be the single
  clearest takeaway, written to be readable on a phone screen.
- caption: {info['caption_words']} words, matching the voice described above, one clear takeaway, one soft
  call to action at the end.
- hashtags: {info['min_hashtags']} to {info['max_hashtags']} relevant hashtags, each starting with '#'.
- NEVER invent statistics, client names, or results. If the pillar is "stats", you MUST cite a real,
  named, verifiable source and year (e.g. "HubSpot, 2024"). If you cannot think of a verifiable statistic,
  write a non-statistical insight instead and set "source" to an empty string.
- If pillar is "lesson", it must read as a general, real-sounding lesson. Do not invent fake client names,
  fake brand names, or fake numeric results.
- Output STRICT JSON only, matching exactly this schema, no markdown fences, no extra commentary:

{{
  "mode": "{mode}",
  "platform": "{info['platform_label']}",
  "pillar": "{pillar}",
  "hook": "string, under 12 words",
  "photocard_text": "string, max 25 words",
  "caption": "string, {info['caption_words']} words",
  "hashtags": ["#tag1", "#tag2"],
  "source": "string like 'HubSpot, 2024', or empty string if not a stats post"
}}
"""
    return rules.strip()


def _validate(data, mode, pillar):
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in data or data[field] in (None, ""):
            if field == "source":
                continue  # empty source is allowed when there is no statistic
            errors.append(f"missing or empty field '{field}'")

    if errors:
        return errors

    if data.get("mode") != mode:
        errors.append(f"mode must be '{mode}'")
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

    return errors


def _call_gemini(client, model, prompt):
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.9,
        ),
    )
    text = response.text or ""
    text = text.strip()
    # Defensive: strip accidental markdown code fences even though we asked for pure JSON.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return json.loads(text)


def pick_pillar(config, state, mode):
    pillars = config["pillars"]
    idx = state["pillar_index"].get(mode, 0) % len(pillars)
    state["pillar_index"][mode] = (idx + 1) % len(pillars)
    return pillars[idx]


def generate_post(config, state, mode, custom_topic=None, forced_pillar=None):
    """
    Generates and validates one post. Returns a dict ready to log + render,
    or raises GenerationError after exhausting retries.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GenerationError("GEMINI_API_KEY environment variable is not set.")

    client = genai.Client(api_key=api_key)
    model = config["gemini"]["model"]
    max_retries = config["gemini"]["max_retries"]

    pillar = forced_pillar or pick_pillar(config, state, mode)

    log = storage.load_content_log(config)
    now = storage.now_dhaka(config)
    recent = storage.recent_entries(log, mode, config["duplicate_topic_cooldown_days"], now)
    avoid_hooks = [p["hook"] for p in recent][-15:]

    last_errors = []
    for attempt in range(1, max_retries + 1):
        feedback = "; ".join(last_errors) if last_errors else None
        prompt = _build_prompt(mode, pillar, avoid_hooks, custom_topic=custom_topic, feedback=feedback)
        try:
            data = _call_gemini(client, model, prompt)
        except Exception as exc:  # malformed JSON, API error, etc.
            last_errors = [f"API/JSON error: {exc}"]
            continue

        errors = _validate(data, mode, pillar)
        if errors:
            last_errors = errors
            continue

        if _is_duplicate(data["hook"], recent):
            last_errors = ["hook/topic is too similar to a post from the last "
                            f"{config['duplicate_topic_cooldown_days']} days"]
            continue

        post_id = uuid.uuid4().hex[:10]
        entry = {
            "id": post_id,
            "mode": mode,
            "platform": data["platform"],
            "pillar": pillar,
            "hook": data["hook"],
            "photocard_text": data["photocard_text"],
            "caption": data["caption"],
            "hashtags": data["hashtags"],
            "source": data.get("source", ""),
            "created_at": now.isoformat(),
            "posted": False,
        }
        storage.append_log_entry(config, log, entry)
        return entry

    raise GenerationError(
        f"Could not generate a valid '{mode}' post after {max_retries} attempts. "
        f"Last errors: {'; '.join(last_errors) if last_errors else 'unknown'}"
    )
