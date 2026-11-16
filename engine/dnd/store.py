"""Campaign directory, hash-chained event log, and state cache."""

import copy
import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from dnd.errors import EngineError

ENGINE_VERSION = '0.1.0'
REPO_ROOT = Path(__file__).resolve().parents[2]
_NAME = re.compile(r'^[a-z][a-z0-9-]{0,62}$')


def locate_root(explicit, environ=None):
    """Return ``(campaigns_root, explicit)``.

    ``--campaign`` and ``DND_CAMPAIGN`` are the directory that contains
    one subdirectory per campaign. The default is ``<repo>/campaigns``.
    """
    if explicit:
        return Path(explicit), True
    envmap = os.environ if environ is None else environ
    override = (envmap.get('DND_CAMPAIGN') or '').strip()
    if override:
        return Path(override), True
    return REPO_ROOT / 'campaigns', False


def check_name(name):
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise EngineError('BAD_ARGS', f'campaign name {name!r} is not a slug')
    return name


def campaign_dir(root, name):
    check_name(name)
    return Path(root) / name


def set_active(root, name, directory):
    """Remember the active campaign for this root and for ~/.dnd_active."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / '.dnd_active').write_text(name + '\n', encoding='utf-8')
    home_pointer = Path.home() / '.dnd_active'
    home_pointer.write_text(str(Path(directory).resolve()) + '\n', encoding='utf-8')


def resolve_active(root, explicit):
    """Return the active campaign directory, or raise."""
    root = Path(root)
    pointer = root / '.dnd_active'
    if pointer.is_file():
        name = pointer.read_text(encoding='utf-8').strip()
        if not name:
            raise EngineError('NO_CAMPAIGN', 'no campaign is loaded')
        directory = root / name
        if not directory.is_dir():
            raise EngineError(
                'UNKNOWN_ID',
                f'campaign {name} does not exist',
                {'campaign': name},
            )
        return directory
    if explicit:
        raise EngineError('NO_CAMPAIGN', 'no campaign is loaded')
    home_pointer = Path.home() / '.dnd_active'
    if home_pointer.is_file():
        raw = home_pointer.read_text(encoding='utf-8').strip()
        directory = Path(raw)
        if directory.is_dir() and (directory / 'campaign.json').is_file():
            return directory
        raise EngineError(
            'UNKNOWN_ID',
            'active campaign does not exist',
            {'path': raw},
        )
    raise EngineError('NO_CAMPAIGN', 'no campaign is loaded')


def open_campaign(explicit):
    root, explicit_root = locate_root(explicit)
    directory = resolve_active(root, explicit_root)
    if not (directory / 'campaign.json').is_file():
        raise EngineError('NO_CAMPAIGN', 'no campaign is loaded', {'path': str(directory)})
    return directory


@contextmanager
def locked(directory):
    """Hold an exclusive flock for one command."""
    path = Path(directory) / 'lock'
    handle = path.open('a+')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def event_hash(event):
    """sha256 of prev_hash plus the event without ``hash`` or ``ts``.

    The spec hashes every field except ``hash``. ``ts`` is also left out so
    two runs with the same seed stay identical once timestamps are ignored.
    """
    prev = event.get('prev_hash') or ''
    body = {key: value for key, value in event.items() if key not in ('hash', 'ts')}
    payload = prev + canonical(body)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def read_events(directory):
    path = Path(directory) / 'events.jsonl'
    if not path.exists():
        return []
    events = []
    for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise EngineError(
                'LOG_TAMPERED',
                f'events.jsonl line {line_no} is not JSON',
                {'line': line_no},
            ) from exc
    return events


def apply_changes(state, changes):
    for change in changes or []:
        if change.get('op') != 'set' or 'path' not in change:
            raise EngineError('LOG_TAMPERED', 'event has a change that is not set')
        _set_path(state, change['path'], copy.deepcopy(change.get('to')))
    return state


def _set_path(state, path, value):
    parts = str(path).split('.')
    if not parts or any(part == '' for part in parts):
        raise EngineError('LOG_TAMPERED', f'bad change path {path!r}')
    cursor = state
    for part in parts[:-1]:
        current = cursor.get(part)
        if not isinstance(current, dict):
            current = {}
            cursor[part] = current
        cursor = current
    cursor[parts[-1]] = value


def fold(events):
    """Replay a valid log. Raise LOG_TAMPERED when the chain does not match.

    A revert event drops the target command's changes. Rolls already made stay
    spent: ``rng.counter`` changes on the reverted event still apply.
    """
    reverted = {
        event.get('reverts') for event in events
        if event.get('type') == 'revert' and event.get('reverts') is not None
    }
    state = {}
    prev = ''
    for index, event in enumerate(events):
        if event.get('prev_hash') != prev or event.get('hash') != event_hash(event):
            raise EngineError(
                'LOG_TAMPERED',
                'event hash chain does not match',
                {'event_id': event.get('id'), 'index': index},
            )
        changes = event.get('changes') or []
        if event.get('id') in reverted:
            changes = [change for change in changes if change.get('path') == 'rng.counter']
        apply_changes(state, changes)
        prev = event['hash']
    return state, prev


def write_state(directory, state):
    path = Path(directory) / 'state.json'
    text = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + '\n'
    path.write_text(text, encoding='utf-8')


def read_state(directory):
    path = Path(directory) / 'state.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None


def append_event(directory, event):
    """Fold the existing log, append ``event`` (without id/hash/ts), write state.

    Returns ``(event, state)``. A bad chain raises LOG_TAMPERED and writes nothing.
    """
    events = read_events(directory)
    _state, prev = fold(events)
    next_id = (events[-1]['id'] + 1) if events else 1
    event['id'] = next_id
    event['prev_hash'] = prev
    event['hash'] = event_hash(event)
    event['ts'] = timestamp()
    new_state, _prev = fold(events + [event])
    path = Path(directory) / 'events.jsonl'
    with path.open('a', encoding='utf-8') as handle:
        handle.write(canonical(event) + '\n')
    write_state(directory, new_state)
    return event, new_state


def verify(directory):
    """Re-hash the log, replay it, and rebuild state.json when the cache differs."""
    with locked(directory):
        events = read_events(directory)
        state, _prev = fold(events)
        current = read_state(directory)
        if current != state:
            write_state(directory, state)
        head = events[-1]['hash'] if events else ''
        return {'ok': True, 'events': len(events), 'head_hash': head}


def turn_view(state):
    combat = (state or {}).get('combat') or {}
    if not combat.get('active'):
        return {'combat': False, 'round': None, 'current': None, 'next': None}
    order = combat.get('order') or []
    index = combat.get('current_index')
    current = None
    nxt = None
    if isinstance(index, int) and order:
        current = order[index % len(order)].get('id')
        nxt = order[(index + 1) % len(order)].get('id')
    return {
        'combat': True,
        'round': combat.get('round'),
        'current': current,
        'next': nxt,
    }


def read_meta(directory):
    return json.loads((Path(directory) / 'campaign.json').read_text(encoding='utf-8'))


def initial_changes(seed, start, chapter):
    return [
        {'op': 'set', 'path': 'adventure', 'from': None, 'to': {
            'flags': {}, 'looted': [], 'revealed': [], 'triggered': [],
        }},
        {'op': 'set', 'path': 'chapter', 'from': None, 'to': chapter},
        {'op': 'set', 'path': 'characters', 'from': None, 'to': {}},
        {'op': 'set', 'path': 'combat', 'from': None, 'to': {
            'active': False, 'current_index': None, 'economy': {}, 'order': [], 'round': None,
        }},
        {'op': 'set', 'path': 'effects', 'from': None, 'to': []},
        {'op': 'set', 'path': 'location', 'from': None, 'to': start},
        {'op': 'set', 'path': 'monsters', 'from': None, 'to': {}},
        {'op': 'set', 'path': 'rng', 'from': None, 'to': {'counter': 0, 'seed': seed}},
        {'op': 'set', 'path': 'seats', 'from': None, 'to': {}},
        {'op': 'set', 'path': 'time', 'from': None, 'to': {'day': 1, 'minute': 0}},
    ]
