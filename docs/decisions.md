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
- Equip spends an action during combat (see P3). The druid restriction on metal armor is not enforced.

## P3

- Initiative is a Dexterity check. Ties break by Dexterity score, then one d20 roll-off. A remaining tie breaks by id, so the roll-off cannot loop.
- A surprised creature cannot act or take reactions on its first turn. The turn still arrives and ends. There are no squares, so a ranged attack declared at 5 feet has disadvantage when any living foe is in the fight. Opportunity attacks are not automatic: a reaction attack is declared with `--reaction`.
- A monster dies at 0 HP. A character at 0 HP is dying and can only make a death save, on their own turn. Three successes stabilize, three failures kill, a natural 20 restores 1 HP, and a natural 1 is two failures. Damage that drops a character to 0 and continues for at least their maximum HP kills them outright. Damage against a character already at 0 HP is one death-save failure, or two if the hit was a critical.
- Victory awards the XP of monsters killed in that combat, split evenly among living characters, with the remainder dropped. Fleeing or a parley awards none. A goblin is worth 50 XP.
- Players see monster hit points as `healthy`, `bloodied`, `dead`, or the character statuses. The event log keeps the number and `log` strips `target_hp` for anyone who is not the DM.
- Help in combat makes the next ally attack against that target have advantage. Hide sets a Stealth total; the next attack by the hidden creature has advantage. Dodge imposes disadvantage on attacks against that creature until its next turn starts.
- `--bonus` on Disengage or Hide is allowed only with Nimble Escape or Cunning Action.
- `NO_SLOT` waits for spellcasting in P4.

## P4

- A spell is fully resolved when the SRD record already carries the numbers: instantaneous healing, instantaneous saves for half or no damage, and instantaneous spell attacks. Named extras are sleep (hit-point pool), shield (+5 AC until the caster's next turn), bless (+1d4, concentration), magic missile (automatic 1d4+1 darts), mage armor (13 + Dexterity), false life (temporary hit points), and guiding bolt (the attack, plus advantage on the next attack against that target). Acid arrow is manual because the record omits the end-of-turn damage. Anything else returns `effect: manual`, still spends the slot, and still takes concentration when the spell needs it.
- Components are not checked. A focus or component pouch is assumed. `--range` is optional; omitting it means the cast is within the spell's range. A Self spell with an area uses the area size as the limit.
- The bonus-action spell rule applies only during combat, and only until that creature's turn ends. A ritual cannot be cast in combat. A wizard may ritual-cast a spell from the spellbook without preparing it, and a ritual spends no slot.
- Disciple of Life adds 2 + the slot level to healing from a spell of 1st level or higher. Cure wounds and healing word do nothing to undead and constructs.
- Sleep does not reduce hit points. It applies unconscious, lowest current hit points first, and damage ends that sleep. Upcast sleep adds 2d8 per slot above 1st.
- A short rest spends Arcane Recovery if a use remains and refills the lowest expended slots up to half the wizard level, rounded up. The use itself returns on a long rest. Second Wind and warlock spell slots return on a short rest. Hit dice heal the die plus Constitution, minimum 0. A long rest restores hit points, half the hit dice (minimum one), every spell slot, and short- or long-rest features, and reduces exhaustion by 1. Both rests are refused during combat. A dead character is not restored.
- Spell slots of a level the caster does not have, or that are already expended, are `NO_SLOT`.

## P5

- `campaign new` runs adventure validation. A minimal adventure with one location still passes. DCs must be integers. `custom:` monsters are accepted only when the adventure defines them.
- `search` rolls Perception and Investigation once each and reveals every hidden feature or exit whose skill meets the DC. A hidden connection cannot be used until then. The mill cellar is also reached by the grinding-room stairs.
- Yard and cellar `on_enter` hooks spawn the encounter once and start combat. Goblin surprise is one Stealth roll against the party's highest passive Perception. It surprises the party only when the roll is higher.
- Experience from a fight is split when that fight ends, and the remainder of that split is dropped. Three goblins give 37 each and the cellar gives 75 each, which is 112 each. A milestone can make a level available before the XP table does. Level-up is one level at a time, and only while the character is below the available level, capped at 5.
- Hit points on level-up use the average (rounded up) plus Constitution, and Dwarven Toughness adds 1. An ability increase that changes Constitution also adds or removes 1 hit point per level the character has reached. Extra Attack, invocations, metamagic, and a hunter's prey option are recorded on the sheet; the attack command does not swing twice yet.
- A wizard adds two spellbook spells per level. Other casters add spells to match `spells_known`. A circle's bonus cantrip is the feature, not an extra cantrip slot.
- `time advance` moves the clock and clears round-duration conditions. Treasure is taken item by item from `loot:<id>`.

## P6

- Extra Attack (the level-5 fighter, barbarian, monk, paladin, and ranger features) allows two `attack`s for one action. The second swing does not refresh the action. A bonus-action or reaction attack does not gain the extra swing.
- `undo` appends a revert that points at the last state-changing event. Replay skips that event's changes except `rng.counter`, so the dice stay spent and the log still shows the attempt.
- `override` sets one path and refuses `rng`. It does not rewrite older events.
- `player add` registers a seat. Controllers are `bot:<name>`, `javi`, or `dm`. The engine still cannot see which process ran a command. A `javi` or `dm` seat is stamped `relayed_by: dm`.
- Player-facing output drops `dm_notes`, `dm_only`, `target_hp`, monster hit-point objects, secret dice, and trap DCs.

## Command transactions

- Campaign commands lock before reading and verifying the event log. All events in a command are prepared and validated before the batch is appended, so a rejected command writes no events or state changes. Ordinary commits apply the new batch to the verified snapshot; undo replays history because it changes which earlier events apply.
- `state.json` is replaced atomically after the log append. Read commands use the verified log snapshot, so a missing or stale cache does not change their output.
