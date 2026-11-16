# SRD 5.1 data source

Vendored, unmodified, from the 5e-bits 5e-srd-api repository.

- Repository: https://github.com/5e-bits/5e-srd-api
- Commit: `3b6da29a45b3b328c2409cf97a14e2507e291bf8`
- Commit date: 2026-11-14
- Source path: `packages/5e-database/src/2014/en/`
- Vendored path: `engine/data/srd-5.1/`

Only the 2014 English JSON in that directory is included. The 2024 dataset is not.

Each file is a JSON list of records. The loader indexes them by the `index` field (the slug). Entry counts:

| Kind | File | Count |
| --- | --- | --- |
| ability-score | 5e-SRD-Ability-Scores.json | 6 |
| alignment | 5e-SRD-Alignments.json | 9 |
| background | 5e-SRD-Backgrounds.json | 1 |
| class | 5e-SRD-Classes.json | 12 |
| condition | 5e-SRD-Conditions.json | 15 |
| damage-type | 5e-SRD-Damage-Types.json | 13 |
| equipment-category | 5e-SRD-Equipment-Categories.json | 39 |
| equipment | 5e-SRD-Equipment.json | 237 |
| feat | 5e-SRD-Feats.json | 1 |
| feature | 5e-SRD-Features.json | 407 |
| language | 5e-SRD-Languages.json | 16 |
| level | 5e-SRD-Levels.json | 290 |
| magic-item | 5e-SRD-Magic-Items.json | 362 |
| magic-school | 5e-SRD-Magic-Schools.json | 8 |
| monster | 5e-SRD-Monsters.json | 334 |
| proficiency | 5e-SRD-Proficiencies.json | 117 |
| race | 5e-SRD-Races.json | 9 |
| rule | 5e-SRD-Rules.json | 137 |
| skill | 5e-SRD-Skills.json | 18 |
| spell | 5e-SRD-Spells.json | 319 |
| subclass | 5e-SRD-Subclasses.json | 12 |
| subrace | 5e-SRD-Subraces.json | 4 |
| trait | 5e-SRD-Traits.json | 38 |
| weapon-property | 5e-SRD-Weapon-Properties.json | 11 |

License and attribution: `NOTICE.md` at the repository root.
