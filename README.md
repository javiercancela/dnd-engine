# dnd-engine

Local D&D 5e rules engine for the System Reference Document 5.1 (2014 rules).

This phase vendors the SRD 5.1 JSON and loads entries by kind and slug. The engine uses the Python 3.11+ standard library only.

## Tests

From the repository root:

```bash
cd engine && python3 -m unittest discover -s tests
```

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
