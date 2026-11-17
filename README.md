# dnd-engine

Local D&D 5e rules engine for the System Reference Document 5.1 (2014 rules). One JSON object on stdout. Exit 0 is success, 1 is a rules or permission error, 2 is bad usage. A rejected command writes no event.

The engine uses the Python 3.11+ standard library only. Campaign files live under `--campaign` or `DND_CAMPAIGN` (default `campaigns/` in the repo root).

## Run

`bin/dnd` finds the engine no matter what the working directory is:

```bash
bin/dnd help
bin/dnd help --about
bin/dnd campaign new <name> --adventure FILE --seed N --as dm --campaign DIR
```

Tests, from the repository root:

```bash
cd engine && python3 -m unittest discover -s tests
```

## Who is acting

`--as dm` is the DM. `--as <char-id>` is that character. It is required on every command except `help`, `campaign info`, `verify`, and `adventure validate`.

A character's `player` is `bot:<name>`, `javi`, or `dm`. The engine cannot see which process typed the command. It does enforce:

- You may change only your own sheet. Attacking or targeting someone else is allowed.
- In combat, anything that spends an action, bonus action, or movement must be the current creature, or `--as dm` when the current creature is a monster. Otherwise `NOT_YOUR_TURN`.
- `--reaction` is the exception and spends the reaction.
- A dying, incapacitated, or dead character can only `death-save`.
- The DM may run a command `--as` a character whose seat is `javi` or `dm`. The event is stamped `relayed_by: dm`.

Register seats with `player add <id> --controller bot:<name>|javi|dm` before play. `javi` and `dm` seats are the ones the DM relays.

## Envelope

Success:

```json
{"ok": true, "cmd": "attack", "event_ids": [12], "result": {}, "rolls": [], "turn": {"combat": true, "round": 1, "current": "pc-tor", "next": "goblin-1"}, "warnings": []}
```

Failure:

```json
{"ok": false, "cmd": "attack", "error": {"code": "NOT_YOUR_TURN", "message": "...", "details": {}}}
```

`turn.combat` is false outside a fight. `rolls[]` has `expr`, `kept`, `mod`, `total`, and `counter`.

## Error codes

| Code | Exit | Meaning |
|---|---|---|
| `BAD_ARGS` | 2 | The command line is wrong. |
| `NO_CAMPAIGN` | 1 | No campaign is selected. |
| `UNKNOWN_ID` | 1 | That character, monster, spell, place, or item does not exist. |
| `NOT_AUTHORIZED` | 1 | That actor may not do that. |
| `NOT_YOUR_TURN` | 1 | It is not your turn. |
| `NO_ACTION_LEFT` | 1 | That action, bonus action, reaction, or movement is already spent. |
| `NO_SLOT` | 1 | That spell slot is gone, or you do not have a slot of that level. |
| `OUT_OF_RANGE` | 1 | The declared range is past the weapon or spell. |
| `INVALID_TARGET` | 1 | That target cannot be chosen. |
| `INCAPACITATED` | 1 | That creature cannot act. A dying character may only death-save. |
| `RULE_VIOLATION` | 1 | The rules reject the choice. |
| `LOG_TAMPERED` | 1 | `events.jsonl` no longer matches its hash chain. |

## Commands

DM-only: `campaign new`, `player add`, `char create`, `char assign-scores`, `contest`, `group-check`, `combat start`, `combat end`, monster actions (`--as dm`), `damage`, `temp-hp`, `condition add`, `condition remove`, `rest short`, `rest long`, `go`, `trap trigger`, `encounter spawn`, `inv give`, `xp award`, `xp milestone`, `time advance`, `undo`, `override`, `roll --secret`, `--secret-dc`.

Anyone: `help`, `campaign info`, `campaign load`, `verify`, `adventure validate`, `look`, `state show`, `char list`, `log`, `combat status`.

The owning character, or the DM for a `javi` or `dm` seat: `check`, `save`, `attack`, `cast`, `death-save`, `action`, `move`, `search`, `trap spot`, `trap disarm`, `equip`, `unequip`, `use`, `inv show`, `inv transfer`, `level up`, `rest spend-hd`.

| Command | Result fields that matter |
|---|---|
| `campaign new <name> --adventure FILE [--seed N]` | `campaign`, `seed`, `start_location` |
| `player add <id> --controller bot:<name>\|javi\|dm` | `player`, `controller` |
| `char create --json-in FILE` | `id`, `hp`, `ac`, `spell_dc`, `spell_attack` |
| `char show <id>` | The sheet. Other players see only the public view. |
| `roll <expr> [--adv\|--dis] [--reason TEXT] [--secret]` | One roll. `--secret` is DM-only and hidden in player logs. |
| `check <id> <skill> --dc N` / `save <id> <ability> --dc N` | `total`, `success`, `natural` |
| `encounter spawn <id> [--roll-hp] [--start-combat]` | `spawned` |
| `combat start --combatants a,b [--surprised a,b]` | `order`, `current` |
| `combat status` | `round`, `current`, `order`, `combatants`. Monster `hp` is DM-only; others get `status`. |
| `combat end --reason fled\|victory\|parley` | `xp` on a victory, split evenly, remainder dropped |
| `turn end` | `next`, `round` |
| `attack <attacker> <target> --weapon <id> --range FT [--reaction] [--adv\|--dis] [--cover half\|three-quarters]` | `hit`, `crit`, `attack_roll`, `ac`, `damage`, `target_status`. A level-5 Extra Attack spends one action for two attacks. |
| `cast <id> <spell> [--targets a,b] [--slot N] [--ritual] [--reaction]` | `effect`, `slot`. Unmodelled spells return `effect: manual` and still spend the slot. |
| `action <id> dash\|disengage\|dodge\|help\|hide\|ready\|search\|use-object\|second-wind` | `action` |
| `move <id> --ft N [--difficult]` | `spent`, `remaining` |
| `damage <target> <amount\|expr> --type T` | `amount`, `target_status`. May break concentration. |
| `heal <target> <amount\|expr>` / `temp-hp <target> N` | New hit points or the kept temporary total. |
| `death-save <id>` | `successes`, `failures`, `state` |
| `condition add\|remove <id> <condition> [--duration rounds:N\|until:end-of-turn:<id>\|save:<ability>:<dc>]` | The condition |
| `look` | `read_aloud`, `exits`, `features`, `npcs`. Players do not get `dm_notes` or `dm_only`. |
| `go <location> [--travel-hours N]` | Moves and may spawn an `on_enter` encounter. |
| `search <id>` | `found` for hidden features and exits |
| `trap spot\|trigger\|disarm` | `success` or `damage`. The DC is not shown to players. |
| `inv give <item> --to <id> [--qty N] [--from loot:<treasure>]` | The transfer |
| `xp award <amount> [--to all\|a,b]` / `xp milestone <level>` | `available` level, capped at 5 |
| `level up <id> [--json-in FILE] [--roll]` | One level. Choices cover subclass, `asi`, `spells`, `cantrips`. |
| `rest short [--spend id:N]` / `rest long` | Hit dice, slots, Second Wind, Arcane Recovery |
| `log [--last N] [--type attack]` | Events. Players do not see monster hit points, secret dice, or DM notes. |
| `undo --reason TEXT` | `reverts` the last change. The dice counter stays where it is. |
| `override <path> <json> --reason TEXT` | Sets that path. `rng` is forbidden. |
| `verify` | `ok`, `events`, `head_hash` or `LOG_TAMPERED` |

## A turn

1. `combat status --as <you>` to see `current`.
2. If it is you: `move`, then `attack` or `cast` or `action`, then `turn end`.
3. A level-5 fighter, paladin, ranger, barbarian, or monk may `attack` twice before the action is gone.
4. Off your turn, only `--reaction` (for example `cast pc-mira shield --reaction`).
5. The DM runs monsters with `--as dm` on the monster's turn, and ends that turn the same way.
6. `combat end --reason victory --as dm` splits the XP of monsters that died.

## Limits the DM should know

These are recorded in `docs/decisions.md`. The short list:

- There is no map. Range is the number you declare. A ranged attack declared at 5 feet is in melee.
- Opportunity attacks are not automatic. Declare one with `attack --reaction`.
- Extra Attack is two attacks per Attack action. It does not grant a third, and it does not apply to a bonus-action or reaction attack.
- Spells without a full mechanical model return `effect: manual` after the slot is spent. Acid arrow is manual. Shocking grasp does not check metal armor.
- Components are assumed. Rituals are out of combat and spend no slot. A wizard may ritual-cast from the spellbook.
- The bonus-action spell rule lasts until that turn ends.
- Sleep does not reduce hit points. Concentration ends on incapacitation (including sleep, 0 HP, or death), replacement by another concentration spell, or a failed Constitution save after damage.
- Rests cannot start during combat. A short rest spends Arcane Recovery if a use remains. The use comes back on a long rest.
- Level-up stops at 5. Average hit points unless `--roll`. Invocations, metamagic, and pact boons are recorded as features, not fully resolved. A hunter's prey choice is stored. A land druid's bonus cantrip is the feature, not an extra cantrip picked at the table.
- `time advance` clears round-duration conditions. It does not expire every hour-long spell by the clock.
- The engine cannot tell which bot ran a command. Seat honesty is on the bots. `undo` appends a revert and leaves the dice counter advanced.

## Attribution

This work includes material taken from the System Reference Document 5.1 ("SRD 5.1") by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.

See `NOTICE.md` for the 5e-bits MIT license. The interface spec is `docs/engine-spec.md`.
