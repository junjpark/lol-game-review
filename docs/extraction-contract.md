# Extraction contract

Maps every field the review payload needs to its concrete source in Riot's JSON.
Derived from `data/raw/NA1_5628109779_*.json` (patch 16.16, 40-minute ranked solo game).

**Source files**
- `M` = `*_match.json` — end-of-game summary, identities, outcome
- `T` = `*_timeline.json` — per-minute frames and timestamped events

Paths are relative to the file root, e.g. `M info.participants[i].teamPosition`.

> ⚠️ **Read the [Gaps](#gaps) section first.** Three of the four `knowable_at_the_time`
> fields in the original payload design are **not obtainable** from this data. That
> changes the Phase 7.3 plan.

---

## Match-level

| Need | Source | Path | Notes |
|---|---|---|---|
| Is this ranked solo/duo | M | `info.queueId` | **420**. `gameMode: CLASSIC` does NOT mean ranked |
| Patch | M | `info.gameVersion` | `16.16.804.9184`; match on first two segments |
| Game length | M | `info.gameDuration` | **seconds** (timeline timestamps are ms) |
| Remake / surrender | M | `info.participants[i].gameEndedInEarlySurrender`, `.gameEndedInSurrender` | Phase 1.3.4 filter |
| Outcome | M | `info.teams[i].win` | also on each participant; team-level is the cleaner label |
| Outcome (alt) | T | last event `GAME_END.winningTeam` | redundant but handy |

---

## Identity and join

| Need | Source | Path | Notes |
|---|---|---|---|
| Participant ↔ PUUID | T | `info.participants[]` → `{participantId, puuid}` | also in `M info.participants[]`; joins either way |
| participantId values | T | keys of `info.frames[i].participantFrames` | **strings** `"1"`–`"10"`; int also inside as `.participantId` |
| Role | M | `info.participants[i].teamPosition` | TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY |
| Champion | M | `info.participants[i].championName` | |
| Team | M | `info.participants[i].teamId` | 100 / 200 |
| Lane opponent | — | same `teamPosition`, opposite `teamId` | verify `teamPosition` is never empty across a corpus |

**Role fields — verified disagreement in this match:**

| Field | Value for p1 (Garen, top) | Verdict |
|---|---|---|
| `teamPosition` | TOP | ✅ use this |
| `individualPosition` | TOP | agrees here, not guaranteed |
| `role` | NONE | ❌ different axis (NONE/SOLO/CARRY/SUPPORT) |
| `lane` | **JUNGLE** | ❌ flatly wrong for a top laner |

---

## Frame cadence

| Question | Answer |
|---|---|
| Frame spacing | `T info.frameInterval` = **60000** (stated, no inference needed) |
| Timestamp unit | **milliseconds** (`0, 60029, 120034 …`) |
| Frame 0 | Real data: 500 gold, level 1, fountain position. Valid but uninformative. Keep as a 50% anchor |
| Last frame | Aligns with game end — 2416946 ms ≈ `gameDuration` 2416 s |
| Frame count | 42 for a 40-minute game |

---

## Per-minute state

All under `T info.frames[i].participantFrames["N"]`.

| Need | Path | Notes |
|---|---|---|
| Gold (cumulative earned) | `.totalGold` | **use this for gold diff** |
| Gold (held now) | `.currentGold` | spending money, not earnings |
| Gold income rate | `.goldPerSecond` | |
| XP | `.xp` | |
| Level | `.level` | |
| CS | `.minionsKilled + .jungleMinionsKilled` | two fields, must be summed |
| Position | `.position.{x,y}` | present every frame, every player |
| **Current HP** | `.championStats.health` | ✅ available after all |
| **Max HP** | `.championStats.healthMax` | `hp_pct` = health / healthMax |
| Combat stats | `.championStats.*` | armor, attackDamage, movementSpeed, magicResist, abilityHaste, +15 more |
| Damage (cumulative) | `.damageStats.*` | `totalDamageDoneToChampions`, `totalDamageTaken`, split by magic/physical/true |
| Enemy CC time | `.timeEnemySpentControlled` | |

---

## Events

16 types, 1,622 events in this match. All under `T info.frames[i].events[]`.

| Type | n | Position? | Key fields |
|---|---|---|---|
| `WARD_PLACED` | 517 | ❌ **no** | `creatorId`, `wardType` |
| `ITEM_PURCHASED` | 269 | no | `participantId`, `itemId` |
| `ITEM_DESTROYED` | 243 | no | `participantId`, `itemId` |
| `SKILL_LEVEL_UP` | 177 | no | `participantId`, `skillSlot`, `levelUpType` |
| `LEVEL_UP` | 166 | no | `participantId`, `level` |
| `CHAMPION_KILL` | 67 | ✅ | `killerId`, `victimId`, `assistingParticipantIds`, `bounty`, `shutdownBounty`, `killStreakLength`, `victimDamageDealt[]`, `victimDamageReceived[]` |
| `TURRET_PLATE_DESTROYED` | 65 | ✅ | `killerId`, `laneType`, `teamId` |
| `WARD_KILL` | 54 | ❌ no | `killerId`, `wardType` |
| `BUILDING_KILL` | 18 | ✅ | `killerId`, `assists`, `buildingType`, `towerType`, `laneType`, `teamId`, `bounty` |
| `ITEM_UNDO` | 12 | no | `participantId`, `beforeId`, `afterId`, `goldGain` |
| `ELITE_MONSTER_KILL` | 11 | ✅ | `killerId`, `killerTeamId`, `monsterType`, `monsterSubType`, `assists`, `bounty` |
| `CHAMPION_SPECIAL_KILL` | 10 | ✅ | `killerId`, `killType`, `multiKillLength` |
| `ITEM_SOLD` | 10 | no | `participantId`, `itemId` |
| `PAUSE_END` | 1 | no | `realTimestamp` |
| `DRAGON_SOUL_GIVEN` | 1 | no | `teamId`, `name` |
| `GAME_END` | 1 | no | `gameId`, `winningTeam`, `realTimestamp` |

**Enumerations observed**

- `wardType`: `YELLOW_TRINKET`, `BLUE_TRINKET`, `CONTROL_WARD`, `SIGHT_WARD`, `UNDEFINED`
- `monsterType`: `DRAGON`, `HORDE`, `RIFTHERALD`, `BARON_NASHOR`
- `monsterSubType`: `EARTH_DRAGON`, `WATER_DRAGON`, `FIRE_DRAGON` (dragon only)
- `buildingType` / `towerType`: `TOWER_BUILDING` / `OUTER_TURRET`
- `laneType`: `TOP_LANE`, `MID_LANE`, `BOT_LANE`

**Semantics worth knowing**

- `teamId` on `BUILDING_KILL` / `TURRET_PLATE_DESTROYED` is the team that **owned** the
  destroyed structure, not the destroyer. (Sample: `killerId` 8 from team 200,
  `teamId: 100`.)
- `killerId: 0` means a non-champion killed it — minions taking a plate.
- `CHAMPION_KILL.victimDamageReceived[]` is a per-spell breakdown of every damage
  instance, including monsters (`type: MONSTER`). Rich but verbose — this is most of
  the file's size.
- `ITEM_UNDO` exists, so build reconstruction must replay purchases/sells/undos in
  order rather than summing.

---

## Payload fields — feasibility

| Payload field | Status | Source |
|---|---|---|
| `gold_diff_vs_lane` | ✅ | `totalGold` diff vs. lane opponent |
| `gold_diff_z` | ✅ derived | raw diff + baseline table |
| `cs`, `cs_baseline` | ✅ | `minionsKilled + jungleMinionsKilled` |
| `hp_pct` | ✅ | `championStats.health / .healthMax` |
| `level`, `enemy_level` | ✅ | `.level` |
| `position` (semantic) | ✅ derived | `.position` + hand-defined map regions |
| `objective_timers` | ✅ derived | `ELITE_MONSTER_KILL` timestamps + spawn rules |
| `action_taken` | ✅ derived | positions + events (Phase 7.2 rules) |
| `direct_cost` | ✅ derived | death ts → position re-entry; CS/XP delta |
| `downstream` | ✅ derived | events to next resolution |
| `winprob_*` | model output | not extraction |
| `wards_active_nearby` | ⚠️ **degraded** | ward events carry no position — see Gaps |
| `enemy_jungler_last_seen` | ❌ **unavailable** | see Gaps |
| `enemies_visible_on_minimap` | ❌ **unavailable** | see Gaps |
| Flash / summoner availability | ❌ **unavailable** | no summoner-cast event type exists |

---

## Gaps

**1. Wards have no position.** `WARD_PLACED` carries only `creatorId` and `wardType`.
There is no x/y, so "was there a ward covering this spot" cannot be computed as
designed. Partial workaround: the creator's position at the nearest frame approximates
placement, but frames are 60s apart, so the error is large. A weaker, honest substitute
is non-spatial: "your team had N wards placed in the previous 90 seconds."

**2. No vision state anywhere.** The timeline reports every player's position on every
frame regardless of who could see them. There is no fog-of-war representation, so
`enemies_visible_on_minimap` and `enemy_jungler_last_seen` cannot be derived — computing
them would mean *modelling* vision from ward placements (which have no position, see
gap 1) and champion proximity. That's a research problem, not an extraction rule.

**3. No summoner spell events.** Nothing in the 16 event types records a Flash or
Teleport cast. Whether a player had Flash — which materially changes whether a play was
reasonable — is not recoverable.

### Implication for Phase 7.3

`knowable_at_the_time` was the designed defence against hindsight bias, and `docs/decisions.md`
calls hindsight bias the top product risk. Three of its four fields are now unavailable.
Options, in rough order of preference:

1. **Reframe rather than reconstruct.** Don't claim to know what the player could see.
   Phrase verdicts so they never assert awareness: "you were deep with no recent team
   vision in that area" rather than "you should have seen the jungler coming."
2. **Proximity proxy.** Use frame positions to compute how far the nearest enemy was and
   whether any teammate was nearby — a crude but genuine "how exposed were you" signal.
3. **Non-spatial ward recency.** Count of team wards placed in the preceding window.
4. **Accept the limit explicitly in the output**, which is more honest than a fabricated
   confidence and fits the project's existing stance on counterfactuals.
