"""Spellcasting and rests."""

import copy
import re

from dnd.chargen import ability_mod, advantage_state, auto_fail_save, rule
from dnd.dice import roll_expr
from dnd.errors import EngineError
from dnd import combat
from dnd import srd

PREPARED = ('wizard', 'cleric', 'druid', 'paladin', 'ranger')
CASTERS = ('bard', 'cleric', 'druid', 'paladin', 'ranger', 'sorcerer', 'warlock', 'wizard')
# The structured damage is only the first tick. The end-of-turn acid is not in the record.
INCOMPLETE = {'acid-arrow'}
NAMED = {
    'sleep': 'sleep',
    'shield': 'shield',
    'bless': 'bless',
    'magic-missile': 'missile',
    'mage-armor': 'mage-armor',
    'false-life': 'temp',
    'guiding-bolt': 'attack',
}


def model_of(spell):
    """How the engine resolves this spell. ``manual`` spends the slot and stops."""
    if spell['index'] in INCOMPLETE:
        return 'manual'
    named = NAMED.get(spell['index'])
    if named:
        return named
    if spell.get('heal_at_slot_level') and spell.get('duration') == 'Instantaneous' and not spell.get('damage'):
        return 'heal'
    success = (spell.get('dc') or {}).get('dc_success')
    if spell.get('dc') and spell.get('damage') and spell.get('duration') == 'Instantaneous' and success in ('half', 'none'):
        return 'save'
    if spell.get('attack_type') and spell.get('damage') and spell.get('duration') == 'Instantaneous':
        return 'attack'
    return 'manual'


def modelled_counts():
    spells = [
        spell for spell in srd.all('spell')
        if spell['level'] <= 5 and any(entry['index'] in CASTERS for entry in spell.get('classes') or [])
    ]
    modelled = [spell['index'] for spell in spells if model_of(spell) != 'manual']
    manual = [spell['index'] for spell in spells if model_of(spell) == 'manual']
    return {'modelled': sorted(modelled), 'manual': sorted(manual)}


def cast_spell(state, caster_id, spell_index, targets, slot, declared_range, actor, ritual, reaction):
    caster = combat._require_creature(state, caster_id)
    if caster_id not in (state.get('characters') or {}):
        raise EngineError('INVALID_TARGET', 'only a character can cast', {'id': caster_id})
    combat._assert_controls(state, actor, caster_id)
    if combat._incapacitated(caster):
        raise EngineError('INCAPACITATED', f'{caster_id} cannot act', {'id': caster_id})
    try:
        spell = srd.get('spell', spell_index)
    except KeyError:
        raise EngineError('UNKNOWN_ID', f'unknown spell {spell_index}', {'spell': spell_index}) from None
    if not _available(caster, spell, ritual):
        rule('spell is not prepared or known', spell=spell_index)
    if ritual and not spell.get('ritual'):
        rule('that spell is not a ritual', spell=spell_index)
    if ritual and (state.get('combat') or {}).get('active'):
        rule('a ritual takes too long during combat', spell=spell_index)
    casting = _casting_time(spell, reaction)
    _check_turn_and_economy(state, actor, caster_id, casting)
    _check_bonus_rule(state, caster_id, spell, casting, ritual)
    targets = list(targets or [])
    if not targets and (spell.get('range') == 'Self' or spell_index in ('shield', 'mage-armor', 'false-life')):
        targets = [caster_id]
    if spell_index == 'shield':
        targets = [caster_id]
    for target_id in targets:
        combat._require_creature(state, target_id)
    limit = _range_limit(spell)
    if declared_range is not None and declared_range > limit:
        raise EngineError('OUT_OF_RANGE', f'{declared_range} ft is out of range', {'range': declared_range, 'limit': limit})
    slot_used = _reserve_slot(caster, spell, slot, ritual)
    new_state = combat._copy(state)
    caster = combat._require_creature(new_state, caster_id)
    rolls = []
    kind = model_of(spell)
    if kind == 'heal':
        result = _resolve_heal(new_state, caster, spell, targets, slot_used or spell['level'], rolls)
    elif kind == 'temp':
        result = _resolve_temp(new_state, caster, spell, targets, slot_used or spell['level'], rolls)
    elif kind == 'save':
        result = _resolve_save(new_state, caster, spell, targets, slot_used, rolls)
    elif kind == 'attack':
        result = _resolve_attack(new_state, caster, spell, targets, slot_used, rolls)
    elif kind == 'sleep':
        result = _resolve_sleep(new_state, caster, spell, targets, slot_used or 1, rolls)
    elif kind == 'shield':
        caster['shield_spell'] = True
        result = {'effect': 'shield', 'ac_bonus': 5}
    elif kind == 'bless':
        result = _resolve_bless(new_state, caster, targets, slot_used or 1)
    elif kind == 'missile':
        result = _resolve_missile(new_state, caster, spell, targets, slot_used or 1, rolls)
    elif kind == 'mage-armor':
        result = _resolve_mage_armor(new_state, targets)
    else:
        result = {'effect': 'manual', 'spell': spell_index}
    if slot_used is not None:
        _spend_slot(caster, slot_used)
    if spell.get('concentration'):
        _set_concentration(new_state, caster, spell_index, targets)
    _mark_turn_spell(new_state, caster_id, spell, casting, ritual)
    _spend_casting(new_state, caster_id, casting)
    if not (new_state.get('combat') or {}).get('active'):
        _advance_time(new_state, _minutes(spell, ritual))
    result.update({'spell': spell_index, 'slot': slot_used})
    changes = _diff(state, new_state, [caster_id, *targets])
    event = {
        'actor': actor,
        'changes': changes,
        'cmd': f'cast {caster_id} {spell_index} --as {actor}',
        'rolls': rolls,
        'type': 'cast',
        'outcome': result,
    }
    return result, event


def short_rest(state, spend):
    if (state.get('combat') or {}).get('active'):
        rule('a rest cannot start during combat')
    new_state = copy.deepcopy(state)
    healed = []
    for char_id, count in spend:
        healed.append(_spend_hit_dice(new_state, char_id, count))
    recovered = []
    for character in (new_state.get('characters') or {}).values():
        _recharge(character, 'short')
        if character.get('class') == 'warlock':
            _refill_slots(character)
        recovered.extend(_arcane_recovery(character))
    _advance_time(new_state, 60)
    changes = _diff(state, new_state, list((new_state.get('characters') or {}).keys()))
    event = {
        'actor': 'dm', 'changes': changes, 'cmd': 'rest short --as dm', 'rolls': [],
        'type': 'rest', 'outcome': {'kind': 'short', 'healed': healed, 'recovered': recovered},
    }
    # Hit-die rolls were stored on the heal rows; lift them onto the event.
    event['rolls'] = [roll for row in healed for roll in row.get('rolls') or []]
    for row in healed:
        row.pop('rolls', None)
    return event['outcome'], event


def long_rest(state):
    if (state.get('combat') or {}).get('active'):
        rule('a rest cannot start during combat')
    new_state = copy.deepcopy(state)
    for character in (new_state.get('characters') or {}).values():
        if character.get('dead'):
            continue
        character['hp']['current'] = character['hp']['max']
        character['stable'] = False
        character['death_saves'] = {'s': 0, 'f': 0}
        character['exhaustion'] = max(0, (character.get('exhaustion') or 0) - 1)
        character['blessed'] = False
        character['shield_spell'] = False
        character['mage_armor'] = False
        character['conditions'] = [
            row for row in character.get('conditions') or []
            if isinstance(row, str) or not (row.get('source') or row.get('duration'))
        ]
        casting = character.get('spellcasting')
        if casting:
            casting['concentration'] = None
            _refill_slots(character)
        _recharge(character, 'long')
        _regain_hit_dice(character)
    _advance_time(new_state, 8 * 60)
    changes = _diff(state, new_state, list((new_state.get('characters') or {}).keys()))
    event = {
        'actor': 'dm', 'changes': changes, 'cmd': 'rest long --as dm', 'rolls': [],
        'type': 'rest', 'outcome': {'kind': 'long'},
    }
    return event['outcome'], event


def spend_hit_dice(state, char_id, count, actor):
    if (state.get('combat') or {}).get('active'):
        rule('hit dice are spent outside combat')
    character = combat._require_creature(state, char_id)
    combat._assert_controls(state, actor, char_id)
    new_state = copy.deepcopy(state)
    healed = _spend_hit_dice(new_state, char_id, count)
    changes = _diff(state, new_state, [char_id])
    event = {
        'actor': actor, 'changes': changes, 'cmd': f'rest spend-hd {char_id} {count} --as {actor}',
        'rolls': healed.get('rolls') or [], 'type': 'rest',
    }
    healed.pop('rolls', None)
    event['outcome'] = healed
    return healed, event


def _available(character, spell, ritual):
    casting = character.get('spellcasting') or {}
    index = spell['index']
    if spell['level'] == 0:
        return index in (casting.get('cantrips') or [])
    if ritual and character.get('class') == 'wizard' and index in (casting.get('known') or []) and spell.get('ritual'):
        return True
    if index in (casting.get('domain') or []):
        return True
    if character.get('class') in PREPARED:
        return index in (casting.get('prepared') or [])
    return index in (casting.get('known') or [])


def _casting_time(spell, reaction):
    text = spell.get('casting_time') or '1 action'
    if reaction and text != '1 reaction':
        rule('that spell is not a reaction', spell=spell['index'])
    if text == '1 reaction':
        return 'reaction'
    if text == '1 bonus action':
        return 'bonus'
    if text == '1 action':
        return 'action'
    return 'long'


def _check_turn_and_economy(state, actor, caster_id, casting):
    active = (state.get('combat') or {}).get('active')
    if not active:
        if casting == 'reaction':
            rule('there is no reaction to take outside combat')
        return
    if casting == 'long':
        rule('that casting time is too long during combat')
    if casting == 'reaction':
        combat._assert_economy(state, caster_id, 'reaction')
        return
    combat._assert_turn_owner(state, actor, caster_id)
    combat._assert_economy(state, caster_id, casting)


def _check_bonus_rule(state, caster_id, spell, casting, ritual):
    if ritual or spell['level'] == 0 or not (state.get('combat') or {}).get('active'):
        return
    economy = ((state.get('combat') or {}).get('economy') or {}).get(caster_id) or {}
    if casting == 'bonus' and economy.get('leveled_action'):
        rule('a bonus-action spell cannot follow another leveled spell this turn', spell=spell['index'])
    if casting != 'bonus' and economy.get('bonus_spell'):
        rule('only a cantrip can be cast after a bonus-action spell this turn', spell=spell['index'])


def _mark_turn_spell(state, caster_id, spell, casting, ritual):
    if ritual or spell['level'] == 0 or not (state.get('combat') or {}).get('active'):
        return
    economy = state['combat']['economy'][caster_id]
    if casting == 'bonus':
        economy['bonus_spell'] = True
    elif casting == 'action':
        economy['leveled_action'] = True


def _spend_casting(state, caster_id, casting):
    if not (state.get('combat') or {}).get('active') or casting == 'long':
        return
    combat._spend(state, caster_id, casting)


def _reserve_slot(character, spell, slot, ritual):
    if spell['level'] == 0 or ritual:
        return None
    slot = spell['level'] if slot is None else slot
    if slot < spell['level']:
        rule('slot is below the spell level', slot=slot, spell=spell['index'])
    slots = (character.get('spellcasting') or {}).get('slots') or {}
    row = slots.get(str(slot))
    if not row or row.get('used', 0) >= row.get('max', 0):
        raise EngineError('NO_SLOT', 'no spell slot of that level', {'slot': slot})
    return slot


def _spend_slot(character, slot):
    character['spellcasting']['slots'][str(slot)]['used'] += 1


def _set_concentration(state, caster, spell_index, targets):
    casting = caster.get('spellcasting')
    if not casting:
        return
    if casting.get('concentration'):
        combat._clear_concentration(state, caster)
    casting['concentration'] = {'spell': spell_index, 'targets': list(targets)}


def _resolve_heal(state, caster, spell, targets, slot, rolls):
    if not targets:
        raise EngineError('BAD_ARGS', 'name a target')
    expr = _slot_expr(spell.get('heal_at_slot_level') or {}, slot, caster)
    healed = []
    for target_id in targets:
        target = combat._require_creature(state, target_id)
        amount, record = _roll_amount(state, expr, rolls, target_id)
        if _no_heal(target):
            amount = 0
        else:
            amount += _life_bonus(caster, spell, slot)
            _heal(target, amount)
        healed.append({'id': target_id, 'amount': amount})
    return {'effect': 'heal', 'healing': healed}


def _resolve_temp(state, caster, spell, targets, slot, rolls):
    expr = _slot_expr(spell.get('heal_at_slot_level') or {}, slot, caster)
    done = []
    for target_id in targets:
        target = combat._require_creature(state, target_id)
        amount, _record = _roll_amount(state, expr, rolls, target_id)
        target['hp']['temp'] = max(target['hp'].get('temp') or 0, amount)
        done.append({'id': target_id, 'temp': target['hp']['temp']})
    return {'effect': 'temp', 'targets': done}


def _resolve_save(state, caster, spell, targets, slot, rolls):
    if not targets:
        raise EngineError('BAD_ARGS', 'name a target')
    ability = spell['dc']['dc_type']['index']
    success_mode = spell['dc'].get('dc_success') or 'none'
    expr = _damage_expr(spell, slot, caster.get('level', 1))
    damage_type = _damage_type(spell)
    dc = (caster.get('spellcasting') or {}).get('dc') or 10
    done = []
    for target_id in targets:
        target = combat._require_creature(state, target_id)
        if combat._is_dead(target):
            raise EngineError('INVALID_TARGET', f'{target_id} is dead', {'id': target_id})
        total, natural = _save(state, target, ability, rolls)
        saved = total >= dc
        amount, _record = _roll_amount(state, expr, rolls, target_id)
        if saved and success_mode == 'none':
            amount = 0
        elif saved and success_mode == 'half':
            amount //= 2
        amount = combat._mitigate(target, damage_type, amount)
        combat._apply_damage_parts(state, target, [{'type': damage_type, 'amount': amount}], False)
        conc_rolls, conc = combat.check_concentration(state, target, amount)
        rolls.extend(conc_rolls)
        done.append({
            'id': target_id, 'save': total, 'saved': saved, 'damage': amount, 'type': damage_type,
            'status': combat._health_word(target),
        })
    return {'effect': 'save', 'dc': dc, 'targets': done}


def _resolve_attack(state, caster, spell, targets, slot, rolls):
    if not targets:
        raise EngineError('BAD_ARGS', 'name a target')
    expr = _damage_expr(spell, slot, caster.get('level', 1))
    damage_type = _damage_type(spell)
    bonus = (caster.get('spellcasting') or {}).get('attack') or 0
    done = []
    for target_id in targets:
        target = combat._require_creature(state, target_id)
        if combat._is_dead(target):
            raise EngineError('INVALID_TARGET', f'{target_id} is dead', {'id': target_id})
        counter = state['rng']['counter']
        rolled = roll_expr('1d20', state['rng']['seed'], counter, 'none')
        state['rng']['counter'] = counter + 1
        natural = rolled['kept'][0]
        total = natural + bonus
        rolls.append(combat._d20_record(caster.get('id'), counter, rolled, bonus, total))
        ac = combat._ac_of(target)
        hit = natural != 1 and (natural == 20 or total >= ac)
        crit = natural == 20
        damages = []
        if hit:
            damage_expr = combat._crit_expr(expr) if crit else expr
            amount, _record = _roll_amount(state, damage_expr, rolls, target_id)
            amount = combat._mitigate(target, damage_type, amount)
            damages.append({'type': damage_type, 'amount': amount})
            combat._apply_damage_parts(state, target, damages, crit)
            if spell['index'] == 'guiding-bolt':
                target['guiding_bolt'] = True
            conc_rolls, _conc = combat.check_concentration(state, target, amount)
            rolls.extend(conc_rolls)
        done.append({
            'id': target_id, 'hit': hit, 'crit': bool(hit and crit), 'attack_roll': total, 'ac': ac,
            'damage': damages, 'status': combat._health_word(target),
        })
    return {'effect': 'attack', 'targets': done}


def _resolve_sleep(state, caster, spell, targets, slot, rolls):
    dice = 5 + 2 * (slot - 1)
    amount, _record = _roll_amount(state, f'{dice}d8', rolls, 'sleep')
    pool = amount
    ordered = []
    for target_id in targets:
        target = combat._require_creature(state, target_id)
        if combat._is_dead(target) or target['hp']['current'] <= 0:
            continue
        if 'unconscious' in _names(target):
            continue
        ordered.append(target)
    ordered.sort(key=lambda creature: (creature['hp']['current'], creature.get('id') or ''))
    affected = []
    for target in ordered:
        current = target['hp']['current']
        if current <= pool:
            pool -= current
            rows = [row for row in target.get('conditions') or [] if not (isinstance(row, dict) and row.get('index') == 'unconscious')]
            rows.append({'index': 'unconscious', 'source': 'sleep', 'duration': {'type': 'rounds', 'rounds': 10}})
            target['conditions'] = rows
            affected.append(target.get('id'))
    return {'effect': 'sleep', 'pool': amount, 'affected': affected, 'remaining': pool}


def _resolve_bless(state, caster, targets, slot):
    limit = 3 + max(0, slot - 1)
    if not targets or len(targets) > limit:
        rule('bless targets the wrong number of creatures', need=limit, got=len(targets))
    for target_id in targets:
        target = combat._require_creature(state, target_id)
        if combat._is_dead(target):
            raise EngineError('INVALID_TARGET', f'{target_id} is dead', {'id': target_id})
        target['blessed'] = True
    return {'effect': 'bless', 'targets': list(targets), 'concentration': True}


def _resolve_missile(state, caster, spell, targets, slot, rolls):
    if not targets:
        raise EngineError('BAD_ARGS', 'name a target')
    darts = 3 + max(0, slot - 1)
    # One dart is 1d4+1. Darts are dealt round-robin, each an automatic hit.
    totals = {target_id: 0 for target_id in targets}
    types = {target_id: 'force' for target_id in targets}
    for dart in range(darts):
        target_id = targets[dart % len(targets)]
        target = combat._require_creature(state, target_id)
        amount, _record = _roll_amount(state, '1d4+1', rolls, target_id)
        amount = combat._mitigate(target, 'force', amount)
        totals[target_id] += amount
    done = []
    for target_id, amount in totals.items():
        target = combat._require_creature(state, target_id)
        combat._apply_damage_parts(state, target, [{'type': 'force', 'amount': amount}], False)
        conc_rolls, _conc = combat.check_concentration(state, target, amount)
        rolls.extend(conc_rolls)
        done.append({'id': target_id, 'damage': amount, 'type': 'force', 'status': combat._health_word(target)})
    return {'effect': 'missile', 'targets': done}


def _resolve_mage_armor(state, targets):
    if len(targets) != 1:
        raise EngineError('BAD_ARGS', 'mage armor needs one target')
    target = combat._require_creature(state, targets[0])
    if combat._wearing_armor(target):
        rule('mage armor needs a creature without armor', id=targets[0])
    target['mage_armor'] = True
    return {'effect': 'mage-armor', 'id': targets[0], 'ac': combat._ac_of(target)}


def _save(state, target, ability, rolls):
    if auto_fail_save(target, ability):
        return 0, None
    counter = state['rng']['counter']
    mode, sources, _warnings = advantage_state(target, 'save', ability, False, False, None)
    rolled = roll_expr('1d20', state['rng']['seed'], counter, mode)
    counter += 1
    bless = 0
    bless_roll = None
    if target.get('blessed'):
        bless_roll = roll_expr('1d4', state['rng']['seed'], counter, 'none')
        bless = bless_roll['kept'][0]
        counter += 1
    state['rng']['counter'] = counter
    bonus = combat._save_bonus(target, ability)
    total = rolled['kept'][0] + bonus + bless
    rolls.append(combat._d20_record(target.get('id'), counter - 1 - (1 if bless_roll else 0), rolled, bonus, rolled['kept'][0] + bonus))
    if bless_roll:
        rolls.append({
            'adv': 'none', 'adv_sources': ['bless'], 'counter': counter - 1,
            'dice': bless_roll['dice'], 'expr': '1d4', 'kept': bless_roll['kept'],
            'label': 'bless', 'mod': 0, 'total': bless,
        })
    return total, rolled['kept'][0]


def _compact(expr):
    return (expr or '').replace(' ', '')


def _slot_expr(table, slot, caster):
    text = _compact(table.get(str(slot)) or table.get(str(min(int(key) for key in table))))
    mod = ability_mod((caster.get('abilities') or {}).get((caster.get('spellcasting') or {}).get('ability') or 'int') or 10)
    if 'MOD' not in text:
        return text
    if mod >= 0:
        return text.replace('MOD', str(mod))
    return text.replace('+ MOD', f'- {abs(mod)}').replace('MOD', '0')


def _damage_expr(spell, slot, level):
    part = (spell.get('damage') or [{}])[0]
    if part.get('damage_at_slot_level'):
        table = part['damage_at_slot_level']
        return _compact(table.get(str(slot or spell['level'])) or table[str(spell['level'])])
    table = part.get('damage_at_character_level') or {}
    chosen = '1'
    for key in sorted(table, key=int):
        if int(key) <= level:
            chosen = key
    return _compact(table.get(chosen) or '1d4')


def _damage_type(spell):
    part = (spell.get('damage') or [{}])[0]
    return (part.get('damage_type') or {}).get('index') or 'force'


def _roll_amount(state, expr, rolls, label):
    counter = state['rng']['counter']
    rolled = roll_expr(expr, state['rng']['seed'], counter, 'none')
    state['rng']['counter'] = counter + 1
    amount = max(0, rolled['total'])
    rolls.append({
        'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
        'expr': expr, 'kept': rolled['kept'], 'label': label, 'mod': rolled['mod'], 'total': amount,
    })
    return amount, rolls[-1]


def _life_bonus(caster, spell, slot):
    if spell['level'] < 1:
        return 0
    if any(feature.get('id') == 'disciple-of-life' for feature in caster.get('features') or []):
        return 2 + slot
    return 0


def _no_heal(creature):
    if creature.get('no_heal_caster'):
        return True
    return creature.get('type') in ('undead', 'construct')


def _heal(creature, amount):
    if creature.get('dead'):
        rule('a dead creature cannot be healed', id=creature.get('id'))
    current = max(0, creature['hp']['current'])
    creature['hp']['current'] = min(creature['hp']['max'], current + max(0, amount))
    if creature['hp']['current'] > 0:
        creature['stable'] = False
        creature['death_saves'] = {'s': 0, 'f': 0}


def _spend_hit_dice(state, char_id, count):
    if count < 1:
        raise EngineError('BAD_ARGS', 'spend at least one hit die')
    character = combat._require_creature(state, char_id)
    if char_id not in (state.get('characters') or {}):
        raise EngineError('INVALID_TARGET', 'only a character has hit dice', {'id': char_id})
    key = f"d{character.get('hit_die') or 8}"
    have = (character.get('hit_dice') or {}).get(key, 0)
    if count > have:
        rule('not enough hit dice', have=have, need=count)
    rolls = []
    healed = 0
    mod = ability_mod(character['abilities']['con'])
    for _ in range(count):
        amount, _record = _roll_amount(state, f'1d{character.get("hit_die") or 8}', rolls, char_id)
        gained = max(0, amount + mod)
        # _roll_amount already added the die total without the modifier.
        rolls[-1]['mod'] = mod
        rolls[-1]['total'] = gained
        healed += gained
    character['hit_dice'][key] = have - count
    _heal(character, healed)
    return {'id': char_id, 'spent': count, 'amount': healed, 'hp': character['hp']['current'], 'rolls': rolls}


def _regain_hit_dice(character):
    level = character.get('level') or 1
    key = f"d{character.get('hit_die') or 8}"
    have = (character.get('hit_dice') or {}).get(key, 0)
    character.setdefault('hit_dice', {})[key] = min(level, have + max(1, level // 2))


def _recharge(character, kind):
    for feature in character.get('features') or []:
        recharge = feature.get('recharge')
        if recharge == kind or (kind == 'long' and recharge == 'short'):
            feature['uses'] = feature.get('max', feature.get('uses', 0))


def _arcane_recovery(character):
    feature = next((row for row in character.get('features') or [] if row.get('id') == 'arcane-recovery'), None)
    if feature is None or feature.get('uses', 0) < 1:
        return []
    budget = (character.get('level', 1) + 1) // 2
    recovered = []
    slots = (character.get('spellcasting') or {}).get('slots') or {}
    for level in range(1, 6):
        row = slots.get(str(level))
        if not row:
            continue
        while row['used'] > 0 and budget >= level:
            row['used'] -= 1
            budget -= level
            recovered.append({'id': character['id'], 'slot': level})
    if recovered:
        feature['uses'] = 0
    return recovered


def _refill_slots(character):
    for row in ((character.get('spellcasting') or {}).get('slots') or {}).values():
        row['used'] = 0


def _range_limit(spell):
    if spell.get('range') == 'Self' and spell.get('area_of_effect'):
        return spell['area_of_effect'].get('size') or 0
    if spell.get('range') == 'Self':
        return 0
    if spell.get('range') == 'Touch':
        return 5
    if spell.get('range') == 'Unlimited':
        return 10 ** 9
    match = re.search(r'\d+', spell.get('range') or '')
    return int(match.group(0)) if match else 0


def _minutes(spell, ritual):
    text = spell.get('casting_time') or ''
    minutes = 10 if ritual else 0
    if text in ('1 action', '1 bonus action', '1 reaction'):
        return minutes
    if text == '1 minute':
        return minutes + 1
    if text == '10 minutes':
        return minutes + 10
    if text == '1 hour':
        return minutes + 60
    return minutes


def _advance_time(state, minutes):
    if minutes <= 0:
        return
    time = state.setdefault('time', {'day': 1, 'minute': 0})
    total = time.get('minute', 0) + minutes
    time['day'] = time.get('day', 1) + total // (24 * 60)
    time['minute'] = total % (24 * 60)


def _names(creature):
    from dnd.chargen import condition_names
    return condition_names(creature)


def _diff(before, after, ids):
    changes = []
    if before.get('combat') != after.get('combat'):
        changes.append({'op': 'set', 'path': 'combat', 'from': before.get('combat'), 'to': after.get('combat')})
    if before.get('time') != after.get('time'):
        changes.append({'op': 'set', 'path': 'time', 'from': before.get('time'), 'to': after.get('time')})
    if before.get('rng') != after.get('rng'):
        changes.append({'op': 'set', 'path': 'rng.counter', 'from': before['rng']['counter'], 'to': after['rng']['counter']})
    seen = set()
    for creature_id in ids:
        if creature_id in seen:
            continue
        seen.add(creature_id)
        if (before.get('characters') or {}).get(creature_id) != (after.get('characters') or {}).get(creature_id):
            if creature_id in (after.get('characters') or {}):
                changes.append({
                    'op': 'set', 'path': f'characters.{creature_id}',
                    'from': (before.get('characters') or {}).get(creature_id),
                    'to': after['characters'][creature_id],
                })
        elif (before.get('monsters') or {}).get(creature_id) != (after.get('monsters') or {}).get(creature_id):
            if creature_id in (after.get('monsters') or {}):
                changes.append({
                    'op': 'set', 'path': f'monsters.{creature_id}',
                    'from': (before.get('monsters') or {}).get(creature_id),
                    'to': after['monsters'][creature_id],
                })
    return changes
