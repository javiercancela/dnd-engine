"""Character commands: create, checks, inventory."""

import copy

from dnd.chargen import (
    ABILITIES,
    ability_mod,
    advantage_state,
    auto_fail_save,
    carry_weight,
    compile_character,
    condition_names,
    public_view,
    refresh_gear,
    rule,
    same_multiset,
    save_bonus,
    skill_bonus,
    status_of,
)
from dnd.dice import roll_expr
from dnd.errors import EngineError
from dnd import srd

COIN_KINDS = ('cp', 'sp', 'ep', 'gp', 'pp')


def create_character(state, data):
    """Return ``(result, event)``. Rolled characters stay pending until assign-scores."""
    if not isinstance(data, dict):
        rule('character document must be a JSON object')
    char_id = data.get('id')
    if char_id in (state.get('characters') or {}):
        rule('character already exists', id=char_id)
    method = (data.get('abilities') or {}).get('method')
    seed = state['rng']['seed']
    counter = state['rng']['counter']
    rolls = []
    if method == 'rolled':
        scores, rolls = _roll_pool(seed, counter)
        pending = {
            'id': char_id,
            'name': data.get('name'),
            'player': data.get('player'),
            'pending_scores': scores,
            'build': data,
        }
        changes = [
            {'op': 'set', 'path': f'characters.{char_id}', 'from': None, 'to': pending},
            {'op': 'set', 'path': 'rng.counter', 'from': counter, 'to': counter + 6},
        ]
        result = {'id': char_id, 'pending': True, 'scores': scores}
    else:
        base = (data.get('abilities') or {})
        character = compile_character(data, {ability: base.get(ability) for ability in ABILITIES})
        changes = [
            {'op': 'set', 'path': f'characters.{char_id}', 'from': None, 'to': character},
        ]
        changes.extend(_seat_changes(state, character['player']))
        result = _created_result(character)
    event = _event(
        'char-create',
        'dm',
        f'char create {char_id} --as dm',
        rolls,
        changes,
    )
    return result, event


def assign_scores(state, char_id, scores, actor):
    character = _character(state, char_id)
    _owner_or_dm(actor, character)
    pool = character.get('pending_scores')
    if not pool:
        rule('character does not have unassigned scores', id=char_id)
    if not isinstance(scores, dict):
        rule('scores must be a JSON object')
    assigned = {}
    for ability in ABILITIES:
        value = scores.get(ability)
        if not isinstance(value, int):
            rule(f'{ability} score is missing')
        assigned[ability] = value
    if not same_multiset(assigned.values(), pool):
        rule('assigned scores do not match the rolled pool', scores=assigned, pool=pool)
    compiled = compile_character(character['build'], assigned)
    changes = [
        {'op': 'set', 'path': f'characters.{char_id}', 'from': character, 'to': compiled},
    ]
    changes.extend(_seat_changes(state, compiled['player']))
    event = _event(
        'assign-scores',
        actor,
        f'char assign-scores {char_id} --as {actor}',
        [],
        changes,
    )
    return _created_result(compiled), event


def show_character(state, char_id, actor):
    character = _character(state, char_id)
    if actor == 'dm' or actor == char_id:
        sheet = dict(character)
        sheet['status'] = status_of(character)
        return sheet
    return public_view(character)


def list_characters(state):
    party = []
    for char_id in sorted((state.get('characters') or {})):
        character = state['characters'][char_id]
        if character.get('pending_scores') is not None:
            party.append({
                'id': char_id,
                'name': character.get('name'),
                'player': character.get('player'),
                'status': 'pending',
            })
            continue
        view = public_view(character)
        view['player'] = character.get('player')
        party.append(view)
    return {'party': party, 'seats': state.get('seats') or {}}


def state_view(state, actor, section):
    section = section or 'all'
    if section not in ('party', 'location', 'combat', 'all'):
        raise EngineError('BAD_ARGS', 'unknown state section', {'section': section})
    party = []
    for char_id in sorted((state.get('characters') or {})):
        character = state['characters'][char_id]
        if actor == 'dm' or actor == char_id:
            shown = dict(character)
            shown['status'] = status_of(character)
        else:
            shown = public_view(character)
        party.append(shown)
    location = {'location': state.get('location'), 'chapter': state.get('chapter')}
    combat = state.get('combat') or {'active': False}
    if section == 'party':
        return {'party': party}
    if section == 'location':
        return location
    monsters = []
    for monster_id in sorted((state.get('monsters') or {})):
        monster = state['monsters'][monster_id]
        view = {
            'id': monster_id,
            'name': monster.get('name'),
            'status': status_of(monster),
        }
        view['conditions'] = condition_names(monster)
        if actor == 'dm':
            view['hp'] = monster.get('hp')
        monsters.append(view)
    if section == 'combat':
        return {'combat': combat, 'monsters': monsters}
    return {
        'party': party,
        'location': location['location'],
        'chapter': location['chapter'],
        'time': state.get('time'),
        'combat': combat,
        'monsters': monsters,
    }


def roll_check(state, char_id, which, dc, actor, flag_adv, flag_dis, help_from, secret_dc, saving=False):
    character = _ready(state, char_id)
    _owner_or_dm(actor, character)
    if secret_dc and actor != 'dm':
        raise EngineError('NOT_AUTHORIZED', 'only the DM can hide a DC', {'actor': actor})
    if help_from:
        if help_from == char_id:
            raise EngineError('BAD_ARGS', '--help-from must be someone else')
        helper = _ready(state, help_from)
        if status_of(helper) in ('dying', 'dead'):
            rule('helper cannot help', id=help_from)
    if saving:
        if which not in ABILITIES:
            raise EngineError('UNKNOWN_ID', f'unknown save {which}', {'save': which})
        roll_kind = 'save'
    else:
        roll_kind = 'check'
        _check_kind(which)
    if saving and auto_fail_save(character, which):
        outcome = {'total': None, 'dc': dc, 'success': False, 'natural': None, 'auto': 'fail'}
        rolls = []
        changes = []
        warnings = []
    else:
        mode, sources, warnings = advantage_state(
            character, roll_kind, which, flag_adv, flag_dis, help_from,
        )
        counter = state['rng']['counter']
        rolled = roll_expr('1d20', state['rng']['seed'], counter, mode)
        natural = rolled['kept'][0]
        bonus = _bonus_for(character, which) if not saving else save_bonus(character, which)
        total = natural + bonus
        rolls = [_roll_record(which, counter, rolled, mode, sources, bonus, total, '1d20')]
        counter += 1
        if saving and character.get('blessed'):
            bless_roll = roll_expr('1d4', state['rng']['seed'], counter, 'none')
            total += bless_roll['kept'][0]
            rolls.append(_roll_record(which, counter, bless_roll, 'none', ['bless'], 0, bless_roll['kept'][0], '1d4'))
            counter += 1
        outcome = {'total': total, 'dc': dc, 'success': total >= dc, 'natural': natural}
        changes = [_counter_change(state['rng']['counter'], counter)]
    cmd = _check_cmd(roll_kind, char_id, which, dc, actor, flag_adv, flag_dis, help_from, secret_dc)
    event = _event(roll_kind if roll_kind == 'save' else 'check', actor, cmd, rolls, changes)
    event['outcome'] = outcome
    if secret_dc:
        event['secret_dc'] = True
    return outcome, event, warnings


def roll_contest(state, left_id, left_skill, right_id, right_skill):
    left = _ready(state, left_id)
    right = _ready(state, right_id)
    _check_kind_skill(left_skill)
    _check_kind_skill(right_skill)
    counter = state['rng']['counter']
    rolls = []
    totals = {}
    for offset, (character, skill) in enumerate(((left, left_skill), (right, right_skill))):
        mode, sources, _warnings = advantage_state(character, 'check', skill, False, False, None)
        rolled = roll_expr('1d20', state['rng']['seed'], counter + offset, mode)
        bonus = _bonus_for(character, skill)
        total = rolled['kept'][0] + bonus
        totals[character['id']] = {
            'total': total,
            'natural': rolled['kept'][0],
            'skill': skill,
        }
        rolls.append(_roll_record(skill, counter + offset, rolled, mode, sources, bonus, total, '1d20'))
    winner = None
    if totals[left_id]['total'] > totals[right_id]['total']:
        winner = left_id
    elif totals[right_id]['total'] > totals[left_id]['total']:
        winner = right_id
    outcome = {'a': totals[left_id], 'b': totals[right_id], 'winner': winner}
    event = _event(
        'contest',
        'dm',
        f'contest {left_id} {left_skill} {right_id} {right_skill} --as dm',
        rolls,
        [_counter_change(counter, counter + 2)],
    )
    event['outcome'] = outcome
    return outcome, event


def roll_group_check(state, skill, dc):
    _check_kind_skill(skill)
    members = [
        character for _char_id, character in sorted((state.get('characters') or {}).items())
        if character.get('pending_scores') is None
    ]
    if not members:
        rule('the party is empty')
    counter = state['rng']['counter']
    rolls = []
    results = []
    successes = 0
    for offset, character in enumerate(members):
        mode, sources, _warnings = advantage_state(character, 'check', skill, False, False, None)
        rolled = roll_expr('1d20', state['rng']['seed'], counter + offset, mode)
        bonus = _bonus_for(character, skill)
        total = rolled['kept'][0] + bonus
        success = total >= dc
        successes += int(success)
        results.append({
            'id': character['id'],
            'total': total,
            'natural': rolled['kept'][0],
            'success': success,
        })
        rolls.append(_roll_record(skill, counter + offset, rolled, mode, sources, bonus, total, '1d20'))
    outcome = {
        'dc': dc,
        'successes': successes,
        'party': len(members),
        'success': successes * 2 >= len(members),
        'rolls': results,
    }
    event = _event(
        'group-check',
        'dm',
        f'group-check {skill} --dc {dc} --as dm',
        rolls,
        [_counter_change(counter, counter + len(members))],
    )
    event['outcome'] = outcome
    return outcome, event


def show_inventory(state, char_id, actor):
    character = _character(state, char_id)
    _owner_or_dm(actor, character)
    if character.get('pending_scores') is not None:
        rule('scores are not assigned', id=char_id)
    attuned = sum(1 for entry in character['inventory'] if entry.get('attuned'))
    return {
        'items': character['inventory'],
        'coins': character['coins'],
        'weight': carry_weight(character),
        'capacity': character['abilities']['str'] * 15,
        'attunement': {'used': attuned, 'max': 3},
    }


def give_item(state, item, qty, target_id, source, adventure=None):
    target = _ready(state, target_id)
    if source and source.startswith('loot:'):
        return _give_loot(state, item, qty, target, source.split(':', 1)[1], adventure)
    if item in COIN_KINDS:
        return _give_coins(state, target, item, qty, source)
    _known_item(item)
    updated = _copy_character(target)
    if source and source.startswith('loot:'):
        pass
    elif source:
        donor = _ready(state, source)
        if donor['id'] == target['id']:
            raise EngineError('BAD_ARGS', '--from and --to are the same character')
        donor_copy = _copy_character(donor)
        _take_item(donor_copy, item, qty)
        _add_item(updated, item, qty)
        return _item_event(
            'inv-give',
            f'inv give {item} --to {target_id} --qty {qty} --from {source} --as dm',
            [(donor['id'], donor, donor_copy), (target_id, target, updated)],
            'dm',
        )
    _add_item(updated, item, qty)
    return _item_event(
        'inv-give',
        f'inv give {item} --to {target_id} --qty {qty} --as dm',
        [(target_id, target, updated)],
        'dm',
    )


def transfer_item(state, item, source_id, target_id, qty, actor):
    if actor != source_id:
        raise EngineError('NOT_AUTHORIZED', 'only the owner can hand over an item', {'actor': actor})
    if source_id == target_id:
        raise EngineError('BAD_ARGS', 'transfer needs two characters')
    source = _ready(state, source_id)
    target = _ready(state, target_id)
    source_copy = _copy_character(source)
    target_copy = _copy_character(target)
    if item in COIN_KINDS:
        _take_coins(source_copy, item, qty)
        target_copy['coins'][item] = target_copy['coins'].get(item, 0) + qty
    else:
        _known_item(item)
        _take_item(source_copy, item, qty)
        _add_item(target_copy, item, qty)
    refresh_gear(source_copy)
    refresh_gear(target_copy)
    return _item_event(
        'inv-transfer',
        f'inv transfer {item} {source_id} {target_id} --qty {qty} --as {actor}',
        [(source_id, source, source_copy), (target_id, target, target_copy)],
        actor,
    )


def equip_item(state, char_id, item, slot, actor):
    character = _ready(state, char_id)
    _owner_or_dm(actor, character)
    updated = _copy_character(character)
    entry = _find_item(updated, item)
    if entry is None:
        raise EngineError('UNKNOWN_ID', f'{item} is not in the inventory', {'item': item})
    record = _equipment_record(item)
    slot = slot or _infer_slot(record)
    if slot not in ('main', 'off', 'armor', 'shield'):
        raise EngineError('BAD_ARGS', 'slot must be main, off, armor, or shield', {'slot': slot})
    _clear_slot(updated, slot)
    props = [prop['index'] for prop in record.get('properties') or []]
    if slot == 'main' and 'two-handed' in props:
        _clear_slot(updated, 'off')
        _clear_slot(updated, 'shield')
    if slot in ('off', 'shield'):
        main = _find_equipped(updated, 'main')
        if main:
            main_record = _equipment_record(main['item'])
            main_props = [prop['index'] for prop in main_record.get('properties') or []]
            if 'two-handed' in main_props:
                main['equipped'] = None
    entry['equipped'] = slot
    refresh_gear(updated)
    return _item_event(
        'equip',
        f'equip {char_id} {item} --slot {slot} --as {actor}',
        [(char_id, character, updated)],
        actor,
    )


def unequip_item(state, char_id, item, actor):
    character = _ready(state, char_id)
    _owner_or_dm(actor, character)
    updated = _copy_character(character)
    entry = _find_item(updated, item)
    if entry is None or not entry.get('equipped'):
        raise EngineError('UNKNOWN_ID', f'{item} is not equipped', {'item': item})
    entry['equipped'] = None
    refresh_gear(updated)
    return _item_event(
        'unequip',
        f'unequip {char_id} {item} --as {actor}',
        [(char_id, character, updated)],
        actor,
    )


def use_item(state, char_id, item, target_id, actor):
    character = _ready(state, char_id)
    _owner_or_dm(actor, character)
    target_id = target_id or char_id
    target = _ready(state, target_id)
    updated_user = _copy_character(character)
    _take_item(updated_user, item, 1)
    counter = state['rng']['counter']
    rolls = []
    outcome = {'item': item, 'target': target_id}
    changes_pairs = []
    if item == 'potion-of-healing':
        rolled = roll_expr('2d4+2', state['rng']['seed'], counter, 'none')
        amount = rolled['total']
        if target_id == char_id:
            healed = updated_user
            before = character
        else:
            healed = _copy_character(target)
            before = target
        _heal(healed, amount)
        outcome.update({'effect': 'heal', 'amount': amount, 'hp': healed['hp']['current']})
        rolls.append(_roll_record(
            'potion-of-healing', counter, rolled, 'none', [], rolled['mod'], rolled['total'], '2d4+2',
        ))
        changes = [_counter_change(counter, counter + 1)]
        if target_id == char_id:
            changes_pairs.append((char_id, character, healed))
        else:
            changes_pairs.append((char_id, character, updated_user))
            changes_pairs.append((target_id, before, healed))
    else:
        _known_consumable(item)
        outcome['effect'] = 'manual'
        changes = []
        changes_pairs.append((char_id, character, updated_user))
    event = _event(
        'use',
        actor,
        f'use {char_id} {item} --as {actor}',
        rolls,
        changes + [
            {'op': 'set', 'path': f'characters.{pair[0]}', 'from': pair[1], 'to': pair[2]}
            for pair in changes_pairs
        ],
    )
    event['outcome'] = outcome
    return outcome, event


def _heal(character, amount):
    current = character['hp']['current']
    if current < 0:
        current = 0
    character['hp']['current'] = min(character['hp']['max'], current + amount)
    if character['hp']['current'] > 0:
        character['death_saves'] = {'s': 0, 'f': 0}


def _give_coins(state, target, kind, qty, source):
    updated = _copy_character(target)
    pairs = []
    if source and not source.startswith('loot:'):
        donor = _ready(state, source)
        donor_copy = _copy_character(donor)
        _take_coins(donor_copy, kind, qty)
        pairs.append((donor['id'], donor, donor_copy))
    updated['coins'][kind] = updated['coins'].get(kind, 0) + qty
    pairs.append((target['id'], target, updated))
    return _item_event(
        'inv-give',
        f'inv give {kind} --to {target["id"]} --qty {qty} --as dm',
        pairs,
        'dm',
    )


def _give_loot(state, item, qty, target, treasure_id, adventure):
    if adventure is None:
        rule('that loot is not in this adventure', treasure=treasure_id)
    treasure = next((row for row in adventure.get('treasure') or [] if row.get('id') == treasure_id), None)
    if treasure is None:
        raise EngineError('UNKNOWN_ID', f'unknown treasure {treasure_id}', {'treasure': treasure_id})
    progress = copy.deepcopy(state.get('adventure') or {})
    taken = progress.setdefault('loot', {}).setdefault(treasure_id, {'coins': {}, 'items': {}})
    updated = _copy_character(target)
    if item in COIN_KINDS:
        have = (treasure.get('coins') or {}).get(item, 0) - taken['coins'].get(item, 0)
        if qty > have:
            rule('that treasure does not have enough coin', coin=item, have=have)
        taken['coins'][item] = taken['coins'].get(item, 0) + qty
        updated['coins'][item] = updated['coins'].get(item, 0) + qty
    else:
        _known_item(item)
        have = 0
        for entry in treasure.get('items') or []:
            ref = entry.get('ref') or ''
            slug = ref.split(':', 1)[1] if ref.startswith('srd:') else ref
            if slug == item:
                have += entry.get('qty', 1)
        have -= taken['items'].get(item, 0)
        if qty > have:
            rule('that treasure does not have enough of that item', item=item, have=have)
        taken['items'][item] = taken['items'].get(item, 0) + qty
        _add_item(updated, item, qty)
    changes = [
        {'op': 'set', 'path': f'characters.{target["id"]}', 'from': target, 'to': updated},
        {'op': 'set', 'path': 'adventure', 'from': state.get('adventure'), 'to': progress},
    ]
    event = _event(
        'inv-give',
        'dm',
        f'inv give {item} --to {target["id"]} --qty {qty} --from loot:{treasure_id} --as dm',
        [],
        changes,
    )
    return {'item': item, 'qty': qty, 'treasure': treasure_id}, event


def _item_event(event_type, cmd, pairs, actor):
    changes = [
        {'op': 'set', 'path': f'characters.{char_id}', 'from': old, 'to': new}
        for char_id, old, new in pairs
    ]
    return {}, _event(event_type, actor, cmd, [], changes)


def _created_result(character):
    result = {
        'id': character['id'],
        'pending': False,
        'hp': character['hp'],
        'ac': character['ac'],
        'passive_perception': character['passive_perception'],
    }
    if character.get('spellcasting'):
        result['spell_dc'] = character['spellcasting']['dc']
        result['spell_attack'] = character['spellcasting']['attack']
    return result


def _roll_pool(seed, counter):
    scores = []
    rolls = []
    for offset in range(6):
        rolled = roll_expr('4d6kh3', seed, counter + offset, 'none')
        scores.append(sum(rolled['kept']))
        rolls.append(_roll_record(
            '4d6kh3', counter + offset, rolled, 'none', [], 0, rolled['total'], '4d6kh3',
        ))
    return scores, rolls


def _roll_record(label, counter, rolled, mode, sources, bonus, total, expr):
    return {
        'adv': mode,
        'adv_sources': sources,
        'counter': counter,
        'dice': rolled['dice'],
        'expr': expr,
        'kept': rolled['kept'],
        'label': label,
        'mod': bonus,
        'total': total,
    }


def _check_cmd(kind, char_id, which, dc, actor, flag_adv, flag_dis, help_from, secret_dc):
    verb = 'save' if kind == 'save' else 'check'
    parts = [verb, char_id, which, '--dc', str(dc), '--as', actor]
    if flag_adv:
        parts.append('--adv')
    if flag_dis:
        parts.append('--dis')
    if help_from:
        parts.extend(['--help-from', help_from])
    if secret_dc:
        parts.append('--secret-dc')
    return ' '.join(parts)


def _bonus_for(character, which):
    if which in ABILITIES:
        return ability_mod(character['abilities'][which])
    return skill_bonus(character, which)


def _check_kind(which):
    if which in ABILITIES:
        return 'ability'
    try:
        srd.get('skill', which)
    except KeyError:
        raise EngineError('UNKNOWN_ID', f'unknown check {which}', {'check': which}) from None
    return 'check'


def _check_kind_skill(which):
    if which in ABILITIES:
        return 'ability'
    try:
        srd.get('skill', which)
    except KeyError:
        raise EngineError('UNKNOWN_ID', f'unknown skill {which}', {'skill': which}) from None
    return 'check'


def _character(state, char_id):
    character = (state.get('characters') or {}).get(char_id)
    if character is None:
        raise EngineError('UNKNOWN_ID', f'unknown character {char_id}', {'id': char_id})
    return character


def _ready(state, char_id):
    character = _character(state, char_id)
    if character.get('pending_scores') is not None:
        rule('scores are not assigned', id=char_id)
    return character


def _owner_or_dm(actor, character):
    if actor == 'dm' or actor == character['id']:
        return
    raise EngineError('NOT_AUTHORIZED', 'that is not your character', {'actor': actor})


def _seat_changes(state, player):
    seats = state.get('seats') or {}
    if player in seats:
        return []
    return [{'op': 'set', 'path': f'seats.{player}', 'from': None, 'to': {'controller': player}}]


def _counter_change(old, new):
    return {'op': 'set', 'path': 'rng.counter', 'from': old, 'to': new}


def _event(event_type, actor, cmd, rolls, changes):
    return {
        'actor': actor,
        'changes': changes,
        'cmd': cmd,
        'rolls': rolls,
        'type': event_type,
    }


def _copy_character(character):
    import copy
    return copy.deepcopy(character)


def _find_item(character, item):
    for entry in character.get('inventory') or []:
        if entry['item'] == item and entry.get('qty', 0) > 0:
            return entry
    return None


def _find_equipped(character, slot):
    for entry in character.get('inventory') or []:
        if entry.get('equipped') == slot:
            return entry
    return None


def _clear_slot(character, slot):
    for entry in character.get('inventory') or []:
        if entry.get('equipped') == slot:
            entry['equipped'] = None


def _add_item(character, item, qty):
    found = _find_item(character, item)
    if found:
        found['qty'] += qty
    else:
        character['inventory'].append({'item': item, 'qty': qty, 'equipped': None})


def _take_item(character, item, qty):
    found = _find_item(character, item)
    if found is None or found.get('qty', 0) < qty:
        rule('not enough of that item', item=item)
    found['qty'] -= qty
    if found['qty'] == 0:
        found['equipped'] = None
        character['inventory'] = [entry for entry in character['inventory'] if entry['qty'] > 0]
    refresh_gear(character)


def _take_coins(character, kind, qty):
    if character['coins'].get(kind, 0) < qty:
        rule('not enough coins', coin=kind)
    character['coins'][kind] -= qty


def _known_item(item):
    try:
        srd.get('equipment', item)
        return
    except KeyError:
        pass
    try:
        srd.get('magic-item', item)
        return
    except KeyError:
        raise EngineError('UNKNOWN_ID', f'unknown item {item}', {'item': item}) from None


def _known_consumable(item):
    _known_item(item)
    if 'potion' in item or 'scroll' in item or item.startswith('oil'):
        return
    rule('that item is not a consumable', item=item)


def _equipment_record(item):
    try:
        return srd.get('equipment', item)
    except KeyError:
        raise EngineError('UNKNOWN_ID', f'{item} is not equipment', {'item': item}) from None


def _infer_slot(record):
    category = record.get('armor_category')
    if category == 'Shield':
        return 'shield'
    if category in ('Light', 'Medium', 'Heavy'):
        return 'armor'
    if record.get('damage'):
        return 'main'
    raise EngineError('BAD_ARGS', 'pass --slot for that item', {'item': record.get('index')})


def relay_for(state, actor):
    if not actor or actor == 'dm':
        return None
    character = (state.get('characters') or {}).get(actor)
    if character and character.get('player') in ('javi', 'dm'):
        return 'dm'
    return None
