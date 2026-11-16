"""CLI for P1: campaigns, seeded rolls, the event log, and help."""

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

from dnd.dice import roll_expr
from dnd.errors import EngineError, exit_code
from dnd import party
from dnd import store

# Verbatim SRD 5.1 attribution. tests/test_p1.py checks it against NOTICE.md.
SRD_ATTRIBUTION = (
    'This work includes material taken from the System Reference Document 5.1 '
    '("SRD 5.1") by Wizards of the Coast LLC and available at '
    'https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 '
    'is licensed under the Creative Commons Attribution 4.0 International '
    'License available at https://creativecommons.org/licenses/by/4.0/legalcode.'
)

_ACTOR = re.compile(r'^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$')

_VALUE_FLAGS = {
    '--campaign', '--adventure', '--seed', '--as', '--reason',
    '--last', '--since', '--type', '--json-in', '--dc', '--help-from',
    '--to', '--from', '--qty', '--slot', '--section', '--target',
}

COMMANDS = (
    {
        'name': 'campaign new',
        'usage': 'dnd campaign new <name> --adventure FILE [--seed N] --as dm [--campaign DIR]',
        'who': 'dm',
        'summary': 'Create a campaign, copy the adventure, and log the seed.',
    },
    {
        'name': 'campaign load',
        'usage': 'dnd campaign load <name> --as <actor> [--campaign DIR]',
        'who': 'any',
        'summary': 'Set the active campaign.',
    },
    {
        'name': 'campaign info',
        'usage': 'dnd campaign info [--campaign DIR]',
        'who': 'any; --as is optional',
        'summary': 'Party, location, time, and whether combat is active.',
    },
    {
        'name': 'verify',
        'usage': 'dnd verify [--campaign DIR]',
        'who': 'any; --as is optional',
        'summary': 'Re-hash the event log and replay it onto state.json.',
    },
    {
        'name': 'roll',
        'usage': 'dnd roll <expr> --as <actor> [--adv|--dis] [--reason TEXT] [--secret] [--campaign DIR]',
        'who': '--as required; --secret is dm only',
        'summary': 'Roll NdM[+-K], kh/kl, or d% with the campaign seed.',
    },
    {
        'name': 'log',
        'usage': 'dnd log --as <actor> [--last N] [--since ID] [--type TYPE] [--campaign DIR]',
        'who': '--as required',
        'summary': 'Show events. Secret rolls are hidden from anyone who is not the DM.',
    },
    {
        'name': 'help',
        'usage': 'dnd help [command] [--about]',
        'who': 'any; --as is optional',
        'summary': 'List commands. --about prints the SRD attribution.',
    },
    {
        'name': 'char create',
        'usage': 'dnd char create --json-in FILE --as dm',
        'who': 'dm',
        'summary': 'Build a level-1 SRD character.',
    },
    {
        'name': 'char assign-scores',
        'usage': 'dnd char assign-scores <id> --json-in FILE --as <owner|dm>',
        'who': 'owner or dm',
        'summary': 'Assign a rolled ability pool.',
    },
    {
        'name': 'char show',
        'usage': 'dnd char show <id> --as <actor>',
        'who': 'owner or dm see the full sheet; others see a public view',
        'summary': 'Show one character.',
    },
    {
        'name': 'char list',
        'usage': 'dnd char list --as <actor>',
        'who': 'any',
        'summary': 'Party roster and seats.',
    },
    {
        'name': 'state show',
        'usage': 'dnd state show --as <actor> [--section party|location|combat|all]',
        'who': 'any',
        'summary': 'Filtered campaign snapshot.',
    },
    {
        'name': 'check',
        'usage': 'dnd check <char> <ability|skill> --dc N --as <owner|dm> [--adv|--dis] [--help-from ID] [--secret-dc]',
        'who': 'owner or dm',
        'summary': 'Ability or skill check.',
    },
    {
        'name': 'save',
        'usage': 'dnd save <id> <ability> --dc N --as <owner|dm> [--adv|--dis]',
        'who': 'owner or dm',
        'summary': 'Saving throw.',
    },
    {
        'name': 'contest',
        'usage': 'dnd contest <a> <skill> <b> <skill> --as dm',
        'who': 'dm',
        'summary': 'Opposed check.',
    },
    {
        'name': 'group-check',
        'usage': 'dnd group-check <skill> --dc N --as dm',
        'who': 'dm',
        'summary': 'Party check. Succeeds when half or more succeed.',
    },
    {
        'name': 'inv show',
        'usage': 'dnd inv show <char> --as <owner|dm>',
        'who': 'owner or dm',
        'summary': 'Items, coins, weight, and attunement.',
    },
    {
        'name': 'inv give',
        'usage': 'dnd inv give <item> --to <char> [--qty N] [--from <char>|loot:<id>] --as dm',
        'who': 'dm',
        'summary': 'Give an item or coins.',
    },
    {
        'name': 'inv transfer',
        'usage': 'dnd inv transfer <item> <from> <to> [--qty N] --as <from>',
        'who': 'the character handing the item over',
        'summary': 'Hand an item or coins to another character.',
    },
    {
        'name': 'equip',
        'usage': 'dnd equip <char> <item> [--slot main|off|armor|shield] --as <owner|dm>',
        'who': 'owner or dm',
        'summary': 'Equip an item and recompute AC and attacks.',
    },
    {
        'name': 'unequip',
        'usage': 'dnd unequip <char> <item> --as <owner|dm>',
        'who': 'owner or dm',
        'summary': 'Unequip an item and recompute AC and attacks.',
    },
    {
        'name': 'use',
        'usage': 'dnd use <char> <item> [--target ID] --as <owner|dm>',
        'who': 'owner or dm',
        'summary': 'Use a potion or other consumable.',
    },
)


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise EngineError('BAD_ARGS', message)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        payload = dispatch(argv)
    except EngineError as exc:
        emit(_failure(exc, argv))
        return exit_code(exc.code)
    emit(payload)
    return 0


def dispatch(argv):
    explicit, rest = extract_campaign(argv)
    parser = _parser()
    args = parser.parse_args(rest)
    group = args.group
    verb = getattr(args, 'verb', None)
    if group == 'campaign' and verb == 'new':
        return cmd_campaign_new(args, explicit)
    if group == 'campaign' and verb == 'load':
        return cmd_campaign_load(args, explicit)
    if group == 'campaign' and verb == 'info':
        return cmd_campaign_info(args, explicit)
    if group == 'verify':
        return cmd_verify(explicit)
    if group == 'roll':
        return cmd_roll(args, explicit)
    if group == 'log':
        return cmd_log(args, explicit)
    if group == 'help':
        return cmd_help(args)
    if group == 'char' and verb == 'create':
        return cmd_char_create(args, explicit)
    if group == 'char' and verb == 'assign-scores':
        return cmd_char_assign(args, explicit)
    if group == 'char' and verb == 'show':
        return cmd_char_show(args, explicit)
    if group == 'char' and verb == 'list':
        return cmd_char_list(args, explicit)
    if group == 'state' and verb == 'show':
        return cmd_state_show(args, explicit)
    if group == 'check':
        return cmd_check(args, explicit, saving=False)
    if group == 'save':
        return cmd_check(args, explicit, saving=True)
    if group == 'contest':
        return cmd_contest(args, explicit)
    if group == 'group-check':
        return cmd_group_check(args, explicit)
    if group == 'inv' and verb == 'show':
        return cmd_inv_show(args, explicit)
    if group == 'inv' and verb == 'give':
        return cmd_inv_give(args, explicit)
    if group == 'inv' and verb == 'transfer':
        return cmd_inv_transfer(args, explicit)
    if group == 'equip':
        return cmd_equip(args, explicit)
    if group == 'unequip':
        return cmd_unequip(args, explicit)
    if group == 'use':
        return cmd_use(args, explicit)
    raise EngineError('BAD_ARGS', 'choose a command')


def cmd_campaign_new(args, explicit):
    _require_dm(args.actor)
    name = store.check_name(args.name)
    adventure = _read_adventure(args.adventure)
    root, _explicit = store.locate_root(explicit)
    directory = store.campaign_dir(root, name)
    if directory.exists():
        raise EngineError('BAD_ARGS', f'campaign {name} already exists', {'campaign': name})
    seed = args.seed
    if seed is None:
        seed = random.SystemRandom().randrange(1, 2**31)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise EngineError('BAD_ARGS', f'campaign {name} already exists', {'campaign': name}) from exc
    try:
        with store.locked(directory):
            (directory / 'adventure.json').write_bytes(adventure['raw'])
            meta = {
                'adventure_sha256': adventure['sha256'],
                'created': store.timestamp(),
                'engine_version': store.ENGINE_VERSION,
                'name': name,
                'ruleset': 'srd-5.1',
                'seed': seed,
            }
            (directory / 'campaign.json').write_text(
                json.dumps(meta, indent=2, sort_keys=True) + '\n',
                encoding='utf-8',
            )
            event = {
                'actor': 'dm',
                'changes': store.initial_changes(seed, adventure['start'], adventure['chapter']),
                'cmd': f'campaign new {name} --seed {seed} --as dm',
                'rolls': [],
                'type': 'campaign',
            }
            event, state = store.append_event(directory, event)
    except Exception:
        # A failed create should not leave a half-written campaign behind
        # when the failure happened before the log was valid. Leave the
        # directory if the event was committed; otherwise remove it.
        if not (directory / 'events.jsonl').exists():
            _remove_tree(directory)
        raise
    store.set_active(root, name, directory)
    return _ok(
        'campaign new',
        {'campaign': name, 'seed': seed, 'start_location': adventure['start']},
        event_ids=[event['id']],
        turn=store.turn_view(state),
    )


def cmd_campaign_load(args, explicit):
    _require_actor(args.actor)
    name = store.check_name(args.name)
    root, _explicit = store.locate_root(explicit)
    directory = store.campaign_dir(root, name)
    if not (directory / 'campaign.json').is_file():
        raise EngineError('UNKNOWN_ID', f'campaign {name} does not exist', {'campaign': name})
    store.set_active(root, name, directory)
    meta = store.read_meta(directory)
    with store.locked(directory):
        state = store.read_state(directory) or {}
    return _ok(
        'campaign load',
        {
            'campaign': meta['name'],
            'chapter': state.get('chapter'),
            'location': state.get('location'),
            'combat': bool((state.get('combat') or {}).get('active')),
        },
        turn=store.turn_view(state),
    )


def cmd_campaign_info(_args, explicit):
    directory = store.open_campaign(explicit)
    meta = store.read_meta(directory)
    with store.locked(directory):
        state = store.read_state(directory) or {}
    return _ok(
        'campaign info',
        {
            'campaign': meta['name'],
            'party': sorted((state.get('characters') or {}).keys()),
            'location': state.get('location'),
            'time': state.get('time'),
            'combat': bool((state.get('combat') or {}).get('active')),
        },
        turn=store.turn_view(state),
    )


def cmd_verify(explicit):
    directory = store.open_campaign(explicit)
    result = store.verify(directory)
    with store.locked(directory):
        state = store.read_state(directory) or {}
    return _ok('verify', result, turn=store.turn_view(state))


def cmd_roll(args, explicit):
    actor = _require_actor(args.actor)
    if args.secret and actor != 'dm':
        raise EngineError(
            'NOT_AUTHORIZED',
            'only the DM can roll in secret',
            {'actor': actor},
            cmd='roll',
        )
    mode, sources, warnings = _advantage(args.adv, args.dis)
    directory = store.open_campaign(explicit)
    with store.locked(directory):
        events = store.read_events(directory)
        state, _prev = store.fold(events)
        try:
            seed = state['rng']['seed']
            counter = state['rng']['counter']
        except (KeyError, TypeError) as exc:
            raise EngineError('LOG_TAMPERED', 'campaign state has no rng') from exc
        try:
            rolled = roll_expr(args.expr, seed, counter, mode)
        except ValueError as exc:
            raise EngineError('BAD_ARGS', str(exc), cmd='roll') from exc
        record = {
            'adv': mode,
            'adv_sources': sources,
            'counter': counter,
            'dice': rolled['dice'],
            'expr': args.expr,
            'kept': rolled['kept'],
            'mod': rolled['mod'],
            'total': rolled['total'],
        }
        parts = ['roll', args.expr, '--as', actor]
        if args.adv:
            parts.append('--adv')
        if args.dis:
            parts.append('--dis')
        if args.reason:
            parts.extend(['--reason', args.reason])
        if args.secret:
            parts.append('--secret')
        event = {
            'actor': actor,
            'changes': [{
                'op': 'set',
                'path': 'rng.counter',
                'from': counter,
                'to': counter + 1,
            }],
            'cmd': ' '.join(parts),
            'rolls': [record],
            'type': 'roll',
        }
        relay = party.relay_for(state, actor)
        if relay:
            event['relayed_by'] = relay
        if args.reason:
            event['reason'] = args.reason
        if args.secret:
            event['secret'] = True
        event, state = store.append_event(directory, event)
    envelope_roll = dict(record)
    envelope_roll['id'] = f'r-{counter:04d}'
    return _ok(
        'roll',
        {'expr': args.expr, 'total': rolled['total'], 'adv': mode},
        event_ids=[event['id']],
        rolls=[envelope_roll],
        turn=store.turn_view(state),
        warnings=warnings,
    )


def cmd_log(args, explicit):
    actor = _require_actor(args.actor)
    if args.last is not None and args.last < 1:
        raise EngineError('BAD_ARGS', '--last must be at least 1', cmd='log')
    if args.since is not None and args.since < 0:
        raise EngineError('BAD_ARGS', '--since must be zero or more', cmd='log')
    directory = store.open_campaign(explicit)
    with store.locked(directory):
        events = store.read_events(directory)
        store.fold(events)
        state = store.read_state(directory) or {}
    rows = events
    if args.since is not None:
        rows = [event for event in rows if event.get('id', 0) > args.since]
    if args.event_type:
        rows = [event for event in rows if event.get('type') == args.event_type]
    if args.last is not None:
        rows = rows[-args.last:]
    if actor != 'dm':
        rows = [_redact(event, actor) for event in rows]
    return _ok('log', {'events': rows}, turn=store.turn_view(state))


def cmd_help(args):
    if args.about:
        return _ok('help', {
            'about': SRD_ATTRIBUTION,
            'engine_version': store.ENGINE_VERSION,
            'ruleset': 'srd-5.1',
        }, turn=None)
    topic = ' '.join(args.topic or [])
    if not topic:
        listed = list(COMMANDS)
    else:
        listed = [command for command in COMMANDS if command['name'] == topic]
        if not listed:
            listed = [
                command for command in COMMANDS
                if command['name'].startswith(topic + ' ') or command['name'] == topic
            ]
        if not listed:
            raise EngineError('BAD_ARGS', f'unknown command {topic!r}', cmd='help')
    return _ok('help', {'commands': listed}, turn=None)


def _read_adventure(path_text):
    path = Path(path_text)
    if not path.is_file():
        raise EngineError('BAD_ARGS', 'adventure file does not exist', {'path': str(path)})
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EngineError('BAD_ARGS', 'adventure file is not JSON') from exc
    if not isinstance(data, dict):
        raise EngineError('BAD_ARGS', 'adventure file must be a JSON object')
    ruleset = data.get('ruleset', 'srd-5.1')
    if ruleset != 'srd-5.1':
        raise EngineError(
            'RULE_VIOLATION',
            'only SRD 5.1 adventures are accepted',
            {'ruleset': ruleset},
        )
    start = data.get('start')
    if start is not None and not isinstance(start, str):
        raise EngineError('BAD_ARGS', 'adventure start must be a string')
    chapter = None
    chapters = data.get('chapters')
    if isinstance(chapters, list) and chapters and isinstance(chapters[0], dict):
        chapter_id = chapters[0].get('id')
        if isinstance(chapter_id, str):
            chapter = chapter_id
    return {
        'raw': raw,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'start': start,
        'chapter': chapter,
    }


def _advantage(adv, dis):
    if adv and dis:
        return 'none', ['flag:adv', 'flag:dis'], ['advantage and disadvantage cancel']
    if adv:
        return 'adv', ['flag:adv'], []
    if dis:
        return 'dis', ['flag:dis'], []
    return 'none', [], []


def _redact(event, actor):
    hidden = dict(event)
    if event.get('secret'):
        hidden['rolls'] = [
            {'counter': roll.get('counter'), 'secret': True}
            for roll in event.get('rolls') or []
        ]
    if actor != 'dm' and event.get('secret_dc') and isinstance(hidden.get('outcome'), dict):
        outcome = dict(hidden['outcome'])
        outcome.pop('dc', None)
        outcome.pop('success', None)
        hidden['outcome'] = outcome
    return hidden


def _require_actor(actor):
    if actor is None:
        raise EngineError('BAD_ARGS', '--as is required')
    if _ACTOR.fullmatch(actor) is None:
        raise EngineError('BAD_ARGS', 'actor id is not a slug', {'actor': actor})
    return actor


def _require_dm(actor):
    actor = _require_actor(actor)
    if actor != 'dm':
        raise EngineError('NOT_AUTHORIZED', 'only the DM can do that', {'actor': actor})
    return actor


def _ok(cmd, result, event_ids=None, rolls=None, turn=None, warnings=None):
    return {
        'ok': True,
        'cmd': cmd,
        'event_ids': event_ids or [],
        'result': result,
        'rolls': rolls or [],
        'turn': turn,
        'warnings': warnings or [],
    }


def _failure(exc, argv):
    return {
        'ok': False,
        'cmd': exc.cmd or guess_cmd(argv),
        'error': {
            'code': exc.code,
            'message': exc.message,
            'details': exc.details,
        },
    }


def emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + '\n')


def extract_campaign(argv):
    rest = []
    campaign = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == '--campaign':
            if index + 1 >= len(argv):
                raise EngineError('BAD_ARGS', '--campaign needs a directory')
            campaign = argv[index + 1]
            index += 2
            continue
        if token.startswith('--campaign='):
            campaign = token.split('=', 1)[1]
            index += 1
            continue
        rest.append(token)
        index += 1
    if campaign is not None and not str(campaign).strip():
        raise EngineError('BAD_ARGS', '--campaign needs a directory')
    return campaign, rest


def guess_cmd(argv):
    words = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUE_FLAGS:
            skip_next = True
            continue
        if token.startswith('--'):
            continue
        words.append(token)
        if words[0] not in ('campaign', 'char', 'state', 'inv'):
            return words[0]
        if len(words) == 2:
            return words[0] + ' ' + words[1]
    if words:
        return ' '.join(words)
    return 'dnd'


def _parser():
    parser = JsonParser(prog='dnd', add_help=False)
    sub = parser.add_subparsers(dest='group', required=True, parser_class=JsonParser)

    campaign = sub.add_parser('campaign', add_help=False)
    campaign_sub = campaign.add_subparsers(dest='verb', required=True, parser_class=JsonParser)

    new_p = campaign_sub.add_parser('new', add_help=False)
    new_p.add_argument('name')
    new_p.add_argument('--adventure', required=True)
    new_p.add_argument('--seed', type=int)
    new_p.add_argument('--as', dest='actor')

    load_p = campaign_sub.add_parser('load', add_help=False)
    load_p.add_argument('name')
    load_p.add_argument('--as', dest='actor')

    info_p = campaign_sub.add_parser('info', add_help=False)
    info_p.add_argument('--as', dest='actor')

    verify_p = sub.add_parser('verify', add_help=False)
    verify_p.add_argument('--as', dest='actor')

    roll_p = sub.add_parser('roll', add_help=False)
    roll_p.add_argument('expr')
    roll_p.add_argument('--as', dest='actor')
    roll_p.add_argument('--adv', action='store_true')
    roll_p.add_argument('--dis', action='store_true')
    roll_p.add_argument('--reason')
    roll_p.add_argument('--secret', action='store_true')

    log_p = sub.add_parser('log', add_help=False)
    log_p.add_argument('--as', dest='actor')
    log_p.add_argument('--last', type=int)
    log_p.add_argument('--since', type=int)
    log_p.add_argument('--type', dest='event_type')

    help_p = sub.add_parser('help', add_help=False)
    help_p.add_argument('topic', nargs='*')
    help_p.add_argument('--about', action='store_true')

    char = sub.add_parser('char', add_help=False)
    char_sub = char.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    create_p = char_sub.add_parser('create', add_help=False)
    create_p.add_argument('--json-in', required=True)
    create_p.add_argument('--as', dest='actor')
    assign_p = char_sub.add_parser('assign-scores', add_help=False)
    assign_p.add_argument('id')
    assign_p.add_argument('--json-in', required=True)
    assign_p.add_argument('--as', dest='actor')
    show_p = char_sub.add_parser('show', add_help=False)
    show_p.add_argument('id')
    show_p.add_argument('--as', dest='actor')
    list_p = char_sub.add_parser('list', add_help=False)
    list_p.add_argument('--as', dest='actor')

    state_p = sub.add_parser('state', add_help=False)
    state_sub = state_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    state_show = state_sub.add_parser('show', add_help=False)
    state_show.add_argument('--as', dest='actor')
    state_show.add_argument('--section')

    check_p = sub.add_parser('check', add_help=False)
    check_p.add_argument('id')
    check_p.add_argument('which')
    check_p.add_argument('--dc', type=int, required=True)
    check_p.add_argument('--as', dest='actor')
    check_p.add_argument('--adv', action='store_true')
    check_p.add_argument('--dis', action='store_true')
    check_p.add_argument('--help-from')
    check_p.add_argument('--secret-dc', action='store_true')

    save_p = sub.add_parser('save', add_help=False)
    save_p.add_argument('id')
    save_p.add_argument('which')
    save_p.add_argument('--dc', type=int, required=True)
    save_p.add_argument('--as', dest='actor')
    save_p.add_argument('--adv', action='store_true')
    save_p.add_argument('--dis', action='store_true')

    contest_p = sub.add_parser('contest', add_help=False)
    contest_p.add_argument('left')
    contest_p.add_argument('left_skill')
    contest_p.add_argument('right')
    contest_p.add_argument('right_skill')
    contest_p.add_argument('--as', dest='actor')

    group_p = sub.add_parser('group-check', add_help=False)
    group_p.add_argument('skill')
    group_p.add_argument('--dc', type=int, required=True)
    group_p.add_argument('--as', dest='actor')

    inv = sub.add_parser('inv', add_help=False)
    inv_sub = inv.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    inv_show = inv_sub.add_parser('show', add_help=False)
    inv_show.add_argument('id')
    inv_show.add_argument('--as', dest='actor')
    inv_give = inv_sub.add_parser('give', add_help=False)
    inv_give.add_argument('item')
    inv_give.add_argument('--to', required=True)
    inv_give.add_argument('--qty', type=int, default=1)
    inv_give.add_argument('--from', dest='from_char')
    inv_give.add_argument('--as', dest='actor')
    inv_transfer = inv_sub.add_parser('transfer', add_help=False)
    inv_transfer.add_argument('item')
    inv_transfer.add_argument('source')
    inv_transfer.add_argument('dest')
    inv_transfer.add_argument('--qty', type=int, default=1)
    inv_transfer.add_argument('--as', dest='actor')

    equip_p = sub.add_parser('equip', add_help=False)
    equip_p.add_argument('id')
    equip_p.add_argument('item')
    equip_p.add_argument('--slot')
    equip_p.add_argument('--as', dest='actor')

    unequip_p = sub.add_parser('unequip', add_help=False)
    unequip_p.add_argument('id')
    unequip_p.add_argument('item')
    unequip_p.add_argument('--as', dest='actor')

    use_p = sub.add_parser('use', add_help=False)
    use_p.add_argument('id')
    use_p.add_argument('item')
    use_p.add_argument('--target')
    use_p.add_argument('--as', dest='actor')
    return parser


def _with_campaign(explicit):
    directory = store.open_campaign(explicit)
    events = store.read_events(directory)
    state, _prev = store.fold(events)
    return directory, state


def _commit(directory, event, state):
    relay = party.relay_for(state, event.get('actor'))
    if relay and 'relayed_by' not in event:
        event['relayed_by'] = relay
    saved, new_state = store.append_event(directory, event)
    return saved, new_state


def _read_json(path_text):
    path = Path(path_text)
    if not path.is_file():
        raise EngineError('BAD_ARGS', 'json file does not exist', {'path': str(path)})
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise EngineError('BAD_ARGS', 'json file is not JSON') from exc
    return data


def cmd_char_create(args, explicit):
    _require_dm(args.actor)
    data = _read_json(args.json_in)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        result, event = party.create_character(state, data)
        saved, new_state = _commit(directory, event, state)
    return _ok('char create', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_char_assign(args, explicit):
    actor = _require_actor(args.actor)
    scores = _read_json(args.json_in)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        result, event = party.assign_scores(state, args.id, scores, actor)
        saved, new_state = _commit(directory, event, state)
    return _ok('char assign-scores', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_char_show(args, explicit):
    actor = _require_actor(args.actor)
    _directory, state = _with_campaign(explicit)
    return _ok('char show', party.show_character(state, args.id, actor), turn=store.turn_view(state))


def cmd_char_list(args, explicit):
    _require_actor(args.actor)
    _directory, state = _with_campaign(explicit)
    return _ok('char list', party.list_characters(state), turn=store.turn_view(state))


def cmd_state_show(args, explicit):
    actor = _require_actor(args.actor)
    _directory, state = _with_campaign(explicit)
    return _ok('state show', party.state_view(state, actor, args.section), turn=store.turn_view(state))


def cmd_check(args, explicit, saving):
    actor = _require_actor(args.actor)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        outcome, event, warnings = party.roll_check(
            state, args.id, args.which, args.dc, actor,
            getattr(args, 'adv', False), getattr(args, 'dis', False),
            getattr(args, 'help_from', None), getattr(args, 'secret_dc', False),
            saving=saving,
        )
        saved, new_state = _commit(directory, event, state)
    return _ok(
        'save' if saving else 'check',
        outcome,
        event_ids=[saved['id']],
        rolls=saved['rolls'],
        turn=store.turn_view(new_state),
        warnings=warnings,
    )


def cmd_contest(args, explicit):
    _require_dm(args.actor)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        outcome, event = party.roll_contest(state, args.left, args.left_skill, args.right, args.right_skill)
        saved, new_state = _commit(directory, event, state)
    return _ok('contest', outcome, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_group_check(args, explicit):
    _require_dm(args.actor)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        outcome, event = party.roll_group_check(state, args.skill, args.dc)
        saved, new_state = _commit(directory, event, state)
    return _ok('group-check', outcome, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_inv_show(args, explicit):
    actor = _require_actor(args.actor)
    _directory, state = _with_campaign(explicit)
    return _ok('inv show', party.show_inventory(state, args.id, actor), turn=store.turn_view(state))


def cmd_inv_give(args, explicit):
    _require_dm(args.actor)
    if args.qty < 1:
        raise EngineError('BAD_ARGS', '--qty must be at least 1')
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        _result, event = party.give_item(state, args.item, args.qty, args.to, args.from_char)
        saved, new_state = _commit(directory, event, state)
    return _ok('inv give', {'item': args.item, 'qty': args.qty, 'to': args.to}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_inv_transfer(args, explicit):
    actor = _require_actor(args.actor)
    if args.qty < 1:
        raise EngineError('BAD_ARGS', '--qty must be at least 1')
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        _result, event = party.transfer_item(state, args.item, args.source, args.dest, args.qty, actor)
        saved, new_state = _commit(directory, event, state)
    return _ok('inv transfer', {'item': args.item, 'qty': args.qty}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_equip(args, explicit):
    actor = _require_actor(args.actor)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        _result, event = party.equip_item(state, args.id, args.item, args.slot, actor)
        saved, new_state = _commit(directory, event, state)
    return _ok('equip', {'id': args.id, 'item': args.item}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_unequip(args, explicit):
    actor = _require_actor(args.actor)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        _result, event = party.unequip_item(state, args.id, args.item, actor)
        saved, new_state = _commit(directory, event, state)
    return _ok('unequip', {'id': args.id, 'item': args.item}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_use(args, explicit):
    actor = _require_actor(args.actor)
    directory, state = _with_campaign(explicit)
    with store.locked(directory):
        state, _prev = store.fold(store.read_events(directory))
        outcome, event = party.use_item(state, args.id, args.item, args.target, actor)
        saved, new_state = _commit(directory, event, state)
    return _ok('use', outcome, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def _remove_tree(directory):
    path = Path(directory)
    if not path.is_dir():
        return
    for child in sorted(path.iterdir(), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            _remove_tree(child)
    path.rmdir()
