# HelloFides Bot

A Telegram bot that sends Jubayer Ahmed ready-to-post content for two feeds:

- **LinkedIn - Personal** (his own profile, 3x/week)
- **Facebook - HelloFides** (the company page, 3x/week)

Both are delivered to **one destination**: a private Telegram channel called
"HelloFides". The bot never posts anywhere by itself - it sends a photocard
image + caption + hashtags to that channel, and you post it manually from there.

Everything runs for **$0**: Telegram Bot API, GitHub Actions (free scheduler),
Google Gemini free tier for text (with Groq and Mistral free tiers as
automatic fallback, see section 2), Pixabay/Unsplash free tiers for photos,
and a headless Chromium browser (Playwright, free) for rendering the cards.

---

## 1. What you need to create (20-25 minutes, one time)

### 1a. Create the Telegram bot with BotFather

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a name and a username ending in `bot`.
3. BotFather replies with a **token** like `123456789:AAExampleToken`. Copy it -
   this is your `TELEGRAM_BOT_TOKEN`.

### 1b. Create the "HelloFides" channel and add the bot as admin

1. In Telegram, create a new **channel** (not a group) named "HelloFides" -
   Private is fine (recommended, since this is your own content staging area).
2. Open the channel's settings -> **Administrators** -> add your bot -> give it
   at least "Post Messages" permission.
3. Post one message to the channel yourself (e.g. "test") so there's at least
   one message in the channel's history.
4. The channel's numeric ID (starts with `-100...`) can't be read from the
   Telegram app UI directly. Easiest way: forward any message from the channel
   to **@userinfobot** - it replies with the origin chat's ID. That's your
   `TELEGRAM_CHANNEL_ID`.

### 1c. (Optional) Your own chat ID, for error notifications

1. Message **@userinfobot** directly (not a forward) - it replies with your
   numeric user ID. This is `TELEGRAM_OWNER_CHAT_ID`.
2. This is optional. If you skip it, a failed scheduled post has nowhere to
   report the error except the channel itself, which the code falls back to.

### 1d. Get a free Gemini API key

1. Go to Google AI Studio, sign in, create an API key (free tier). This is
   `GEMINI_API_KEY`.

### 1d2. (Recommended) Get free Groq and Mistral keys - fallback providers

Gemini's free tier is small (as few as 20 requests/day on some projects) and
does get exhausted. These two are only used automatically if Gemini is
unavailable - see section 2. Both are free, no card required:

1. **Groq**: console.groq.com/keys - sign in, create a key -> `GROQ_API_KEY`.
2. **Mistral**: console.mistral.ai/api-keys - sign in, create a key ->
   `MISTRAL_API_KEY`.
3. Leave either blank if you'd rather skip it - the bot just removes it from
   the fallback chain silently, it's never treated as an error.

### 1e. Get free Pixabay and Unsplash keys (for the photo on every card)

1. **Pixabay**: pixabay.com/api/docs - sign up, copy your API key ->
   `PIXABAY_API_KEY`.
2. **Unsplash**: unsplash.com/developers - create an app, copy the **Access
   Key** -> `UNSPLASH_ACCESS_KEY`.
3. Both are free, no billing. At least one is required for cards to have a
   real topic photo; having both gives more variety.

### 1f. Put this code on GitHub

```
cd HelloFides_Bot
git init
git add .
git commit -m "Initial commit: HelloFides bot"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```
Make the repo **Public** (see "Why public?" below).

### 1g. Add your secrets to GitHub

In your repo: **Settings -> Secrets and variables -> Actions -> New repository secret**.

| Secret name | Required? | Value |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | from step 1a |
| `TELEGRAM_CHANNEL_ID` | required | from step 1b |
| `TELEGRAM_OWNER_CHAT_ID` | optional | from step 1c |
| `GEMINI_API_KEY` | required | from step 1d |
| `GROQ_API_KEY` | strongly recommended | from step 1d2 |
| `MISTRAL_API_KEY` | optional | from step 1d2 |
| `PIXABAY_API_KEY` | strongly recommended | from step 1e |
| `UNSPLASH_ACCESS_KEY` | strongly recommended | from step 1e |

### 1h. Turn on the workflow

Go to the **Actions** tab, enable workflows if prompted. **HelloFides Bot**
is now scheduled per `.github/workflows/schedule.yml`.

### Why public?

The bot checks Telegram for new DM commands every 5 minutes (~288 runs/day).
GitHub Actions minutes are **unlimited and free on public repositories**;
private repos only get 2,000 free minutes/month, which this frequency (plus
the Playwright browser setup each run) would likely exceed. No secrets live
in the repo - they're all in GitHub Secrets - so a public repo is safe. If
you'd rather keep it private, reduce the `*/5 * * * *` line in `schedule.yml`
to poll less often (e.g. every 15-30 minutes).

---

## 2. Content generation resilience (why a quota hit no longer breaks anything)

Gemini's free tier can run out mid-day. Instead of failing, each scheduled
post now goes through a fallback chain, tried in this order, and the whole
chain only runs once a week per mode because of batching (see below):

1. **gemini_primary** (`gemini-3.8-flash`)
2. **gemini_lite** (`gemini-3.5-flash-lite`) - a different model, so it has
   its own separate daily quota even though it uses the same `GEMINI_API_KEY`
3. **groq** (`openai/gpt-oss-20b`) - free, no card, from step 1d2
4. **mistral** (`mistral-small-latest`) - free, no card, from step 1d2
5. **Local post bank** (`data/fallback_bank.json`) - 31 hand-written, honest,
   generic Company/Personal posts. Used only if every provider above fails.
   This step cannot fail, so a post always goes out.

Rules this follows, enforced in `generator.py` / `providers.py` /
`provider_state.py`:
- A `429` on a provider is never retried on that provider - it's recorded
  with a cooldown (read from the API's own retry delay when present) and the
  chain immediately moves to the next provider.
- Any other error (timeout, bad JSON) gets one retry on the same provider,
  then moves on.
- A provider is never permanently disabled. Once its cooldown passes,
  `gemini_primary` is tried first again automatically on the next attempt.
- **Batching**: one API call asks a provider for a whole week of posts (3
  Company + 3 Personal, different pillars) at once, saved to
  `data/content_queue.json`. Scheduled runs and `/post` just pop the next
  queued item - no API call on a normal day. The queue only refills when it
  has fewer than 3 items left for that mode, and at most once per calendar
  day, so a bad day doesn't retry every 5 minutes. This brings real API
  usage down to roughly **2 calls/week** instead of one per post.
- **Regenerate** button: tries a different queued item first; only calls a
  provider if the queue is genuinely empty.
- Telegram never receives raw API errors, stack traces, or JSON - full detail
  goes to the workflow's own run log (stderr) only. If every provider *and*
  the local bank fail (should not happen in normal use), you get one short
  message - "Content generation failed today, will retry tomorrow" - and at
  most one such message per calendar day.

Run `python test_fallback.py` to see this verified with mocked providers (no
real API calls): 429-failover, cooldown recovery, local-bank fallback, and
the once-a-day queue refill all pass without hitting the network.

---

## 3. Test it before relying on the schedule

### Test locally first (optional but recommended)

```
cd HelloFides_Bot
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env         # then fill in your real values in .env
```

`main.py` automatically loads `.env` for local runs (it's gitignored). Then:

```
python main.py scheduled --mode personal
python main.py scheduled --mode company
python main.py poll
```

The first two send a full Personal and Company post to the **channel**
(not your DM). The third checks for and responds to any DM commands you've sent.

### Test on GitHub Actions

**Actions -> HelloFides Bot -> Run workflow**, choose `personal`, `company`,
or `poll` - runs immediately, doesn't wait for the schedule.

---

## 4. Using the bot day to day

Commands - send these as a **DM to the bot** (not in the channel). Only your
`TELEGRAM_OWNER_CHAT_ID` or a current admin of the HelloFides channel can use them:

- `/post company` - generate a Facebook/HelloFides post now, sent to the channel
- `/post personal` - generate a LinkedIn/Jubayer post now, sent to the channel
- `/schedule` - show the current posting schedule
- `/help` - show the command list

Under every photocard in the channel:
- **Regenerate** - new caption/content, same pillar and platform
- **New design** - same content, different photocard layout
- **Mark as posted** - logs it as posted and removes the buttons

## 5. Changing the schedule, voice, or brand colors

Everything tunable lives in `config.yaml` - schedule days/times, brand colors,
the Personal name-block lines, duplicate-topic cooldown, Gemini model name,
minimum font size, etc. If you change the **times**, also update the matching
cron lines at the top of `.github/workflows/schedule.yml` (cron is in UTC;
Asia/Dhaka is UTC+6).

To use your real HelloFides logo, replace `assets/logo.png` with your own PNG.
To add a circular avatar to the Personal name block, add `assets/profile.jpg`
(your own photo, square works best) - picked up automatically, no code changes.

## 6. How the pieces fit together

- `generator.py` - batch generation (one call = a week of posts per mode),
  queue management, and strict validation. Checks every post isn't a repeat
  of a topic from the last 60 days (`content_log.json`).
- `providers.py` - the actual transport for each provider in the fallback
  chain (Gemini SDK, or plain HTTPS for Groq/Mistral's OpenAI-compatible
  endpoints). Normalizes every provider's output to the same JSON shape.
- `provider_state.py` - tracks each provider's cooldown/last error/requests
  today in `data/provider_state.json`, so a 429 is remembered across runs.
- `data/fallback_bank.json` - the 31-post local bank used only if every
  provider fails.
- `photo_fetch.py` - fetches a topic photo from Unsplash or Pixabay, records
  source/photographer/URL/license, and follows each API's usage rules
  (Unsplash's required download-tracking ping on actual use).
- `designer_html.py` - renders the photocard as real HTML/CSS (Poppins +
  DM Sans, navy/white/accent brand colors, photo with a navy duotone
  treatment) via a headless Chromium browser (Playwright), screenshotted to
  PNG. Three layout systems per mode.
- `bot.py` - Telegram delivery to the HelloFides channel, DM command handling,
  owner/channel-admin authorization, inline buttons, and the once-a-day
  quiet failure notice (never raw errors).
- `main.py` - the entry point GitHub Actions calls (`scheduled` or `poll`).
- `storage.py` - shared helpers for config/state/log files.
- `test_fallback.py` - mocked tests for the fallback chain (no real API calls).
- `designer.py` - the original Pillow-based renderer from the first version of
  this bot. No longer used by the live pipeline; kept only for reference.
- Nothing runs 24/7. Each GitHub Actions run does one thing and exits; state
  (`content_log.json`, `data/bot_state.json`, `data/content_queue.json`,
  `data/provider_state.json`) is committed back to the repo so the next run
  remembers what happened.

## 7. Known gaps (not yet done)

- `generator.py`'s Gemini prompts still use the original 4-pillar system
  (strategy/stats/howto/lesson) for both modes. The expanded Personal content
  types (mystery, myth-vs-reality, mistake, tip, checklist, hot take, concept
  sample, service offer, etc.) exist as hand-written design previews
  (`previews_v4/`, `previews_v5/`) but are not yet wired into live Gemini
  generation - that's a separate follow-up.
