# HelloFides Bot

A Telegram bot that sends Jubayer Ahmed ready-to-post content for two channels:

- **LinkedIn - Personal** (his own profile, 3x/week)
- **Facebook - HelloFides** (the company page, 4x/week)

The bot never posts anywhere by itself. It only sends you a photocard image + caption
+ hashtags on Telegram, and you post it manually.

Everything runs for **$0**: Telegram Bot API, GitHub Actions (free scheduler),
Google Gemini free tier, and Pillow (no paid design tools).

---

## 1. What you need to create (15-20 minutes, one time)

### 1a. Create the Telegram bot with BotFather

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a name (e.g. "HelloFides Content Bot") and a username
   ending in `bot` (e.g. `hellofides_content_bot`).
3. BotFather will reply with a **token** that looks like `123456789:AAExampleToken`.
   Copy it - this is your `TELEGRAM_BOT_TOKEN`.

### 1b. Get your Telegram chat ID (so the bot only talks to you)

1. In Telegram, search for **@userinfobot** and send it any message.
2. It replies with your numeric user ID, e.g. `987654321`. Copy it - this is your
   `TELEGRAM_CHAT_ID`.
3. Now open a chat with the bot you just created (search its username) and send
   `/start` once, so Telegram allows it to message you first.

### 1c. Get a free Gemini API key

1. Go to Google AI Studio and sign in with your Google account.
2. Create an API key (free tier). Copy it - this is your `GEMINI_API_KEY`.
3. Free tier limits (subject to change by Google): a daily request cap and a
   requests-per-minute cap on the flash model this bot uses. This bot only makes a
   handful of requests per day (a few posts + occasional regenerate taps), which is
   comfortably inside the free tier.

### 1d. Put this code on GitHub

1. Create a **new GitHub repository**. Make it **Public** (see "Why public?" below).
2. Push this folder to it:
   ```
   git init
   git add .
   git commit -m "Initial commit: HelloFides bot"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

### 1e. Add your secrets to GitHub

In your new GitHub repo: **Settings -> Secrets and variables -> Actions -> New repository secret**.
Add all three:

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from step 1a |
| `TELEGRAM_CHAT_ID` | from step 1b |
| `GEMINI_API_KEY` | from step 1c |

### 1f. Turn on the workflow

1. Go to the **Actions** tab of your repo. If prompted, click "I understand my
   workflows, go ahead and enable them."
2. You'll see **HelloFides Bot** listed. That's it - the schedule in
   `.github/workflows/schedule.yml` is already active.

### Why public?

The bot checks Telegram for new commands every 5 minutes, which means the workflow
runs about 288 times a day. GitHub Actions minutes are **unlimited and free on
public repositories**. Private repos only get 2,000 free minutes/month, which this
polling frequency would exceed (that would start costing money, breaking the
zero-cost requirement). No secrets or personal data live in the repo itself - they're
all in GitHub Secrets - so a public repo is safe. If you'd rather keep it private,
you can edit the `*/5 * * * *` line in `schedule.yml` to poll less often (e.g. every
15-30 minutes), which will fit in the private free tier but makes commands slower to
respond.

---

## 2. Test it before relying on the schedule

### Test locally first (optional but recommended)

```
cd HelloFides_Bot
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env         # then fill in your 3 real values in .env
```

`main.py` automatically loads `.env` for local runs (it's gitignored, so your real
keys never get committed). Then just run:

```
python main.py scheduled --mode personal
python main.py scheduled --mode company
python main.py poll
```

The first two should send you a full Personal and Company post on Telegram right
away. The third checks for and responds to any commands/button taps you've sent.

### Test on GitHub Actions

Go to **Actions -> HelloFides Bot -> Run workflow**, choose `personal`, `company`,
or `poll` from the dropdown, and run it manually any time - you don't have to wait
for the schedule. This is the easiest way to confirm your 3 secrets are correct.

---

## 3. Using the bot day to day

Commands (only you can use them - the bot ignores every other Telegram user):

- `/start` - welcome + command list
- `/post` - generate one post now (mode chosen from today's schedule)
- `/personal` - generate a LinkedIn/Personal post now
- `/company` - generate a Facebook/HelloFides post now
- `/topic <text>` - generate a post about a specific topic (bot asks which platform)
- `/schedule` - show the current posting schedule
- `/help` - show the command list

Under every photocard:
- **Regenerate** - new caption/content, same pillar and platform
- **New design** - same content, different photocard layout
- **Mark as posted** - logs it as posted and removes the buttons

## 4. Changing the schedule, voice, or brand colors

Everything tunable lives in `config.yaml` - schedule days/times, brand colors,
footer text, duplicate-topic cooldown, Gemini model name, minimum font size, etc.
If you change the **times**, also update the matching cron lines at the top of
`.github/workflows/schedule.yml` (cron is in UTC; Asia/Dhaka is UTC+6).

To use your real HelloFides logo instead of the placeholder wordmark, just replace
`assets/logo.png` with your own transparent PNG (square-ish, works best under ~500px
tall) - it's picked up automatically, no code changes needed.

## 5. How the pieces fit together

- `generator.py` - calls Gemini, demands strict JSON, validates it, checks it isn't
  a repeat of a topic from the last 60 days (`content_log.json`), retries up to 3
  times, then reports an error instead of sending anything broken.
- `designer.py` - renders the photocard with Pillow (3 layouts per mode), shrinking
  the font until the text fits (minimum ~28px), using the bundled Poppins font
  (Google Fonts, free/open license, see `assets/fonts/OFL.txt`).
- `bot.py` - Telegram commands and button handling.
- `main.py` - the entry point GitHub Actions calls (`scheduled` or `poll`).
- `storage.py` - shared helpers for config/state/log files.
- Nothing runs 24/7. Each GitHub Actions run does one thing and exits; state
  (`content_log.json`, `data/bot_state.json`) is committed back to the repo so the
  next run remembers what happened.
