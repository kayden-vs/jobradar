# JobRadar — Agent Rules

## 1. Context-First Workflow

**Before writing or modifying any code**, read the `jobradar-context` skill:
1. Start with `SKILL.md` — it has the project summary, structure, and current state.
2. If your task touches architecture, modules, or schemas, also read `references/architecture.md`.
3. If your task involves a design decision (new feature, technology choice, trade-off), read `references/decisions.md`.
4. If you need recent change history, read `references/changelog.md` (last 15 entries only, unless investigating older context).

**Do NOT re-scan the full codebase if the skill files already answer your question.**

## 2. Mandatory Update Protocol

After implementing any code change, you **MUST** update the context files:

### changelog.md (ALWAYS)
- Add a new entry at the **top** of the file (reverse-chronological order).
- Format:
  ```markdown
  ## [YYYY-MM-DD] Brief title
  **What**: What was changed (1–3 sentences)
  **Why**: Why the change was made
  **Files**: List of modified/created/deleted files
  **Status**: Complete | In Progress | Reverted
  ```

### architecture.md (IF applicable)
- Update if you added/removed/renamed a module, changed the DB schema, modified the pipeline flow, added a new source, or changed API integrations.

### decisions.md (IF applicable)
- Add a new ADR entry if you made a non-trivial design decision (chose one approach over another, introduced a new dependency, changed a convention).

### SKILL.md (RARELY)
- Update only if the project summary, structure, or current state section is now inaccurate (e.g., a disabled source was fixed and re-enabled, or a new top-level module was added).

## 3. Changelog Size Management

When `changelog.md` exceeds **300 lines**:
1. Move all entries **except the last 30** into `references/changelog_archive.md` (append at the end).
2. Add a note at the bottom of `changelog.md`: `> Older entries archived in changelog_archive.md`

## 4. Coding Conventions

- **Python 3.11+** — use `str | None` union syntax, not `Optional[str]`.
- **No unnecessary dependencies** — check `requirements.txt` before adding anything. Prefer stdlib.
- **Logging** — use `logging.getLogger(__name__)` in every module. Never `print()` for operational output.
- **Config** — all user-tunable values go in `profile.yaml`. No hardcoded preferences in code.
- **Database** — SQLite via `storage/db.py`. All DB access goes through this module. Schema migrations use safe `ALTER TABLE ADD COLUMN` with try/except for idempotency.
- **Job dict shape** — all pipeline stages pass `list[dict]` where each dict has keys: `title`, `company`, `location`, `url`, `description`, `source`, `salary`, `posted_at`. Optional: `tags`, `batch_year`, `stipend`.
- **Error handling** — sources should catch and log errors per-company/per-page, never crash the full pipeline.
- **Secrets** — `.env` file, loaded via `python-dotenv`. Keys: `GROQ_API_KEY`, `SERPER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Comments** — preserve all existing comments and docstrings unrelated to your change.
