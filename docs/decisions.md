# Decisions

When `docs/engine-spec.md` is ambiguous or contradicts itself, pick the simplest option that still serves the spec's goals: the engine owns the rules, dice, state, and turn order; the log can be audited; the same seed and commands replay. Record the choice here and continue. Do not stop to ask.

Paths in the spec such as `/workspace/rpg/...` mean this repo root.

## Standing

- The repo stays public. Spec §2 and §9 say "private repo". Javi kept it public (2026-11-16). `adventures-private/` and `campaigns/` stay gitignored and are never committed.

## P0

- `srd.resolve('srd:<slug>')` returns a record only when that slug exists on exactly one kind. 79 slugs are shared (`goblin` is a language and a monster). Use `srd:<kind>:<slug>`, or `srd.get(kind, 'srd:<slug>')`, to pick one.

## P1

- `--campaign` and `DND_CAMPAIGN` are the campaigns root. A campaign lives at `<root>/<name>`. The default root is `<repo>/campaigns`.
- The event hash is `sha256(prev_hash + canonical JSON of the event without hash or ts)`. The spec hashes every field except `hash`. `ts` is left out as well, so two runs match once timestamps are ignored. Any other one-byte edit still makes `verify` return `LOG_TAMPERED`.
- A bad event log is `LOG_TAMPERED` and is not rewritten. A missing, stale, or unreadable `state.json` is rebuilt from the log, and `verify` returns ok.
- `--adv` and `--dis` on a single die roll a second face and keep the high or low one. Both flags cancel, the command warns, and one die is rolled. Either flag on a multi-dice or `kh`/`kl` expression is `BAD_ARGS`.
- Dice count is capped at 100.
- `campaign new` sets the active campaign, as does `campaign load`. `<root>/.dnd_active` stores the name. `~/.dnd_active` stores the absolute path. An explicit root uses its own pointer.
- The stored roll field is `counter`, matching the example event in §5. The prose in §3.4 calls that value `roll_counter`.
- Before characters exist, any actor slug may `roll` or `log`. `campaign new` and `roll --secret` require `--as dm`.
- `campaign new --adventure` accepts a JSON object. If `ruleset` is present it must be `srd-5.1` (`RULE_VIOLATION`). Full validation is P5.
- `player add` and `bin/dnd` wait for P6.
- These error codes wait until a command can actually raise them: `NOT_YOUR_TURN`, `NO_ACTION_LEFT`, `OUT_OF_RANGE`, `INVALID_TARGET`, `INCAPACITATED` (combat, P3) and `NO_SLOT` (spells, P4).

## P2

- Stored ability scores are the finals after racial bonuses. The §5 sketch for Mira shows the pre-racial array and AC 12; that sketch is not the derived sheet. With the high elf bonus her AC is 13.
- `char create` builds level 1 only. `level up` stays in P5.
- `equipment_choice: "default"` takes option (a). A category choice takes the first SRD entry in that category, so the fighter's martial weapon is a battleaxe and the holy symbol is an amulet. Packs are stored as their contents. A versatile weapon uses its one-handed damage. The first non-light melee weapon is equipped in the main hand.
- A race that has SRD subraces must be created as the subrace. A level-1 subclass is the only SRD subclass when that subclass has a level-1 row (life, draconic, fiend). Other classes have no subclass yet.
- The high elf cantrip is an extra wizard cantrip on top of the class list. Cleric and druid domain spells are stored on `domain` and are always prepared without counting against the prepared limit. A wizard's level-1 spellbook is 6 spells, because the level row has no `spells_known` count.
- `languages` in the create document are the chosen extras. Automatic racial languages are added by the engine. A fighter must choose a fighting style. A rogue's expertise is two proficient skills, or one skill and thieves' tools. A dwarf chooses one of smith's tools, brewer's supplies, or mason's tools.
- `--help-from` grants advantage when the helper is another character who is not dying. It does not spend that character's action. Any advantage mixed with any disadvantage cancels.
- Paralyzed, stunned, and unconscious creatures auto-fail Strength and Dexterity saves and do not roll, so the dice counter does not move. Poisoned, frightened, and exhaustion 1+ impose disadvantage on checks. Exhaustion 3+ imposes disadvantage on saves, as does restrained on Dexterity saves. Armor with stealth disadvantage imposes disadvantage on Stealth.
- A contest tie has `winner` null. A group check succeeds when `successes * 2 >= party size`.
- Commands whose actor is a character with player `javi` or `dm` record `relayed_by: dm`.
- `inv give --from loot:<id>` adds the item without checking adventure treasure. That check is P5. Potion of healing heals 2d4+2, capped at max HP, and revives from 0. Other potions, scrolls, and oils are consumed and return `effect: manual`.
- Coin weight is 50 pieces per pound. Item weight is the vendored weight times quantity. A thrown weapon has one melee attack line.
- The public character view is id, name, race, class, level, and status (`healthy`, `bloodied` at half or below, `dying`, or `pending`).
- Equip does not spend an action yet. `NOT_YOUR_TURN` and `NO_ACTION_LEFT` still wait for combat in P3. The druid restriction on metal armor is not enforced.
