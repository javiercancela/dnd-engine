# Public event history

- The DM's `log` returns the complete stored events, including commands, reasons, state changes, rolls, RNG state, and hash-chain fields. Player logs are public projections; they are not replayable audit logs.
- Player events retain event identity, type, actor, timestamp, relay/revert references, public rolls, and explicit public outcome fields. `cmd` is a command label without arguments. Free-form reasons and sources are omitted because they may contain DM secrets.
- Creature snapshots in player history show the viewing character's own sheet, other characters' public summaries, and monster names and health statuses. Arbitrary overrides, leaf changes, other state paths, and unknown outcome fields are omitted. Numeric monster HP is never included, including rolled spawn HP and healing outcomes.
- RNG state and raw event hashes are omitted from player history: the seed reconstructs secret dice, and unsalted hashes can be used to guess concealed values. Secret dice retain only their counter and `secret: true`. The full chain remains available to the DM and to `verify`.
