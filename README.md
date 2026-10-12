# Notes MCP

A personal note-taking MCP server. It uses Streamable HTTP and stores notes in SQLite.

## Tools

| Tool | What it does |
|---|---|
| `add_note` | Create a note with a title, Markdown content, tags, and an optional pin |
| `get_note` | Read one note in full |
| `list_notes` | List notes newest first, with pinned notes on top. Filter by tag or pinned. Paginated |
| `search_notes` | Search titles, content and tags. Every word must match |
| `update_note` | Change a note's title, content, tags or pin, or append text to it |
| `delete_note` | Delete a note |
| `list_tags` | List all tags with their counts |

## Run locally

```bash
uv sync
MCP_API_KEY=dev uv run python server.py   # http://localhost:8000/mcp
```

## Deploy on Railway

1. Push this folder to a GitHub repo. In Railway, choose **New → GitHub Repo**. Railway finds the `Dockerfile` through `railway.json`.
2. **Add a volume** (service → right-click → Attach Volume) mounted at `/data`. Without it, your notes are wiped on every redeploy.
3. **Variables:** set `MCP_API_KEY` to a long random string, for example `openssl rand -hex 32`. Railway sets `PORT` for you.
4. **Settings → Networking → Generate Domain.**

Your MCP endpoint is `https://<your-app>.up.railway.app/mcp`. You can check that it's up at `/health`.

## Connect a client

**Claude Code:**
```bash
claude mcp add --transport http notes https://<your-app>.up.railway.app/mcp \
  --header "Authorization: Bearer <MCP_API_KEY>"
```

**Clients that can't send headers** (such as claude.ai → Settings → Connectors → Add custom connector): put the key in the URL:
```
https://<your-app>.up.railway.app/mcp?key=<MCP_API_KEY>
```

## Config

| Env var | Default | |
|---|---|---|
| `MCP_API_KEY` | unset (no auth, which prints a warning) | Required in production |
| `NOTES_DB_PATH` | `/data/notes.db` in Docker, `./data/notes.db` locally | Path to the SQLite file |
| `PORT` | `8000` | Set automatically by Railway |
