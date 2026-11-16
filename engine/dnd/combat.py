"""Turn order, attacks, and hit points."""

import copy
import re

from dnd.chargen import ability_mod, condition_names, rule, status_of
from dnd.dice import roll_expr
from dnd.errors import EngineError
from dnd import srd

XP_BY_CR = {
    0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700, 4: 1100,
    5: 1800, 6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900, 11: 7200,
    12: 8400, 13: 10000, 14: 11500, 15: 13000, 16: 15000, 17: 18000,
    18: 20000, 19: 22000, 20: 25000, 21: 33000, 22: 41000, 23: 50000,
    24: 62000, 25: 75000, 30: 155000,
}
INCAPACITATING = ('incapacitated', 'paralyzed', 'petrified', 'stunned', 'unconscious')
CONDITIONS = (
    'blinded', 'charmed', 'deafened', 'exhaustion', 'frightened', 'grappled',
    'incapacitated', 'invisible', 'paralyzed', 'petrified', 'poisoned', 'prone',
    'restrained', 'stunned', 'unconscious',
)
COVER_AC = {'half': 2, 'three-quarters': 5}


def spawn_encounter(state, adventure, encounter_id, roll_hp):
    encounter = next(
        (row for row in adventure.get('encounters') or [] if row.get('id') == encounter_id),
        None,
    )
    if encounter is None:
        raise EngineError('UNKNOWN_ID', f'unknown encounter {encounter_id}', {'id': encounter_id})
    monsters = dict(state.get('monsters') or {})
    spawned = []
    counter = state['rng']['counter']
    rolls = []
    seed = state['rng']['seed']
    for group in encounter.get('monsters') or []:
        block = _monster_block(adventure, group['ref'])
        count = group.get('count', 1)
        for _ in range(count):
            monster_id = _next_id(monsters, _slug_name(block.get('name') or group['ref']))
            monster, counter, made = _make_monster(
                monster_id, group['ref'], block, encounter_id, roll_hp, seed, counter,
            )
            monsters[monster_id] = monster
            spawned.append(monster_id)
            rolls.extend(made)
    new_state = _copy(state)
    new_state['monsters'] = monsters
    new_state['rng']['counter'] = counter
    changes = [{'op': 'set', 'path': 'monsters', 'from': state.get('monsters') or {}, 'to': monsters}]
    if counter != state['rng']['counter']:
        changes.append(_counter(state['rng']['counter'], counter))
    event = _event('encounter-spawn', 'dm', f'encounter spawn {encounter_id} --as dm', rolls, changes)
    event['outcome'] = {'spawned': spawned}
    return {'spawned': spawned}, event


def start_combat(state, ids, surprised):
    if (state.get('combat') or {}).get('active'):
        rule('combat is already active')
    surprised = list(surprised or [])
    rows = []
    counter = state['rng']['counter']
    seed = state['rng']['seed']
    rolls = []
    for creature_id in ids:
        creature = _require_creature(state, creature_id)
        if _is_dead(creature):
            raise EngineError('INVALID_TARGET', f'{creature_id} is dead', {'id': creature_id})
        rolled = roll_expr('1d20', seed, counter, 'none')
        dex = _dex_score(creature)
        total = rolled['kept'][0] + ability_mod(dex)
        rows.append({'id': creature_id, 'init': total, 'dex': dex, 'roll': rolled['kept'][0]})
        rolls.append(_d20_record(creature_id, counter, rolled, ability_mod(dex), total))
        counter += 1
    ordered, counter, extra = _break_ties(rows, seed, counter)
    rolls.extend(extra)
    combat = {
        'active': True,
        'round': 1,
        'order': [{'id': row['id'], 'init': row['init']} for row in ordered],
        'current_index': 0,
        'economy': {},
        'surprised': surprised,
        'surprised_done': [],
        'help': {},
        'participants': list(ids),
    }
    new_state = _copy(state)
    new_state['combat'] = combat
    new_state['rng']['counter'] = counter
    _begin_turn(new_state, ordered[0]['id'])
    changes = [
        {'op': 'set', 'path': 'combat', 'from': state.get('combat'), 'to': new_state['combat']},
        _counter(state['rng']['counter'], counter),
    ]
    event = _event(
        'combat-start', 'dm',
        'combat start --combatants ' + ','.join(ids) + ' --as dm',
        rolls, changes,
    )
    result = {'order': new_state['combat']['order'], 'current': ordered[0]['id']}
    event['outcome'] = result
    return result, event


def combat_status(state, actor):
    combat = state.get('combat') or {}
    if not combat.get('active'):
        return {'active': False, 'combatants': []}
    current = _current_id(state)
    combatants = []
    for row in combat.get('order') or []:
        creature = _require_creature(state, row['id'])
        view = {'id': row['id'], 'init': row['init'], 'status': _health_word(creature)}
        if actor == 'dm' or actor == row['id']:
            view['hp'] = copy.deepcopy(creature.get('hp'))
        combatants.append(view)
    return {
        'active': True,
        'round': combat.get('round'),
        'current': current,
        'order': combat.get('order'),
        'combatants': combatants,
    }


def end_turn(state, actor):
    _require_active(state)
    current = _current_id(state)
    _assert_turn_owner(state, actor, current)
    new_state = _copy(state)
    rolls = _tick_creature(new_state, current)
    combat = new_state['combat']
    surprised = combat.get('surprised') or []
    finished = combat.get('surprised_done') or []
    if current in surprised and current not in finished:
        combat.setdefault('surprised_done', []).append(current)
    nxt, wrapped = _advance_index(new_state)
    if wrapped:
        _tick_rounds(new_state)
    _begin_turn(new_state, nxt)
    changes = [{'op': 'set', 'path': 'combat', 'from': state.get('combat'), 'to': new_state['combat']}]
    if new_state['rng']['counter'] != state['rng']['counter']:
        changes.append(_counter(state['rng']['counter'], new_state['rng']['counter']))
    for creature_id in _touched(state, new_state):
        changes.append(_creature_change(state, new_state, creature_id))
    event = _event('turn-end', actor, f'turn end --as {actor}', rolls, changes)
    result = {'next': nxt, 'round': new_state['combat']['round']}
    event['outcome'] = result
    return result, event


def end_combat(state, reason):
    _require_active(state)
    if reason not in ('fled', 'victory', 'parley'):
        raise EngineError('BAD_ARGS', 'reason must be fled, victory, or parley', {'reason': reason})
    new_state = _copy(state)
    awarded = []
    if reason == 'victory':
        total = 0
        for creature_id in new_state['combat'].get('participants') or []:
            monster = (new_state.get('monsters') or {}).get(creature_id)
            if monster and monster.get('dead'):
                total += monster.get('xp') or 0
        living = [
            creature_id for creature_id in new_state['combat'].get('participants') or []
            if creature_id in (new_state.get('characters') or {})
            and not (new_state['characters'][creature_id].get('dead'))
        ]
        share = total // len(living) if living else 0
        for creature_id in living:
            character = new_state['characters'][creature_id]
            character['xp'] = character.get('xp', 0) + share
            awarded.append({'id': creature_id, 'xp': share})
    rounds = new_state['combat'].get('round')
    new_state['combat'] = {
        'active': False, 'current_index': None, 'economy': {}, 'order': [], 'round': None,
    }
    changes = [{'op': 'set', 'path': 'combat', 'from': state.get('combat'), 'to': new_state['combat']}]
    for row in awarded:
        changes.append(_creature_change(state, new_state, row['id']))
    event = _event('combat-end', 'dm', f'combat end --reason {reason} --as dm', [], changes)
    result = {'reason': reason, 'rounds': rounds, 'xp': awarded, 'winner': 'party' if reason == 'victory' else None}
    event['outcome'] = result
    return result, event


def attack(state, attacker_id, target_id, weapon, natural, declared_range, actor, flag_adv, flag_dis, reaction, offhand, cover):
    _require_active(state)
    attacker = _require_creature(state, attacker_id)
    _assert_controls(state, actor, attacker_id)
    if not reaction:
        _assert_turn_owner(state, actor, attacker_id)
    if _incapacitated(attacker):
        raise EngineError('INCAPACITATED', f'{attacker_id} cannot act', {'id': attacker_id})
    kind = 'reaction' if reaction else 'bonus' if offhand else 'action'
    _assert_economy(state, attacker_id, kind)
    target = _require_creature(state, target_id)
    if attacker_id == target_id or _is_dead(target):
        raise EngineError('INVALID_TARGET', f'{target_id} is not a valid target', {'id': target_id})
    attack_row, item = _find_attack(attacker, weapon, natural)
    normal, long, ranged = _reach(attack_row, item)
    if declared_range > long:
        raise EngineError('OUT_OF_RANGE', f'{declared_range} ft is out of range', {'range': declared_range, 'long': long})
    new_state = _copy(state)
    attacker = _require_creature(new_state, attacker_id)
    target = _require_creature(new_state, target_id)
    attack_row, item = _find_attack(attacker, weapon, natural)
    adv, dis, warnings = _attack_advantage(
        new_state, attacker, target, attack_row, declared_range, normal, ranged, flag_adv, flag_dis,
    )
    if adv and dis:
        mode = 'none'
        warnings.append('advantage and disadvantage cancel')
    elif adv:
        mode = 'adv'
    elif dis:
        mode = 'dis'
    else:
        mode = 'none'
    counter = new_state['rng']['counter']
    seed = new_state['rng']['seed']
    rolled = roll_expr('1d20', seed, counter, mode)
    counter += 1
    natural_roll = rolled['kept'][0]
    bonus = attack_row['bonus']
    total = natural_roll + bonus
    ac = _ac_of(target) + COVER_AC.get(cover or '', 0)
    crit = natural_roll == 20
    hit = natural_roll != 1 and (crit or total >= ac)
    if hit and declared_range <= 5 and set(condition_names(target)).intersection(('paralyzed', 'unconscious')):
        crit = True
    damages = []
    roll_records = [_d20_record(attacker_id, counter - 1, rolled, bonus, total)]
    if hit:
        for part in attack_row.get('parts') or [{'dice': attack_row['damage'], 'type': attack_row['damage_type']}]:
            expr = _crit_expr(part['dice']) if crit else part['dice']
            damage_roll = roll_expr(expr, seed, counter, 'none')
            counter += 1
            amount = _mitigate(target, part['type'], max(0, damage_roll['total']))
            damages.append({'type': part['type'], 'amount': amount})
            roll_records.append({
                'adv': 'none', 'adv_sources': [], 'counter': counter - 1,
                'dice': damage_roll['dice'], 'expr': expr, 'kept': damage_roll['kept'],
                'label': part['type'], 'mod': damage_roll['mod'], 'total': amount,
            })
        _apply_damage_parts(new_state, target, damages, crit)
        attacker['hidden'] = False
        help_map = new_state['combat'].setdefault('help', {})
        help_map.pop(target_id, None)
    _spend(new_state, attacker_id, kind)
    new_state['rng']['counter'] = counter
    result = {
        'hit': hit,
        'crit': bool(hit and crit),
        'attack_roll': total,
        'ac': ac,
        'damage': damages,
        'target_status': _health_word(target),
    }
    changes = [
        {'op': 'set', 'path': 'combat', 'from': state.get('combat'), 'to': new_state['combat']},
        _counter(state['rng']['counter'], counter),
        _creature_change(state, new_state, attacker_id),
        _creature_change(state, new_state, target_id),
    ]
    label = weapon or natural
    event = _event(
        'attack', actor,
        f'attack {attacker_id} {target_id} --weapon {label} --range {declared_range} --as {actor}',
        roll_records, changes,
    )
    event['outcome'] = dict(result)
    event['outcome']['target_hp'] = copy.deepcopy(target.get('hp'))
    return result, event, warnings


def deal_damage(state, target_id, expr, damage_type, source):
    target = _require_creature(state, target_id)
    if _is_dead(target):
        raise EngineError('INVALID_TARGET', f'{target_id} is dead', {'id': target_id})
    new_state = _copy(state)
    target = _require_creature(new_state, target_id)
    counter = new_state['rng']['counter']
    amount, record = _amount(expr, new_state['rng']['seed'], counter)
    rolls = []
    if record:
        counter += 1
        rolls.append(record)
    amount = _mitigate(target, damage_type, max(0, amount))
    _apply_damage_parts(new_state, target, [{'type': damage_type, 'amount': amount}], False)
    new_state['rng']['counter'] = counter
    changes = [_creature_change(state, new_state, target_id)]
    if counter != state['rng']['counter']:
        changes.append(_counter(state['rng']['counter'], counter))
    result = {'amount': amount, 'type': damage_type, 'target_status': _health_word(target)}
    event = _event('damage', 'dm', f'damage {target_id} {expr} --type {damage_type} --as dm', rolls, changes)
    event['outcome'] = dict(result)
    event['outcome']['target_hp'] = copy.deepcopy(target.get('hp'))
    if source:
        event['source'] = source
    return result, event


def heal_target(state, target_id, expr):
    target = _require_creature(state, target_id)
    if target.get('dead'):
        rule('a dead creature cannot be healed', id=target_id)
    new_state = _copy(state)
    target = _require_creature(new_state, target_id)
    counter = new_state['rng']['counter']
    amount, record = _amount(expr, new_state['rng']['seed'], counter)
    rolls = []
    if record:
        counter += 1
        rolls.append(record)
    amount = max(0, amount)
    current = max(0, target['hp']['current'])
    target['hp']['current'] = min(target['hp']['max'], current + amount)
    if target['hp']['current'] > 0:
        target['stable'] = False
        target['death_saves'] = {'s': 0, 'f': 0}
    new_state['rng']['counter'] = counter
    changes = [_creature_change(state, new_state, target_id)]
    if counter != state['rng']['counter']:
        changes.append(_counter(state['rng']['counter'], counter))
    result = {'amount': amount, 'hp': target['hp']['current'], 'target_status': _health_word(target)}
    event = _event('heal', 'dm', f'heal {target_id} {expr} --as dm', rolls, changes)
    event['outcome'] = result
    return result, event


def set_temp_hp(state, target_id, amount):
    if amount < 0:
        raise EngineError('BAD_ARGS', 'temporary hit points cannot be negative')
    target = _require_creature(state, target_id)
    new_state = _copy(state)
    target = _require_creature(new_state, target_id)
    target['hp']['temp'] = max(target['hp'].get('temp') or 0, amount)
    changes = [_creature_change(state, new_state, target_id)]
    result = {'temp': target['hp']['temp']}
    event = _event('temp-hp', 'dm', f'temp-hp {target_id} {amount} --as dm', [], changes)
    event['outcome'] = result
    return result, event


def death_save(state, char_id, actor):
    _require_active(state)
    character = _require_creature(state, char_id)
    if char_id not in (state.get('characters') or {}):
        raise EngineError('INVALID_TARGET', 'only a character makes death saves', {'id': char_id})
    _assert_controls(state, actor, char_id)
    _assert_turn_owner(state, actor, char_id)
    if character.get('dead'):
        raise EngineError('INCAPACITATED', f'{char_id} is dead', {'id': char_id})
    if character.get('stable'):
        rule('already stable', id=char_id)
    if character['hp']['current'] > 0:
        rule('death saves are only at 0 hit points', id=char_id)
    new_state = _copy(state)
    character = _require_creature(new_state, char_id)
    counter = new_state['rng']['counter']
    rolled = roll_expr('1d20', new_state['rng']['seed'], counter, 'none')
    natural = rolled['kept'][0]
    saves = character.setdefault('death_saves', {'s': 0, 'f': 0})
    state_name = 'dying'
    if natural == 20:
        character['hp']['current'] = 1
        character['stable'] = False
        character['death_saves'] = {'s': 0, 'f': 0}
        state_name = 'conscious'
    elif natural == 1:
        saves['f'] = saves.get('f', 0) + 2
    elif natural >= 10:
        saves['s'] = saves.get('s', 0) + 1
    else:
        saves['f'] = saves.get('f', 0) + 1
    if state_name != 'conscious':
        if saves.get('f', 0) >= 3:
            character['dead'] = True
            character['hp']['current'] = 0
            state_name = 'dead'
        elif saves.get('s', 0) >= 3:
            character['stable'] = True
            state_name = 'stable'
    new_state['rng']['counter'] = counter + 1
    result = {
        'successes': character.get('death_saves', {}).get('s', 0),
        'failures': character.get('death_saves', {}).get('f', 0),
        'state': state_name,
        'natural': natural,
    }
    changes = [
        _creature_change(state, new_state, char_id),
        _counter(counter, counter + 1),
    ]
    event = _event('death-save', actor, f'death-save {char_id} --as {actor}', [
        _d20_record(char_id, counter, rolled, 0, natural),
    ], changes)
    event['outcome'] = result
    return result, event


def change_condition(state, target_id, name, remove, duration, level):
    if name not in CONDITIONS:
        rule('unknown condition', condition=name)
    target = _require_creature(state, target_id)
    new_state = _copy(state)
    target = _require_creature(new_state, target_id)
    if name == 'exhaustion':
        if remove:
            target['exhaustion'] = 0
        else:
            target['exhaustion'] = level if level is not None else min(6, (target.get('exhaustion') or 0) + 1)
            if not 1 <= target['exhaustion'] <= 6:
                rule('exhaustion must be from 1 to 6')
    else:
        rows = _condition_rows(target)
        if remove:
            target['conditions'] = [row for row in rows if row['index'] != name]
        else:
            target['conditions'] = [row for row in rows if row['index'] != name]
            target['conditions'].append({'index': name, 'duration': duration})
    changes = [_creature_change(state, new_state, target_id)]
    verb = 'remove' if remove else 'add'
    event = _event('condition', 'dm', f'condition {verb} {target_id} {name} --as dm', [], changes)
    event['outcome'] = {'condition': name, 'removed': remove, 'exhaustion': target.get('exhaustion') or 0}
    return event['outcome'], event


def take_action(state, actor_id, verb, target_id, actor, bonus, trigger):
    _require_active(state)
    creature = _require_creature(state, actor_id)
    _assert_controls(state, actor, actor_id)
    _assert_turn_owner(state, actor, actor_id)
    if _incapacitated(creature):
        raise EngineError('INCAPACITATED', f'{actor_id} cannot act', {'id': actor_id})
    if verb not in ('dash', 'disengage', 'dodge', 'help', 'hide', 'ready', 'search', 'use-object'):
        raise EngineError('BAD_ARGS', f'unknown action {verb}', {'action': verb})
    use_bonus = bool(bonus)
    if use_bonus and verb not in ('disengage', 'hide'):
        rule('only disengage and hide can be a bonus action', action=verb)
    if use_bonus and not _has_bonus_cunning(creature):
        rule('no feature grants that bonus action', action=verb)
    if verb == 'help' and not target_id:
        raise EngineError('BAD_ARGS', 'help needs a target')
    kind = 'bonus' if use_bonus else 'action'
    _assert_economy(state, actor_id, kind)
    new_state = _copy(state)
    creature = _require_creature(new_state, actor_id)
    rolls = []
    counter = new_state['rng']['counter']
    if verb == 'dash':
        economy = new_state['combat']['economy'][actor_id]
        economy['movement_ft'] = economy.get('movement_ft', 0) + (_speed(creature) or 0)
    elif verb == 'disengage':
        creature['disengaged'] = True
    elif verb == 'dodge':
        creature['dodging'] = True
    elif verb == 'help':
        if target_id == actor_id or _is_dead(_require_creature(new_state, target_id)):
            raise EngineError('INVALID_TARGET', f'{target_id} is not a valid target', {'id': target_id})
        new_state['combat'].setdefault('help', {})[target_id] = actor_id
    elif verb == 'hide':
        mode, sources, _warnings = _hide_advantage(creature)
        rolled = roll_expr('1d20', new_state['rng']['seed'], counter, mode)
        bonus = _stealth_bonus(creature)
        total = rolled['kept'][0] + bonus
        creature['hidden'] = total
        rolls.append(_d20_record(actor_id, counter, rolled, bonus, total))
        counter += 1
    elif verb == 'ready':
        creature['readied'] = trigger or ''
    _spend(new_state, actor_id, kind)
    new_state['rng']['counter'] = counter
    changes = [
        {'op': 'set', 'path': 'combat', 'from': state.get('combat'), 'to': new_state['combat']},
        _creature_change(state, new_state, actor_id),
    ]
    if counter != state['rng']['counter']:
        changes.append(_counter(state['rng']['counter'], counter))
    event = _event('action', actor, f'action {actor_id} {verb} --as {actor}', rolls, changes)
    event['outcome'] = {'action': verb, 'bonus': use_bonus}
    return event['outcome'], event


def move_creature(state, actor_id, feet, difficult, actor):
    _require_active(state)
    creature = _require_creature(state, actor_id)
    _assert_controls(state, actor, actor_id)
    _assert_turn_owner(state, actor, actor_id)
    if _incapacitated(creature):
        raise EngineError('INCAPACITATED', f'{actor_id} cannot act', {'id': actor_id})
    if feet < 0:
        raise EngineError('BAD_ARGS', 'distance cannot be negative')
    cost = feet * (2 if difficult else 1)
    economy = (state.get('combat') or {}).get('economy', {}).get(actor_id) or {}
    if economy.get('movement_ft', 0) < cost:
        raise EngineError('NO_ACTION_LEFT', 'not enough movement', {'have': economy.get('movement_ft', 0), 'need': cost})
    new_state = _copy(state)
    new_state['combat']['economy'][actor_id]['movement_ft'] -= cost
    changes = [{'op': 'set', 'path': 'combat', 'from': state.get('combat'), 'to': new_state['combat']}]
    event = _event('move', actor, f'move {actor_id} --ft {feet} --as {actor}', [], changes)
    event['outcome'] = {'spent': cost, 'remaining': new_state['combat']['economy'][actor_id]['movement_ft']}
    return event['outcome'], event


def require_combat_action(state, actor, creature_id):
    """Equip and use spend an action while combat is active."""
    if not (state.get('combat') or {}).get('active'):
        return None
    creature = _require_creature(state, creature_id)
    _assert_controls(state, actor, creature_id)
    _assert_turn_owner(state, actor, creature_id)
    if _incapacitated(creature):
        raise EngineError('INCAPACITATED', f'{creature_id} cannot act', {'id': creature_id})
    _assert_economy(state, creature_id, 'action')
    economy = copy.deepcopy(state['combat']['economy'][creature_id])
    economy['action'] = False
    return {'op': 'set', 'path': f'combat.economy.{creature_id}', 'from': state['combat']['economy'][creature_id], 'to': economy}


def _begin_turn(state, creature_id):
    creature = _require_creature(state, creature_id)
    creature['dodging'] = False
    creature['disengaged'] = False
    surprised = creature_id in (state['combat'].get('surprised') or []) and creature_id not in (state['combat'].get('surprised_done') or [])
    if surprised or _is_dead(creature):
        economy = {'action': False, 'bonus': False, 'reaction': False, 'movement_ft': 0}
    else:
        economy = {
            'action': True,
            'bonus': True,
            'reaction': True,
            'movement_ft': 0 if _incapacitated(creature) else _speed(creature),
        }
    state['combat'].setdefault('economy', {})[creature_id] = economy


def _advance_index(state):
    combat = state['combat']
    order = combat.get('order') or []
    if not order:
        return None, False
    start = combat.get('current_index') or 0
    wrapped = False
    index = start
    for _ in range(len(order)):
        index = (index + 1) % len(order)
        if index == 0:
            combat['round'] = (combat.get('round') or 1) + 1
            wrapped = True
        creature_id = order[index]['id']
        if not _is_dead(_require_creature(state, creature_id)):
            combat['current_index'] = index
            return creature_id, wrapped
    combat['current_index'] = start
    return order[start]['id'], wrapped


def _tick_creature(state, creature_id):
    creature = _require_creature(state, creature_id)
    rolls = []
    kept = []
    for row in _condition_rows(creature):
        duration = row.get('duration') or {}
        if duration.get('type') == 'end-of-turn' and duration.get('id') == creature_id:
            continue
        if duration.get('type') == 'save':
            counter = state['rng']['counter']
            rolled = roll_expr('1d20', state['rng']['seed'], counter, 'none')
            state['rng']['counter'] = counter + 1
            bonus = _save_bonus(creature, duration.get('ability') or 'con')
            total = rolled['kept'][0] + bonus
            rolls.append(_d20_record(creature_id, counter, rolled, bonus, total))
            if total >= duration.get('dc', 10):
                continue
        kept.append(row)
    creature['conditions'] = kept
    return rolls


def _tick_rounds(state):
    for bucket in (state.get('characters') or {}, state.get('monsters') or {}):
        for creature in bucket.values():
            kept = []
            for row in _condition_rows(creature):
                duration = row.get('duration') or {}
                if duration.get('type') == 'rounds':
                    duration['rounds'] = duration.get('rounds', 1) - 1
                    if duration['rounds'] <= 0:
                        continue
                kept.append(row)
            creature['conditions'] = kept


def _attack_advantage(state, attacker, target, attack_row, declared, normal, ranged, flag_adv, flag_dis):
    adv = []
    dis = []
    if flag_adv:
        adv.append('flag:adv')
    if flag_dis:
        dis.append('flag:dis')
    if declared > normal:
        dis.append('long-range')
    if ranged and declared <= 5 and _any_hostile(state, attacker):
        dis.append('ranged-in-melee')
    names = set(condition_names(attacker))
    if names.intersection(('poisoned', 'frightened', 'prone', 'restrained', 'blinded')) or (attacker.get('exhaustion') or 0) >= 3:
        dis.append('condition')
    if target.get('dodging'):
        dis.append('dodge')
    if set(condition_names(target)).intersection(('paralyzed', 'unconscious', 'restrained', 'stunned', 'blinded')):
        adv.append('target-condition')
    if attacker.get('invisible') or 'invisible' in names:
        adv.append('invisible')
    if attacker.get('hidden'):
        adv.append('hidden')
    helper = (state.get('combat') or {}).get('help', {}).get(_id_of(target))
    if helper and helper != _id_of(attacker):
        adv.append(f'help:{helper}')
    return adv, dis, []


def _apply_damage_parts(state, target, damages, crit):
    total = sum(part['amount'] for part in damages)
    was_down = target['hp']['current'] <= 0
    leftover = _soak(target, total)
    is_monster = target.get('ref') or target.get('id') in (state.get('monsters') or {})
    if was_down and not is_monster and not target.get('dead'):
        fails = 2 if crit else 1
        saves = target.setdefault('death_saves', {'s': 0, 'f': 0})
        saves['f'] = saves.get('f', 0) + fails
        if saves['f'] >= 3:
            target['dead'] = True
        return
    if target['hp']['current'] > 0:
        return
    if is_monster:
        target['dead'] = True
        target['hp']['current'] = 0
        return
    if leftover >= target['hp']['max']:
        target['dead'] = True
        target['hp']['current'] = 0
        return
    target['stable'] = False
    target.setdefault('death_saves', {'s': 0, 'f': 0})


def _soak(creature, amount):
    temp = creature['hp'].get('temp') or 0
    if temp:
        used = min(temp, amount)
        creature['hp']['temp'] = temp - used
        amount -= used
    current = max(0, creature['hp']['current'])
    dealt = min(current, amount)
    creature['hp']['current'] = current - dealt
    return amount - dealt


def _mitigate(creature, damage_type, amount):
    resists = set(creature.get('damage_resistances') or [])
    vulns = set(creature.get('damage_vulnerabilities') or [])
    immunities = set(creature.get('damage_immunities') or [])
    if damage_type in immunities:
        return 0
    if damage_type in resists and damage_type in vulns:
        return amount
    if damage_type in resists:
        return amount // 2
    if damage_type in vulns:
        return amount * 2
    return amount


def _make_monster(monster_id, ref, block, encounter_id, roll_hp, seed, counter):
    rolls = []
    hp = block.get('hit_points') or 1
    if roll_hp and block.get('hit_points_roll'):
        rolled = roll_expr(block['hit_points_roll'], seed, counter, 'none')
        hp = max(1, rolled['total'])
        rolls.append({
            'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
            'expr': block['hit_points_roll'], 'kept': rolled['kept'], 'label': monster_id,
            'mod': rolled['mod'], 'total': hp,
        })
        counter += 1
    abilities = {
        'str': block.get('strength') or 10,
        'dex': block.get('dexterity') or 10,
        'con': block.get('constitution') or 10,
        'int': block.get('intelligence') or 10,
        'wis': block.get('wisdom') or 10,
        'cha': block.get('charisma') or 10,
    }
    armor = block.get('armor_class') or [{'value': 10}]
    ac = armor[0].get('value', 10) if isinstance(armor[0], dict) else armor[0]
    monster = {
        'id': monster_id,
        'ref': ref,
        'name': block.get('name') or monster_id,
        'encounter': encounter_id,
        'hp': {'max': hp, 'current': hp, 'temp': 0},
        'ac': ac,
        'abilities': abilities,
        'speed': _parse_speed(block.get('speed')),
        'attacks': _monster_attacks(block),
        'xp': block.get('xp') if block.get('xp') is not None else XP_BY_CR.get(block.get('challenge_rating'), 0),
        'damage_resistances': list(block.get('damage_resistances') or []),
        'damage_immunities': list(block.get('damage_immunities') or []),
        'damage_vulnerabilities': list(block.get('damage_vulnerabilities') or []),
        'conditions': [],
        'exhaustion': 0,
        'dead': False,
        'special': [row.get('name') for row in block.get('special_abilities') or [] if row.get('name')],
    }
    return monster, counter, rolls


def _monster_attacks(block):
    attacks = []
    for action in block.get('actions') or []:
        if 'attack_bonus' not in action:
            continue
        normal, long, ranged = _text_reach(action.get('desc') or '')
        parts = []
        for piece in action.get('damage') or []:
            parts.append({
                'dice': piece.get('damage_dice') or '1d4',
                'type': (piece.get('damage_type') or {}).get('index') or 'bludgeoning',
            })
        if not parts:
            parts = [{'dice': '1d4', 'type': 'bludgeoning'}]
        attacks.append({
            'weapon': _slug_name(action.get('name') or 'attack'),
            'name': action.get('name'),
            'bonus': action['attack_bonus'],
            'damage': parts[0]['dice'],
            'damage_type': parts[0]['type'],
            'parts': parts,
            'range': f'{normal}/{long}' if ranged else 'melee',
            'normal': normal,
            'long': long,
            'ranged': ranged,
        })
    return attacks


def _find_attack(creature, weapon, natural):
    name = natural or weapon
    if not name:
        raise EngineError('BAD_ARGS', 'pass --weapon or --natural')
    slug = _slug_name(name)
    for attack in creature.get('attacks') or []:
        if attack.get('weapon') == slug or _slug_name(attack.get('name') or '') == slug:
            item = None
            if not creature.get('ref'):
                try:
                    item = srd.get('equipment', attack['weapon'])
                except KeyError:
                    item = None
            return attack, item
    raise EngineError('UNKNOWN_ID', f'no attack named {name}', {'weapon': name})


def _reach(attack, item):
    if 'normal' in attack:
        return attack['normal'], attack['long'], attack.get('ranged', False)
    text = attack.get('range') or 'melee'
    if text == 'melee':
        if item and item.get('throw_range'):
            thrown = item['throw_range']
            return 5, thrown.get('long', thrown.get('normal', 5)), False
        reach = 5
        if item and any(prop.get('index') == 'reach' for prop in item.get('properties') or []):
            reach = 10
        return reach, reach, False
    if '/' in str(text):
        normal, long = str(text).split('/', 1)
        return int(normal), int(long), True
    return int(text), int(text), False


def _text_reach(desc):
    ranged = re.search(r'range (\d+)/(\d+)', desc)
    if ranged:
        return int(ranged.group(1)), int(ranged.group(2)), True
    reach = re.search(r'reach (\d+)', desc)
    if reach:
        feet = int(reach.group(1))
        return feet, feet, False
    return 5, 5, False


def _crit_expr(expr):
    match = re.fullmatch(r'(\d*)d(\d+)([+-]\d+)?', expr or '')
    if not match:
        return expr
    count = int(match.group(1) or '1') * 2
    return f'{count}d{match.group(2)}{match.group(3) or ""}'


def _amount(expr, seed, counter):
    text = str(expr)
    if re.fullmatch(r'\d+', text):
        return int(text), None
    rolled = roll_expr(text, seed, counter, 'none')
    record = {
        'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
        'expr': text, 'kept': rolled['kept'], 'label': text, 'mod': rolled['mod'],
        'total': max(0, rolled['total']),
    }
    return max(0, rolled['total']), record


def _monster_block(adventure, ref):
    if ref.startswith('custom:'):
        for row in adventure.get('custom_monsters') or []:
            if row.get('id') == ref:
                return row.get('stat_block') or {}
        raise EngineError('UNKNOWN_ID', f'unknown custom monster {ref}', {'ref': ref})
    slug = ref.split(':', 1)[1] if ref.startswith('srd:') else ref
    try:
        return srd.get('monster', slug)
    except KeyError:
        raise EngineError('UNKNOWN_ID', f'unknown monster {ref}', {'ref': ref}) from None


def _next_id(monsters, prefix):
    number = 1
    while f'{prefix}-{number}' in monsters:
        number += 1
    return f'{prefix}-{number}'


def _slug_name(name):
    text = name.split(':', 1)[-1].lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


def _parse_speed(speed):
    if isinstance(speed, dict):
        speed = speed.get('walk') or '30'
    match = re.search(r'\d+', str(speed or '30'))
    return int(match.group(0)) if match else 30


def _break_ties(rows, seed, counter):
    rows = sorted(rows, key=lambda row: (-row['init'], -row['dex'], row['id']))
    ordered = []
    extra = []
    index = 0
    while index < len(rows):
        end = index + 1
        while end < len(rows) and rows[end]['init'] == rows[index]['init'] and rows[end]['dex'] == rows[index]['dex']:
            end += 1
        group = rows[index:end]
        if len(group) > 1:
            for row in group:
                rolled = roll_expr('1d20', seed, counter, 'none')
                row['tiebreak'] = rolled['kept'][0]
                extra.append(_d20_record(row['id'], counter, rolled, 0, rolled['kept'][0]))
                counter += 1
            group.sort(key=lambda row: (-row['tiebreak'], row['id']))
        ordered.extend(group)
        index = end
    return ordered, counter, extra


def _require_active(state):
    if not (state.get('combat') or {}).get('active'):
        rule('combat is not active')


def _current_id(state):
    combat = state.get('combat') or {}
    order = combat.get('order') or []
    index = combat.get('current_index') or 0
    if not order:
        return None
    return order[index]['id']


def _assert_turn_owner(state, actor, creature_id):
    current = _current_id(state)
    if actor == current == creature_id:
        return
    if actor == 'dm' and creature_id == current and creature_id in (state.get('monsters') or {}):
        return
    if actor == 'dm' and creature_id == current and _can_relay(state, creature_id):
        return
    raise EngineError('NOT_YOUR_TURN', f"it is {current}'s turn", {'current': current})


def _assert_controls(state, actor, creature_id):
    if creature_id in (state.get('monsters') or {}):
        if actor != 'dm':
            raise EngineError('NOT_AUTHORIZED', 'only the DM acts for a monster', {'actor': actor})
        return
    if actor == creature_id:
        return
    if actor == 'dm' and _can_relay(state, creature_id):
        return
    raise EngineError('NOT_AUTHORIZED', 'that is not your character', {'actor': actor, 'id': creature_id})


def _can_relay(state, creature_id):
    character = (state.get('characters') or {}).get(creature_id) or {}
    return character.get('player') in ('javi', 'dm')


def _assert_economy(state, creature_id, kind):
    economy = ((state.get('combat') or {}).get('economy') or {}).get(creature_id) or {}
    if kind == 'movement':
        return
    if not economy.get(kind, False):
        raise EngineError('NO_ACTION_LEFT', f'no {kind} left', {'kind': kind})


def _spend(state, creature_id, kind):
    state['combat']['economy'][creature_id][kind] = False


def _incapacitated(creature):
    if creature.get('dead'):
        return True
    if set(condition_names(creature)).intersection(INCAPACITATING):
        return True
    return creature.get('hp', {}).get('current', 1) <= 0


def _is_dead(creature):
    return bool(creature.get('dead'))


def _require_creature(state, creature_id):
    if creature_id in (state.get('characters') or {}):
        return state['characters'][creature_id]
    if creature_id in (state.get('monsters') or {}):
        return state['monsters'][creature_id]
    raise EngineError('UNKNOWN_ID', f'unknown creature {creature_id}', {'id': creature_id})


def _id_of(creature):
    return creature.get('id')


def _dex_score(creature):
    if creature.get('abilities'):
        return creature['abilities'].get('dex') or 10
    return 10


def _speed(creature):
    return creature.get('speed') or creature.get('base_speed') or 30


def _ac_of(creature):
    if creature.get('ac') is not None and creature.get('ref'):
        return creature['ac']
    return creature.get('ac') or 10


def _health_word(creature):
    if creature.get('ref'):
        if creature.get('dead') or creature.get('hp', {}).get('current', 1) <= 0:
            return 'dead'
    return status_of(creature)


def _any_hostile(state, attacker):
    attacker_id = attacker.get('id')
    monsters = attacker_id in (state.get('monsters') or {})
    for creature_id, creature in (state.get('characters') or {}).items():
        if monsters and not _is_dead(creature) and creature_id != attacker_id:
            return True
    for creature_id, creature in (state.get('monsters') or {}).items():
        if not monsters and not _is_dead(creature) and creature_id != attacker_id:
            return True
    return False


def _has_bonus_cunning(creature):
    if 'Nimble Escape' in (creature.get('special') or []):
        return True
    return any(feature.get('id') == 'cunning-action' for feature in creature.get('features') or [])


def _hide_advantage(creature):
    dis = ['armor'] if creature.get('stealth_disadvantage') else []
    if dis:
        return 'dis', dis, []
    return 'none', [], []


def _stealth_bonus(creature):
    if creature.get('ref'):
        return ability_mod(_dex_score(creature))
    from dnd.chargen import skill_bonus
    return skill_bonus(creature, 'stealth')


def _save_bonus(creature, ability):
    if creature.get('proficiencies'):
        from dnd.chargen import save_bonus
        return save_bonus(creature, ability)
    return ability_mod((creature.get('abilities') or {}).get(ability) or 10)


def _condition_rows(creature):
    rows = []
    for entry in creature.get('conditions') or []:
        if isinstance(entry, str):
            rows.append({'index': entry, 'duration': None})
        elif isinstance(entry, dict) and entry.get('index'):
            rows.append(entry)
    return rows


def _touched(before, after):
    ids = []
    for bucket in ('characters', 'monsters'):
        for creature_id in (after.get(bucket) or {}):
            if (before.get(bucket) or {}).get(creature_id) != after[bucket][creature_id]:
                ids.append(creature_id)
    return ids


def _creature_change(before, after, creature_id):
    if creature_id in (after.get('characters') or {}):
        return {
            'op': 'set',
            'path': f'characters.{creature_id}',
            'from': (before.get('characters') or {}).get(creature_id),
            'to': after['characters'][creature_id],
        }
    return {
        'op': 'set',
        'path': f'monsters.{creature_id}',
        'from': (before.get('monsters') or {}).get(creature_id),
        'to': after['monsters'][creature_id],
    }


def _counter(old, new):
    return {'op': 'set', 'path': 'rng.counter', 'from': old, 'to': new}


def _event(event_type, actor, cmd, rolls, changes):
    return {'actor': actor, 'changes': changes, 'cmd': cmd, 'rolls': rolls, 'type': event_type}


def _d20_record(label, counter, rolled, bonus, total):
    return {
        'adv': 'none',
        'adv_sources': [],
        'counter': counter,
        'dice': rolled['dice'],
        'expr': '1d20',
        'kept': rolled['kept'],
        'label': label,
        'mod': bonus,
        'total': total,
    }


def _copy(state):
    return copy.deepcopy(state)


def parse_duration(text):
    if not text:
        return None
    if text.startswith('rounds:'):
        return {'type': 'rounds', 'rounds': int(text.split(':', 1)[1])}
    if text.startswith('until:end-of-turn:'):
        return {'type': 'end-of-turn', 'id': text.split(':', 2)[2]}
    if text.startswith('save:'):
        _tag, ability, dc = text.split(':')
        return {'type': 'save', 'ability': ability, 'dc': int(dc)}
    raise EngineError('BAD_ARGS', 'bad condition duration', {'duration': text})
