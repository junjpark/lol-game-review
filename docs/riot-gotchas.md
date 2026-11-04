# Riot API gotchas

Expensive to learn; read before touching `riot/`, `extract/` or collection scripts. Moved verbatim
from CLAUDE.md on Nov 4, 2026. Add new ones here (and a check, if one can catch it).

- Two routing schemes: platform (`na1`) for LEAGUE-V4, regional (`americas`) for
  MATCH-V5 and ACCOUNT-V1. See `src/lolreview/riot/routing.py`.
- **Hong Kong = server `tw2`** (Taiwan/HK/Macao): ladder on `tw2.api…`, match history on
  `sea.api…`, Riot ID lookups on `asia.api…`. ACCOUNT-V1 is only served by americas/asia/europe
  (`routing.account_host`). Match downloads route by the match ID's own prefix
  (`routing.match_host`), so `TW2_…` games fetch fine whatever `.env` says. ph2/th2 merged into
  sg2 in Jan 2025.
- **One corpus per server.** `collect_summoners.py` stamps each player with `platform`;
  `collect_matches.py` refuses to add games from another server to an existing corpus name.
  Use names like `emerald_tw2`.
- `gameMode: CLASSIC` does **not** mean ranked — `queueId: 420` does.
- **Patch numbering:** `gameVersion` uses `16.x` in 2026; Riot's public patch names use
  the year (`26.x`). Patch notes "26.19" = `gameVersion` "16.19". `TARGET_PATCHES` must
  use the 16.x form — a 26.x value silently rejected an entire 5,000-match pull once.
- Match IDs increase over time, so sorting cached files by name sorts oldest-first.
- `TURRET_PLATE_DESTROYED` positions are **not** at the turret (they scatter by thousands of
  units). Use the owner team + lane's outer turret from `lolreview/rift.py` instead.
  `BUILDING_KILL` positions, by contrast, are exact.
- `CHAMPION_SPECIAL_KILL` (first blood, multikill, ace) is recorded at a **different position**
  from its `CHAMPION_KILL`, typically 300–400 units away (likely killer vs. victim location,
  unverified). Every one matches a kill by the same killer within 50 ms; join on that.
- `DRAGON_SOUL_GIVEN` with `teamId: 0` fires after the game's **2nd** dragon to reveal the
  soul type; only `teamId` 100/200 means a team earned it (always at its 4th dragon).
- `WARD_PLACED`/`WARD_KILL` also fire for champion ability objects: `wardType` `UNDEFINED`
  (Zyra seeds, Jhin traps, Yorick, Illaoi, …) and `TEEMO_MUSHROOM`. Count only
  `extract.events.REAL_WARD_TYPES` to match Riot's ward totals.
- Team kills on the scoreboard = player kills + deaths to turrets/minions (credited to the
  enemy); deaths to neutral monsters count for nobody. See `is_scoreboard_execution`.
- Riot's `wasAfk` participant flag is reliable (every flagged player's team lost); prefer it
  to idle-position heuristics.
- Map landmarks (turrets, inhibitors, pits, camps) live in `src/lolreview/rift.py`, measured
  from the corpus. The map is point-symmetric via `rift.mirror()`.
- `gameDuration` is in seconds; timeline event timestamps are in milliseconds.
- `timeEnemySpentControlled` (participant frames) is **milliseconds** of CC dealt, summed
  over enemies; it equals the match summary's `totalTimeCCDealt` (seconds) × 1000 — not
  `timeCCingOthers`.
- Use `teamPosition` for role, not `role`/`lane`/`individualPosition`.
- Timeline indexes players by participant ID; the match file by PUUID. Join via
  `info.participants[]`.
- CS is `minionsKilled + jungleMinionsKilled`, not one field.
- `totalGold` (cumulative earned) vs `currentGold` (held now) — using the wrong one
  silently corrupts every gold-diff feature.
- Development API keys expire every 24h. Never hard-code; always via `.env`.
- Timelines are ~1.8 MB pretty-printed. The real fetcher must gzip or minify — at 5k
  matches, `indent=2` would mean ~9 GB of cache.
