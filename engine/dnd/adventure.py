"""Adventure validation, travel, traps, and experience."""

import copy

from dnd.chargen import ability_mod, rule, skill_bonus
from dnd.dice import roll_expr
from dnd.errors import EngineError
from dnd import combat
from dnd import srd


def validate(data):
    """Reject an adventure whose references or DCs do not resolve."""
    if not isinstance(data, dict):
        raise EngineError('BAD_ARGS', 'adventure file must be a JSON object')
    if data.get('format') not in (None, 'dnd-adventure/1'):
        rule('adventure format is not dnd-adventure/1', format=data.get('format'))
    if data.get('ruleset', 'srd-5.1') != 'srd-5.1':
        rule('only SRD 5.1 adventures are accepted', ruleset=data.get('ruleset'))
    locations = data.get('locations') or []
    if not isinstance(locations, list) or not locations:
        rule('adventure needs locations')
    ids = []
    for location in locations:
        if not isinstance(location, dict) or not location.get('id'):
            rule('a location needs an id')
        ids.append(location['id'])
    if len(ids) != len(set(ids)):
        rule('duplicate location id')
    start = data.get('start')
    if start not in ids:
        raise EngineError('UNKNOWN_ID', 'adventure start is not a location', {'start': start})
    custom = {row.get('id') for row in data.get('custom_monsters') or []}
    encounters = {row.get('id'): row for row in data.get('encounters') or []}
    traps = {row.get('id'): row for row in data.get('traps') or []}
    treasures = {row.get('id'): row for row in data.get('treasure') or []}
    npcs = {row.get('id'): row for row in data.get('npcs') or []}
    for row in data.get('custom_monsters') or []:
        if not str(row.get('id', '')).startswith('custom:'):
            rule('custom monster id must start with custom:', id=row.get('id'))
        block = row.get('stat_block')
        if not isinstance(block, dict) or 'hit_points' not in block:
            rule('custom monster needs a stat block', id=row.get('id'))
    for encounter in encounters.values():
        for group in encounter.get('monsters') or []:
            _resolve_monster(group.get('ref'), custom)
        _integer(encounter.get('xp_override'), 'xp_override', allow_none=True)
    for location in locations:
        for link in location.get('connections') or []:
            if link.get('to') not in ids:
                raise EngineError('UNKNOWN_ID', 'connection target does not exist', {'to': link.get('to')})
            _dc_block(link.get('hidden'), 'hidden')
            _dc_block(link.get('locked'), 'locked')
            _integer((link.get('travel_minutes')), 'travel_minutes', allow_none=True)
        for feature in location.get('features') or []:
            _dc_block(feature.get('hidden'), 'hidden')
        for spawn in location.get('on_enter') or []:
            if spawn.get('spawn') not in encounters:
                raise EngineError('UNKNOWN_ID', 'on_enter encounter does not exist', {'spawn': spawn.get('spawn')})
        for trap_id in location.get('traps') or []:
            if trap_id not in traps:
                raise EngineError('UNKNOWN_ID', 'trap does not exist', {'trap': trap_id})
        for treasure_id in location.get('treasure') or []:
            if treasure_id not in treasures:
                raise EngineError('UNKNOWN_ID', 'treasure does not exist', {'treasure': treasure_id})
        for npc_id in location.get('npcs') or []:
            if npc_id not in npcs:
                raise EngineError('UNKNOWN_ID', 'npc does not exist', {'npc': npc_id})
    for trap in traps.values():
        _dc_block(trap.get('detect'), 'detect')
        _dc_block(trap.get('disarm'), 'disarm')
        save = (trap.get('effect') or {}).get('save') or {}
        _integer(save.get('dc'), 'save dc', allow_none=True)
    for treasure in treasures.values():
        _dc_block(treasure.get('hidden'), 'hidden')
        for item in treasure.get('items') or []:
            _resolve_item(item.get('ref'))
    for npc in npcs.values():
        if npc.get('stat_ref'):
            _resolve_monster(npc['stat_ref'], custom)
        if npc.get('attitude') not in (None, 'friendly', 'indifferent', 'hostile'):
            rule('npc attitude is not friendly, indifferent, or hostile', npc=npc.get('id'))
    for milestone in (data.get('xp') or {}).get('milestones') or []:
        _integer(milestone.get('level'), 'milestone level')
    return {
        'id': data.get('id'),
        'title': data.get('title'),
        'locations': len(ids),
        'encounters': len(encounters),
        'custom_monsters': len(custom),
    }


def look(state, adventure, actor):
    location = _here(state, adventure)
    revealed = set((state.get('adventure') or {}).get('revealed') or [])
    spotted = set((state.get('adventure') or {}).get('spotted') or [])
    dm = actor == 'dm'
    exits = []
    for link in location.get('connections') or []:
        hidden = link.get('hidden')
        key = _exit_key(location['id'], link.get('to'))
        if hidden and key not in revealed and not dm:
            continue
        row = {'to': link.get('to'), 'label': link.get('label')}
        if hidden and (dm or key in revealed):
            row['hidden'] = True
        if dm and hidden:
            row['dc'] = hidden.get('dc')
            row['skill'] = hidden.get('skill')
        exits.append(row)
    features = []
    for feature in location.get('features') or []:
        hidden = feature.get('hidden')
        row = {'id': feature.get('id'), 'name': feature.get('name'), 'public': feature.get('public')}
        if hidden and (dm or feature.get('id') in revealed):
            row['reveal'] = hidden.get('reveal')
        if dm and hidden:
            row['dc'] = hidden.get('dc')
            row['skill'] = hidden.get('skill')
        features.append(row)
    npcs = []
    catalog = {row['id']: row for row in adventure.get('npcs') or []}
    for npc_id in location.get('npcs') or []:
        npc = catalog[npc_id]
        row = {'id': npc_id, 'name': npc.get('name'), 'public': npc.get('public'), 'attitude': npc.get('attitude')}
        if dm:
            row['dm_only'] = npc.get('dm_only')
            row['stat_ref'] = npc.get('stat_ref')
        npcs.append(row)
    trap_catalog = {row['id']: row for row in adventure.get('traps') or []}
    traps = []
    for trap_id in location.get('traps') or []:
        if not dm and trap_id not in spotted:
            continue
        trap = trap_catalog[trap_id]
        row = {'id': trap_id, 'name': trap.get('name')}
        if dm:
            row['detect'] = trap.get('detect')
            row['disarm'] = trap.get('disarm')
            row['effect'] = trap.get('effect')
        traps.append(row)
    treasure_catalog = {row['id']: row for row in adventure.get('treasure') or []}
    treasure = []
    for treasure_id in location.get('treasure') or []:
        row_src = treasure_catalog[treasure_id]
        if row_src.get('hidden') and treasure_id not in revealed and not dm:
            continue
        row = {'id': treasure_id}
        if dm or not row_src.get('hidden'):
            row['coins'] = row_src.get('coins') or {}
            row['items'] = row_src.get('items') or []
        treasure.append(row)
    result = {
        'id': location['id'],
        'name': location.get('name'),
        'read_aloud': location.get('read_aloud'),
        'description_public': location.get('description_public'),
        'exits': exits,
        'features': features,
        'npcs': npcs,
        'traps': traps,
        'treasure': treasure,
    }
    if dm:
        result['dm_notes'] = location.get('dm_notes')
    return result


def go(state, adventure, dest, travel_hours, actor):
    if actor != 'dm':
        raise EngineError('NOT_AUTHORIZED', 'only the DM moves the party', {'actor': actor})
    if (state.get('combat') or {}).get('active'):
        rule('the party cannot travel during combat')
    location = _here(state, adventure)
    link = None
    for candidate in location.get('connections') or []:
        if candidate.get('to') == dest:
            link = candidate
            break
    if link is None:
        raise EngineError('UNKNOWN_ID', 'no connection goes there', {'to': dest})
    hidden = link.get('hidden')
    key = _exit_key(location['id'], dest)
    revealed = set((state.get('adventure') or {}).get('revealed') or [])
    if hidden and key not in revealed:
        rule('that way is hidden', to=dest)
    locked = link.get('locked')
    if locked:
        rule('that way is locked', to=dest, dc=locked.get('dc'))
    new_state = copy.deepcopy(state)
    minutes = travel_hours * 60 if travel_hours is not None else (link.get('travel_minutes') or 0)
    _advance(new_state, minutes)
    new_state['location'] = dest
    progress = new_state.setdefault('adventure', {})
    visited = list(progress.get('visited') or [])
    if location['id'] not in visited:
        visited.append(location['id'])
    if dest not in visited:
        visited.append(dest)
    progress['visited'] = visited
    events = [_progress_event(state, new_state, [], f'go {dest} --as dm', {'location': dest, 'minutes': minutes})]
    entered, _final = _enter(new_state, adventure, dest)
    events.extend(entered)
    return {'location': dest, 'minutes': minutes}, events


def search_location(state, adventure, char_id, actor):
    character = combat._require_creature(state, char_id)
    combat._assert_controls(state, actor, char_id)
    if character.get('pending_scores') is not None:
        rule('scores are not assigned', id=char_id)
    if (state.get('combat') or {}).get('active'):
        rule('search the room after combat')
    location = _here(state, adventure)
    new_state = copy.deepcopy(state)
    character = combat._require_creature(new_state, char_id)
    rolls = []
    totals = {}
    for skill in ('perception', 'investigation'):
        totals[skill] = _roll_skill(new_state, character, skill, rolls)
    found = []
    progress = new_state.setdefault('adventure', {})
    revealed = list(progress.get('revealed') or [])
    for feature in location.get('features') or []:
        hidden = feature.get('hidden')
        if not hidden or feature.get('id') in revealed:
            continue
        if totals.get(hidden.get('skill') or 'perception', 0) >= hidden.get('dc', 0):
            revealed.append(feature['id'])
            found.append({'kind': 'feature', 'id': feature['id'], 'reveal': hidden.get('reveal')})
    for link in location.get('connections') or []:
        hidden = link.get('hidden')
        key = _exit_key(location['id'], link.get('to'))
        if not hidden or key in revealed:
            continue
        if totals.get(hidden.get('skill') or 'perception', 0) >= hidden.get('dc', 0):
            revealed.append(key)
            found.append({'kind': 'exit', 'to': link.get('to'), 'label': link.get('label')})
    progress['revealed'] = revealed
    result = {'found': found, 'totals': totals}
    event = _progress_event(state, new_state, rolls, f'search {char_id} --as {actor}', result)
    return result, [event]


def trap_spot(state, adventure, trap_id, char_id, actor):
    trap = _trap(adventure, trap_id)
    character = combat._require_creature(state, char_id)
    combat._assert_controls(state, actor, char_id)
    detect = trap.get('detect') or {}
    new_state = copy.deepcopy(state)
    character = combat._require_creature(new_state, char_id)
    rolls = []
    total = _roll_skill(new_state, character, detect.get('skill') or 'perception', rolls)
    success = total >= detect.get('dc', 0)
    if success:
        spotted = list(new_state.setdefault('adventure', {}).get('spotted') or [])
        if trap_id not in spotted:
            spotted.append(trap_id)
        new_state['adventure']['spotted'] = spotted
    result = {'trap': trap_id, 'success': success, 'total': total, 'dc': detect.get('dc')}
    return result, [_progress_event(state, new_state, rolls, f'trap spot {trap_id} {char_id} --as {actor}', result)]


def trap_trigger(state, adventure, trap_id, target_id):
    trap = _trap(adventure, trap_id)
    disarmed = set((state.get('adventure') or {}).get('disarmed') or [])
    if trap_id in disarmed:
        rule('that trap is disarmed', trap=trap_id)
    effect = trap.get('effect') or {}
    target = combat._require_creature(state, target_id) if target_id else None
    if target_id and target is None:
        raise EngineError('UNKNOWN_ID', 'unknown target', {'id': target_id})
    new_state = copy.deepcopy(state)
    rolls = []
    save = effect.get('save') or {}
    success = False
    total = None
    if target_id and save:
        target = combat._require_creature(new_state, target_id)
        total, natural = _save(new_state, target, save.get('ability') or 'dex', rolls)
        success = total >= save.get('dc', 0)
    amount = 0
    if effect.get('damage') and target_id:
        amount, _record = _expr_amount(new_state, effect['damage'], rolls, trap_id)
        if success and save.get('half'):
            amount //= 2
        target = combat._require_creature(new_state, target_id)
        amount = combat._mitigate(target, effect.get('type') or 'bludgeoning', amount)
        combat._apply_damage_parts(new_state, target, [{'type': effect.get('type') or 'bludgeoning', 'amount': amount}], False)
        concentration_rolls, _concentration = combat.check_concentration(new_state, target, amount)
        rolls.extend(concentration_rolls)
    triggered = list(new_state.setdefault('adventure', {}).get('triggered') or [])
    if trap_id not in triggered:
        triggered.append(trap_id)
    new_state['adventure']['triggered'] = triggered
    result = {'trap': trap_id, 'damage': amount, 'saved': success, 'total': total}
    return result, [_progress_event(state, new_state, rolls, f'trap trigger {trap_id} --as dm', result)]


def trap_disarm(state, adventure, trap_id, char_id, actor):
    trap = _trap(adventure, trap_id)
    character = combat._require_creature(state, char_id)
    combat._assert_controls(state, actor, char_id)
    disarm = trap.get('disarm') or {}
    new_state = copy.deepcopy(state)
    character = combat._require_creature(new_state, char_id)
    rolls = []
    total = _roll_skill(new_state, character, disarm.get('skill') or 'dex', rolls)
    success = total >= disarm.get('dc', 0)
    if success:
        done = list(new_state.setdefault('adventure', {}).get('disarmed') or [])
        if trap_id not in done:
            done.append(trap_id)
        new_state['adventure']['disarmed'] = done
    result = {'trap': trap_id, 'success': success, 'total': total, 'dc': disarm.get('dc')}
    return result, [_progress_event(state, new_state, rolls, f'trap disarm {trap_id} {char_id} --as {actor}', result)]


def advance_time(state, minutes):
    if minutes < 0:
        raise EngineError('BAD_ARGS', 'minutes cannot be negative')
    new_state = copy.deepcopy(state)
    _advance(new_state, minutes)
    for bucket in (new_state.get('characters') or {}, new_state.get('monsters') or {}):
        for creature in bucket.values():
            creature['conditions'] = [
                row for row in creature.get('conditions') or []
                if isinstance(row, str) or (row.get('duration') or {}).get('type') != 'rounds'
            ]
    result = {'day': new_state['time']['day'], 'minute': new_state['time']['minute']}
    return result, [_progress_event(state, new_state, [], f'time advance {minutes} --as dm', result)]


def award_xp(state, amount, targets):
    if amount < 0:
        raise EngineError('BAD_ARGS', 'xp cannot be negative')
    ids = list((state.get('characters') or {}).keys()) if targets == ['all'] or not targets else targets
    living = [char_id for char_id in ids if char_id in (state.get('characters') or {}) and not state['characters'][char_id].get('dead')]
    if not living:
        rule('nobody can take that xp')
    share = amount // len(living)
    new_state = copy.deepcopy(state)
    awarded = []
    for char_id in living:
        character = new_state['characters'][char_id]
        character['xp'] = character.get('xp', 0) + share
        awarded.append({'id': char_id, 'xp': share, 'total': character['xp']})
    _refresh_available(new_state)
    result = {'awarded': awarded, 'available': (new_state.get('progression') or {}).get('available')}
    return result, [_progress_event(state, new_state, [], f'xp award {amount} --as dm', result)]


def milestone(state, level, adventure):
    if not isinstance(level, int) or isinstance(level, bool) or not 2 <= level <= 5:
        raise EngineError('BAD_ARGS', 'milestone level must be from 2 to 5', {'level': level})
    new_state = copy.deepcopy(state)
    progress = new_state.setdefault('progression', {})
    progress['milestone'] = max(progress.get('milestone') or 1, level)
    _refresh_available(new_state)
    result = {'milestone': progress['milestone'], 'available': progress.get('available')}
    return result, [_progress_event(state, new_state, [], f'xp milestone {level} --as dm', result)]


def _enter(state, adventure, dest):
    location = next(row for row in adventure['locations'] if row['id'] == dest)
    events = []
    for hook in location.get('on_enter') or []:
        token = f"enter:{dest}:{hook.get('spawn')}"
        triggered = list((state.get('adventure') or {}).get('triggered') or [])
        if hook.get('once') and token in triggered:
            continue
        result, event = combat.spawn_encounter(state, adventure, hook['spawn'], False)
        events.append(event)
        state = _apply_event_copy(state, event)
        ids = result['spawned']
        surprised = []
        if hook.get('start_combat') and ids and not (state.get('combat') or {}).get('active'):
            party = [
                char_id for char_id, character in (state.get('characters') or {}).items()
                if not character.get('dead')
            ]
            encounter = next(row for row in adventure.get('encounters') or [] if row['id'] == hook['spawn'])
            if (encounter.get('surprise') or {}).get('stealth_vs_passive') and party:
                before = copy.deepcopy(state)
                surprised, rolls = _surprise(state, adventure, encounter, party)
                events.append(_progress_event(
                    before, state, rolls, f'surprise {hook.get("spawn")}',
                    {'surprised': surprised},
                ))
            if party:
                _started, start_event = combat.start_combat(state, party + ids, surprised)
                events.append(start_event)
                state = _apply_event_copy(state, start_event)
        before = copy.deepcopy(state)
        progress = state.setdefault('adventure', {})
        done = list(progress.get('triggered') or [])
        if token not in done:
            done.append(token)
        progress['triggered'] = done
        events.append(_progress_event(
            before, state, [], f'on_enter {hook.get("spawn")}',
            {'spawned': ids, 'surprised': surprised},
        ))
    return events, state


def _surprise(state, adventure, encounter, party):
    bonus = 0
    for group in encounter.get('monsters') or []:
        block = _block(adventure, group.get('ref'))
        bonus = max(bonus, _stealth_bonus(block))
    counter = state['rng']['counter']
    rolled = roll_expr('1d20', state['rng']['seed'], counter, 'none')
    state['rng']['counter'] = counter + 1
    total = rolled['kept'][0] + bonus
    highest = max(state['characters'][char_id].get('passive_perception') or 10 for char_id in party)
    rolls = [{
        'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
        'expr': '1d20', 'kept': rolled['kept'], 'label': 'stealth', 'mod': bonus, 'total': total,
    }]
    if total > highest:
        return list(party), rolls
    return [], rolls


def _stealth_bonus(block):
    for row in block.get('proficiencies') or []:
        prof = row.get('proficiency') or {}
        if prof.get('index') == 'skill-stealth':
            return row.get('value') or 0
    return ability_mod(block.get('dexterity') or 10)


def _block(adventure, ref):
    if str(ref).startswith('custom:'):
        for row in adventure.get('custom_monsters') or []:
            if row.get('id') == ref:
                return row.get('stat_block') or {}
    slug = ref.split(':', 1)[1] if str(ref).startswith('srd:') else ref
    return srd.get('monster', slug)


def _apply_event_copy(state, event):
    new_state = copy.deepcopy(state)
    for change in event.get('changes') or []:
        if change.get('op') != 'set':
            continue
        _assign(new_state, change['path'], copy.deepcopy(change['to']))
    return new_state


def _assign(state, path, value):
    parts = path.split('.')
    cursor = state
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _progress_event(before, after, rolls, cmd, outcome):
    changes = []
    for path in ('location', 'time', 'adventure', 'progression', 'rng.counter'):
        old = _get_path(before, path)
        new = _get_path(after, path)
        if old != new:
            changes.append({'op': 'set', 'path': path, 'from': copy.deepcopy(old), 'to': copy.deepcopy(new)})
    for bucket in ('characters', 'monsters'):
        for creature_id, creature in (after.get(bucket) or {}).items():
            old = (before.get(bucket) or {}).get(creature_id)
            if old != creature:
                changes.append({
                    'op': 'set', 'path': f'{bucket}.{creature_id}',
                    'from': copy.deepcopy(old), 'to': copy.deepcopy(creature),
                })
    event = {'actor': 'dm', 'changes': changes, 'cmd': cmd, 'rolls': rolls, 'type': 'adventure', 'outcome': outcome}
    return event


def _get_path(state, path):
    cursor = state
    for part in path.split('.'):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _roll_skill(state, character, skill, rolls):
    counter = state['rng']['counter']
    rolled = roll_expr('1d20', state['rng']['seed'], counter, 'none')
    state['rng']['counter'] = counter + 1
    if skill in ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma', 'str', 'dex', 'con', 'int', 'wis', 'cha'):
        ability = {'strength': 'str', 'dexterity': 'dex', 'constitution': 'con', 'intelligence': 'int', 'wisdom': 'wis', 'charisma': 'cha'}.get(skill, skill)
        bonus = ability_mod(character['abilities'][ability])
    else:
        bonus = skill_bonus(character, skill)
    total = rolled['kept'][0] + bonus
    rolls.append({
        'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
        'expr': '1d20', 'kept': rolled['kept'], 'label': skill, 'mod': bonus, 'total': total,
    })
    return total


def _save(state, creature, ability, rolls):
    counter = state['rng']['counter']
    rolled = roll_expr('1d20', state['rng']['seed'], counter, 'none')
    state['rng']['counter'] = counter + 1
    bonus = combat._save_bonus(creature, ability)
    total = rolled['kept'][0] + bonus
    rolls.append({
        'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
        'expr': '1d20', 'kept': rolled['kept'], 'label': ability, 'mod': bonus, 'total': total,
    })
    return total, rolled['kept'][0]


def _expr_amount(state, expr, rolls, label):
    counter = state['rng']['counter']
    rolled = roll_expr(str(expr).replace(' ', ''), state['rng']['seed'], counter, 'none')
    state['rng']['counter'] = counter + 1
    amount = max(0, rolled['total'])
    rolls.append({
        'adv': 'none', 'adv_sources': [], 'counter': counter, 'dice': rolled['dice'],
        'expr': str(expr).replace(' ', ''), 'kept': rolled['kept'], 'label': label,
        'mod': rolled['mod'], 'total': amount,
    })
    return amount, rolls[-1]


def _advance(state, minutes):
    if minutes <= 0:
        return
    time = state.setdefault('time', {'day': 1, 'minute': 0})
    total = time.get('minute', 0) + minutes
    time['day'] = time.get('day', 1) + total // (24 * 60)
    time['minute'] = total % (24 * 60)


def _refresh_available(state):
    progress = state.setdefault('progression', {})
    best = progress.get('milestone') or 1
    for character in (state.get('characters') or {}).values():
        best = max(best, _level_for_xp(character.get('xp') or 0))
    progress['available'] = min(5, best)


def _level_for_xp(xp):
    thresholds = (0, 300, 900, 2700, 6500)
    level = 1
    for index, need in enumerate(thresholds, start=1):
        if xp >= need:
            level = index
    return level


def _here(state, adventure):
    current = state.get('location')
    for location in adventure.get('locations') or []:
        if location.get('id') == current:
            return location
    raise EngineError('UNKNOWN_ID', 'the party is not in a known location', {'location': current})


def _trap(adventure, trap_id):
    for trap in adventure.get('traps') or []:
        if trap.get('id') == trap_id:
            return trap
    raise EngineError('UNKNOWN_ID', f'unknown trap {trap_id}', {'trap': trap_id})


def _exit_key(source, dest):
    return f'{source}->{dest}'


def _dc_block(block, label):
    if block is None:
        return
    if not isinstance(block, dict):
        rule(f'{label} must be an object')
    _integer(block.get('dc'), f'{label} dc')


def _integer(value, label, allow_none=False):
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        rule(f'{label} must be an integer', value=value)


def _resolve_monster(ref, custom):
    if not isinstance(ref, str) or ':' not in ref:
        raise EngineError('UNKNOWN_ID', 'monster ref must be srd: or custom:', {'ref': ref})
    if ref.startswith('custom:'):
        if ref not in custom:
            raise EngineError('UNKNOWN_ID', f'unknown custom monster {ref}', {'ref': ref})
        return
    if ref.startswith('srd:'):
        try:
            srd.get('monster', ref.split(':', 1)[1])
            return
        except KeyError:
            raise EngineError('UNKNOWN_ID', f'unknown monster {ref}', {'ref': ref}) from None
    raise EngineError('UNKNOWN_ID', f'unknown monster {ref}', {'ref': ref})


def _resolve_item(ref):
    if not isinstance(ref, str):
        raise EngineError('UNKNOWN_ID', 'item ref is missing')
    slug = ref.split(':', 1)[1] if ref.startswith('srd:') else ref
    for kind in ('equipment', 'magic-item'):
        try:
            srd.get(kind, slug)
            return
        except KeyError:
            continue
    raise EngineError('UNKNOWN_ID', f'unknown item {ref}', {'ref': ref})

