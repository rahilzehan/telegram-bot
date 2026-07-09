# Content Unlock Bot

A Telegram bot that gates a file download behind required channel
memberships. Users must join every configured channel before they can unlock
the download. Membership is re-checked before every unlock request, so leaving
a channel immediately revokes access.

Built with [python-telegram-bot](https://python-telegram-bot.org/) (v21, async).

## Features

- **Channel verification** - users must join all required channels before
  getting access. A `Verify Channels` button checks membership in every
  channel and denies access if any is missing.
- **Re-verification** - membership is re-checked on every unlock request. If a
  user leaves a channel later, access is blocked until they re-verify.
- **Live admin configuration** - the download/website/shortener URLs and the
  required-channel list are managed via admin commands and persisted in
  `settings.json`. Changes take effect immediately, with no code edits or
  restart.
- **Content unlock flow** - after verification the bot shows `Unlock Download`
  (opens the shortener), then `I Completed`, then `Download File` and
  `Open Website`.
- **Admin tools** - update URLs, manage channels, view statistics, and
  broadcast a message to all users.
- **Local JSON storage** - users, statistics and settings are stored in
  `users.json`, `stats.json` and `settings.json`. No database or cloud
  services required.
- **Config via `.env`** - the bot token, admin username and the initial seed
  values are read from `.env`.

## Requirements

- Python 3.10+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

1. **Clone and enter the project**

   ```bash
   git clone <repository-url>
   cd telegram-bot-project
   ```

2. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure environment variables**

   Copy the example file and fill in your values:

   ```bash
   cp .env.example .env
   ```

   | Variable         | Description                                                    |
   | ---------------- | -------------------------------------------------------------- |
   | `BOT_TOKEN`      | Telegram bot token from @BotFather                             |
   | `ADMIN_USERNAME` | Admin Telegram username (with or without `@`)                  |
   | `SHORTENER_URL`  | Initial shortener URL (later editable via `/setshortener`)     |
   | `WEBSITE_URL`    | Initial website URL (later editable via `/setwebsite`)         |
   | `MEDIAFIRE_URL`  | Initial download URL (later editable via `/setfile`)           |

   The URL values seed `settings.json` on first run. After that, the live
   values in `settings.json` (managed by admin commands) take precedence.

4. **Make the bot an admin of every required channel**

   For membership checks to work, the bot account must be a member (ideally an
   administrator) of each required channel.

5. **Run the bot**

   ```bash
   python bot.py
   ```

## Admin commands

These only work for the user whose Telegram username matches `ADMIN_USERNAME`.
All changes are saved to `settings.json` and take effect immediately, without
restarting the bot.

| Command                         | Description                                  |
| ------------------------------- | -------------------------------------------- |
| `/setfile <url>`                | Set the download (MediaFire) URL             |
| `/setwebsite <url>`             | Set the website URL                          |
| `/setshortener <url>`           | Set the shortener URL                        |
| `/addchannel <id> <url> [title]`| Add a required channel                       |
| `/removechannel <id>`           | Remove a required channel                    |
| `/listchannels`                 | List all required channels                   |
| `/stats`                        | Show user and unlock statistics              |
| `/broadcast`                    | Broadcast a message to all users             |

### Channels

- **Public channels:** id is the `@username`, url is `https://t.me/<username>`.
- **Private channels:** id is the numeric chat id (e.g. `-1001234567890`), url
  is the invite link. The bot must be an admin of the private channel.

Example:

```
/addchannel @mychannel https://t.me/mychannel My Channel
/addchannel -1001234567890 https://t.me/+abcdef Private VIP
/removechannel @mychannel
```

## Data files

- `users.json` - one record per user (id, username, verified flag, unlock
  request count, timestamps).
- `stats.json` - global counters such as total unlock requests.
- `settings.json` - live URLs and required-channel list (seeded from `.env`).

All three files are created automatically and are git-ignored.

## Project structure

```
bot.py           # Handlers, admin commands and application bootstrap
config.py        # Env loading and seed values
settings.py      # Runtime, editable settings (URLs + channels) in settings.json
validators.py    # Channel membership checks
storage.py       # Local JSON storage for users and stats
requirements.txt # Python dependencies
.env.example     # Template for environment variables
```
