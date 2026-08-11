# Agent tooling: Cursor / Claude Code / Codex

How the three tools share config in this repo, and what stays global on your machine.

## Instructions (shared — edit one file)

| Tool | Reads | How |
|---|---|---|
| Cursor | `AGENTS.md` | native support |
| Codex | `AGENTS.md` | native support |
| Claude Code | `CLAUDE.md` | one-line `@AGENTS.md` import |

Rule: **all project instructions go in `AGENTS.md`.** Don't add `.cursor/rules/` or grow `CLAUDE.md` — that's how the three drift apart. (CLAUDE.md is a plain import, not a symlink, because OneDrive sync breaks symlinks.)

## MCP servers (project-level)

Playwright MCP is configured for dashboard testing:

- Claude Code: `.mcp.json` (repo root)
- Cursor: `.cursor/mcp.json`
- Codex: no project-level file — add to `~/.codex/config.toml`:

```toml
[mcp_servers.playwright]
command = "npx"
args = ["@playwright/mcp@latest", "--headless"]
```

Keep the two JSON files identical when adding servers, and mirror to the Codex TOML.

### Worth adding later (only if you actually use them)

- A sqlite MCP pointed at `data/*.sqlite3` for inspecting local state without writing ad-hoc scripts.
- A postgres MCP for the docker-compose instance — read-only credentials only.

## Global config hygiene checklist

Since your MCPs are configured globally, check these once:

1. `~/.cursor/mcp.json`, `~/.claude.json` (or `claude mcp list`), `~/.codex/config.toml` — do all three list the same servers? Prune servers you don't use; every enabled MCP adds tool definitions to context on **every** request, which is the main efficiency leak.
2. Anything project-specific (like Playwright for this repo) should live in the project files above, not globally — global servers load in every repo whether needed or not.
3. Claude Code skills (`~/.claude/skills/`) and Codex prompts (`~/.codex/prompts/`): if a playbook is Atlas-specific, move it into the repo (`.claude/skills/` works project-level; for Cursor/Codex, reference it from `AGENTS.md`) so all three tools and any teammate get it.

## Local state (gitignored)

`.cursor/hooks/state/` and `.claude/settings.local.json` are per-machine and ignored. `AGENTS.md`, `CLAUDE.md`, `.mcp.json`, `.cursor/mcp.json` are committed and shared.
