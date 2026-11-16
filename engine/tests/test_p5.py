"""P5 adventure: Brackwater Mill, and level-up through 5."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dnd import srd

ENGINE = Path(__file__).resolve().parents[1]
PARTY = Path(__file__).resolve().parent / 'fixtures' / 'party'
MILL = Path(__file__).resolve().parent / 'fixtures' / 'brackwater-mill.json'
WAYSTONE = Path(__file__).resolve().parent / 'fixtures' / 'waystone.json'
IDS = ('pc-tor', 'pc-mira', 'pc-sel', 'pc-pip')


def _walk_keys(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            _walk_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, found)


class AdventureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'campaigns'
        self.home = Path(self.tmp.name) / 'home'
        self.choices = Path(self.tmp.name) / 'choices'
        self.root.mkdir()
        self.home.mkdir()
        self.choices.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, args):
        env = os.environ.copy()
        env['HOME'] = str(self.home)
        env['DND_CAMPAIGN'] = str(self.root)
        proc = subprocess.run(
            [sys.executable, '-m', 'dnd', *args],
            cwd=ENGINE,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.stderr, '', proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(proc.stdout.count('\n'), 1)
        return proc.returncode, payload

    def ok(self, args):
        code, payload = self.invoke(args)
        self.assertEqual(code, 0, payload)
        return payload

    def level(self, char_id, choices):
        args = ['level', 'up', char_id, '--as', char_id]
        if choices:
            path = self.choices / f'{char_id}-{choices.get("tag", char_id)}.json'
            path.write_text(json.dumps({key: value for key, value in choices.items() if key != 'tag'}), encoding='utf-8')
            args[3:3] = ['--json-in', str(path)]
        payload = self.ok(args)
        return payload['result']

    def test_validate_rejects_a_bad_connection(self):
        report = self.ok(['adventure', 'validate', str(MILL)])['result']
        self.assertEqual(report['locations'], 4)
        self.assertEqual(report['custom_monsters'], 1)
        self.ok(['adventure', 'validate', str(WAYSTONE)])
        bad = json.loads(MILL.read_text(encoding='utf-8'))
        bad['locations'][0]['connections'][0]['to'] = 'missing-room'
        path = Path(self.tmp.name) / 'bad.json'
        path.write_text(json.dumps(bad), encoding='utf-8')
        code, payload = self.invoke(['adventure', 'validate', str(path)])
        self.assertEqual(payload['error']['code'], 'UNKNOWN_ID')
        bad = json.loads(MILL.read_text(encoding='utf-8'))
        bad['traps'][0]['detect']['dc'] = '12'
        path.write_text(json.dumps(bad), encoding='utf-8')
        code, payload = self.invoke(['campaign', 'new', 'nope', '--adventure', str(path), '--as', 'dm'])
        self.assertEqual(payload['error']['code'], 'RULE_VIOLATION')

    def test_brackwater_playthrough_and_level_five(self):
        self.ok(['campaign', 'new', 'mill', '--adventure', str(MILL), '--seed', '7', '--as', 'dm'])
        for char_id in IDS:
            self.ok(['char', 'create', '--json-in', str(PARTY / f'{char_id}.json'), '--as', 'dm'])
        road_player = self.ok(['look', '--as', 'pc-pip'])['result']
        road_dm = self.ok(['look', '--as', 'dm'])['result']
        player_keys = set()
        _walk_keys(road_player, player_keys)
        self.assertNotIn('dm_notes', player_keys)
        self.assertNotIn('dm_only', player_keys)
        self.assertIn('dm_notes', road_dm)
        self.assertEqual(road_player['id'], 'mill-road')
        seen = {'mill-road'}

        self.ok(['go', 'mill-yard', '--as', 'dm'])
        seen.add('mill-yard')
        status = self.ok(['combat', 'status', '--as', 'dm'])['result']
        self.assertTrue(status['active'])
        for combatant in status['combatants']:
            if combatant['id'].startswith('goblin'):
                self.ok(['damage', combatant['id'], '20', '--type', 'slashing', '--as', 'dm'])
        yard = self.ok(['combat', 'end', '--reason', 'victory', '--as', 'dm'])['result']
        self.assertEqual(sorted(row['xp'] for row in yard['xp']), [37, 37, 37, 37])

        found = self.ok(['search', 'pc-tor', '--as', 'pc-tor'])['result']['found']
        self.assertIn('tracks', [row.get('id') for row in found])
        self.ok(['go', 'mill-floor', '--as', 'dm'])
        seen.add('mill-floor')
        floor_player = self.ok(['look', '--as', 'pc-pip'])['result']
        floor_dm = self.ok(['look', '--as', 'dm'])['result']
        keys = set()
        _walk_keys(floor_player, keys)
        self.assertNotIn('dm_notes', keys)
        self.assertIn('dm_notes', floor_dm)
        self.assertEqual(floor_player['npcs'][0]['name'], 'Oswin')
        self.assertNotIn('dm_only', floor_player['npcs'][0])
        self.assertIn('dm_only', floor_dm['npcs'][0])
        triggered = self.ok(['trap', 'trigger', 'trap-millstone', '--target', 'pc-tor', '--as', 'dm'])['result']
        self.assertGreater(triggered['damage'], 0)
        self.ok(['trap', 'disarm', 'trap-millstone', 'pc-tor', '--as', 'pc-tor'])
        self.ok(['trap', 'disarm', 'trap-millstone', 'pc-pip', '--as', 'pc-pip'])
        disarmed = self.ok(['trap', 'disarm', 'trap-millstone', 'pc-sel', '--as', 'pc-sel'])['result']
        self.assertTrue(disarmed['success'])

        self.ok(['go', 'mill-cellar', '--as', 'dm'])
        seen.add('mill-cellar')
        status = self.ok(['combat', 'status', '--as', 'dm'])['result']
        for combatant in status['combatants']:
            if not combatant['id'].startswith('pc-'):
                self.ok(['damage', combatant['id'], '40', '--type', 'slashing', '--as', 'dm'])
        self.ok(['combat', 'end', '--reason', 'victory', '--as', 'dm'])
        self.assertEqual(seen, {'mill-road', 'mill-yard', 'mill-floor', 'mill-cellar'})
        for char_id in IDS:
            self.assertEqual(self.ok(['char', 'show', char_id, '--as', 'dm'])['result']['xp'], 112)

        self.ok(['inv', 'give', 'gp', '--qty', '40', '--to', 'pc-tor', '--from', 'loot:tr-cellar', '--as', 'dm'])
        self.ok(['inv', 'give', 'potion-of-healing', '--qty', '2', '--to', 'pc-sel', '--from', 'loot:tr-cellar', '--as', 'dm'])
        self.ok(['inv', 'give', 'spell-scroll-1st', '--to', 'pc-mira', '--from', 'loot:tr-cellar', '--as', 'dm'])
        tor_coins = self.ok(['inv', 'show', 'pc-tor', '--as', 'pc-tor'])['result']['coins']['gp']
        self.assertGreaterEqual(tor_coins, 40)
        spawned = self.ok(['encounter', 'spawn', 'rat-nest', '--as', 'dm'])['result']['spawned']
        self.assertEqual(spawned, ['mill-rat-swarm-1'])
        monsters = self.ok(['state', 'show', '--section', 'all', '--as', 'dm'])['result']['monsters']
        rat = next(row for row in monsters if row['id'] == 'mill-rat-swarm-1')
        self.assertEqual(rat['hp']['max'], 20)
        clock = self.ok(['time', 'advance', '30', '--as', 'dm'])['result']
        self.assertGreaterEqual(clock['minute'], 30)

        self.ok(['xp', 'milestone', '2', '--as', 'dm'])
        self.level('pc-tor', None)
        self.level('pc-pip', None)
        self.level('pc-sel', None)
        self.level('pc-mira', {'tag': 'mira2', 'subclass': 'evocation', 'spells': ['grease', 'thunderwave']})
        for char_id in IDS:
            self.assertEqual(self.ok(['char', 'show', char_id, '--as', 'dm'])['result']['level'], 2)

        self.ok(['xp', 'milestone', '5', '--as', 'dm'])
        self.level('pc-tor', {'tag': 'tor3', 'subclass': 'champion'})
        self.level('pc-pip', {'tag': 'pip3', 'subclass': 'thief'})
        self.level('pc-sel', None)
        self.level('pc-mira', {'tag': 'mira3', 'spells': ['misty-step', 'scorching-ray']})
        self.level('pc-tor', {'tag': 'tor4', 'asi': {'str': 2}})
        self.level('pc-pip', {'tag': 'pip4', 'asi': {'dex': 2}})
        self.level('pc-sel', {'tag': 'sel4', 'asi': {'wis': 2}, 'cantrips': ['mending']})
        self.level('pc-mira', {
            'tag': 'mira4', 'asi': {'int': 2}, 'cantrips': ['ray-of-frost'],
            'spells': ['invisibility', 'mirror-image'],
        })
        self.level('pc-tor', None)
        self.level('pc-pip', None)
        self.level('pc-sel', None)
        self.level('pc-mira', {'tag': 'mira5', 'spells': ['fireball', 'counterspell']})

        expect = {
            'pc-tor': {'level': 5, 'max': 54, 'current': 49, 'subclass': 'champion', 'feature': 'extra-attack-1'},
            'pc-mira': {'level': 5, 'max': 27, 'current': 27, 'subclass': 'evocation', 'feature': 'sculpt-spells'},
            'pc-sel': {'level': 5, 'max': 38, 'current': 38, 'subclass': 'life', 'feature': 'destroy-undead-cr-1-2-or-below'},
            'pc-pip': {'level': 5, 'max': 38, 'current': 38, 'subclass': 'thief', 'feature': 'uncanny-dodge'},
        }
        for char_id, row in expect.items():
            sheet = self.ok(['char', 'show', char_id, '--as', 'dm'])['result']
            self.assertEqual(sheet['level'], 5)
            self.assertEqual(sheet['hp']['max'], row['max'], char_id)
            self.assertEqual(sheet['hp']['current'], row['current'], char_id)
            self.assertEqual(sheet['subclass'], row['subclass'])
            self.assertIn(row['feature'], [feature['id'] for feature in sheet['features']])
        self.assertEqual(self.ok(['char', 'show', 'pc-mira', '--as', 'dm'])['result']['spellcasting']['dc'], 15)
        self.assertEqual(self.ok(['char', 'show', 'pc-sel', '--as', 'dm'])['result']['spellcasting']['dc'], 15)


def _class_spells(class_index, level, count, skip):
    if count <= 0:
        return []
    found = []
    for spell in srd.all('spell'):
        if spell['level'] != level:
            continue
        if not any(entry['index'] == class_index for entry in spell.get('classes') or []):
            continue
        if spell['index'] in skip or spell['index'] in found:
            continue
        found.append(spell['index'])
        if len(found) == count:
            break
    return found


class OtherClassesTest(unittest.TestCase):
    """Every other SRD class can be created and leveled to 5."""

    def test_remaining_classes_reach_level_five(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'campaigns'
        home = Path(tmp.name) / 'home'
        choices = Path(tmp.name) / 'choices'
        root.mkdir()
        home.mkdir()
        choices.mkdir()

        def invoke(args):
            env = os.environ.copy()
            env['HOME'] = str(home)
            env['DND_CAMPAIGN'] = str(root)
            proc = subprocess.run(
                [sys.executable, '-m', 'dnd', *args], cwd=ENGINE, env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.stderr, '', proc.stderr)
            payload = json.loads(proc.stdout)
            return proc.returncode, payload

        code, payload = invoke(['campaign', 'new', 'hall', '--adventure', str(WAYSTONE), '--seed', '3', '--as', 'dm'])
        self.assertEqual(code, 0, payload)
        plans = {
            'barbarian': {'skills': ['athletics', 'perception'], 'levels': [
                {}, {'subclass': 'berserker'}, {'asi': {'str': 2}}, {},
            ]},
            'bard': {'skills': ['arcana', 'history', 'persuasion'], 'levels': 'bard'},
            'druid': {'skills': ['medicine', 'nature'], 'levels': 'druid'},
            'monk': {'skills': ['acrobatics', 'stealth'], 'levels': [
                {}, {'subclass': 'open-hand'}, {'asi': {'dex': 2}}, {},
            ]},
            'paladin': {'skills': ['athletics', 'persuasion'], 'levels': 'paladin'},
            'ranger': {'skills': ['animal-handling', 'athletics', 'nature'], 'levels': 'ranger'},
            'sorcerer': {'skills': ['arcana', 'persuasion'], 'subclass': 'draconic', 'levels': 'sorcerer'},
            'warlock': {'skills': ['arcana', 'history'], 'subclass': 'fiend', 'levels': 'warlock'},
        }
        for class_index, plan in plans.items():
            char_id = f'pc-{class_index}'
            doc = {
                'id': char_id, 'name': class_index.title(), 'player': 'bot:test',
                'race': 'human', 'class': class_index, 'background': 'acolyte',
                'abilities': {'method': 'standard-array', 'str': 15, 'dex': 14, 'con': 13, 'int': 12, 'wis': 10, 'cha': 8},
                'skills': plan['skills'], 'languages': ['dwarvish', 'elvish', 'giant'],
            }
            if plan.get('subclass'):
                doc['subclass'] = plan['subclass']
            doc.update(_level1_spells(class_index))
            path = Path(tmp.name) / f'{class_index}.json'
            path.write_text(json.dumps(doc), encoding='utf-8')
            code, payload = invoke(['char', 'create', '--json-in', str(path), '--as', 'dm'])
            self.assertEqual(code, 0, (class_index, payload))
            code, payload = invoke(['xp', 'milestone', '5', '--as', 'dm'])
            self.assertEqual(code, 0, payload)
            known = set(doc.get('spells_known') or [])
            cantrips = set(spell for spell in known if srd.get('spell', spell)['level'] == 0)
            leveled = known - cantrips
            for step, choice in enumerate(_level_choices(class_index, plan['levels'], leveled, cantrips), start=2):
                args = ['level', 'up', char_id, '--as', char_id]
                if choice:
                    choice_path = choices / f'{char_id}-{step}.json'
                    choice_path.write_text(json.dumps(choice), encoding='utf-8')
                    args.extend(['--json-in', str(choice_path)])
                code, payload = invoke(args)
                self.assertEqual(code, 0, (class_index, step, payload))
            sheet = invoke(['char', 'show', char_id, '--as', 'dm'])[1]['result']
            self.assertEqual(sheet['level'], 5, class_index)
            self.assertGreater(sheet['hp']['max'], sheet and 1)


def _level1_spells(class_index):
    level_row = srd.get('level', f'{class_index}-1')
    info = level_row.get('spellcasting') or {}
    cantrips = _class_spells(class_index, 0, info.get('cantrips_known') or 0, set())
    leveled = _class_spells(class_index, 1, info.get('spells_known') or 0, set(cantrips))
    prepared_need = 0
    if class_index in ('cleric', 'druid'):
        prepared_need = 1
        leveled = _class_spells(class_index, 1, prepared_need, set())
    doc = {}
    if cantrips or leveled:
        doc['spells_known'] = cantrips + leveled
    if class_index in ('cleric', 'druid'):
        doc['spells_prepared'] = leveled
        doc['spells_known'] = cantrips
    return doc


def _level_choices(class_index, plan, leveled, cantrips):
    if plan != class_index and not isinstance(plan, str):
        return plan
    # Filled after create from the live sheet is hard here; use the class tables.
    choices = []
    known = set(leveled)
    known_cantrips = set(cantrips)
    for level in range(2, 6):
        info = srd.get('level', f'{class_index}-{level}').get('spellcasting') or {}
        choice = {}
        features = [feature['index'] for feature in srd.get('level', f'{class_index}-{level}').get('features') or []]
        if 'bard-college' in features:
            choice['subclass'] = 'lore'
            choice['expertise'] = ['arcana', 'history']
            choice['skills'] = ['stealth', 'athletics', 'acrobatics']
        if 'druid-circle' in features:
            choice['subclass'] = 'land'
        if 'martial-archetype' in features:
            choice['subclass'] = 'champion'
        if 'monastic-tradition' in features:
            choice['subclass'] = 'open-hand'
        if 'sacred-oath' in features:
            choice['subclass'] = 'devotion'
        if 'ranger-archetype' in features:
            choice['subclass'] = 'hunter'
            choice['hunters_prey'] = 'colossus-slayer'
        if any(feature.endswith('fighting-style') for feature in features):
            choice['fighting_style'] = _first_style(class_index, features)
        if any('ability-score-improvement' in feature for feature in features):
            choice['asi'] = {'str': 2}
        cantrip_need = info.get('cantrips_known') or 0
        if cantrip_need > len(known_cantrips):
            extra = _class_spells(class_index, 0, cantrip_need - len(known_cantrips), known_cantrips)
            choice['cantrips'] = extra
            known_cantrips.update(extra)
        if class_index == 'wizard':
            extra = _class_spells(class_index, 1, 2, known)
            choice['spells'] = extra
            known.update(extra)
        elif 'spells_known' in info and info['spells_known'] > len(known):
            extra = _class_spells(class_index, 1, info['spells_known'] - len(known), known)
            if len(extra) < info['spells_known'] - len(known):
                extra += _class_spells(class_index, min(level, 2), info['spells_known'] - len(known) - len(extra), known.union(extra))
            choice['spells'] = extra
            known.update(extra)
        choices.append(choice)
    return choices


def _first_style(class_index, features):
    feature_id = next(feature for feature in features if feature.endswith('fighting-style'))
    feature = srd.get('feature', feature_id)
    options = feature['feature_specific']['subfeature_options']['from']['options']
    return options[0]['item']['index']


if __name__ == '__main__':
    unittest.main()
