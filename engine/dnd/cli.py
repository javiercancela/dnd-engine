"""CLI for P1: campaigns, seeded rolls, the event log, and help."""

import argparse
import hashlib
import json
import random
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from dnd import adventure
from dnd import combat
from dnd import levelup
from dnd.dice import roll_expr
from dnd import spells
from dnd.errors import EngineError, exit_code
from dnd import history
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
    '--combatants', '--surprised', '--weapon', '--natural', '--range',
    '--cover', '--type', '--ft', '--duration', '--level', '--source',
        '--targets', '--slot', '--spend', '--travel-hours', '--controller',
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
    {
        'name': 'encounter spawn',
        'usage': 'dnd encounter spawn <id> --as dm [--roll-hp] [--start-combat]',
        'who': 'dm',
        'summary': 'Create monsters from the adventure.',
    },
    {
        'name': 'combat start',
        'usage': 'dnd combat start --combatants id,id --as dm [--surprised id,id]',
        'who': 'dm',
        'summary': 'Roll initiative and start round 1.',
    },
    {
        'name': 'combat status',
        'usage': 'dnd combat status --as <actor>',
        'who': 'any',
        'summary': 'Order, round, and public status. Monster hit points are numbers only for the DM.',
    },
    {
        'name': 'combat end',
        'usage': 'dnd combat end --reason fled|victory|parley --as dm',
        'who': 'dm',
        'summary': 'End combat. Victory awards monster XP, split evenly.',
    },
    {
        'name': 'turn end',
        'usage': 'dnd turn end --as <current>',
        'who': 'current creature, or the DM for a monster',
        'summary': 'Advance the turn and tick durations.',
    },
    {
        'name': 'attack',
        'usage': 'dnd attack <attacker> <target> --weapon <id> --range FT --as <actor> [--adv|--dis] [--reaction] [--cover half|three-quarters]',
        'who': 'the attacker, or the DM for a monster',
        'summary': 'Roll an attack and apply damage.',
    },
    {
        'name': 'damage',
        'usage': 'dnd damage <target> <amount|expr> --type T --as dm',
        'who': 'dm',
        'summary': 'Apply damage, resistance, and dropping to 0 HP.',
    },
    {
        'name': 'heal',
        'usage': 'dnd heal <target> <amount|expr> --as dm',
        'who': 'dm',
        'summary': 'Restore hit points, capped at the maximum.',
    },
    {
        'name': 'temp-hp',
        'usage': 'dnd temp-hp <target> N --as dm',
        'who': 'dm',
        'summary': 'Set temporary hit points to the higher value.',
    },
    {
        'name': 'death-save',
        'usage': 'dnd death-save <id> --as <owner>',
        'who': 'owner or dm',
        'summary': 'Death saving throw at 0 hit points.',
    },
    {
        'name': 'condition add',
        'usage': 'dnd condition add <id> <condition> [--duration TEXT] [--level N] --as dm',
        'who': 'dm',
        'summary': 'Add a condition or set exhaustion.',
    },
    {
        'name': 'condition remove',
        'usage': 'dnd condition remove <id> <condition> --as dm',
        'who': 'dm',
        'summary': 'Remove a condition.',
    },
    {
        'name': 'action',
        'usage': 'dnd action <id> dash|disengage|dodge|help|hide|ready|search|use-object [--bonus] --as <actor>',
        'who': 'the creature, or the DM for a monster',
        'summary': 'Spend an action. Dodge imposes disadvantage on attacks against the creature.',
    },
    {
        'name': 'move',
        'usage': 'dnd move <id> --ft N [--difficult] --as <actor>',
        'who': 'the creature, or the DM for a monster',
        'summary': 'Spend movement.',
    },
    {
        'name': 'cast',
        'usage': 'dnd cast <id> <spell> [--targets a,b] [--slot N] [--range FT] --as <actor> [--ritual] [--reaction]',
        'who': 'the caster, or the DM for a relayed seat',
        'summary': 'Cast a prepared or known spell. Unmodelled spells return effect manual.',
    },
    {
        'name': 'rest short',
        'usage': 'dnd rest short --as dm [--spend id:N,id:N]',
        'who': 'dm',
        'summary': 'Spend an hour. Hit dice, Second Wind, warlock slots, and Arcane Recovery.',
    },
    {
        'name': 'rest long',
        'usage': 'dnd rest long --as dm',
        'who': 'dm',
        'summary': 'Spend eight hours. Restore hit points, hit dice, slots, and features.',
    },
    {
        'name': 'rest spend-hd',
        'usage': 'dnd rest spend-hd <id> N --as <owner>',
        'who': 'owner or dm',
        'summary': 'Spend hit dice outside combat.',
    },
    {
        'name': 'adventure validate',
        'usage': 'dnd adventure validate FILE',
        'who': 'any',
        'summary': 'Check that an adventure file\'s refs and DCs resolve.',
    },
    {
        'name': 'look',
        'usage': 'dnd look --as <actor>',
        'who': 'any',
        'summary': 'The current location. Players do not see dm_notes.',
    },
    {
        'name': 'go',
        'usage': 'dnd go <location> --as dm [--travel-hours N]',
        'who': 'dm',
        'summary': 'Take a connection. on_enter can spawn an encounter.',
    },
    {
        'name': 'search',
        'usage': 'dnd search <id> --as <owner>',
        'who': 'owner or dm',
        'summary': 'Look for hidden features and exits.',
    },
    {
        'name': 'trap trigger',
        'usage': 'dnd trap trigger <id> [--target <char>] --as dm',
        'who': 'dm',
        'summary': 'Set off a trap.',
    },
    {
        'name': 'trap disarm',
        'usage': 'dnd trap disarm <id> <char> --as <owner>',
        'who': 'owner or dm',
        'summary': 'Try to disarm a trap.',
    },
    {
        'name': 'xp award',
        'usage': 'dnd xp award <amount> [--to all|a,b] --as dm',
        'who': 'dm',
        'summary': 'Split experience evenly.',
    },
    {
        'name': 'xp milestone',
        'usage': 'dnd xp milestone <level> --as dm',
        'who': 'dm',
        'summary': 'Make a level available.',
    },
    {
        'name': 'level up',
        'usage': 'dnd level up <id> --as <owner> [--json-in FILE] [--roll]',
        'who': 'owner or dm',
        'summary': 'Advance one level, up to 5.',
    },
    {
        'name': 'time advance',
        'usage': 'dnd time advance <minutes> --as dm',
        'who': 'dm',
        'summary': 'Move the campaign clock.',
    },
    {
        'name': 'player add',
        'usage': 'dnd player add <id> --controller bot:<name>|javi|dm --as dm',
        'who': 'dm',
        'summary': 'Register a seat. javi and dm seats are relayed by the DM.',
    },
    {
        'name': 'undo',
        'usage': 'dnd undo --reason TEXT --as dm',
        'who': 'dm',
        'summary': 'Revert the last state change. The dice counter stays advanced.',
    },
    {
        'name': 'override',
        'usage': 'dnd override <path> <json> --reason TEXT --as dm',
        'who': 'dm',
        'summary': 'Set one state path. It cannot touch rng.',
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
    if group == 'encounter' and verb == 'spawn':
        return cmd_spawn(args, explicit)
    if group == 'combat' and verb == 'start':
        return cmd_combat_start(args, explicit)
    if group == 'combat' and verb == 'status':
        return cmd_combat_status(args, explicit)
    if group == 'combat' and verb == 'end':
        return cmd_combat_end(args, explicit)
    if group == 'turn' and verb == 'end':
        return cmd_turn_end(args, explicit)
    if group == 'attack':
        return cmd_attack(args, explicit)
    if group == 'damage':
        return cmd_damage(args, explicit)
    if group == 'heal':
        return cmd_heal(args, explicit)
    if group == 'temp-hp':
        return cmd_temp_hp(args, explicit)
    if group == 'death-save':
        return cmd_death_save(args, explicit)
    if group == 'condition':
        return cmd_condition(args, explicit)
    if group == 'action':
        return cmd_action(args, explicit)
    if group == 'move':
        return cmd_move(args, explicit)
    if group == 'cast':
        return cmd_cast(args, explicit)
    if group == 'rest' and verb == 'short':
        return cmd_rest_short(args, explicit)
    if group == 'rest' and verb == 'long':
        return cmd_rest_long(args, explicit)
    if group == 'rest' and verb == 'spend-hd':
        return cmd_spend_hd(args, explicit)
    if group == 'adventure' and verb == 'validate':
        return cmd_adventure_validate(args)
    if group == 'look':
        return cmd_look(args, explicit)
    if group == 'go':
        return cmd_go(args, explicit)
    if group == 'search':
        return cmd_search(args, explicit)
    if group == 'trap':
        return cmd_trap(args, explicit)
    if group == 'time' and verb == 'advance':
        return cmd_time(args, explicit)
    if group == 'xp' and verb == 'award':
        return cmd_xp_award(args, explicit)
    if group == 'xp' and verb == 'milestone':
        return cmd_xp_milestone(args, explicit)
    if group == 'level' and verb == 'up':
        return cmd_level_up(args, explicit)
    if group == 'player' and verb == 'add':
        return cmd_player_add(args, explicit)
    if group == 'undo':
        return cmd_undo(args, explicit)
    if group == 'override':
        return cmd_override(args, explicit)
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
        with store.transaction(directory) as campaign:
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
            saved, state = campaign.append_events([event])
            event = saved[0]
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
    meta = store.read_meta(directory)
    with store.transaction(directory) as campaign:
        state = campaign.state
    store.set_active(root, name, directory)
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
    with store.transaction(directory) as campaign:
        state = campaign.state
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
    with store.transaction(directory) as campaign:
        result = campaign.verify()
        state = campaign.state
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
    with store.transaction(directory) as campaign:
        state = campaign.state
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
        saved, state = campaign.append_events([event])
        event = saved[0]
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
    with store.transaction(directory) as campaign:
        events, state = campaign.events, campaign.state
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
    adventure.validate(data)
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


_HIDDEN_KEYS = {'dm_notes', 'dm_only', 'target_hp', 'secret_dc'}


def _player_view(value, monster=False):
    """Drop DM-only fields and monster hit points from a player-facing value."""
    if isinstance(value, list):
        return [_player_view(item, monster) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get('ref')
    is_monster = monster or (
        isinstance(ref, str) and (ref.startswith('srd:') or ref.startswith('custom:'))
    )
    cleaned = {}
    for key, item in value.items():
        if key in _HIDDEN_KEYS:
            continue
        if is_monster and key == 'hp':
            continue
        if key == 'changes':
            cleaned[key] = [_player_change(change) for change in item or []]
            continue
        cleaned[key] = _player_view(item, is_monster)
    return cleaned


def _player_change(change):
    path = str(change.get('path') or '')
    monster = path == 'monsters' or path.startswith('monsters.')
    shown = dict(change)
    shown['from'] = _player_view(change.get('from'), monster)
    shown['to'] = _player_view(change.get('to'), monster)
    return shown


def _redact(event, actor):
    hidden = dict(event)
    if event.get('secret'):
        hidden['rolls'] = [
            {'counter': roll.get('counter'), 'secret': True}
            for roll in event.get('rolls') or []
        ]
    if actor != 'dm':
        hidden = _player_view(hidden)
        if event.get('type') == 'adventure' and isinstance(hidden.get('outcome'), dict):
            outcome = dict(hidden['outcome'])
            outcome.pop('dc', None)
            hidden['outcome'] = outcome
        if event.get('secret_dc') and isinstance(hidden.get('outcome'), dict):
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
        if words[0] not in (
            'campaign', 'char', 'state', 'inv', 'combat', 'encounter', 'turn',
            'condition', 'rest', 'adventure', 'trap', 'xp', 'level', 'time', 'player',
        ):
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

    encounter = sub.add_parser('encounter', add_help=False)
    encounter_sub = encounter.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    spawn_p = encounter_sub.add_parser('spawn', add_help=False)
    spawn_p.add_argument('id')
    spawn_p.add_argument('--as', dest='actor')
    spawn_p.add_argument('--roll-hp', action='store_true')
    spawn_p.add_argument('--start-combat', action='store_true')

    combat_p = sub.add_parser('combat', add_help=False)
    combat_sub = combat_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    start_p = combat_sub.add_parser('start', add_help=False)
    start_p.add_argument('--combatants', required=True)
    start_p.add_argument('--surprised', default='')
    start_p.add_argument('--as', dest='actor')
    status_p = combat_sub.add_parser('status', add_help=False)
    status_p.add_argument('--as', dest='actor')
    end_p = combat_sub.add_parser('end', add_help=False)
    end_p.add_argument('--reason', required=True)
    end_p.add_argument('--as', dest='actor')

    turn_p = sub.add_parser('turn', add_help=False)
    turn_sub = turn_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    turn_end = turn_sub.add_parser('end', add_help=False)
    turn_end.add_argument('--as', dest='actor')

    attack_p = sub.add_parser('attack', add_help=False)
    attack_p.add_argument('attacker')
    attack_p.add_argument('target')
    attack_p.add_argument('--weapon')
    attack_p.add_argument('--natural')
    attack_p.add_argument('--range', dest='reach', type=int, required=True)
    attack_p.add_argument('--as', dest='actor')
    attack_p.add_argument('--adv', action='store_true')
    attack_p.add_argument('--dis', action='store_true')
    attack_p.add_argument('--reaction', action='store_true')
    attack_p.add_argument('--offhand', action='store_true')
    attack_p.add_argument('--cover')

    damage_p = sub.add_parser('damage', add_help=False)
    damage_p.add_argument('id')
    damage_p.add_argument('expr')
    damage_p.add_argument('--type', dest='damage_type', required=True)
    damage_p.add_argument('--source')
    damage_p.add_argument('--as', dest='actor')

    heal_p = sub.add_parser('heal', add_help=False)
    heal_p.add_argument('id')
    heal_p.add_argument('expr')
    heal_p.add_argument('--source')
    heal_p.add_argument('--as', dest='actor')

    temp_p = sub.add_parser('temp-hp', add_help=False)
    temp_p.add_argument('id')
    temp_p.add_argument('amount', type=int)
    temp_p.add_argument('--as', dest='actor')

    death_p = sub.add_parser('death-save', add_help=False)
    death_p.add_argument('id')
    death_p.add_argument('--as', dest='actor')

    condition_p = sub.add_parser('condition', add_help=False)
    condition_sub = condition_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    for cond_verb in ('add', 'remove'):
        cond = condition_sub.add_parser(cond_verb, add_help=False)
        cond.add_argument('id')
        cond.add_argument('name')
        cond.add_argument('--duration')
        cond.add_argument('--level', type=int)
        cond.add_argument('--as', dest='actor')

    action_p = sub.add_parser('action', add_help=False)
    action_p.add_argument('id')
    action_p.add_argument('verb')
    action_p.add_argument('target', nargs='?')
    action_p.add_argument('--bonus', action='store_true')
    action_p.add_argument('--trigger')
    action_p.add_argument('--as', dest='actor')

    move_p = sub.add_parser('move', add_help=False)
    move_p.add_argument('id')
    move_p.add_argument('--ft', type=int, required=True)
    move_p.add_argument('--difficult', action='store_true')
    move_p.add_argument('--as', dest='actor')

    cast_p = sub.add_parser('cast', add_help=False)
    cast_p.add_argument('id')
    cast_p.add_argument('spell')
    cast_p.add_argument('--targets', default='')
    cast_p.add_argument('--slot', type=int)
    cast_p.add_argument('--range', dest='reach', type=int)
    cast_p.add_argument('--ritual', action='store_true')
    cast_p.add_argument('--reaction', action='store_true')
    cast_p.add_argument('--as', dest='actor')

    rest_p = sub.add_parser('rest', add_help=False)
    rest_sub = rest_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    short_p = rest_sub.add_parser('short', add_help=False)
    short_p.add_argument('--spend', default='')
    short_p.add_argument('--as', dest='actor')
    long_p = rest_sub.add_parser('long', add_help=False)
    long_p.add_argument('--as', dest='actor')
    dice_p = rest_sub.add_parser('spend-hd', add_help=False)
    dice_p.add_argument('id')
    dice_p.add_argument('count', type=int)
    dice_p.add_argument('--as', dest='actor')

    adventure_p = sub.add_parser('adventure', add_help=False)
    adventure_sub = adventure_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    validate_p = adventure_sub.add_parser('validate', add_help=False)
    validate_p.add_argument('file')

    look_p = sub.add_parser('look', add_help=False)
    look_p.add_argument('--as', dest='actor')

    go_p = sub.add_parser('go', add_help=False)
    go_p.add_argument('dest')
    go_p.add_argument('--travel-hours', type=int)
    go_p.add_argument('--as', dest='actor')

    search_p = sub.add_parser('search', add_help=False)
    search_p.add_argument('id')
    search_p.add_argument('--as', dest='actor')

    trap_p = sub.add_parser('trap', add_help=False)
    trap_sub = trap_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    spot_p = trap_sub.add_parser('spot', add_help=False)
    spot_p.add_argument('trap')
    spot_p.add_argument('id')
    spot_p.add_argument('--as', dest='actor')
    trigger_p = trap_sub.add_parser('trigger', add_help=False)
    trigger_p.add_argument('trap')
    trigger_p.add_argument('--target')
    trigger_p.add_argument('--as', dest='actor')
    disarm_p = trap_sub.add_parser('disarm', add_help=False)
    disarm_p.add_argument('trap')
    disarm_p.add_argument('id')
    disarm_p.add_argument('--as', dest='actor')

    time_p = sub.add_parser('time', add_help=False)
    time_sub = time_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    advance_p = time_sub.add_parser('advance', add_help=False)
    advance_p.add_argument('minutes', type=int)
    advance_p.add_argument('--as', dest='actor')

    xp_p = sub.add_parser('xp', add_help=False)
    xp_sub = xp_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    award_p = xp_sub.add_parser('award', add_help=False)
    award_p.add_argument('amount', type=int)
    award_p.add_argument('--to', default='all')
    award_p.add_argument('--as', dest='actor')
    mile_p = xp_sub.add_parser('milestone', add_help=False)
    mile_p.add_argument('level', type=int)
    mile_p.add_argument('--as', dest='actor')

    level_p = sub.add_parser('level', add_help=False)
    level_sub = level_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    up_p = level_sub.add_parser('up', add_help=False)
    up_p.add_argument('id')
    up_p.add_argument('--json-in')
    up_p.add_argument('--roll', action='store_true')
    up_p.add_argument('--as', dest='actor')

    player_p = sub.add_parser('player', add_help=False)
    player_sub = player_p.add_subparsers(dest='verb', required=True, parser_class=JsonParser)
    add_p = player_sub.add_parser('add', add_help=False)
    add_p.add_argument('player_id')
    add_p.add_argument('--controller', required=True)
    add_p.add_argument('--as', dest='actor')

    undo_p = sub.add_parser('undo', add_help=False)
    undo_p.add_argument('--reason', required=True)
    undo_p.add_argument('--as', dest='actor')

    override_p = sub.add_parser('override', add_help=False)
    override_p.add_argument('path')
    override_p.add_argument('value')
    override_p.add_argument('--reason', required=True)
    override_p.add_argument('--as', dest='actor')
    return parser


@contextmanager
def _campaign(explicit):
    directory = store.open_campaign(explicit)
    with store.transaction(directory) as campaign:
        yield campaign


def _with_campaign(explicit):
    """Read a verified snapshot under the campaign lock."""
    with _campaign(explicit) as campaign:
        return campaign.directory, campaign.state


def _commit(campaign, event, state):
    saved, new_state = _commit_many(campaign, [event], state)
    return saved[0], new_state


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
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = party.create_character(state, data)
        saved, new_state = _commit(campaign, event, state)
    return _ok('char create', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_char_assign(args, explicit):
    actor = _require_actor(args.actor)
    scores = _read_json(args.json_in)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = party.assign_scores(state, args.id, scores, actor)
        saved, new_state = _commit(campaign, event, state)
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
    with _campaign(explicit) as campaign:
        state = campaign.state
        outcome, event, warnings = party.roll_check(
            state, args.id, args.which, args.dc, actor,
            getattr(args, 'adv', False), getattr(args, 'dis', False),
            getattr(args, 'help_from', None), getattr(args, 'secret_dc', False),
            saving=saving,
        )
        saved, new_state = _commit(campaign, event, state)
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
    with _campaign(explicit) as campaign:
        state = campaign.state
        outcome, event = party.roll_contest(state, args.left, args.left_skill, args.right, args.right_skill)
        saved, new_state = _commit(campaign, event, state)
    return _ok('contest', outcome, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_group_check(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        outcome, event = party.roll_group_check(state, args.skill, args.dc)
        saved, new_state = _commit(campaign, event, state)
    return _ok('group-check', outcome, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_inv_show(args, explicit):
    actor = _require_actor(args.actor)
    _directory, state = _with_campaign(explicit)
    return _ok('inv show', party.show_inventory(state, args.id, actor), turn=store.turn_view(state))


def cmd_inv_give(args, explicit):
    _require_dm(args.actor)
    if args.qty < 1:
        raise EngineError('BAD_ARGS', '--qty must be at least 1')
    with _campaign(explicit) as campaign:
        directory, state = campaign.directory, campaign.state
        adventure = _read_adventure_doc(directory) if args.from_char and str(args.from_char).startswith('loot:') else None
        _result, event = party.give_item(state, args.item, args.qty, args.to, args.from_char, adventure)
        saved, new_state = _commit(campaign, event, state)
    return _ok('inv give', {'item': args.item, 'qty': args.qty, 'to': args.to}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_inv_transfer(args, explicit):
    actor = _require_actor(args.actor)
    if args.qty < 1:
        raise EngineError('BAD_ARGS', '--qty must be at least 1')
    with _campaign(explicit) as campaign:
        state = campaign.state
        _result, event = party.transfer_item(state, args.item, args.source, args.dest, args.qty, actor)
        saved, new_state = _commit(campaign, event, state)
    return _ok('inv transfer', {'item': args.item, 'qty': args.qty}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_equip(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        _result, event = party.equip_item(state, args.id, args.item, args.slot, actor)
        _attach_action(state, event, actor, args.id)
        saved, new_state = _commit(campaign, event, state)
    return _ok('equip', {'id': args.id, 'item': args.item}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_unequip(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        _result, event = party.unequip_item(state, args.id, args.item, actor)
        _attach_action(state, event, actor, args.id)
        saved, new_state = _commit(campaign, event, state)
    return _ok('unequip', {'id': args.id, 'item': args.item}, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_use(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        outcome, event = party.use_item(state, args.id, args.item, args.target, actor)
        _attach_action(state, event, actor, args.id)
        saved, new_state = _commit(campaign, event, state)
    return _ok('use', outcome, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def _attach_action(state, event, actor, creature_id):
    change = combat.require_combat_action(state, actor, creature_id)
    if change:
        event['changes'].append(change)


def _read_adventure_doc(directory):
    path = directory / 'adventure.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _ids(text):
    return [part.strip() for part in (text or '').split(',') if part.strip()]


def cmd_spawn(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        directory, state = campaign.directory, campaign.state
        adventure = _read_adventure_doc(directory)
        result, event = combat.spawn_encounter(state, adventure, args.id, args.roll_hp)
        events = [event]
        if args.start_combat:
            staged = store.apply_changes(campaign.state, event['changes'])
            ids_to_fight = list((staged.get('characters') or {}).keys()) + result['spawned']
            started, start_event = combat.start_combat(staged, ids_to_fight, [])
            events.append(start_event)
            result = {'spawned': result['spawned'], 'combat': started}
        saved, new_state = _commit_many(campaign, events, state)
        ids = [event['id'] for event in saved]
        rolls = [roll for event in saved for roll in event['rolls']]
    return _ok('encounter spawn', result, event_ids=ids, rolls=rolls, turn=store.turn_view(new_state))


def cmd_combat_start(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.start_combat(state, _ids(args.combatants), _ids(args.surprised))
        saved, new_state = _commit(campaign, event, state)
    return _ok('combat start', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_combat_status(args, explicit):
    actor = _require_actor(args.actor)
    _directory, state = _with_campaign(explicit)
    return _ok('combat status', combat.combat_status(state, actor), turn=store.turn_view(state))


def cmd_combat_end(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.end_combat(state, args.reason)
        saved, new_state = _commit(campaign, event, state)
    return _ok('combat end', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_turn_end(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.end_turn(state, actor)
        saved, new_state = _commit(campaign, event, state)
    return _ok('turn end', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_attack(args, explicit):
    actor = _require_actor(args.actor)
    if args.cover and args.cover not in ('half', 'three-quarters'):
        raise EngineError('BAD_ARGS', 'cover must be half or three-quarters')
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event, warnings = combat.attack(
            state, args.attacker, args.target, args.weapon, args.natural, args.reach,
            actor, args.adv, args.dis, args.reaction, args.offhand, args.cover,
        )
        saved, new_state = _commit(campaign, event, state)
    if actor != 'dm':
        result = dict(result)
        result.pop('target_hp', None)
    return _ok('attack', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state), warnings=warnings)


def cmd_damage(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.deal_damage(state, args.id, args.expr, args.damage_type, args.source)
        saved, new_state = _commit(campaign, event, state)
    return _ok('damage', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_heal(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.heal_target(state, args.id, args.expr)
        saved, new_state = _commit(campaign, event, state)
    return _ok('heal', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_temp_hp(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.set_temp_hp(state, args.id, args.amount)
        saved, new_state = _commit(campaign, event, state)
    return _ok('temp-hp', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_death_save(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.death_save(state, args.id, actor)
        saved, new_state = _commit(campaign, event, state)
    return _ok('death-save', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_condition(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.change_condition(
            state, args.id, args.name, args.verb == 'remove',
            combat.parse_duration(args.duration), args.level,
        )
        saved, new_state = _commit(campaign, event, state)
    return _ok(f'condition {args.verb}', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_action(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.take_action(
            state, args.id, args.verb, args.target, actor, args.bonus, args.trigger,
        )
        saved, new_state = _commit(campaign, event, state)
    return _ok('action', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_move(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = combat.move_creature(state, args.id, args.ft, args.difficult, actor)
        saved, new_state = _commit(campaign, event, state)
    return _ok('move', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_cast(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = spells.cast_spell(
            state, args.id, args.spell, _ids(args.targets), args.slot, args.reach,
            actor, args.ritual, args.reaction,
        )
        saved, new_state = _commit(campaign, event, state)
    return _ok('cast', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def _parse_spend(text):
    pairs = []
    for part in _ids(text):
        if ':' not in part:
            raise EngineError('BAD_ARGS', 'spend entries look like id:N', {'spend': part})
        char_id, count = part.split(':', 1)
        if not count.isdigit() or int(count) < 1:
            raise EngineError('BAD_ARGS', 'hit dice count must be a positive number', {'spend': part})
        pairs.append((char_id, int(count)))
    return pairs


def cmd_rest_short(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = spells.short_rest(state, _parse_spend(args.spend))
        saved, new_state = _commit(campaign, event, state)
    return _ok('rest short', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_rest_long(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = spells.long_rest(state)
        saved, new_state = _commit(campaign, event, state)
    return _ok('rest long', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_spend_hd(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = spells.spend_hit_dice(state, args.id, args.count, actor)
        saved, new_state = _commit(campaign, event, state)
    return _ok('rest spend-hd', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def _commit_many(campaign, events, state):
    for event in events:
        relay = party.relay_for(state, event.get('actor'))
        if relay and 'relayed_by' not in event:
            event['relayed_by'] = relay
    return campaign.append_events(events)


def cmd_adventure_validate(args):
    path = Path(args.file)
    if not path.is_file():
        raise EngineError('BAD_ARGS', 'adventure file does not exist', {'path': str(path)})
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise EngineError('BAD_ARGS', 'adventure file is not JSON') from exc
    return _ok('adventure validate', adventure.validate(data), turn=None)


def cmd_look(args, explicit):
    actor = _require_actor(args.actor)
    directory, state = _with_campaign(explicit)
    doc = _read_adventure_doc(directory)
    return _ok('look', adventure.look(state, doc, actor), turn=store.turn_view(state))


def cmd_go(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        directory, state = campaign.directory, campaign.state
        doc = _read_adventure_doc(directory)
        result, events = adventure.go(state, doc, args.dest, args.travel_hours, actor)
        saved, new_state = _commit_many(campaign, events, state)
    return _ok('go', result, event_ids=[item['id'] for item in saved], turn=store.turn_view(new_state))


def cmd_search(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        directory, state = campaign.directory, campaign.state
        doc = _read_adventure_doc(directory)
        result, events = adventure.search_location(state, doc, args.id, actor)
        saved, new_state = _commit_many(campaign, events, state)
    return _ok('search', result, event_ids=[item['id'] for item in saved], rolls=saved[-1]['rolls'], turn=store.turn_view(new_state))


def cmd_trap(args, explicit):
    actor = _require_actor(args.actor)
    with _campaign(explicit) as campaign:
        directory, state = campaign.directory, campaign.state
        doc = _read_adventure_doc(directory)
        if args.verb == 'trigger':
            _require_dm(actor)
            result, events = adventure.trap_trigger(state, doc, args.trap, args.target)
        elif args.verb == 'spot':
            result, events = adventure.trap_spot(state, doc, args.trap, args.id, actor)
        else:
            result, events = adventure.trap_disarm(state, doc, args.trap, args.id, actor)
        saved, new_state = _commit_many(campaign, events, state)
    if actor != 'dm' and isinstance(result, dict):
        result = dict(result)
        result.pop('dc', None)
    return _ok(f'trap {args.verb}', result, event_ids=[item['id'] for item in saved], rolls=saved[-1]['rolls'], turn=store.turn_view(new_state))


def cmd_time(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, events = adventure.advance_time(state, args.minutes)
        saved, new_state = _commit_many(campaign, events, state)
    return _ok('time advance', result, event_ids=[item['id'] for item in saved], turn=store.turn_view(new_state))


def cmd_xp_award(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        targets = _ids(args.to)
        result, events = adventure.award_xp(state, args.amount, targets)
        saved, new_state = _commit_many(campaign, events, state)
    return _ok('xp award', result, event_ids=[item['id'] for item in saved], turn=store.turn_view(new_state))


def cmd_xp_milestone(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        directory, state = campaign.directory, campaign.state
        doc = _read_adventure_doc(directory)
        result, events = adventure.milestone(state, args.level, doc)
        saved, new_state = _commit_many(campaign, events, state)
    return _ok('xp milestone', result, event_ids=[item['id'] for item in saved], turn=store.turn_view(new_state))


def cmd_level_up(args, explicit):
    actor = _require_actor(args.actor)
    choices = _read_json(args.json_in) if args.json_in else {}
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = levelup.level_up(state, args.id, choices, args.roll, actor)
        saved, new_state = _commit(campaign, event, state)
    return _ok('level up', result, event_ids=[saved['id']], rolls=saved['rolls'], turn=store.turn_view(new_state))


def cmd_player_add(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = history.add_seat(state, args.player_id, args.controller)
        saved, new_state = _commit(campaign, event, state)
    return _ok('player add', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_undo(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        events = campaign.events
        result, event = history.undo(events, args.reason)
        saved, new_state = _commit(campaign, event, state)
    return _ok('undo', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


def cmd_override(args, explicit):
    _require_dm(args.actor)
    with _campaign(explicit) as campaign:
        state = campaign.state
        result, event = history.override(state, args.path, args.value, args.reason)
        saved, new_state = _commit(campaign, event, state)
    return _ok('override', result, event_ids=[saved['id']], turn=store.turn_view(new_state))


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
