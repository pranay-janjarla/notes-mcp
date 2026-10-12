"""Notes MCP server: a personal note-taker over Streamable HTTP, backed by SQLite."""

import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

DB_PATH = Path(os.environ.get("NOTES_DB_PATH", "./data/notes.db"))
API_KEY = os.environ.get("MCP_API_KEY", "")
PORT = int(os.environ.get("PORT", "8000"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_tags(tags: list[str] | None) -> str:
    # Stored as ",a,b," so a tag can be matched exactly with LIKE '%,tag,%'.
    clean = sorted({t.strip().lower() for t in tags or [] if t.strip()})
    return f",{','.join(clean)}," if clean else ""


def to_dict(row: sqlite3.Row, full: bool = True) -> dict:
    note = {
        "id": row["id"],
        "title": row["title"],
        "tags": [t for t in row["tags"].split(",") if t],
        "pinned": bool(row["pinned"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if full:
        note["content"] = row["content"]
    else:
        preview = row["content"].strip().replace("\n", " ")
        note["preview"] = preview[:160] + ("…" if len(preview) > 160 else "")
    return note


def fetch(conn: sqlite3.Connection, note_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise ToolError(f"Note {note_id} not found")
    return row


mcp = MCPServer(
    name="notes",
    instructions=(
        "A personal notes store. Use add_note to save something the user wants to "
        "remember, search_notes / list_notes to find notes, and get_note to read one in "
        "full before editing it. Tags are lowercase keywords."
    ),
)


@mcp.tool()
def add_note(title: str, content: str = "", tags: list[str] | None = None, pinned: bool = False) -> dict:
    """Create a new note. Content may be Markdown."""
    ts = now()
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO notes (title, content, tags, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (title.strip(), content, normalize_tags(tags), int(pinned), ts, ts),
        )
        return to_dict(fetch(conn, cur.lastrowid))


@mcp.tool()
def get_note(note_id: int) -> dict:
    """Return one note with its full content."""
    with closing(db()) as conn:
        return to_dict(fetch(conn, note_id))


@mcp.tool()
def list_notes(tag: str | None = None, pinned_only: bool = False, limit: int = 20, offset: int = 0) -> dict:
    """List notes (newest first, pinned on top) with a short preview. Optionally filter by tag."""
    where, args = [], []
    if tag:
        where.append("tags LIKE ?")
        args.append(f"%,{tag.strip().lower()},%")
    if pinned_only:
        where.append("pinned = 1")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(limit, 100))
    with closing(db()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM notes {clause}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM notes {clause} ORDER BY pinned DESC, updated_at DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
    return {"total": total, "notes": [to_dict(r, full=False) for r in rows]}


@mcp.tool()
def search_notes(query: str, limit: int = 20) -> dict:
    """Search note titles, content and tags. Every whitespace-separated word must match (case-insensitive)."""
    words = query.split()
    if not words:
        return {"total": 0, "notes": []}
    clause = " AND ".join("(title LIKE ? OR content LIKE ? OR tags LIKE ?)" for _ in words)
    args = [f"%{w}%" for w in words for _ in range(3)]
    with closing(db()) as conn:
        rows = conn.execute(
            f"SELECT * FROM notes WHERE {clause} ORDER BY pinned DESC, updated_at DESC LIMIT ?",
            [*args, max(1, min(limit, 100))],
        ).fetchall()
    return {"total": len(rows), "notes": [to_dict(r, full=False) for r in rows]}


@mcp.tool()
def update_note(
    note_id: int,
    title: str | None = None,
    content: str | None = None,
    append: str | None = None,
    tags: list[str] | None = None,
    pinned: bool | None = None,
) -> dict:
    """Edit a note. Only the fields you pass change. `content` replaces the body; `append` adds text to the end of it. `tags` replaces the tag list."""
    with closing(db()) as conn, conn:
        row = fetch(conn, note_id)
        body = row["content"] if content is None else content
        if append:
            body = f"{body.rstrip()}\n\n{append}" if body.strip() else append
        conn.execute(
            "UPDATE notes SET title = ?, content = ?, tags = ?, pinned = ?, updated_at = ? WHERE id = ?",
            (
                row["title"] if title is None else title.strip(),
                body,
                row["tags"] if tags is None else normalize_tags(tags),
                row["pinned"] if pinned is None else int(pinned),
                now(),
                note_id,
            ),
        )
        return to_dict(fetch(conn, note_id))


@mcp.tool()
def delete_note(note_id: int) -> dict:
    """Permanently delete a note."""
    with closing(db()) as conn, conn:
        title = fetch(conn, note_id)["title"]
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return {"deleted": note_id, "title": title}


@mcp.tool()
def list_tags() -> dict:
    """List every tag in use with how many notes carry it."""
    counts: dict[str, int] = {}
    with closing(db()) as conn:
        for (tags,) in conn.execute("SELECT tags FROM notes WHERE tags != ''"):
            for t in filter(None, tags.split(",")):
                counts[t] = counts.get(t, 0) + 1
    return {"tags": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


class APIKeyAuth:
    """Require MCP_API_KEY on /mcp, as `Authorization: Bearer <key>` or `?key=<key>`.

    The query-string form exists for clients (like claude.ai custom connectors) that
    can't send custom headers.
    """

    def __init__(self, app, key: str):
        self.app, self.key = app, key

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.key and not scope["path"].startswith("/health"):
            request = Request(scope)
            header = request.headers.get("authorization", "")
            supplied = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("key", "")
            if not secrets.compare_digest(supplied.encode(), self.key.encode()):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app():
    init_db()
    app = mcp.streamable_http_app(host="0.0.0.0", stateless_http=True, json_response=True)
    return APIKeyAuth(app, API_KEY)


if __name__ == "__main__":
    if not API_KEY:
        print("WARNING: MCP_API_KEY is not set — the server is open to anyone with the URL.")
    uvicorn.run(build_app(), host="0.0.0.0", port=PORT, proxy_headers=True, forwarded_allow_ips="*")
