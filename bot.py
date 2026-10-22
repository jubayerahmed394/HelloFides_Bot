"""
Telegram bot logic for HelloFides Bot.

Runs as a short-lived script (invoked by GitHub Actions on a schedule), not
a long-running process: each call fetches any new Telegram updates since the
last run, handles them, and exits. State (update offset, pending posts,
pillar rotation) is saved to data/bot_state.json and committed back to the
repo by the workflow so it survives between runs.

Only replies to the Telegram user ID in the TELEGRAM_CHAT_ID env var.
"""
import io
import os
import uuid

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

import designer
import generator
import storage

PILLAR_DISPLAY = {
    "strategy": "Social media strategy",
    "stats": "Facts and stats",
    "howto": "Practical how-to tip",
    "lesson": "Lesson learned / mini case study",
}

HELP_TEXT = (
    "HelloFides content bot - I generate ready-to-post content, you post it manually.\n\n"
    "Commands:\n"
    "/post - generate one post now (mode picked from today's schedule)\n"
    "/personal - generate a LinkedIn (personal) post now\n"
    "/company - generate a Facebook (HelloFides) post now\n"
    "/topic <text> - generate a post about a specific topic (I'll ask which platform)\n"
    "/schedule - show the posting schedule\n"
    "/help - show this message\n\n"
    "Under each photocard you'll get buttons to Regenerate the caption, make a New design, "
    "or Mark as posted."
)


def get_owner_id():
    raw = os.environ.get("TELEGRAM_CHAT_ID")
    if not raw:
        raise RuntimeError("TELEGRAM_CHAT_ID environment variable is not set.")
    return int(raw)


def get_bot():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    return Bot(token=token)


def _post_keyboard(post_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Regenerate", callback_data=f"regen:{post_id}"),
            InlineKeyboardButton("New design", callback_data=f"newdesign:{post_id}"),
        ],
        [InlineKeyboardButton("Mark as posted", callback_data=f"posted:{post_id}")],
    ])


async def send_error(bot, chat_id, message):
    await bot.send_message(chat_id=chat_id, text=f"HelloFides bot error:\n{message}")


async def send_post(bot, chat_id, config, state, entry, variant=None):
    """Sends the photocard + caption + hashtags + label (+ source) as separate
    Telegram messages, per the required delivery format, with action buttons."""
    img, used_variant = designer.render_photocard(config, entry, variant=variant)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "photocard.png"

    header = f"{entry['platform']}\nPillar: {PILLAR_DISPLAY.get(entry['pillar'], entry['pillar'])}"

    sent_photo = await bot.send_photo(
        chat_id=chat_id,
        photo=buf,
        caption=header,
        reply_markup=_post_keyboard(entry["id"]),
    )
    await bot.send_message(chat_id=chat_id, text=entry["caption"])
    await bot.send_message(chat_id=chat_id, text=" ".join(entry["hashtags"]))
    if entry.get("source"):
        await bot.send_message(chat_id=chat_id, text=f"Source: {entry['source']}")

    state["pending_posts"][entry["id"]] = {
        "entry": entry,
        "variant": used_variant,
        "chat_id": chat_id,
        "message_id": sent_photo.message_id,
    }
    storage.save_state(config, state)


async def _handle_command(bot, config, state, chat_id, text):
    command, _, arg = text.partition(" ")
    command = command.split("@")[0].lower()
    arg = arg.strip()

    if command == "/start":
        await bot.send_message(chat_id=chat_id, text="Welcome to the HelloFides bot.\n\n" + HELP_TEXT)

    elif command == "/help":
        await bot.send_message(chat_id=chat_id, text=HELP_TEXT)

    elif command == "/schedule":
        sched = config["schedule"]
        msg = (
            f"Timezone: {config['timezone']}\n\n"
            f"LinkedIn - Personal: {', '.join(sched['personal']['days'])} at {sched['personal']['time']}\n"
            f"Facebook - HelloFides: {', '.join(sched['company']['days'])} at {sched['company']['time']}\n\n"
            "Edit config.yaml and schedule.yml to change this."
        )
        await bot.send_message(chat_id=chat_id, text=msg)

    elif command in ("/post", "/personal", "/company"):
        if command == "/personal":
            mode = "personal"
        elif command == "/company":
            mode = "company"
        else:
            mode = _mode_for_today(config)
        await bot.send_message(chat_id=chat_id, text=f"Generating a {mode} post...")
        try:
            entry = generator.generate_post(config, state, mode)
        except generator.GenerationError as exc:
            await send_error(bot, chat_id, str(exc))
            return
        await send_post(bot, chat_id, config, state, entry)

    elif command == "/topic":
        if not arg:
            await bot.send_message(chat_id=chat_id, text="Usage: /topic <your topic>")
            return
        topic_id = uuid.uuid4().hex[:8]
        state["pending_topics"][topic_id] = {"text": arg, "chat_id": chat_id}
        storage.save_state(config, state)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("LinkedIn - Personal", callback_data=f"topicmode:personal:{topic_id}"),
            InlineKeyboardButton("Facebook - HelloFides", callback_data=f"topicmode:company:{topic_id}"),
        ]])
        await bot.send_message(chat_id=chat_id, text=f"Topic: \"{arg}\"\nWhich platform?", reply_markup=keyboard)

    else:
        await bot.send_message(chat_id=chat_id, text="Unknown command. Send /help to see what I can do.")


def _mode_for_today(config):
    today = storage.now_dhaka(config).strftime("%a")
    if today in config["schedule"]["personal"]["days"]:
        return "personal"
    if today in config["schedule"]["company"]["days"]:
        return "company"
    return "personal"


async def _handle_callback(bot, config, state, callback_query):
    data = callback_query.data or ""
    chat_id = callback_query.message.chat_id if callback_query.message else get_owner_id()
    parts = data.split(":")
    action = parts[0]

    if action == "topicmode" and len(parts) == 3:
        _, mode, topic_id = parts
        pending = state["pending_topics"].pop(topic_id, None)
        storage.save_state(config, state)
        await callback_query.answer()
        if not pending:
            await bot.send_message(chat_id=chat_id, text="That topic request expired, please /topic again.")
            return
        await bot.send_message(chat_id=chat_id, text=f"Generating {mode} post about \"{pending['text']}\"...")
        try:
            entry = generator.generate_post(config, state, mode, custom_topic=pending["text"])
        except generator.GenerationError as exc:
            await send_error(bot, chat_id, str(exc))
            return
        await send_post(bot, chat_id, config, state, entry)
        return

    post_id = parts[1] if len(parts) > 1 else None
    pending = state["pending_posts"].get(post_id) if post_id else None

    if action == "regen":
        await callback_query.answer("Regenerating...")
        if not pending:
            await bot.send_message(chat_id=chat_id, text="This post is no longer pending (maybe too old).")
            return
        old_entry = pending["entry"]
        try:
            new_entry = generator.generate_post(
                config, state, old_entry["mode"], forced_pillar=old_entry["pillar"]
            )
        except generator.GenerationError as exc:
            await send_error(bot, chat_id, str(exc))
            return
        await send_post(bot, chat_id, config, state, new_entry)

    elif action == "newdesign":
        await callback_query.answer("Making a new design...")
        if not pending:
            await bot.send_message(chat_id=chat_id, text="This post is no longer pending (maybe too old).")
            return
        entry = pending["entry"]
        next_variant = (pending.get("variant", 0) + 1) % len(designer.LAYOUTS)
        await send_post(bot, chat_id, config, state, entry, variant=next_variant)

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
                    chat_id=pending["chat_id"],
                    message_id=pending["message_id"],
                    caption=f"{pending['entry']['platform']}\nMarked as posted (done)",
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
    owner_id = get_owner_id()
    offset = state.get("last_update_id", 0)

    updates = await bot.get_updates(offset=offset + 1 if offset else None, timeout=0)
    for update in updates:
        state["last_update_id"] = update.update_id

        if update.message and update.message.text:
            if update.message.from_user.id != owner_id:
                continue
            await _handle_command(bot, config, state, update.message.chat_id, update.message.text)

        elif update.callback_query:
            if update.callback_query.from_user.id != owner_id:
                continue
            await _handle_callback(bot, config, state, update.callback_query)

        storage.save_state(config, state)

    storage.save_state(config, state)
