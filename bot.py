"""
Telegram bot logic for HelloFides Bot.

Runs as a short-lived script (invoked by GitHub Actions on a schedule), not
a long-running process: each call fetches any new Telegram updates since the
last run, handles them, and exits. State (update offset, pending posts,
pillar rotation) is saved to data/bot_state.json and committed back to the
repo by the workflow so it survives between runs.

Single delivery destination: the private "HelloFides" Telegram channel
(TELEGRAM_CHANNEL_ID). Commands are sent to the bot via DM, but generated
posts are delivered to the channel. Commands/buttons are only honored from
the optional TELEGRAM_OWNER_CHAT_ID or from a current admin of the channel.
"""
import asyncio
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import date

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

import designer_html
import generator
import photo_fetch
import storage

PILLAR_TAG = {
    "strategy": "STRATEGY",
    "stats": "FACT",
    "howto": "TIP",
    "lesson": "LESSON",
}

LAYOUT_KEYS = {
    "personal": ["L1", "L2", "L3"],
    "company": ["C1", "C2", "C3"],
}

PLATFORM_LABEL = {
    "company": "COMPANY / Facebook (HelloFides page)",
    "personal": "PERSONAL / LinkedIn (Jubayer Ahmed)",
}

HELP_TEXT = (
    "HelloFides content bot - I generate ready-to-post content and deliver it to the "
    "HelloFides channel. You post it manually from there.\n\n"
    "Commands (DM me these):\n"
    "/post company - generate a Facebook (HelloFides) post now\n"
    "/post personal - generate a LinkedIn (Jubayer Ahmed) post now\n"
    "/preview company - same thing, generate a Facebook photocard preview\n"
    "/preview personal - same thing, generate a LinkedIn photocard preview\n"
    "/schedule - show the posting schedule\n"
    "/help - show this message\n\n"
    "You can also just send a message like \"photocard company\" or \"preview personal\" - "
    "I'll understand it the same way.\n\n"
    "Under each photocard in the channel you'll get buttons to Regenerate the caption, "
    "make a New design, or Mark as posted."
)


def get_bot():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    return Bot(token=token)


def get_channel_id():
    raw = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not raw:
        raise RuntimeError("TELEGRAM_CHANNEL_ID environment variable is not set.")
    return int(raw)


def get_owner_chat_id():
    raw = os.environ.get("TELEGRAM_OWNER_CHAT_ID")
    return int(raw) if raw else None


async def is_authorized(bot, user_id):
    """Owner (if configured) or a current admin/creator of the channel."""
    owner_id = get_owner_chat_id()
    if owner_id and user_id == owner_id:
        return True
    try:
        member = await bot.get_chat_member(chat_id=get_channel_id(), user_id=user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


def _post_keyboard(post_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Regenerate", callback_data=f"regen:{post_id}"),
            InlineKeyboardButton("New design", callback_data=f"newdesign:{post_id}"),
        ],
        [InlineKeyboardButton("Mark as posted", callback_data=f"posted:{post_id}")],
    ])


async def notify_total_failure(bot, config, state, chat_id, detail):
    """Every provider AND the local post bank failed - this should be rare.
    Never forwards the raw error to Telegram; full detail goes to the
    workflow's own log output (stderr) only, and at most one short, plain
    message is sent per calendar day no matter how many times this fires."""
    print(f"[HelloFides] generation failed completely: {detail}", file=sys.stderr)
    today = date.today().isoformat()
    if state.get("last_failure_notice_date") == today:
        return
    state["last_failure_notice_date"] = today
    storage.save_state(config, state)
    await bot.send_message(chat_id=chat_id, text="Content generation failed today, will retry tomorrow.")


def _pick_layout_key(mode, entry_id):
    keys = LAYOUT_KEYS[mode]
    return keys[int(entry_id, 16) % len(keys)]


async def _render_card(config, entry, card, layout_key, work_dir):
    """Fetches a topic photo and renders the card via designer_html (sync
    Playwright work run off the event loop)."""
    photo = await asyncio.to_thread(
        photo_fetch.fetch_topic_photo,
        entry["photo_query"],
        "portrait" if entry["mode"] == "personal" else "landscape",
        work_dir,
        f"{entry['id']}.jpg",
    )
    out_png = os.path.join(work_dir, f"{entry['id']}.png")
    await asyncio.to_thread(
        designer_html.render_card_sync,
        entry["mode"], layout_key, card, photo["local_path"], work_dir, out_png, config,
    )
    return out_png, photo


async def send_post(bot, config, state, entry, layout_key=None):
    """Sends the label, photocard + buttons, caption, and hashtags/source to
    the HelloFides channel, per the required delivery format."""
    channel_id = get_channel_id()
    layout_key = layout_key or _pick_layout_key(entry["mode"], entry["id"])
    # generator.py produces one combined "photocard_text" (max 25 words), not a
    # separate hook/support pair - use it as the card's main line, no subline.
    card = {
        "tag": PILLAR_TAG.get(entry["pillar"], entry["pillar"].upper()),
        "hook": entry["photocard_text"], "support": "",
        "pillar": entry["pillar"], "mode": entry["mode"], "id": entry["id"],
        "source": entry.get("source", ""),
    }

    work_dir = tempfile.mkdtemp(prefix="hellofides_render_")
    try:
        out_png, photo = await _render_card(config, entry, card, layout_key, work_dir)

        label = f"{PLATFORM_LABEL[entry['mode']]}\nType: {card['tag']}"
        await bot.send_message(chat_id=channel_id, text=label)

        with open(out_png, "rb") as f:
            sent_photo = await bot.send_photo(
                chat_id=channel_id, photo=f, reply_markup=_post_keyboard(entry["id"]),
            )
        await bot.send_message(chat_id=channel_id, text=entry["caption"])
        hashtag_line = " ".join(entry["hashtags"])
        if entry.get("source"):
            hashtag_line += f"\nSource: {entry['source']}"
        await bot.send_message(chat_id=channel_id, text=hashtag_line)

        state["pending_posts"][entry["id"]] = {
            "entry": entry, "layout_key": layout_key, "chat_id": channel_id,
            "message_id": sent_photo.message_id,
            "photo_credit": {k: photo[k] for k in ("source", "id", "photographer", "source_url", "license_note")},
        }
        storage.save_state(config, state)
        # Delivery log footer - stdout/workflow log only, never sent to Telegram.
        print(f"[delivery log] {entry['id']} mode={entry['mode']} pillar={entry['pillar']} "
              f"provider={entry.get('provider_used', 'unknown')}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _send_photocard_preview(bot, config, state, dm_chat_id, mode, verb="post"):
    """Shared by /post, /preview, and plain-text photocard requests: generates
    (or pops from the queue) one photocard + caption and delivers it to the
    channel, exactly as a scheduled post would."""
    await bot.send_message(chat_id=dm_chat_id, text=f"Generating a {mode} {verb} for the channel...")
    try:
        entry = generator.next_post(config, state, mode)
    except generator.GenerationError as exc:
        await notify_total_failure(bot, config, state, dm_chat_id, str(exc))
        return
    await send_post(bot, config, state, entry)
    await bot.send_message(chat_id=dm_chat_id, text="Sent to the channel.")


def _infer_mode(text):
    """Best-effort mode detection for free-text requests ('photocard company',
    'send me a personal preview', etc). Returns 'company', 'personal', or None."""
    has_company = re.search(r"\bcompany\b", text) is not None
    has_personal = re.search(r"\bpersonal\b", text) is not None
    if has_company and not has_personal:
        return "company"
    if has_personal and not has_company:
        return "personal"
    return None


async def _handle_command(bot, config, state, dm_chat_id, text):
    command, _, arg = text.partition(" ")
    command = command.split("@")[0].lower()
    arg = arg.strip().lower()

    if command == "/help":
        await bot.send_message(chat_id=dm_chat_id, text=HELP_TEXT)

    elif command == "/schedule":
        sched = config["schedule"]["daily"]
        msg = (
            f"Timezone: {config['timezone']}\n\n"
            f"Every day at {sched['time']}: {sched['count']} photocards, alternating "
            "LinkedIn/Personal and Facebook/Company, delivered to the HelloFides channel.\n\n"
            "Edit config.yaml (schedule.daily) and schedule.yml to change this."
        )
        await bot.send_message(chat_id=dm_chat_id, text=msg)

    elif command in ("/post", "/preview"):
        if arg not in ("company", "personal"):
            await bot.send_message(
                chat_id=dm_chat_id,
                text=f"Usage: {command} company  or  {command} personal",
            )
            return
        verb = "photocard preview" if command == "/preview" else "post"
        await _send_photocard_preview(bot, config, state, dm_chat_id, arg, verb=verb)

    elif not command.startswith("/") and ("photocard" in text.lower() or "preview" in text.lower()):
        mode = _infer_mode(text.lower())
        if mode is None:
            await bot.send_message(
                chat_id=dm_chat_id,
                text="Want a Company or Personal photocard? Send /preview company or /preview personal.",
            )
            return
        await _send_photocard_preview(bot, config, state, dm_chat_id, mode, verb="photocard preview")

    else:
        await bot.send_message(chat_id=dm_chat_id, text="Unknown command. Send /help to see what I can do.")


async def _handle_callback(bot, config, state, callback_query):
    channel_id = get_channel_id()
    parts = (callback_query.data or "").split(":")
    action = parts[0]
    post_id = parts[1] if len(parts) > 1 else None
    pending = state["pending_posts"].get(post_id) if post_id else None

    if action == "regen":
        await callback_query.answer("Regenerating...")
        if not pending:
            await bot.send_message(chat_id=channel_id, text="This post is no longer pending (maybe too old).")
            return
        old_entry = pending["entry"]
        try:
            new_entry = generator.regenerate(config, state, old_entry)
        except generator.GenerationError as exc:
            await notify_total_failure(bot, config, state, channel_id, str(exc))
            return
        await send_post(bot, config, state, new_entry)

    elif action == "newdesign":
        await callback_query.answer("Making a new design...")
        if not pending:
            await bot.send_message(chat_id=channel_id, text="This post is no longer pending (maybe too old).")
            return
        entry = pending["entry"]
        keys = LAYOUT_KEYS[entry["mode"]]
        current = keys.index(pending.get("layout_key", keys[0])) if pending.get("layout_key") in keys else 0
        next_key = keys[(current + 1) % len(keys)]
        await send_post(bot, config, state, entry, layout_key=next_key)

    elif action == "posted":
        await callback_query.answer("Marked as posted.")
        if pending:
            log = storage.load_content_log(config)
            for post in log.get("posts", []):
                if post["id"] == post_id:
                    post["posted"] = True
            storage.save_content_log(config, log)
            try:
                await bot.edit_message_caption(
                    chat_id=pending["chat_id"], message_id=pending["message_id"],
                    caption="Marked as posted (done)",
                )
            except Exception:
                pass
            state["pending_posts"].pop(post_id, None)
            storage.save_state(config, state)
    else:
        await callback_query.answer()


async def poll_once(config, state):
    """Fetches and processes all pending Telegram updates once, then returns."""
    bot = get_bot()
    offset = state.get("last_update_id", 0)

    updates = await bot.get_updates(offset=offset + 1 if offset else None, timeout=0)
    for update in updates:
        state["last_update_id"] = update.update_id

        if update.message and update.message.text:
            if not await is_authorized(bot, update.message.from_user.id):
                continue
            await _handle_command(bot, config, state, update.message.chat_id, update.message.text)

        elif update.callback_query:
            if not await is_authorized(bot, update.callback_query.from_user.id):
                continue
            await _handle_callback(bot, config, state, update.callback_query)

        storage.save_state(config, state)

    storage.save_state(config, state)
