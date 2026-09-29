# Free, local-first Gmail agent

This CLI reads Gmail through Google's official API. It makes no paid AI API calls: summaries use a local extractive method by default. Optional Ollama summaries run on your computer and do not send email content to a cloud model.

## Setup

1. In Google Cloud Console, enable the Gmail API and create an OAuth client ID of type **Desktop app**.
2. Download the client JSON as `client_secret.json` in this folder. Keep it private; it is ignored by Git.
3. Install dependencies with `venv\Scripts\python.exe -m pip install -r requirements.txt`.
4. Run `venv\Scripts\python.exe main.py`. On first run, approve the requested Gmail access in your browser. The refresh token is stored locally in `token.json`.

The first authorization requests Gmail modify access so the cleanup tool can move selected messages to Trash. Revoking access is available from your Google Account security settings.

## Commands

```powershell
venv\Scripts\python.exe main.py overview
venv\Scripts\python.exe main.py summarize --query "newer_than:7d" --limit 100
venv\Scripts\python.exe main.py report --query "newer_than:30d" --output reports\monthly.md
venv\Scripts\python.exe main.py cleanup --query "category:promotions older_than:90d" --limit 20
```

Mailbox reads are paginated. `--limit 0` scans every matching message; start with a smaller limit because a full scan makes one Gmail API read per message. Gmail search syntax is supported. The report groups messages by sender and recipient and includes a short summary for each message.

Cleanup always prints the matching messages first and requires you to type `yes`; it moves messages to Trash, never permanently deletes them. `--yes` skips that confirmation and should only be used in automation after validating the query.

## Optional local AI summaries

Install and run [Ollama](https://ollama.com/), then pull a small model such as `llama3.2:3b`:

```powershell
ollama pull llama3.2:3b
venv\Scripts\python.exe main.py summarize --query "newer_than:7d" --engine ollama
```

Set `OLLAMA_MODEL` or `OLLAMA_URL` to use a different local model/server. When Ollama is unavailable, the tool falls back to extractive summaries. Keep in mind that enabling an Ollama server on another machine sends message text to that server.