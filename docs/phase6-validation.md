# Phase 6 validation — the owner's games

Pass bar (fixed in `phase6-spec.md` before any game was seen): ≥ 70% of shown moments real
(important or minor), ≤ 1 unfair per game on average, "showed me something new" in ≥ half the
games, on ≥ 5 games. Each game is labelled by server and queue; TW2 and draft games are
qualitative (the model is NA ranked-solo only).

**Independence rule:** games 1–2 exposed bugs that were then fixed (spec amendments 3–5), so
they're tuning games now. The pass/fail tally counts games reviewed **after** those fixes.
Games 1–2 can be re-marked for the record, but are reported separately.

## Games used to find bugs (not counted toward pass/fail)

### Game 1 · TW2_459670149 · draft · Aatrox top · loss ("all over the place")
Owner (Nov 2, first version of the review):
- **12:00 (+14):** listed as his fight, but the fighting was at dragon/river. He was top, killing
  Nasus.
- **23:00–25:00 (−25):** "verdict correct, analysis wrong". Said he was in the dragon fight; he says
  he was top against a pushing Nasus the whole time and died to him.
- **29:00–31:00 (−21):** same: said he was in the fight; he was top facing Nasus.
- Kills in different lanes were grouped as one "fight".

Cause: the same-minute merge (amendment 2) merged *involvement and fight counts*, not just the
swing. Fixed by amendments 3–4. After the fix the review reads:
- 12:00: "Top lane: you killed Nasus" · "Dragon pit: Wukong killed Aurelion Sol (you weren't there)" ·
  "Bot lane: 2v1 skirmish (you weren't there)".
- 23:00–25:00: "Top lane: you died to Nasus" as its own line. The dragon line still says "(you:
  credited with an assist on Miss Fortune)": **Riot's data credits Aatrox with an assist on Miss
  Fortune at 23:30 in the enemy jungle near dragon; the 23:00 snapshot puts him in mid lane;
  spells were Flash + Ignite (no Teleport).** Open question for the owner: did he touch that
  fight before going top? The review now shows the credit itself, so he can judge.
- 29:00–31:00: his part is "Your base: 2v1 skirmish, won 1–0; enemy took your top base tower (you:
  credited with an assist on Nasus)". The mid fight and mid-base push are separate parts marked
  "you weren't there".

### Game 2 · TW2_459982083 · draft · Aatrox top · win (a stomp)
- One moment (5:00–6:00, +22) grouped his top-lane kill on Garen with Locke's kill on Kog'Maw
  in mid as "fight 2–0". After the fix: "Top lane: you killed Garen" · "Mid lane: Locke killed
  Kog'Maw (you weren't there)".
- Only one moment in a stomp: expected, since swings in a game decided early are small once
  the model is sure. The owner can say whether anything important was missed.

## Counted games (after the fixes)

| # | Match | Server · queue | Moments (important / minor / unfair) | Missed | Something new? |
|---|---|---|---|---|---|
| | | | | | |

Tally: — (needs ≥ 5 games)
