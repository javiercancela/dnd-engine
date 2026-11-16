# `dnd` engine: interface spec v0.1

A local rules engine for D&D 5e (SRD 5.1). Forja builds it. A Dungeon Master (DM) Grok Bot, and later 2–3 player Grok Bots, drive it with Shell commands on the box. Javi can play one character himself through the DM bot's chat.

Status: v0.2, with Javi's decisions applied (§9).

---

## 1. Goals and non-goals

**Goals**
- The engine owns the **rules, dice, state and turn order**. If a number matters (a roll, HP, a slot, a DC result, XP), it comes from the engine and nowhere else.
- Every state change is an **event** in an append-only, hash-chained log. History can be audited, and nobody can fake a roll after the fact.
- It is **deterministic**: the same seed and the same commands always give the same results, so tests can replay a session exactly.
- **Multi-actor**: one DM plus several players, each player limited to their own character, with out-of-turn actions rejected.
- **Adventures plug in as data**: any adventure is a local JSON file. No adventure content lives in the code.

**Non-goals (v1)**
- No narration and no prose generation. The bots tell the story; the engine returns facts.
- No server, no open ports, no network access at runtime, no deploy step.
- No UI beyond the CLI. No grid or map: combat is theater-of-mind with declared ranges (§3.6).
- Not a security boundary. All bots share one box, so `--as` is trusted the way a seat at a table is. The engine stops *mistakes* (wrong turn, wrong character, a spell slot you don't have) and logs every actor. The bots' instructions forbid impersonation.
- Character levels 1–5 only. SRD 5.1 content only: no custom backgrounds, subclasses or races, no multiclassing, no feats beyond what SRD 5.1 lists, no crafting or downtime.
- No player-entered dice. The engine rolls every die, including for Javi's character.

## 2. Stack

- **Python 3.11+, standard library only** (the box has 3.13). One package, `dnd/`, with no `pip install`. `json`, `argparse`, `hashlib`, `random` and `unittest` cover everything here.
- **State**: one directory per campaign holding JSON plus a JSONL event log (§5). Both are plain text that you can diff, back up and read by eye. SQLite would add nothing at this size.
- **Run command**: `python3 -m dnd <command> ...` from `/workspace/rpg/engine`, or the wrapper `/workspace/rpg/bin/dnd` (a 2-line shell script). Every example below uses `dnd`.
- **Tests**: `python3 -m unittest discover -s tests` with no extra tools. Run them locally on the box.
- **Repo**: a private GitHub repo with an English name; Forja picks it (e.g. `dnd-engine`). The repo root is `/workspace/rpg/`. `.gitignore` covers `adventures-private/` and `campaigns/`, because campaigns hold a copy of the adventure. Neither is ever committed.
- **CI**: per Forja's rule, check the repo's GitHub Actions minutes and plan before relying on hosted CI. Local test runs are enough to pass any phase.
- **Campaign location**: `--campaign <dir>`, or the env var `DND_CAMPAIGN`. Default: `/workspace/rpg/campaigns/<name>`.

Why: the bots already call Shell, Python is on the box, stdlib means nothing to install or break, and JSON output is the bots' native format.

## 3. CLI

### 3.1 Conventions

- Syntax: `dnd <group> <verb> [args] --as <actor> [--json-in FILE]`.
- **Output is always one JSON object on stdout.** Nothing else goes to stdout; debug output goes to stderr.
- Success envelope:
  ```json
  {"ok": true, "cmd": "attack", "event_ids": [212, 213],
   "result": { ... command-specific ... },
   "rolls": [ {"id": "r-0113", "expr": "1d20+5", "dice": [14], "mod": 5, "total": 19, "adv": "none"} ],
   "turn": {"combat": true, "round": 2, "current": "pc-mira", "next": "gob-2"},
   "warnings": []}
  ```
- Error envelope, with exit code 1 for a rule or permission error and 2 for bad usage:
  ```json
  {"ok": false, "cmd": "attack",
   "error": {"code": "NOT_YOUR_TURN", "message": "It is gob-2's turn (round 2).", "details": {"current": "gob-2"}}}
  ```
  Error codes: `BAD_ARGS`, `UNKNOWN_ID`, `NOT_AUTHORIZED`, `NOT_YOUR_TURN`, `NO_ACTION_LEFT`, `NO_SLOT`, `OUT_OF_RANGE`, `INVALID_TARGET`, `INCAPACITATED`, `RULE_VIOLATION`, `NO_CAMPAIGN`, `LOG_TAMPERED`.
- A rejected command writes **no** event.
- IDs are slugs: characters `pc-<name>`, monsters `<monster>-<n>` (e.g. `goblin-2`), locations and encounters as named in the adventure file.
- Visibility: output is filtered for the actor. Players never see `dm_only` fields, monster HP numbers (they get `"status": "bloodied"` and the like), hidden DCs or secrets.

### 3.2 Campaign

| Command | Who | Result |
|---|---|---|
| `dnd campaign new <name> --adventure FILE [--seed N]` | dm | Creates the campaign dir, copies the adventure, picks a seed (random if none is given, and the seed is logged). → `{campaign, seed, start_location}` |
| `dnd campaign load <name>` | any | Sets the active campaign (writes `~/.dnd_active`). → `{campaign, chapter, location, combat}` |
| `dnd campaign info` | any | Summary: party, location, time and day, combat flag. |
| `dnd player add <player-id> --controller bot:<name>\|javi\|dm` | dm | Registers a seat. `javi` means "relayed by the DM bot". |
| `dnd verify` | any | Re-hashes the log and replays it to rebuild state; checks that it matches `state.json`. → `{ok, events, head_hash}` or `LOG_TAMPERED`. |

### 3.3 Characters

```
dnd char create --as dm --json-in pc-mira.json
```
Input (DM-only; builds a level-1 PC from SRD options and validates every choice):
```json
{"id": "pc-mira", "name": "Mira", "player": "javi",
 "race": "high-elf", "class": "wizard", "background": "acolyte",
 "abilities": {"method": "standard-array", "str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
 "skills": ["arcana", "investigation"], "languages": ["draconic"],
 "spells_known": ["fire-bolt", "mage-hand", "light", "magic-missile", "shield", "sleep", "burning-hands", "detect-magic", "mage-armor"],
 "spells_prepared": ["magic-missile", "shield", "sleep", "mage-armor"],
 "equipment_choice": "default"}
```
- Ability methods: `standard-array`, `point-buy` (27 points) or `rolled` (the engine rolls 4d6-drop-lowest ×6 and logs it; the order is then assigned with `char assign-scores`).
- The engine derives the modifiers, proficiency bonus, HP (max at level 1), AC from equipped armor, saves, skills, passive Perception, speed, attacks and spell slots.
- Content is limited to SRD 5.1: 9 races, 4 subraces, 12 classes, 1 subclass per class, background **acolyte** only. Anything else is rejected with `RULE_VIOLATION`.

| Command | Who | Result |
|---|---|---|
| `dnd char show <id> --as X` | owner, dm | Full sheet. Other players get the public view (name, race, class, level, status). |
| `dnd char list --as X` | any | Party roster and seats. |
| `dnd state show --as X [--section party\|location\|combat\|all]` | any | A snapshot filtered for the actor. |

### 3.4 Dice, checks, saves

| Command | Who | Notes |
|---|---|---|
| `dnd roll <expr> --as X [--adv\|--dis] [--reason TEXT] [--secret]` | any | `expr`: `NdM[+-K]`, `kh/kl` (keep highest/lowest), `d%`. `--secret` is DM-only; the roll is logged but hidden from players. |
| `dnd check <char> <ability\|skill> --dc N --as X [--adv\|--dis] [--help-from <id>]` | the char's owner, or the dm | → `{total, dc, success, natural}`. The DM may set `--dc` hidden with `--secret-dc`. |
| `dnd save <id> <ability> --dc N --as X [--adv\|--dis]` | owner, dm | Applies proficiency and conditions (e.g. a paralyzed creature auto-fails STR/DEX saves). |
| `dnd contest <a> <skill> <b> <skill> --as dm` | dm | Grapple, shove, stealth vs perception. |
| `dnd group-check <skill> --dc N --as dm` | dm | Succeeds if half or more of the party succeed. |

- The engine applies advantage and disadvantage from conditions and features automatically, merges them with flags, and reports `adv_sources`.
- **The engine rolls every die**, for PCs, monsters and NPCs alike. There is no flag for entering an outside roll, and `override` can't write roll results.
- **RNG**: each roll uses `random.Random(sha256(f"{seed}:{roll_counter}"))`. The counter is stored in state, so a replay produces the same dice. Every roll event stores `roll_counter`, `expr`, raw `dice`, `kept`, `mod` and `total`.

### 3.5 Combat and turn order

| Command | Who | Notes |
|---|---|---|
| `dnd combat start --as dm --combatants pc-mira,pc-tor,goblin-1,goblin-2 [--surprised goblin-1,...]` | dm | Rolls initiative for everyone (DEX check, ties broken by DEX score, then a d20 roll-off) and sets round 1. → `{order: [{id, init}], current}` |
| `dnd combat status --as X` | any | Order, round, the current actor, and each combatant's public status. |
| `dnd turn end --as <current>` | the current actor's owner, or the dm | Advances the turn, ticks durations ("until end of turn"), resets action, bonus action, reaction and movement. → `{next, round}` |
| `dnd combat end --as dm [--reason fled\|victory\|parley]` | dm | Clears initiative and ends combat-only effects. |

- **Turn rule**: during combat, any command that spends an action, bonus action or movement must be issued `--as` the current combatant, or `--as dm` when the current combatant is a monster. Otherwise the engine returns `NOT_YOUR_TURN`.
- **Reactions** (opportunity attacks, *shield*) can be used off-turn with `--reaction`, once per round per creature.
- **Economy tracking**: each turn has `action`, `bonus`, `reaction` and `movement_ft`. Spending something that has already been used returns `NO_ACTION_LEFT`.

### 3.6 Actions

| Command | Who | Notes |
|---|---|---|
| `dnd attack <attacker> <target> --weapon <id>\|--natural <name> --range FT --as X [--adv\|--dis] [--reaction] [--offhand]` | owner/dm | Range is declared, theater-of-mind. The engine checks normal and long range (long range gives disadvantage), applies ranged-attack-in-melee disadvantage when `--range 5` and a hostile is adjacent, checks cover (`--cover half\|three-quarters`), crits on a natural 20 (doubles the damage dice), auto-misses on a natural 1, and resolves damage, resistances, temporary HP and drops to 0 HP. → `{hit, crit, attack_roll, ac, damage: [{type, amount}], target_status}` |
| `dnd cast <caster> <spell> [--targets a,b] [--slot N] [--range FT] --as X [--ritual]` | owner/dm | Checks that the spell is known/prepared, the slot is available, the casting time fits the action economy, range, and the one-leveled-spell-per-turn bonus-action rule. Resolves the attack roll or the targets' saves, applies damage or effects, and sets concentration (replacing an earlier concentration spell). v1 implements mechanical effects for the spells the level 1–5 SRD classes can cast; any other spell returns `{"effect": "manual"}`, spends the slot, and the DM adjudicates. |
| `dnd action <actor> dash\|disengage\|dodge\|help <target>\|hide\|ready <trigger>\|search\|use-object --as X` | owner/dm | Updates the economy and flags (e.g. dodge gives disadvantage to attacks against the actor). |
| `dnd move <actor> --ft N --as X` | owner/dm | Spends movement in combat (difficult terrain via `--difficult`). No positions, just a budget. |
| `dnd damage <target> <amount\|expr> --type T --as dm [--source TEXT]` | dm | For traps, falls and environmental damage. Applies resistance, the concentration save (DC max(10, half the damage)) and instant death from massive damage. |
| `dnd heal <target> <amount\|expr> --as X [--source item\|spell\|feature]` | dm, or via `cast`/`use` | Capped at max HP. Stabilizes and revives from 0. |
| `dnd temp-hp <target> N --as dm` | dm | Keeps the higher value. |
| `dnd death-save <char> --as owner` | owner | Only at 0 HP, on that character's turn. A natural 20 brings them to 1 HP; a natural 1 counts as 2 failures. → `{successes, failures, state: dying\|stable\|dead\|conscious}` |
| `dnd condition add\|remove <target> <condition> [--duration rounds:N\|until:end-of-turn:<id>\|save:<ability>:<dc>] --as dm` | dm | Covers the 15 SRD conditions and exhaustion levels 1–6. Conditions also come from `cast`. |

### 3.7 Rests, exploration, items, progression

| Command | Who | Notes |
|---|---|---|
| `dnd rest short --as dm [--spend pc-tor:2,pc-mira:1]` | dm (players may name their own hit-dice spend with `dnd rest spend-hd <char> N --as owner`) | Rolls hit dice and restores short-rest features (e.g. Arcane Recovery, Warlock slots, Second Wind). Takes 1 hour of game time. |
| `dnd rest long --as dm` | dm | Restores HP, half the hit dice, slots and long-rest features; reduces exhaustion by 1. Takes 8 hours. Rejected during combat. |
| `dnd look --as X` | any | The current location's public view: name, exits, visible features, `read_aloud` (the DM gets everything, including `dm_notes` and secrets). |
| `dnd go <location-id> --as dm [--travel-hours N]` | dm | Must be a listed connection. Advances game time and fires any `on_enter` triggers (e.g. an encounter auto-spawns). |
| `dnd search <char> --as owner\|dm` | | A Perception or Investigation check against the location's hidden things; reveals them on success. |
| `dnd trap trigger <trap-id> [--target <char>] --as dm` / `dnd trap disarm <trap-id> <char> --as owner\|dm` | | Uses the trap's DCs and damage from the adventure file. |
| `dnd encounter spawn <encounter-id> --as dm [--start-combat]` | dm | Creates monster instances from SRD or custom stat blocks. HP is the stat-block average by default, or rolled with `--roll-hp`. → `{spawned: [ids]}` |
| `dnd inv show <char> --as owner\|dm` | | Items, coins, weight vs capacity, attunement (max 3). |
| `dnd inv give <item> --to <char> [--qty N] [--from <char>\|loot:<treasure-id>] --as dm` | dm | Transfers loot. Players hand items to each other with `dnd inv transfer <item> <from> <to> --as <from-owner>`. |
| `dnd equip <char> <item> [--slot main\|off\|armor\|shield] --as owner` / `dnd unequip ...` | owner | Recomputes AC and attacks. Out of combat, or using an action in combat. |
| `dnd use <char> <item> [--target X] --as owner` | owner | Potions (Potion of Healing: 2d4+2), scrolls, consumables. |
| `dnd xp award <amount> [--to all\|a,b] --as dm` / `dnd xp milestone <level> --as dm` | dm | Split evenly. Tells the bots when a level-up is available. |
| `dnd level up <char> --as owner [--json-in choices.json]` | owner | HP from the average or `--roll`, new features, spells and slots, ASI at level 4. Capped at level 5. |
| `dnd time advance <minutes> --as dm` | dm | Ticks durations. |

### 3.8 History and correction

| Command | Who | Notes |
|---|---|---|
| `dnd log [--last N] [--since <event-id>] [--type roll\|attack\|...] --as X` | any (filtered) | Events in human-checkable form. |
| `dnd undo --as dm --reason TEXT` | dm | Reverts the **last state-changing command**: it appends a `revert` event that points at the target, and state is rebuilt without it. The dice counter **does not rewind**, so "undo until I roll better" gets fresh dice, not a reroll of the same ones, and the log shows every attempt. |
| `dnd override <path> <value> --as dm --reason TEXT` | dm | Escape hatch for rulings (e.g. set HP). It can't touch `rng` or past roll events. Always logged with the reason and shown in `log`. |
| `dnd help [command]` | any | Machine-readable command list, so the bots can discover syntax. |

## 4. Actor model

- `--as dm` is the DM bot. `--as <char-id>` acts for that character. `--as` is required on every command except `help`, `campaign info` and `verify`.
- Each character has a `player` seat (`bot:<name>`, `javi`, or `dm` for NPC allies). The engine records the seat but cannot see which bot ran the Shell command, so seat honesty rests on the bots' instructions (§1 non-goals). What it does enforce:
  - Acting as a character changes **only that character**: its own checks, attacks and casts, its own inventory and equipment, and its own level-up. Targeting others in an attack or spell is fine; changing another PC's sheet is `NOT_AUTHORIZED`.
  - In combat, action-spending commands must come from the current combatant (`NOT_YOUR_TURN`). Reactions are the only exception.
  - A PC that is incapacitated, unconscious or dead can't act except for `death-save` (`INCAPACITATED`).
- **The DM may also act `--as <pc>`** for seats marked `javi` or `dm`. That's how Javi plays: he tells the DM bot what he does, and the DM bot runs `dnd attack pc-mira ... --as pc-mira`. The event records `relayed_by: dm`.
- **DM-only commands**: `campaign new`, `player add`, `char create`, `contest`, `group-check`, `combat start/end`, every monster or NPC action, `damage`, `temp-hp`, `condition add/remove`, `rest short/long`, `go`, `trap trigger`, `encounter spawn`, `inv give`, `xp award/milestone`, `time advance`, `undo`, `override`, `roll --secret`, `--secret-dc`.
- Players get filtered output: no monster HP numbers, no hidden DCs, no `dm_only` text, no secret rolls.

## 5. State and event log

```
campaigns/<name>/
  campaign.json     # name, seed, created, engine_version, ruleset: "srd-5.1", adventure_sha256
  adventure.json    # copied at `campaign new` (private: never commit)
  events.jsonl      # append-only source of truth
  state.json        # cache = fold(events); rebuilt by `verify` or after any mismatch
  lock              # flock held during each command (one writer at a time)
```

**Event** (one JSON object per line):
```json
{"id": 213, "ts": "2026-11-16T22:40:11+01:00", "actor": "pc-mira", "relayed_by": "dm",
 "type": "attack", "cmd": "attack pc-mira goblin-2 --weapon dagger --range 5",
 "rolls": [{"counter": 113, "expr": "1d20+4", "dice": [14], "kept": [14], "mod": 4, "total": 18}],
 "changes": [{"op": "set", "path": "combatants.goblin-2.hp", "from": 7, "to": 3},
             {"op": "set", "path": "combat.economy.pc-mira.action", "from": true, "to": false}],
 "prev_hash": "9f2c…", "hash": "41ab…"}
```
- `hash = sha256(prev_hash + canonical_json(event without hash))`. `verify` recomputes the chain and replays `changes` from an empty state to rebuild `state.json`.
- The only writer is the engine. The bots' instructions say never to edit campaign files. If they do, `verify` reports `LOG_TAMPERED` and the DM bot tells Javi.

**state.json sketch**:
```json
{"rng": {"seed": 4242, "counter": 114},
 "time": {"day": 3, "minute": 610},
 "location": "mill-yard", "chapter": "ch1",
 "seats": {"javi": {"controller": "javi"}, "bot:ana": {"controller": "bot:ana"}},
 "characters": {"pc-mira": {"name": "Mira", "player": "javi", "race": "high-elf", "class": "wizard", "subclass": null,
   "level": 1, "xp": 0, "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
   "hp": {"max": 7, "current": 7, "temp": 0}, "hit_dice": {"d6": 1}, "ac": 12, "speed": 30,
   "proficiencies": {"saves": ["int", "wis"], "skills": ["arcana", "investigation"], "weapons": [], "armor": [], "tools": []},
   "spellcasting": {"ability": "int", "slots": {"1": {"max": 2, "used": 0}}, "known": [], "prepared": [], "concentration": null},
   "features": [{"id": "arcane-recovery", "uses": 1, "max": 1, "recharge": "long"}],
   "conditions": [], "exhaustion": 0, "death_saves": {"s": 0, "f": 0},
   "inventory": [{"item": "dagger", "qty": 1, "equipped": "main"}], "coins": {"cp": 0, "sp": 0, "gp": 10}}},
 "monsters": {"goblin-2": {"ref": "srd:goblin", "hp": {"max": 7, "current": 3}, "conditions": [], "encounter": "yard-ambush"}},
 "combat": {"active": true, "round": 2, "order": [{"id": "pc-mira", "init": 17}], "current_index": 0,
            "economy": {"pc-mira": {"action": false, "bonus": true, "reaction": true, "movement_ft": 30}}},
 "effects": [{"id": "fx-7", "source": "sleep", "target": "goblin-1", "expires": {"type": "round", "round": 12}}],
 "adventure": {"revealed": ["mill-cellar-hatch"], "triggered": ["yard-ambush"], "looted": [], "flags": {}}}
```

**SRD data**: `engine/data/srd-5.1/*.json` is vendored, read-only, and loaded by index (`srd:goblin`, `srd:fire-bolt`). Source: §8.

## 6. Adventure file format

One JSON file in `/workspace/rpg/adventures-private/`, which is gitignored and never committed (the sample fixture in §6.1 is the only adventure in the repo). Everything below except IDs and mechanics is the author's own text.

```json
{"format": "dnd-adventure/1", "id": "string", "title": "string", "levels": [1, 5],
 "ruleset": "srd-5.1", "start": "location-id",
 "chapters": [{"id": "ch1", "title": "...", "summary_dm": "...", "locations": ["loc-a", "loc-b"]}],
 "locations": [{"id": "loc-a", "name": "...",
   "read_aloud": "text the author writes from their own book",
   "description_public": "short player-safe summary",
   "dm_notes": "secrets, tactics, motives (dm_only)",
   "connections": [{"to": "loc-b", "label": "north door", "travel_minutes": 1, "locked": {"dc": 15, "key": "item-id"}, "hidden": {"dc": 12, "skill": "perception"}}],
   "features": [{"id": "f-1", "name": "...", "public": "...", "hidden": {"dc": 13, "skill": "investigation", "reveal": "..."}}],
   "on_enter": [{"spawn": "enc-1", "once": true, "start_combat": true}],
   "traps": ["trap-1"], "treasure": ["tr-1"], "npcs": ["npc-1"]}],
 "encounters": [{"id": "enc-1", "monsters": [{"ref": "srd:goblin", "count": 3}, {"ref": "custom:boss", "count": 1}],
   "xp_override": null, "tactics_dm": "...", "surprise": {"stealth_vs_passive": true}}],
 "custom_monsters": [{"id": "custom:boss", "name": "...", "stat_block": { "same shape as SRD monster JSON" }}],
 "npcs": [{"id": "npc-1", "name": "...", "public": "...", "dm_only": "...", "stat_ref": "srd:commoner", "attitude": "friendly|indifferent|hostile"}],
 "traps": [{"id": "trap-1", "name": "...", "detect": {"skill": "perception", "dc": 12}, "disarm": {"skill": "thieves-tools|dex", "dc": 13},
   "effect": {"save": {"ability": "dex", "dc": 13, "half": true}, "damage": "2d6", "type": "piercing", "condition": null}}],
 "treasure": [{"id": "tr-1", "coins": {"gp": 25}, "items": [{"ref": "srd:potion-of-healing", "qty": 1}], "hidden": null}],
 "xp": {"mode": "per-monster|milestone", "milestones": [{"after": "enc-3", "level": 2}]}}
```
- `custom_monsters` is the only non-SRD content the engine accepts, and only from private adventure files. It's how adventures add creatures that aren't in the SRD. LMoP uses several (§8); the author types their stat blocks from their own book into the private file.
- `campaign new` validates the file with `dnd adventure validate FILE` (every ref resolves, every connection target exists, DCs are integers) and rejects it if something's wrong.

### 6.1 Sample adventure (original, for tests): `tests/fixtures/brackwater-mill.json`

Original content written for this spec; no published adventure text. Here is the shape (Forja writes the full JSON from it):

- **Title**: *The Mill at Brackwater*, levels 1–2, 4 PCs.
- **Locations**:
  1. `mill-road`: a muddy lane up to a silent watermill. Exit: `mill-yard`.
  2. `mill-yard`: broken carts and a cold forge. `on_enter` spawns `yard-ambush` (3 × `srd:goblin` hiding behind the carts; they surprise the party if their stealth beats the passive Perception). Hidden feature: tracks leading to a cellar hatch (Perception DC 12). Exits: `mill-floor`, plus `mill-cellar` (hidden until found).
  3. `mill-floor`: the grinding room. NPC `npc-oswin`, a frightened miller tied to a beam (`srd:commoner`, friendly). He says the goblins answer to "the big one below". Trap `trap-millstone`: a loose stone drops (DEX save DC 12, 2d6 bludgeoning, half on success; spotted with Perception DC 13, wedged with Athletics DC 12). Exit: `mill-cellar`.
  4. `mill-cellar`: damp flour sacks. Encounter `cellar-boss`: 1 × `srd:bugbear` + 2 × `srd:wolf`. Treasure `tr-cellar`: 40 gp, 2 × `srd:potion-of-healing`, 1 × `srd:spell-scroll-1st` (*cure wounds*).
- **XP**: per monster (goblin 50, bugbear 200, wolf 50 → 450 total, 112 each for 4 PCs, remainder dropped). Milestone to level 2 after `cellar-boss`.
- **Custom monster for tests**: `custom:mill-rat-swarm` (a copy of `srd:swarm-of-rats` with HP 20) so the custom path gets exercised.

Four test PCs (`tests/fixtures/party/`): `pc-tor` (hill-dwarf fighter, champion path at 3), `pc-mira` (high-elf wizard), `pc-sel` (human cleric, life), `pc-pip` (lightfoot-halfling rogue). All have background acolyte and use the standard array.

## 7. Build phases (each phase ends with a check Forja runs and shows)

| Phase | Builds | Verification |
|---|---|---|
| **P0 Data** | Vendor SRD 5.1 JSON (§8) into `engine/data/srd-5.1/`, pinned to a commit, with `NOTICE.md`. Write a loader with index lookups. | A unit test on counts (12 classes, 9 races, 334 monsters, 319 spells as vendored) and spot values: goblin AC 15, HP 7 (2d6), Scimitar +4 1d6+2; *magic missile* 3 darts × 1d4+1. Compare 3 spot values by hand with the official PDF. |
| **P1 Core** | CLI skeleton, JSON envelopes, campaign new/load/info, locking, event log with hash chain, `verify`, seeded `roll`, `log`, `help`. | The same seed and commands give a byte-identical `events.jsonl` (minus `ts`). Editing one byte in `events.jsonl` makes `verify` report `LOG_TAMPERED`. Every error code in §3.1 has a test. |
| **P2 Characters** | `char create` (methods, validation), derived stats, `check`, `save`, `contest`, `group-check`, inventory, equip, use, coins. | Create the 4 fixture PCs; HP, AC, saves, skills and attacks match hand-computed expected JSON. Invalid builds are rejected (e.g. a wizard choosing Athletics). |
| **P3 Combat** | `encounter spawn`, `combat start/status/end`, turns, economy, `attack`, `damage`, `heal`, temp HP, conditions with durations, 0 HP, death saves, reactions, actions (dash, dodge, help, hide, disengage). | **Scripted battle**: 4 fixture PCs vs 4 goblins, seed `4242`, a fixed script of ~40 commands. Asserts: a golden `events.jsonl` that's identical across 3 runs; a goblin acting on a PC's turn returns `NOT_YOUR_TURN`; `pc-pip` trying `--as pc-tor` returns `NOT_AUTHORIZED`; a PC at 0 HP can only death-save. Combat ends with the correct XP. |
| **P4 Magic and rests** | `cast` (slots, prepared/known, concentration, attack vs save spells, upcasting, rituals, bonus-action rule) for the spells level 1–5 SRD casters get; `manual` fallback; short/long rests and feature recharge. | Tests: *sleep* HP pool, *cure wounds*, *burning hands* (DEX save, half damage), *bless* concentration broken by damage, *shield* as a reaction, a slot spent and a second leveled spell blocked on the same turn. A long rest restores everything; a short rest restores Arcane Recovery and Second Wind as SRD says. |
| **P5 Adventure** | Adventure validation, `look`, `go`, `on_enter`, `search`, traps, treasure, NPCs, time, xp award/milestone, `level up` 1→5. | Full scripted playthrough of *Brackwater Mill* (seed `7`): every location visited, the trap triggered and disarmed, treasure looted, party at level 2, then level each fixture PC to 5. Player-filtered `look` has no `dm_notes`; DM `look` has them. |
| **P6 Table** | `player add`, seat checks, `--as dm` relaying for `javi` seats, `undo` and `override`, output filtering audit, `/workspace/rpg/bin/dnd` wrapper, `README.md` with a cheat sheet for the bots. | Two simulated player processes plus the DM run a combat round from separate shells; out-of-turn and unauthorized commands are rejected; `undo` leaves the dice counter advanced and the log showing both attempts; `verify` passes at the end. |

After P6, Dr. Eggbot designs the DM bot against the README, then the player bots.

## 8. Licensing

**Rules content (verified Nov 16, 2026).**
- The engine uses **SRD 5.1 only** (the 2014 rules, the edition Lost Mine of Phandelver was written for). SRD 5.1 is licensed under **CC-BY-4.0**. It was also published under OGL 1.0a; Wizards added CC-BY-4.0 in January 2023, and that license is irrevocable. Required attribution, verbatim from the SRD 5.1 legal page:

  > This work includes material taken from the System Reference Document 5.1 ("SRD 5.1") by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.

  Wizards asks for no other attribution to Wizards. "Compatible with fifth edition" or "5E compatible" is allowed.
- Put the attribution in `NOTICE.md`, in `README.md`, and in `dnd help --about`.

**Machine-readable source.** The official SRD 5.1 is a PDF only: https://media.wizards.com/2023/downloads/dnd/SRD_CC_v5.1.pdf. For JSON, use the **5e-bits** dataset:
- It lives at `packages/5e-database/src/2014/en/` in https://github.com/5e-bits/5e-srd-api. The old `5e-bits/5e-database` repo is deprecated and points there. Pin commit `3b6da29` (Nov 14, 2026). Vendor only `2014/en/`; the `2024/` folder is out of scope.
- Its code and structure are under the **MIT license**. Its README says the underlying material is under OGL 1.0a, but that material is SRD 5.1, which Wizards also licenses under CC-BY-4.0.
- When vendoring, include the 5e-bits MIT notice and the SRD 5.1 CC-BY attribution in `NOTICE.md`. Treat the PDF as authoritative if the data disagrees.

**Adventures.**
- *Lost Mine of Phandelver* is **not** in the SRD; it's a paid, copyrighted product. The engine repo and its tests ship **no LMoP text, names, maps or stat blocks**.
- Javi writes the LMoP adventure file from his own copy and keeps it in `/workspace/rpg/adventures-private/`, which is gitignored, never committed (not even to the private repo), and never shared.
- Creatures LMoP needs that aren't in SRD 5.1 (for example the twig blight, flameskull, nothic, spectator and Redbrand ruffian are absent from the dataset) go in that file's `custom_monsters`.
- The engine's only adventure content is the original *Brackwater Mill* test fixture.

## 9. Decisions (Javi, 2026-11-16)

1. **Rules**: SRD 5.1 only (the 2014 rules), with data from 5e-bits `2014/en` at commit `3b6da29`. No SRD 5.2.1.
2. **Content**: SRD content only. Background Acolyte and the one SRD subclass per class; no custom backgrounds or subclasses. Custom monsters are still allowed in private adventure files, because LMoP needs them.
3. **Code**: a private GitHub repo with an English name (Forja picks it, e.g. `dnd-engine`). `adventures-private/` (and `campaigns/`) are gitignored and never committed. Check the Actions minutes before relying on hosted CI; local tests are fine.
4. **Dice**: the engine rolls all dice, including for Javi's character. Nobody can enter a roll by hand.
