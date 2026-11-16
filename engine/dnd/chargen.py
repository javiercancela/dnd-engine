"""Level-1 SRD character creation and derived statistics."""

from collections import Counter

from dnd.errors import EngineError
from dnd import srd

ABILITIES = ('str', 'dex', 'con', 'int', 'wis', 'cha')
STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
POINT_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUDGET = 27
WIZARD_SPELLBOOK_AT_1 = 6
PREPARED_CLASSES = ('cleric', 'druid', 'wizard')
ARMOR_PROFS = ('light-armor', 'medium-armor', 'heavy-armor', 'shields', 'all-armor')
_WEAPON_PROF = {
    'battleaxe': 'battleaxes',
    'crossbow-hand': 'hand-crossbows',
    'crossbow-light': 'crossbows-light',
    'dagger': 'daggers',
    'dart': 'darts',
    'handaxe': 'handaxes',
    'light-hammer': 'light-hammers',
    'longbow': 'longbows',
    'longsword': 'longswords',
    'quarterstaff': 'quarterstaffs',
    'rapier': 'rapiers',
    'shortbow': 'shortbows',
    'shortsword': 'shortswords',
    'sling': 'slings',
    'warhammer': 'warhammers',
}


def rule(message, **details):
    raise EngineError('RULE_VIOLATION', message, details)


def ability_mod(score):
    return (score - 10) // 2


def compile_character(data, base_scores):
    """Build a level-1 sheet from a create document and base ability scores."""
    _require_keys(data, ('id', 'name', 'player', 'race', 'class', 'background', 'skills'))
    char_id = _slug(data['id'], 'id')
    name = data['name']
    if not isinstance(name, str) or not name.strip():
        rule('name is required')
    player = data['player']
    if player not in ('javi', 'dm') and not (
        isinstance(player, str) and player.startswith('bot:') and len(player) > 4
    ):
        rule('player must be javi, dm, or bot:<name>', player=player)
    if data.get('level', 1) != 1:
        rule('char create builds a level 1 character', level=data.get('level'))
    if data.get('background') != 'acolyte':
        rule('only the acolyte background is in the SRD', background=data.get('background'))

    base_race, subrace = _resolve_race(data['race'])
    class_record = _get('class', data['class'], 'class')
    background = _get('background', 'acolyte', 'background')
    subclass_index = _resolve_subclass(class_record, data.get('subclass'))
    scores = _base_scores(data, base_scores)
    bonuses = _ability_bonuses(data, base_race, subrace)
    final = {ability: scores[ability] + bonuses.get(ability, 0) for ability in ABILITIES}

    traits = [trait['index'] for trait in base_race.get('traits') or []]
    if subrace:
        traits.extend(trait['index'] for trait in subrace.get('racial_traits') or [])
    profs = _collect_profs(class_record, background, base_race, subrace, subclass_index, data, traits)
    fighting_style = _fighting_style(class_record['index'], data.get('fighting_style'))
    if fighting_style:
        profs.add(fighting_style)

    skills = _skills(data, class_record, background, traits, profs)
    expertise = _expertise(class_record['index'], data.get('expertise'), skills, profs)
    languages = _languages(data, base_race, background, traits)
    spellcasting = _spellcasting(
        class_record, subclass_index, final, data.get('spells_known') or [],
        data.get('spells_prepared') or [], traits,
    )
    equipment = _default_equipment(class_record, background, profs)
    features = _features(class_record['index'], subclass_index)

    character = {
        'id': char_id,
        'name': name.strip(),
        'player': player,
        'race': data['race'],
        'base_race': base_race['index'],
        'subrace': subrace['index'] if subrace else None,
        'class': class_record['index'],
        'subclass': subclass_index,
        'background': 'acolyte',
        'level': 1,
        'xp': 0,
        'abilities': final,
        'base_abilities': scores,
        'modifiers': {ability: ability_mod(final[ability]) for ability in ABILITIES},
        'proficiency_bonus': 2,
        'hp': {'max': 0, 'current': 0, 'temp': 0},
        'hit_dice': {f'd{class_record["hit_die"]}': 1},
        'speed': base_race.get('speed') or 30,
        'base_speed': base_race.get('speed') or 30,
        'proficiencies': _bucket_profs(profs),
        'expertise': expertise,
        'languages': languages,
        'fighting_style': fighting_style,
        'spellcasting': spellcasting,
        'features': features,
        'traits': traits,
        'conditions': [],
        'exhaustion': 0,
        'death_saves': {'s': 0, 'f': 0},
        'inventory': equipment,
        'coins': {'cp': 0, 'sp': 0, 'ep': 0, 'gp': _background_gold(background), 'pp': 0},
        'stealth_disadvantage': False,
    }
    character['proficiencies']['skills'] = sorted(skills)
    _apply_hp(character, class_record['hit_die'], traits)
    refresh_gear(character)
    refresh_skills(character)
    return character


def refresh_gear(character):
    """Recompute AC, attacks, and armor notes from inventory."""
    character['ac'] = _ac(character)
    character['attacks'] = _attacks(character)
    character['stealth_disadvantage'] = _stealth_disadvantage(character)
    _apply_speed(character)
    return character


def refresh_skills(character):
    skills = {}
    for skill in character['proficiencies']['skills']:
        skills[skill] = skill_bonus(character, skill)
    character['skills'] = skills
    character['saves'] = {
        ability: save_bonus(character, ability) for ability in ABILITIES
    }
    perception = skill_bonus(character, 'perception')
    adjustment = _passive_adjustment(character, 'perception')
    character['passive_perception'] = 10 + perception + adjustment
    return character


def skill_bonus(character, skill):
    info = srd.get('skill', skill)
    ability = info['ability_score']['index']
    bonus = ability_mod(character['abilities'][ability])
    if skill in character.get('expertise') or []:
        bonus += 2 * character['proficiency_bonus']
    elif skill in character['proficiencies']['skills']:
        bonus += character['proficiency_bonus']
    return bonus


def save_bonus(character, ability):
    bonus = ability_mod(character['abilities'][ability])
    if ability in character['proficiencies']['saves']:
        bonus += character['proficiency_bonus']
    return bonus


def auto_fail_save(character, ability):
    conditions = set(character.get('conditions') or [])
    if ability in ('str', 'dex') and conditions.intersection(('paralyzed', 'stunned', 'unconscious')):
        return True
    return False


def advantage_state(character, kind, skill, flag_adv, flag_dis, help_from):
    """Return (mode, sources, warnings) for a d20 roll.

    ``kind`` is ``check`` or ``save``. Any advantage mixed with any
    disadvantage cancels to a normal roll.
    """
    adv = []
    dis = []
    if flag_adv:
        adv.append('flag:adv')
    if flag_dis:
        dis.append('flag:dis')
    if help_from and kind == 'check':
        adv.append(f'help:{help_from}')
    conditions = set(character.get('conditions') or [])
    exhaustion = character.get('exhaustion') or 0
    if kind == 'check':
        if conditions.intersection(('poisoned', 'frightened')) or exhaustion >= 1:
            if 'poisoned' in conditions:
                dis.append('poisoned')
            if 'frightened' in conditions:
                dis.append('frightened')
            if exhaustion >= 1:
                dis.append('exhaustion')
        if skill == 'stealth' and character.get('stealth_disadvantage'):
            dis.append('armor')
    if kind == 'save':
        if exhaustion >= 3:
            dis.append('exhaustion')
        if 'restrained' in conditions and skill == 'dex':
            dis.append('restrained')
    warnings = []
    if adv and dis:
        warnings.append('advantage and disadvantage cancel')
        mode = 'none'
    elif adv:
        mode = 'adv'
    elif dis:
        mode = 'dis'
    else:
        mode = 'none'
    return mode, adv + dis, warnings


def _apply_hp(character, hit_die, traits):
    bonus = ability_mod(character['abilities']['con'])
    total = hit_die + bonus
    if 'dwarven-toughness' in traits:
        total += 1
    total = max(1, total)
    character['hp'] = {'max': total, 'current': total, 'temp': 0}


def _apply_speed(character):
    speed = character.get('base_speed') or 30
    body = _equipped_item(character, 'armor')
    if body and _item(body).get('armor_category') == 'Heavy':
        minimum = _item(body).get('str_minimum') or 0
        if character['abilities']['str'] < minimum and character.get('base_race') != 'dwarf':
            speed -= 10
    character['speed'] = speed


def _ac(character):
    dex = ability_mod(character['abilities']['dex'])
    body_id = _equipped_item(character, 'armor')
    shield_id = _equipped_item(character, 'shield')
    if body_id is None:
        if character['class'] == 'barbarian':
            ac = 10 + dex + ability_mod(character['abilities']['con'])
        elif character['class'] == 'monk' and shield_id is None:
            ac = 10 + dex + ability_mod(character['abilities']['wis'])
        else:
            ac = 10 + dex
    else:
        spec = _item(body_id)['armor_class']
        ac = spec['base']
        if spec.get('dex_bonus'):
            bonus = dex
            if 'max_bonus' in spec:
                bonus = min(bonus, spec['max_bonus'])
            ac += bonus
    if shield_id:
        ac += _item(shield_id)['armor_class']['base']
    if body_id and character.get('fighting_style') == 'fighter-fighting-style-defense':
        ac += 1
    return ac


def _attacks(character):
    seen = set()
    attacks = []
    for entry in character.get('inventory') or []:
        item_id = entry['item']
        if item_id in seen:
            continue
        try:
            item = srd.get('equipment', item_id)
        except KeyError:
            continue
        if 'damage' not in item:
            continue
        seen.add(item_id)
        props = [prop['index'] for prop in item.get('properties') or []]
        ranged = item.get('weapon_range') == 'Ranged'
        strength = ability_mod(character['abilities']['str'])
        dexterity = ability_mod(character['abilities']['dex'])
        if ranged:
            ability = 'dex'
            score_mod = dexterity
        elif 'finesse' in props and dexterity > strength:
            ability = 'dex'
            score_mod = dexterity
        else:
            ability = 'str'
            score_mod = strength
        proficient = _weapon_proficient(item, character['proficiencies'])
        bonus = score_mod + (character['proficiency_bonus'] if proficient else 0)
        if ranged and character.get('fighting_style') == 'fighter-fighting-style-archery':
            bonus += 2
        damage_mod = score_mod
        if _dueling(character, item, props, entry.get('equipped')):
            damage_mod += 2
        normal = item['range']['normal']
        long = item['range'].get('long')
        attacks.append({
            'weapon': item_id,
            'bonus': bonus,
            'damage': _damage_text(item['damage']['damage_dice'], damage_mod),
            'damage_type': item['damage']['damage_type']['index'],
            'ability': ability,
            'proficient': proficient,
            'equipped': entry.get('equipped'),
            'range': f'{normal}/{long}' if long else 'melee',
        })
    attacks.sort(key=lambda attack: (attack['equipped'] is None, attack['weapon']))
    return attacks


def _dueling(character, item, props, equipped):
    if character.get('fighting_style') != 'fighter-fighting-style-dueling':
        return False
    if equipped != 'main' or item.get('weapon_range') == 'Ranged' or 'two-handed' in props:
        return False
    return _equipped_item(character, 'off') is None


def _damage_text(dice, mod):
    if mod > 0:
        return f'{dice}+{mod}'
    if mod < 0:
        return f'{dice}{mod}'
    return dice


def _stealth_disadvantage(character):
    for slot in ('armor', 'shield'):
        item_id = _equipped_item(character, slot)
        if item_id and _item(item_id).get('stealth_disadvantage'):
            return True
    return False


def _passive_adjustment(character, skill):
    mode, _sources, _warnings = advantage_state(character, 'check', skill, False, False, None)
    if mode == 'adv':
        return 5
    if mode == 'dis':
        return -5
    return 0


def _equipped_item(character, slot):
    for entry in character.get('inventory') or []:
        if entry.get('equipped') == slot:
            return entry['item']
    return None


def _item(index):
    return srd.get('equipment', index)


def _weapon_proficient(item, profs):
    weapons = set(profs.get('weapons') or [])
    index = item['index']
    if index in weapons or _WEAPON_PROF.get(index) in weapons:
        return True
    category = item.get('weapon_category')
    if category == 'Simple' and 'simple-weapons' in weapons:
        return True
    if category == 'Martial' and 'martial-weapons' in weapons:
        return True
    return False


def _base_scores(data, base_scores):
    if base_scores is None:
        rule('ability scores are not assigned')
    scores = {}
    for ability in ABILITIES:
        value = base_scores.get(ability)
        if not isinstance(value, int):
            rule(f'{ability} score is missing')
        scores[ability] = value
    method = (data.get('abilities') or {}).get('method')
    if method == 'standard-array':
        if sorted(scores.values(), reverse=True) != list(STANDARD_ARRAY):
            rule('standard array must be 15, 14, 13, 12, 10, 8', scores=scores)
    elif method == 'point-buy':
        _check_point_buy(scores)
    elif method == 'rolled':
        pass
    else:
        rule('ability method must be standard-array, point-buy, or rolled', method=method)
    return scores


def _check_point_buy(scores):
    spent = 0
    for ability, score in scores.items():
        if score not in POINT_COST:
            rule('point buy scores must be from 8 to 15', ability=ability, score=score)
        spent += POINT_COST[score]
    if spent != POINT_BUDGET:
        rule('point buy must spend 27 points', spent=spent)


def _ability_bonuses(data, base_race, subrace):
    bonuses = {}
    for record in (base_race, subrace):
        if not record:
            continue
        for entry in record.get('ability_bonuses') or []:
            ability = entry['ability_score']['index']
            bonuses[ability] = bonuses.get(ability, 0) + entry['bonus']
    options = base_race.get('ability_bonus_options')
    if options:
        chosen = data.get('ability_increases') or []
        if len(chosen) != options['choose'] or len(set(chosen)) != len(chosen):
            rule('choose ability increases from the race list', choose=options['choose'])
        allowed = {
            option['ability_score']['index']
            for option in options['from']['options']
        }
        for ability in chosen:
            if ability not in allowed:
                rule('ability increase is not allowed for this race', ability=ability)
            bonuses[ability] = bonuses.get(ability, 0) + 1
    elif data.get('ability_increases'):
        rule('this race does not choose ability increases')
    return bonuses


def _resolve_race(index):
    try:
        subrace = srd.get('subrace', index)
    except KeyError:
        subrace = None
    else:
        return _get('race', subrace['race']['index'], 'race'), subrace
    try:
        base = srd.get('race', index)
    except KeyError:
        rule('race is not in the SRD', race=index)
    if base.get('subraces'):
        rule(f'{index} needs a subrace', race=index)
    return base, None


def _resolve_subclass(class_record, requested):
    only = class_record['subclasses'][0]['index']
    try:
        srd.get('level', f'{only}-1')
    except KeyError:
        if requested:
            rule('this class does not choose a subclass at level 1', subclass=requested)
        return None
    if requested in (None, only):
        return only
    rule('subclass is not the SRD subclass for this class', subclass=requested)


def _fighting_style(class_index, requested):
    if class_index != 'fighter':
        if requested:
            rule('only a fighter chooses a fighting style at level 1')
        return None
    feature = srd.get('feature', 'fighter-fighting-style')
    options = feature['feature_specific']['subfeature_options']['from']['options']
    allowed = [option['item']['index'] for option in options]
    if requested is None:
        rule('a fighter chooses a fighting style')
    index = requested
    if index not in allowed:
        prefixed = f'fighter-fighting-style-{requested}'
        index = prefixed if prefixed in allowed else None
    if index is None:
        rule('fighting style is not in the SRD', fighting_style=requested)
    return index


def _collect_profs(class_record, background, base_race, subrace, subclass_index, data, traits):
    profs = {entry['index'] for entry in class_record.get('proficiencies') or []}
    profs.update(entry['index'] for entry in background.get('starting_proficiencies') or [])
    for trait_id in traits:
        trait = srd.get('trait', trait_id)
        profs.update(entry['index'] for entry in trait.get('proficiencies') or [])
    if subclass_index == 'life':
        profs.add('heavy-armor')
    if 'tool-proficiency' in traits:
        tool = data.get('tool')
        allowed = _choice_indexes(srd.get('trait', 'tool-proficiency')['proficiency_choices'])
        if tool not in allowed:
            rule('choose a dwarf tool proficiency', tool=tool)
        profs.add(tool)
    elif data.get('tool'):
        rule('this race does not choose a tool')
    return profs


def _skills(data, class_record, background, traits, profs):
    granted = {entry['index'].split('-', 1)[1] for entry in background.get('starting_proficiencies') or []}
    for trait_id in traits:
        trait = srd.get('trait', trait_id)
        for entry in trait.get('proficiencies') or []:
            if entry['index'].startswith('skill-'):
                granted.add(entry['index'].split('-', 1)[1])
    choice = next(
        (entry for entry in class_record.get('proficiency_choices') or [] if _is_skill_choice(entry)),
        None,
    )
    chosen = list(data.get('skills') or [])
    if len(chosen) != len(set(chosen)):
        rule('duplicate skill choice')
    allowed = set(_choice_indexes(choice)) if choice else set()
    allowed = {index.split('-', 1)[1] if index.startswith('skill-') else index for index in allowed}
    need = choice['choose'] if choice else 0
    if len(chosen) != need:
        rule('wrong number of class skills', need=need, got=len(chosen))
    for skill in chosen:
        _get('skill', skill, 'skill')
        if skill not in allowed:
            rule('skill is not on the class list', skill=skill)
        if skill in granted:
            rule('skill is already granted', skill=skill)
    bonus = list(data.get('bonus_skills') or [])
    if 'skill-versatility' in traits:
        if len(bonus) != 2 or len(set(bonus)) != 2:
            rule('choose two bonus skills')
        for skill in bonus:
            _get('skill', skill, 'skill')
            if skill in granted or skill in chosen:
                rule('bonus skill is already known', skill=skill)
    elif bonus:
        rule('this race does not choose bonus skills')
    skills = granted.union(chosen).union(bonus)
    for skill in skills:
        profs.add(f'skill-{skill}')
    return skills


def _expertise(class_index, requested, skills, profs):
    if class_index != 'rogue':
        if requested:
            rule('only a rogue chooses expertise at level 1')
        return []
    chosen = list(requested or [])
    if len(chosen) != 2 or len(set(chosen)) != 2:
        rule('choose two expertise proficiencies')
    for entry in chosen:
        if entry == 'thieves-tools':
            if 'thieves-tools' not in profs:
                rule('expertise requires thieves\' tools proficiency')
            continue
        if entry not in skills:
            rule('expertise requires a skill proficiency', skill=entry)
    return chosen


def _languages(data, base_race, background, traits):
    known = [entry['index'] for entry in base_race.get('languages') or []]
    needed = 0
    if base_race.get('language_options'):
        needed += base_race['language_options']['choose']
    if background.get('language_options'):
        needed += background['language_options']['choose']
    if 'extra-language' in traits:
        needed += 1
    chosen = list(data.get('languages') or [])
    if len(chosen) != needed or len(set(chosen)) != len(chosen):
        rule('wrong number of languages', need=needed, got=len(chosen))
    for language in chosen:
        _get('language', language, 'language')
        if language in known:
            rule('language is already known', language=language)
    return sorted(set(known).union(chosen))


def _spellcasting(class_record, subclass_index, abilities, known, prepared, traits):
    class_index = class_record['index']
    level_row = srd.get('level', f'{class_index}-1')
    slot_info = level_row.get('spellcasting') or {}
    if not _casts(slot_info) and 'high-elf-cantrip' not in traits:
        if known or prepared:
            rule('this class has no spells at level 1')
        return None
    ability = None
    spellcasting = class_record.get('spellcasting') or {}
    if spellcasting.get('spellcasting_ability'):
        ability = spellcasting['spellcasting_ability']['index']
    cantrips = []
    leveled = []
    for index in known:
        spell = _get('spell', index, 'spell')
        (cantrips if spell['level'] == 0 else leveled).append(index)
    extra = 1 if 'high-elf-cantrip' in traits else 0
    class_cantrips = slot_info.get('cantrips_known', 0)
    if len(cantrips) != class_cantrips + extra:
        rule(
            'wrong number of cantrips',
            need=class_cantrips + extra,
            got=len(cantrips),
        )
    lists = _class_lists(class_index, traits)
    for index in cantrips:
        spell = srd.get('spell', index)
        if spell['level'] != 0 or not _on_list(spell, lists['cantrip']):
            rule('cantrip is not on the class list', spell=index)
    if class_index == 'wizard':
        expected = WIZARD_SPELLBOOK_AT_1
    elif 'spells_known' in slot_info:
        expected = slot_info['spells_known']
    else:
        expected = 0
    if len(leveled) != expected:
        rule('wrong number of spells known', need=expected, got=len(leveled))
    for index in leveled:
        spell = srd.get('spell', index)
        if spell['level'] != 1 or not _on_list(spell, lists['leveled']):
            rule('spell is not a 1st-level class spell', spell=index)
    domain = _domain_spells(subclass_index, class_index) if subclass_index else []
    if class_index in PREPARED_CLASSES and _casts(slot_info):
        mod = ability_mod(abilities[ability]) if ability else 0
        limit = max(1, mod + 1)
        chosen = [index for index in prepared if index not in domain]
        if len(chosen) != limit or len(set(prepared)) != len(prepared):
            rule('wrong number of prepared spells', need=limit, got=len(chosen))
        pool = set(leveled) if class_index == 'wizard' else None
        for index in chosen:
            spell = _get('spell', index, 'spell')
            if pool is not None and index not in pool:
                rule('prepared spell is not in the spellbook', spell=index)
            if pool is None and (spell['level'] != 1 or not _on_list(spell, lists['leveled'])):
                rule('prepared spell is not a 1st-level class spell', spell=index)
    elif prepared:
        rule('this class does not prepare spells at level 1')
    if not ability:
        return {
            'ability': None,
            'dc': None,
            'attack': None,
            'slots': {},
            'cantrips': sorted(cantrips),
            'known': sorted(leveled),
            'prepared': [],
            'domain': [],
            'concentration': None,
        }
    bonus = ability_mod(abilities[ability])
    slots = {}
    for slot_level in range(1, 10):
        count = slot_info.get(f'spell_slots_level_{slot_level}', 0)
        if count:
            slots[str(slot_level)] = {'max': count, 'used': 0}
    return {
        'ability': ability,
        'dc': 8 + 2 + bonus,
        'attack': 2 + bonus,
        'slots': slots,
        'cantrips': sorted(cantrips),
        'known': sorted(leveled),
        'prepared': sorted(prepared),
        'domain': sorted(domain),
        'concentration': None,
    }


def _casts(slot_info):
    if slot_info.get('cantrips_known') or slot_info.get('spells_known'):
        return True
    return any(slot_info.get(f'spell_slots_level_{level}', 0) for level in range(1, 10))


def _class_lists(class_index, traits):
    cantrip = {class_index}
    leveled = {class_index}
    if 'high-elf-cantrip' in traits:
        cantrip.add('wizard')
    return {'cantrip': cantrip, 'leveled': leveled}


def _on_list(spell, classes):
    return any(entry['index'] in classes for entry in spell.get('classes') or [])


def _domain_spells(subclass_index, class_index):
    try:
        subclass = srd.get('subclass', subclass_index)
    except KeyError:
        return []
    found = []
    for entry in subclass.get('spells') or []:
        for prereq in entry.get('prerequisites') or []:
            if prereq.get('type') == 'level' and prereq.get('index') == f'{class_index}-1':
                found.append(entry['spell']['index'])
    return found


def _default_equipment(class_record, background, profs):
    pairs = []
    for entry in class_record.get('starting_equipment') or []:
        pairs.append((entry['equipment']['index'], entry.get('quantity', 1)))
    pairs.extend(_options(class_record.get('starting_equipment_options') or [], profs))
    for entry in background.get('starting_equipment') or []:
        pairs.append((entry['equipment']['index'], entry.get('quantity', 1)))
    pairs.extend(_options(background.get('starting_equipment_options') or [], profs))
    expanded = []
    for index, qty in pairs:
        item = _get('equipment', index, 'equipment')
        if item.get('contents'):
            for content in item['contents']:
                expanded.append((content['item']['index'], content.get('quantity', 1) * qty))
        else:
            expanded.append((index, qty))
    inventory = []
    for index, qty in expanded:
        found = next((entry for entry in inventory if entry['item'] == index), None)
        if found:
            found['qty'] += qty
        else:
            inventory.append({'item': index, 'qty': qty, 'equipped': None})
    _equip_defaults(inventory)
    return inventory


def _equip_defaults(inventory):
    armor = None
    shield = None
    melee = []
    for entry in inventory:
        try:
            item = srd.get('equipment', entry['item'])
        except KeyError:
            continue
        category = item.get('armor_category')
        if category in ('Light', 'Medium', 'Heavy') and armor is None:
            armor = entry
        elif category == 'Shield' and shield is None:
            shield = entry
        elif item.get('weapon_range') == 'Melee':
            melee.append((entry, item))
    if armor:
        armor['equipped'] = 'armor'
    if shield:
        shield['equipped'] = 'shield'
    melee.sort(key=lambda pair: ('light' in _props(pair[1]), pair[0]['item']))
    for entry, item in melee:
        props = _props(item)
        if 'two-handed' in props and shield is not None:
            continue
        entry['equipped'] = 'main'
        break


def _options(choices, profs):
    found = []
    for choice in choices:
        found.extend(_resolve_choice(choice, profs) or [])
    return found


def _resolve_choice(choice, profs):
    source = choice.get('from') or {}
    kind = source.get('option_set_type')
    count = choice.get('choose', 1)
    if kind == 'equipment_category':
        indexes = [
            entry['index']
            for entry in srd.get('equipment-category', source['equipment_category']['index'])['equipment']
        ]
        return [(indexes[index], 1) for index in range(count)]
    if kind == 'options_array':
        for option in source['options']:
            resolved = _resolve_grant(option, profs)
            if resolved is not None:
                return resolved
    return []


def _resolve_grant(node, profs):
    kind = node.get('option_type')
    if kind == 'counted_reference':
        if not _prereq_ok(node, profs):
            return None
        return [(node['of']['index'], node.get('count', 1))]
    if kind == 'multiple':
        found = []
        for item in node['items']:
            part = _resolve_grant(item, profs)
            if part is None:
                return None
            found.extend(part)
        return found
    if kind == 'choice':
        return _resolve_choice(node['choice'], profs)
    return None


def _prereq_ok(node, profs):
    for prereq in node.get('prerequisites') or []:
        if prereq.get('type') == 'proficiency' and prereq['proficiency']['index'] not in profs:
            return False
    return True


def _features(class_index, subclass_index):
    level_row = srd.get('level', f'{class_index}-1')
    indexes = [feature['index'] for feature in level_row.get('features') or []]
    if subclass_index:
        try:
            sub_level = srd.get('level', f'{subclass_index}-1')
        except KeyError:
            sub_level = None
        if sub_level:
            indexes.extend(feature['index'] for feature in sub_level.get('features') or [])
    entries = []
    for index in indexes:
        entry = {'id': index}
        if index == 'second-wind':
            entry.update({'uses': 1, 'max': 1, 'recharge': 'short'})
        elif index == 'arcane-recovery':
            entry.update({'uses': 1, 'max': 1, 'recharge': 'long'})
        elif index == 'sneak-attack':
            spec = (level_row.get('class_specific') or {}).get('sneak_attack') or {}
            entry['dice'] = f"{spec.get('dice_count', 1)}d{spec.get('dice_value', 6)}"
        entries.append(entry)
    return entries


def _bucket_profs(profs):
    buckets = {'saves': [], 'skills': [], 'weapons': [], 'armor': [], 'tools': []}
    for index in sorted(profs):
        if index.startswith('skill-'):
            buckets['skills'].append(index.split('-', 1)[1])
        elif index.startswith('saving-throw-'):
            buckets['saves'].append(index.split('-')[-1])
        elif index in ARMOR_PROFS:
            buckets['armor'].append(index)
        elif index.endswith(('-tools', '-supplies', '-kit')):
            buckets['tools'].append(index)
        else:
            buckets['weapons'].append(index)
    return buckets


def _background_gold(background):
    gold = background.get('starting_gold') or {}
    if gold.get('unit') == 'gp':
        return gold.get('quantity') or 0
    return 0


def _is_skill_choice(choice):
    indexes = _choice_indexes(choice)
    return bool(indexes) and all(index.startswith('skill-') for index in indexes)


def _choice_indexes(choice):
    if not choice:
        return []
    source = choice.get('from') or {}
    return [
        option.get('item', {}).get('index')
        for option in source.get('options') or []
        if option.get('item')
    ]


def _props(item):
    return [prop['index'] for prop in item.get('properties') or []]


def _get(kind, index, label):
    try:
        return srd.get(kind, index)
    except KeyError:
        rule(f'{label} is not in the SRD', **{label: index})


def _slug(value, label):
    if not isinstance(value, str) or not value.replace('-', '').isalnum() or not value[0].isalpha():
        rule(f'{label} is not a slug', **{label: value})
    return value


def _require_keys(data, keys):
    if not isinstance(data, dict):
        rule('character document must be a JSON object')
    missing = [key for key in keys if key not in data]
    if missing:
        rule('character document is missing fields', missing=missing)


def status_of(character):
    if character.get('pending_scores') is not None:
        return 'pending'
    current = character['hp']['current']
    if current <= 0:
        return 'dying'
    if current * 2 <= character['hp']['max']:
        return 'bloodied'
    return 'healthy'


def public_view(character):
    return {
        'id': character['id'],
        'name': character.get('name'),
        'race': character.get('race'),
        'class': character.get('class'),
        'level': character.get('level'),
        'status': status_of(character),
    }


def carry_weight(character):
    total = 0
    for entry in character.get('inventory') or []:
        try:
            item = srd.get('equipment', entry['item'])
        except KeyError:
            try:
                item = srd.get('magic-item', entry['item'])
            except KeyError:
                continue
        total += (item.get('weight') or 0) * entry.get('qty', 1)
    coins = character.get('coins') or {}
    pieces = sum(coins.get(kind, 0) for kind in ('cp', 'sp', 'ep', 'gp', 'pp'))
    total += pieces / 50
    return total


def same_multiset(left, right):
    return Counter(left) == Counter(right)
