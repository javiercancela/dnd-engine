"""Lazy lookup of the vendored SRD 5.1 JSON.

Records are indexed by kind and slug (the upstream ``index`` field).
Ids used elsewhere have the form ``srd:<slug>``. A slug that appears
in more than one kind has to be qualified as ``srd:<kind>:<slug>``.
"""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'srd-5.1'

# Singular kind name -> vendored file. One file, one kind.
_KIND_FILES = {
    'ability-score': '5e-SRD-Ability-Scores.json',
    'alignment': '5e-SRD-Alignments.json',
    'background': '5e-SRD-Backgrounds.json',
    'class': '5e-SRD-Classes.json',
    'condition': '5e-SRD-Conditions.json',
    'damage-type': '5e-SRD-Damage-Types.json',
    'equipment-category': '5e-SRD-Equipment-Categories.json',
    'equipment': '5e-SRD-Equipment.json',
    'feat': '5e-SRD-Feats.json',
    'feature': '5e-SRD-Features.json',
    'language': '5e-SRD-Languages.json',
    'level': '5e-SRD-Levels.json',
    'magic-item': '5e-SRD-Magic-Items.json',
    'magic-school': '5e-SRD-Magic-Schools.json',
    'monster': '5e-SRD-Monsters.json',
    'proficiency': '5e-SRD-Proficiencies.json',
    'race': '5e-SRD-Races.json',
    'rule': '5e-SRD-Rules.json',
    'skill': '5e-SRD-Skills.json',
    'spell': '5e-SRD-Spells.json',
    'subclass': '5e-SRD-Subclasses.json',
    'subrace': '5e-SRD-Subraces.json',
    'trait': '5e-SRD-Traits.json',
    'weapon-property': '5e-SRD-Weapon-Properties.json',
}

_cache = {}


def kinds():
    """Return the kind names this loader knows, in a stable order."""
    return tuple(_KIND_FILES)


def _load(kind):
    try:
        filename = _KIND_FILES[kind]
    except KeyError:
        known = ', '.join(_KIND_FILES)
        raise KeyError(f'unknown kind {kind!r}; known kinds: {known}') from None
    cached = _cache.get(kind)
    if cached is not None:
        return cached
    path = _DATA_DIR / filename
    with path.open(encoding='utf-8') as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise ValueError(f'{filename} is not a list of records')
    table = {}
    for record in records:
        slug = record['index']
        if slug in table:
            raise ValueError(f'duplicate {kind} slug {slug!r}')
        table[slug] = record
    _cache[kind] = table
    return table


def _plain_slug(slug):
    """Accept ``goblin`` or ``srd:goblin`` and return ``goblin``."""
    if not isinstance(slug, str):
        raise KeyError(slug)
    if slug.startswith('srd:'):
        slug = slug[4:]
    if not slug or ':' in slug:
        raise KeyError(slug)
    return slug


def get(kind, slug):
    """Return one record. ``slug`` may be ``goblin`` or ``srd:goblin``."""
    table = _load(kind)
    key = _plain_slug(slug)
    try:
        return table[key]
    except KeyError:
        raise KeyError(f'{kind}:{key}') from None


def all(kind):
    """Return every record of ``kind``, in file order."""
    return list(_load(kind).values())


def resolve(ref):
    """Resolve ``srd:<slug>`` or ``srd:<kind>:<slug>`` to a record.

    A bare slug must identify exactly one record. Shared slugs (for
    example ``goblin``, which is both a language and a monster) need
    the kind: ``srd:monster:goblin``.
    """
    if not isinstance(ref, str) or not ref.startswith('srd:'):
        raise KeyError(ref)
    body = ref[4:]
    if body.count(':') == 1:
        kind, slug = body.split(':', 1)
        if not slug:
            raise KeyError(ref)
        return get(kind, slug)
    if not body or ':' in body:
        raise KeyError(ref)
    matches = []
    for kind in _KIND_FILES:
        table = _load(kind)
        if body in table:
            matches.append((kind, table[body]))
    if len(matches) == 1:
        return matches[0][1]
    if not matches:
        raise KeyError(ref)
    names = ', '.join(sorted(kind for kind, _record in matches))
    raise KeyError(f'{ref} matches more than one kind: {names}')
