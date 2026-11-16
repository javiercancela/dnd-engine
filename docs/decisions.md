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
