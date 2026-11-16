"""Undo and override. The dice counter never rewinds."""

import json

from dnd.errors import EngineError
from dnd.chargen import rule


def undo(events, reason):
    if not reason or not str(reason).strip():
        raise EngineError('BAD_ARGS', '--reason is required')
    reverted = {
        event.get('reverts') for event in events
        if event.get('type') == 'revert'
    }
    target = None
    for event in reversed(events):
        if event.get('type') == 'revert' or event.get('id') in reverted:
            continue
        if event.get('changes'):
            target = event
            break
    if target is None:
        rule('nothing to undo')
    event = {
        'actor': 'dm',
        'changes': [],
        'cmd': f'undo --reason {reason} --as dm',
        'reason': reason,
        'reverts': target['id'],
        'rolls': [],
        'type': 'revert',
    }
    return {'reverts': target['id'], 'reason': reason}, event


def override(state, path, raw_value, reason):
    if not reason or not str(reason).strip():
        raise EngineError('BAD_ARGS', '--reason is required')
    if not path or path == 'rng' or path.startswith('rng.'):
        rule('override cannot touch rng', path=path)
    value = _parse_value(raw_value)
    current = _lookup(state, path)
    event = {
        'actor': 'dm',
        'changes': [{'op': 'set', 'path': path, 'from': current, 'to': value}],
        'cmd': f'override {path} --reason {reason} --as dm',
        'reason': reason,
        'rolls': [],
        'type': 'override',
    }
    return {'path': path, 'value': value, 'reason': reason}, event


def add_seat(state, player_id, controller):
    if controller not in ('javi', 'dm') and not (
        isinstance(controller, str) and controller.startswith('bot:') and len(controller) > 4
    ):
        rule('controller must be bot:<name>, javi, or dm', controller=controller)
    seats = state.get('seats') or {}
    if player_id in seats:
        rule('seat already registered', player=player_id)
    event = {
        'actor': 'dm',
        'changes': [{
            'op': 'set',
            'path': f'seats.{player_id}',
            'from': None,
            'to': {'controller': controller},
        }],
        'cmd': f'player add {player_id} --controller {controller} --as dm',
        'rolls': [],
        'type': 'seat',
    }
    return {'player': player_id, 'controller': controller}, event


def _parse_value(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _lookup(state, path):
    cursor = state
    for part in path.split('.'):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor
