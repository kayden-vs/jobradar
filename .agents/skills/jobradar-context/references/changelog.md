# JobRadar — Changelog

> Reverse-chronological log of changes. Agents: add new entries at the TOP.

---

## [2026-06-30] Initial context system baseline
**What**: Established the `.agents/` context system (AGENTS.md, SKILL.md, architecture.md, decisions.md, changelog.md) to eliminate repeated AI codebase scans across chat sessions.
**Why**: Every new Antigravity chat was re-scanning the entire codebase, burning thousands of tokens for context that doesn't change between sessions.
**Files**: `.agents/AGENTS.md`, `.agents/skills/jobradar-context/SKILL.md`, `references/architecture.md`, `references/decisions.md`, `references/changelog.md`
**Status**: Complete

## [2026-06-30] Codebase state snapshot (baseline)
**What**: Documenting the current state of the codebase as the starting point for the changelog.
**Why**: Future entries will be diffs against this baseline.
**Current state**:
- 16 sources implemented, 12 enabled, 5 disabled (cutshort, instahyre, wellfound, reddit, hirist)
- Pipeline: sources → dedup → prefilter → ranker (v2, 6-layer) → scorer (Groq llama-4-scout) → telegram
- Application tracker bot with followup_check (7-day draft, 14-day dead)
- Weekly summary digest (Fridays only, ISO-week guard)
- Deployment: EC2 t2.micro, EventBridge schedule, auto-shutdown
- Per-user profiles supported via CLI arg
- Ranker v2 features: skill density, concordance, holy trinity, title richness, location affinity, company tier
**Files**: All files in the repository
**Status**: Complete — this is a snapshot, not a change
