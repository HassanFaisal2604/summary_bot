## Summary Discord Bot (Gemini-based)

This is a **separate Discord bot** that runs alongside your existing `AppilotBot`.
It scans configured channels for the last 24 hours of activity, filters messages
by keywords, summarizes them (using Gemini when available), and **DMs a daily
report** to the configured owner account at **1 PM Pakistan time (PKT)**.

### Environment variables

Set these for the summary bot process:

- `SUMMARY_DISCORD_TOKEN` – token of the new Discord bot application
- `SUMMARY_SERVER_ID` – numeric ID of the Discord server to summarize
- `SUMMARY_OWNER_USER_ID` – numeric ID of the user who receives the DM summary
- `SUMMARY_KEYWORDS` – comma-separated keywords to treat as relevant (e.g. `task,status,completed,error,follow`)
- `SUMMARY_TIMEZONE` – timezone string, defaults to `Asia/Karachi`
- `GEMINI_API_KEY` – your Gemini API key
- `GEMINI_MODEL` – (optional) Gemini model name, defaults to `gemini-1.5-flash`

### Running locally

```bash
cd summary_bot
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m summary_bot.main
```

### Deploying on VPS (alongside existing bot)

1. Copy the `summary-bot/` folder to your VPS next to your existing backend.
2. Create a dedicated virtualenv and install `summary-bot/requirements.txt`.
3. Export the environment variables above (or use a `.env` file).
4. Create a separate process definition (e.g. `systemd` service) that runs:

   ```bash
   cd /path/to/summary_bot
   /path/to/venv/bin/python -m summary_bot.main
   ```

5. Enable and start the service so it runs on boot and restarts on failure.


