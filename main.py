"""
HelloFides Bot - entry point.

Two things this script can do (called by .github/workflows/schedule.yml):

  python main.py scheduled --mode personal   # generate + send today's scheduled post
  python main.py scheduled --mode company
  python main.py poll                        # check for and handle Telegram commands/buttons

This script does not run forever - it does one thing and exits. GitHub Actions
cron calls it on a schedule. See README.md for setup.
"""
import argparse
import asyncio
import sys

import bot
import designer
import generator
import storage


async def run_scheduled(mode):
    config = storage.load_config()
    designer.ensure_placeholder_logo(config)
    state = storage.load_state(config)

    telegram_bot = bot.get_bot()
    chat_id = bot.get_owner_id()

    try:
        entry = generator.generate_post(config, state, mode)
    except generator.GenerationError as exc:
        print(f"Generation failed: {exc}", file=sys.stderr)
        await bot.send_error(telegram_bot, chat_id, str(exc))
        storage.save_state(config, state)
        sys.exit(1)

    await bot.send_post(telegram_bot, chat_id, config, state, entry)
    print(f"Sent scheduled {mode} post: {entry['id']} - {entry['hook']}")


async def run_poll():
    config = storage.load_config()
    designer.ensure_placeholder_logo(config)
    state = storage.load_state(config)
    await bot.poll_once(config, state)
    print("Poll complete.")


def main():
    parser = argparse.ArgumentParser(description="HelloFides Telegram content bot")
    sub = parser.add_subparsers(dest="command", required=True)

    scheduled = sub.add_parser("scheduled", help="Generate and send today's scheduled post")
    scheduled.add_argument("--mode", required=True, choices=["personal", "company"])

    sub.add_parser("poll", help="Process pending Telegram commands/button presses")

    args = parser.parse_args()

    if args.command == "scheduled":
        asyncio.run(run_scheduled(args.mode))
    elif args.command == "poll":
        asyncio.run(run_poll())


if __name__ == "__main__":
    main()
