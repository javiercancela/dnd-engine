"""Advance a character from level 1 through 5."""

import copy

from dnd.chargen import (
    ABILITIES,
    PREPARED_CLASSES,
    ability_mod,
    refresh_gear,
    refresh_skills,
    rule,
)
from dnd.dice import roll_expr
from dnd.errors import EngineError
from dnd import srd

XP_LEVEL = (0, 300, 900, 2700, 6500)
SUBCLASS_FEATURES = {
    'primal-path', 'bard-college', 'druid-circle', 'martial-archetype',
    'monastic-tradition', 'sacred-oath', 'ranger-archetype', 'roguish-archetype',
    'arcane-tradition',
}
WIZARD_SPELLS_PER_LEVEL = 2


def level_up(state, char_id, choices, roll_hp, actor):
    choices = choices or {}
    character = (state.get('characters') or {}).get(char_id)
    if character is None:
        raise EngineError('UNKNOWN_ID', f'unknown character {char_id}', {'id': char_id})
    if actor != char_id and not (actor == 'dm' and character.get('player') in ('javi', 'dm')):
        raise EngineError('NOT_AUTHORIZED', 'that is not your character', {'actor': actor, 'id': char_id})
    if character.get('pending_scores') is not None:
        rule('scores are not assigned', id=char_id)
    if character.get('dead'):
        rule('a dead character cannot level up', id=char_id)
    current = character.get('level') or 1
    if current >= 5:
        rule('level 5 is the cap', level=current)
    available = (state.get('progression') or {}).get('available') or _level_for_xp(character.get('xp') or 0)
    if current >= available:
        rule('no level is available', level=current, available=available)
    new_level = current + 1
    counter_before = state['rng']['counter']
    updated = copy.deepcopy(character)
    con_before = ability_mod(updated['abilities']['con'])
    _gain_hp(state, updated, con_before, roll_hp)
    class_index = updated['class']
    row = srd.get('level', f'{class_index}-{new_level}')
    features = [feature['index'] for feature in row.get('features') or []]
    if SUBCLASS_FEATURES.intersection(features) and not updated.get('subclass'):
        updated['subclass'] = _choose_subclass(class_index, choices.get('subclass'))
    _apply_asi(updated, choices.get('asi'), features)
    con_after = ability_mod(updated['abilities']['con'])
    if con_after != con_before:
        delta = (con_after - con_before) * new_level
        updated['hp']['max'] = max(1, updated['hp']['max'] + delta)
        updated['hp']['current'] = max(0, updated['hp']['current'] + delta)
    updated['level'] = new_level
    updated['proficiency_bonus'] = 3 if new_level >= 5 else 2
    _add_hit_die(updated)
    _add_features(updated, class_index, new_level, features)
    _ensure_spellcasting(updated, class_index)
    _update_slots(updated, row)
    _learn_spells(updated, class_index, row, choices)
    _fighting_style(updated, class_index, features, choices.get('fighting_style'))
    _expertise(updated, features, choices.get('expertise'))
    if 'hunters-prey' in features:
        pick = choices.get('hunters_prey')
        if not pick:
            rule('choose a hunter prey option', feature='hunters-prey')
        updated.setdefault('options', {})['hunters_prey'] = pick
    if 'bonus-proficiencies' in features:
        skills = list(choices.get('skills') or [])
        if len(skills) != 3:
            rule('lore grants three skills', got=len(skills))
        for skill in skills:
            updated['proficiencies']['skills'].append(skill)
        updated['proficiencies']['skills'] = sorted(set(updated['proficiencies']['skills']))
    _sync_spell_math(updated)
    specific = row.get('class_specific') or {}
    if specific:
        updated['class_specific'] = specific
        for feature in updated['features']:
            if feature.get('id') == 'sneak-attack' and specific.get('sneak_attack'):
                dice = specific['sneak_attack']
                feature['dice'] = f"{dice.get('dice_count', 1)}d{dice.get('dice_value', 6)}"
            if feature.get('id') == 'second-wind':
                feature['uses'] = feature.get('max', 1)
            if feature.get('id') == 'arcane-recovery':
                feature['uses'] = feature.get('max', 1)
    refresh_gear(updated)
    refresh_skills(updated)
    rolls = updated.pop('_level_rolls', [])
    changes = [{
        'op': 'set', 'path': f'characters.{char_id}',
        'from': character, 'to': updated,
    }]
    if state['rng']['counter'] != counter_before:
        changes.append({
            'op': 'set', 'path': 'rng.counter',
            'from': counter_before, 'to': state['rng']['counter'],
        })
    event = {
        'actor': actor,
        'changes': changes,
        'cmd': f'level up {char_id} --as {actor}',
        'rolls': rolls,
        'type': 'level',
        'outcome': {
            'id': char_id,
            'level': new_level,
            'hp': updated['hp'],
            'features': [feature['id'] for feature in updated['features']],
        },
    }
    return event['outcome'], event


def _gain_hp(state, character, con_mod, roll_hp):
    die = character.get('hit_die') or 8
    if roll_hp:
        counter = state['rng']['counter']
        rolled = roll_expr(f'1d{die}', state['rng']['seed'], counter, 'none')
        state['rng']['counter'] = counter + 1
        gain = max(1, rolled['total'] + con_mod)
        character.setdefault('_level_rolls', []).append({
            'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
            'expr': f'1d{die}', 'kept': rolled['kept'], 'label': 'hp', 'mod': con_mod, 'total': gain,
        })
    else:
        gain = max(1, die // 2 + 1 + con_mod)
    if 'dwarven-toughness' in (character.get('traits') or []):
        gain += 1
    character['hp']['max'] += gain
    character['hp']['current'] += gain


def _choose_subclass(class_index, requested):
    class_record = srd.get('class', class_index)
    only = class_record['subclasses'][0]['index']
    if requested in (None, only):
        return only
    rule('subclass is not the SRD subclass for this class', subclass=requested)


def _apply_asi(character, asi, features):
    needed = any('ability-score-improvement' in feature for feature in features)
    if not needed:
        if asi:
            rule('this level has no ability score improvement')
        return
    if not isinstance(asi, dict) or not asi:
        rule('choose an ability score improvement', example={'str': 2})
    total = 0
    for ability, amount in asi.items():
        if ability not in ABILITIES or amount not in (1, 2) or isinstance(amount, bool):
            rule('ability increases are 1 or 2', ability=ability, amount=amount)
        total += amount
        if character['abilities'][ability] + amount > 20:
            rule('an ability score cannot go above 20', ability=ability)
    if total != 2:
        rule('an ability score improvement adds 2 points', total=total)
    for ability, amount in asi.items():
        character['abilities'][ability] += amount
    character['modifiers'] = {ability: ability_mod(character['abilities'][ability]) for ability in ABILITIES}


def _add_hit_die(character):
    key = f"d{character.get('hit_die') or 8}"
    dice = character.setdefault('hit_dice', {})
    dice[key] = dice.get(key, 0) + 1


def _add_features(character, class_index, new_level, features):
    have = {feature.get('id') for feature in character.get('features') or []}
    for feature_id in features:
        if feature_id in have:
            continue
        entry = {'id': feature_id}
        if feature_id == 'action-surge-1-use':
            entry.update({'uses': 1, 'max': 1, 'recharge': 'short'})
        if feature_id == 'ki':
            entry.update({'uses': new_level, 'max': new_level, 'recharge': 'short'})
        if 'channel-divinity' in feature_id and feature_id.startswith('channel-divinity'):
            entry.update({'uses': 1, 'max': 1, 'recharge': 'short'})
        character['features'].append(entry)
        have.add(feature_id)
    if character.get('subclass'):
        try:
            sub_row = srd.get('level', f"{character['subclass']}-{new_level}")
        except KeyError:
            sub_row = None
        for feature in (sub_row or {}).get('features') or []:
            if feature['index'] in have:
                continue
            character['features'].append({'id': feature['index']})
            have.add(feature['index'])
    _add_domain_spells(character, new_level)


def _add_domain_spells(character, new_level):
    if not character.get('subclass') or not character.get('spellcasting'):
        return
    try:
        subclass = srd.get('subclass', character['subclass'])
    except KeyError:
        return
    domain = list(character['spellcasting'].get('domain') or [])
    for entry in subclass.get('spells') or []:
        prereq = entry.get('prerequisites') or []
        levels = []
        for item in prereq:
            index = item.get('index') or ''
            if index.rsplit('-', 1)[-1].isdigit():
                levels.append(int(index.rsplit('-', 1)[-1]))
        if levels and min(levels) > new_level:
            continue
        if not levels and new_level < 1:
            continue
        spell = (entry.get('spell') or {}).get('index')
        if spell and spell not in domain:
            domain.append(spell)
    character['spellcasting']['domain'] = sorted(domain)


def _ensure_spellcasting(character, class_index):
    if character.get('spellcasting'):
        return
    class_record = srd.get('class', class_index)
    ability = ((class_record.get('spellcasting') or {}).get('spellcasting_ability') or {}).get('index')
    if not ability:
        return
    character['spellcasting'] = {
        'ability': ability,
        'dc': None,
        'attack': None,
        'slots': {},
        'cantrips': [],
        'known': [],
        'prepared': [],
        'domain': [],
        'concentration': None,
    }


def _update_slots(character, row):
    casting = character.get('spellcasting')
    info = row.get('spellcasting') or {}
    if not casting:
        return
    if not any(info.get(f'spell_slots_level_{level}', 0) for level in range(1, 10)) and not info.get('cantrips_known') and not info.get('spells_known'):
        return
    slots = {}
    for level in range(1, 10):
        count = info.get(f'spell_slots_level_{level}', 0)
        if not count:
            continue
        used = (casting.get('slots') or {}).get(str(level), {}).get('used', 0)
        slots[str(level)] = {'max': count, 'used': min(used, count)}
    casting['slots'] = slots


def _learn_spells(character, class_index, row, choices):
    casting = character.get('spellcasting')
    if not casting:
        if choices.get('spells') or choices.get('cantrips'):
            rule('this class learns no spells')
        return
    info = row.get('spellcasting') or {}
    extra = 1 if 'high-elf-cantrip' in (character.get('traits') or []) else 0
    cantrip_need = (info.get('cantrips_known') or 0) + extra
    new_cantrips = list(choices.get('cantrips') or [])
    if len(casting.get('cantrips') or []) + len(new_cantrips) != cantrip_need:
        rule('wrong number of new cantrips', need=cantrip_need - len(casting.get('cantrips') or []), got=len(new_cantrips))
    for index in new_cantrips:
        spell = _spell(index)
        if spell['level'] != 0 or not _on_list(spell, class_index, character):
            rule('cantrip is not on the class list', spell=index)
        if index in casting['cantrips']:
            rule('cantrip is already known', spell=index)
        casting['cantrips'].append(index)
    casting['cantrips'] = sorted(casting['cantrips'])
    if class_index == 'wizard':
        need = WIZARD_SPELLS_PER_LEVEL
    elif 'spells_known' in info:
        need = info['spells_known'] - len(casting.get('known') or [])
    else:
        need = 0
    new_spells = list(choices.get('spells') or [])
    if len(new_spells) != need:
        rule('wrong number of new spells', need=need, got=len(new_spells))
    highest = max((int(level) for level in (casting.get('slots') or {})), default=0)
    for index in new_spells:
        spell = _spell(index)
        if spell['level'] < 1 or spell['level'] > highest or not _on_list(spell, class_index, character):
            rule('spell is not a class spell you can cast', spell=index)
        if index in casting['known']:
            rule('spell is already known', spell=index)
        casting['known'].append(index)
    casting['known'] = sorted(casting['known'])
    if choices.get('prepared') is not None:
        if class_index not in PREPARED_CLASSES:
            rule('this class does not prepare spells', class_index=class_index)
        if not isinstance(choices['prepared'], list):
            rule('prepared spells must be a list')
        prepared = list(choices['prepared'])
        if any(not isinstance(index, str) for index in prepared):
            rule('prepared spell ids must be strings')
        limit = _prepared_limit(character)
        domain = set(casting.get('domain') or [])
        chosen = [index for index in prepared if index not in domain]
        if len(chosen) > limit:
            rule('too many prepared spells', limit=limit, got=len(chosen))
        if len(set(prepared)) != len(prepared):
            rule('prepared spells must be distinct')
        for index in chosen:
            spell = _spell(index)
            if class_index == 'wizard' and index not in casting['known']:
                rule('prepared spell is not in the spellbook', spell=index)
            if spell['level'] < 1 or spell['level'] > highest:
                rule('prepared spell is too high', spell=index)
            if not _on_list(spell, class_index, character):
                rule('prepared spell is not on the class list', spell=index)
        casting['prepared'] = sorted(set(prepared))


def _prepared_limit(character):
    casting = character['spellcasting']
    mod = ability_mod(character['abilities'][casting['ability']])
    if character['class'] == 'paladin':
        return max(1, character['level'] // 2 + mod)
    return max(1, mod + character['level'])


def _fighting_style(character, class_index, features, requested):
    feature_id = next((feature for feature in features if feature.endswith('fighting-style')), None)
    if feature_id is None:
        if requested:
            rule('this level has no fighting style')
        return
    if any(str(feature.get('id', '')).endswith('fighting-style') and feature.get('id') != feature_id for feature in character['features']):
        return
    feature = srd.get('feature', feature_id)
    options = feature.get('feature_specific', {}).get('subfeature_options', {}).get('from', {}).get('options') or []
    allowed = [option['item']['index'] for option in options]
    if requested not in allowed:
        prefixed = f'{feature_id}-{requested}' if requested else None
        if prefixed in allowed:
            requested = prefixed
        else:
            rule('fighting style is not in the SRD', fighting_style=requested)
    character['fighting_style'] = requested
    character['proficiencies'].setdefault('weapons', [])
    if requested not in character['proficiencies'].get('weapons', []):
        character['proficiencies'].setdefault('other', [])
        character['proficiencies']['other'].append(requested)


def _expertise(character, features, requested):
    if not any(feature.startswith('bard-expertise') or feature == 'rogue-expertise-1' for feature in features):
        if requested and character['class'] != 'rogue':
            rule('this level grants no expertise')
        return
    if character['class'] == 'rogue' and character.get('expertise'):
        return
    chosen = list(requested or [])
    if len(chosen) != 2 or len(set(chosen)) != 2:
        rule('choose two expertise skills')
    skills = set(character['proficiencies'].get('skills') or [])
    for skill in chosen:
        if skill not in skills and skill != 'thieves-tools':
            rule('expertise requires a proficient skill', skill=skill)
        if skill in (character.get('expertise') or []):
            rule('expertise is already chosen', skill=skill)
    character['expertise'] = sorted(set(character.get('expertise') or []).union(chosen))


def _sync_spell_math(character):
    casting = character.get('spellcasting')
    if not casting or not casting.get('ability'):
        return
    bonus = ability_mod(character['abilities'][casting['ability']])
    casting['dc'] = 8 + character['proficiency_bonus'] + bonus
    casting['attack'] = character['proficiency_bonus'] + bonus


def _on_list(spell, class_index, character):
    classes = {class_index}
    if 'high-elf-cantrip' in (character.get('traits') or []) and spell['level'] == 0:
        classes.add('wizard')
    return any(entry['index'] in classes for entry in spell.get('classes') or [])


def _spell(index):
    try:
        return srd.get('spell', index)
    except KeyError:
        raise EngineError('UNKNOWN_ID', f'unknown spell {index}', {'spell': index}) from None


def _level_for_xp(xp):
    level = 1
    for index, need in enumerate(XP_LEVEL, start=1):
        if xp >= need:
            level = index
    return level
