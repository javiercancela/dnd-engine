# dnd-engine

Local D&D 5e rules engine for the System Reference Document 5.1 (2014 rules).

The engine uses the Python 3.11+ standard library only. SRD data is vendored under `engine/data/srd-5.1/`. The interface spec is `docs/engine-spec.md`.

## Tests

From the repository root:

```bash
cd engine && python3 -m unittest discover -s tests
```

## CLI

From the `engine/` directory:

```bash
python3 -m dnd help
python3 -m dnd help --about
python3 -m dnd campaign new <name> --adventure FILE --seed N --as dm --campaign DIR
```

`--campaign DIR` or `DND_CAMPAIGN` is the directory that holds one subdirectory per campaign. The default is `campaigns/` in the repo root.

## Lookup

From the `engine/` directory:

```python
from dnd import srd

srd.get('monster', 'goblin')
srd.all('spell')
srd.resolve('srd:magic-missile')
```

## Attribution

This work includes material taken from the System Reference Document 5.1 ("SRD 5.1") by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.

See `NOTICE.md` for the 5e-bits MIT license.
