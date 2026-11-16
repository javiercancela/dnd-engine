"""P2 characters: derived stats, checks, inventory, rejected builds."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dnd.chargen import auto_fail_save

ENGINE = Path(__file__).resolve().parents[1]
PARTY = Path(__file__).resolve().parent / 'fixtures' / 'party'
ADVENTURE = Path(__file__).resolve().parent / 'fixtures' / 'waystone.json'
EXPECTED = json.loads((PARTY / 'expected.json').read_text(encoding='utf-8'))
IDS = ('pc-tor', 'pc-mira', 'pc-sel', 'pc-pip')


class CharacterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'campaigns'
        self.home = Path(self.tmp.name) / 'home'
        self.root.mkdir()
        self.home.mkdir()
        self.invoke((
            'campaign', 'new', 'party', '--adventure', str(ADVENTURE),
            '--seed', '4242', '--as', 'dm',
        ))

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

    def create_party(self):
        for char_id in IDS:
            code, payload = self.invoke((
                'char', 'create', '--json-in', str(PARTY / f'{char_id}.json'), '--as', 'dm',
            ))
            self.assertEqual(code, 0, payload)
            self.assertFalse(payload['result']['pending'])

    def test_fixture_stats_match_hand_computation(self):
        # Arithmetic is in docs/party-stats.md. expected.json is that result.
        self.create_party()
        for char_id, expected in EXPECTED.items():
            code, payload = self.invoke(('char', 'show', char_id, '--as', 'dm'))
            self.assertEqual(code, 0, payload)
            sheet = payload['result']
            for key, value in expected.items():
                if key == 'spellcasting':
                    self.assertEqual(sheet['spellcasting']['dc'], value['dc'])
                    self.assertEqual(sheet['spellcasting']['attack'], value['attack'])
                else:
                    self.assertEqual(sheet[key], value, char_id + ' ' + key)
            self.assertEqual(sheet['level'], 1)
            self.assertEqual(sheet['background'] if 'background' in sheet else 'acolyte', 'acolyte')

    def test_invalid_builds_are_rejected(self):
        before = (self.root / 'party' / 'events.jsonl').read_bytes()
        cases = {
            'athletics': self._mira(skills=['athletics', 'arcana']),
            'race': self._mira(race='aasimar'),
            'subrace': self._mira(race='elf'),
            'background': self._mira(background='soldier'),
            'array': self._mira(abilities={
                'method': 'standard-array', 'str': 15, 'dex': 15, 'con': 13,
                'int': 12, 'wis': 10, 'cha': 8,
            }),
            'points': self._fighter(abilities={
                'method': 'point-buy', 'str': 15, 'dex': 15, 'con': 15,
                'int': 8, 'wis': 8, 'cha': 10,
            }),
        }
        for name, document in cases.items():
            path = Path(self.tmp.name) / f'{name}.json'
            path.write_text(json.dumps(document), encoding='utf-8')
            code, payload = self.invoke(('char', 'create', '--json-in', str(path), '--as', 'dm'))
            self.assertEqual(code, 1, name)
            self.assertEqual(payload['error']['code'], 'RULE_VIOLATION', name)
        self.assertEqual((self.root / 'party' / 'events.jsonl').read_bytes(), before)

    def test_point_buy_and_rolled_scores(self):
        document = self._fighter(abilities={
            'method': 'point-buy', 'str': 15, 'dex': 15, 'con': 15,
            'int': 8, 'wis': 8, 'cha': 8,
        })
        document['id'] = 'pc-buy'
        document['name'] = 'Buy'
        path = Path(self.tmp.name) / 'buy.json'
        path.write_text(json.dumps(document), encoding='utf-8')
        code, payload = self.invoke(('char', 'create', '--json-in', str(path), '--as', 'dm'))
        self.assertEqual(code, 0, payload)
        code, shown = self.invoke(('char', 'show', 'pc-buy', '--as', 'dm'))
        self.assertEqual(shown['result']['abilities']['str'], 16)
        self.assertEqual(shown['result']['abilities']['int'], 9)

        rolled = self._fighter(abilities={'method': 'rolled'})
        rolled['id'] = 'pc-roll'
        rolled['name'] = 'Roll'
        path.write_text(json.dumps(rolled), encoding='utf-8')
        code, payload = self.invoke(('char', 'create', '--json-in', str(path), '--as', 'dm'))
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload['result']['pending'])
        pool = payload['result']['scores']
        self.assertEqual(len(pool), 6)
        for score in pool:
            self.assertGreaterEqual(score, 3)
            self.assertLessEqual(score, 18)
        assigned = dict(zip(('str', 'dex', 'con', 'int', 'wis', 'cha'), pool))
        path.write_text(json.dumps(assigned), encoding='utf-8')
        code, payload = self.invoke((
            'char', 'assign-scores', 'pc-roll', '--json-in', str(path), '--as', 'dm',
        ))
        self.assertEqual(code, 0, payload)
        code, shown = self.invoke(('char', 'show', 'pc-roll', '--as', 'dm'))
        for ability, base in assigned.items():
            self.assertEqual(shown['result']['abilities'][ability], base + 1)

    def test_checks_saves_contest_and_group(self):
        self.create_party()
        code, check = self.invoke(('check', 'pc-mira', 'arcana', '--dc', '10', '--as', 'dm'))
        self.assertEqual(code, 0, check)
        self.assertEqual(check['result']['total'], check['result']['natural'] + 5)
        self.assertEqual(check['result']['success'], check['result']['total'] >= 10)
        code, save = self.invoke(('save', 'pc-mira', 'str', '--dc', '10', '--as', 'pc-mira'))
        self.assertEqual(save['result']['total'], save['result']['natural'] - 1)
        code, helped = self.invoke((
            'check', 'pc-mira', 'arcana', '--dc', '10', '--help-from', 'pc-tor', '--as', 'dm',
        ))
        self.assertEqual(len(helped['rolls'][0]['dice']), 2)
        self.assertEqual(helped['rolls'][0]['adv'], 'adv')
        code, contest = self.invoke((
            'contest', 'pc-tor', 'athletics', 'pc-pip', 'acrobatics', '--as', 'dm',
        ))
        self.assertEqual(code, 0, contest)
        self.assertIn(contest['result']['winner'], ('pc-tor', 'pc-pip', None))
        code, group = self.invoke(('group-check', 'stealth', '--dc', '10', '--as', 'dm'))
        self.assertEqual(group['result']['party'], 4)
        self.assertEqual(group['result']['success'], group['result']['successes'] >= 2)
        code, hidden = self.invoke((
            'check', 'pc-mira', 'perception', '--dc', '30', '--secret-dc', '--as', 'dm',
        ))
        self.assertTrue(hidden['result']['success'] is False or hidden['result']['success'] is True)
        _code, player_log = self.invoke(('log', '--as', 'pc-pip', '--last', '1'))
        outcome = player_log['result']['events'][0]['outcome']
        self.assertNotIn('dc', outcome)
        self.assertNotIn('success', outcome)
        _code, dm_log = self.invoke(('log', '--as', 'dm', '--last', '1'))
        self.assertIn('dc', dm_log['result']['events'][0]['outcome'])
        code, denied = self.invoke(('check', 'pc-mira', 'arcana', '--dc', '10', '--as', 'pc-pip'))
        self.assertEqual(denied['error']['code'], 'NOT_AUTHORIZED')

    def test_inventory_equip_and_potion(self):
        self.create_party()
        code, shown = self.invoke(('inv', 'show', 'pc-tor', '--as', 'dm'))
        self.assertEqual(shown['result']['coins']['gp'], 15)
        self.assertEqual(shown['result']['capacity'], 15 * 15)
        self.assertEqual(shown['result']['attunement'], {'used': 0, 'max': 3})
        code, equipped = self.invoke(('unequip', 'pc-tor', 'shield', '--as', 'dm'))
        self.assertEqual(code, 0, equipped)
        code, sheet = self.invoke(('char', 'show', 'pc-tor', '--as', 'dm'))
        self.assertEqual(sheet['result']['ac'], 17)
        code, payload = self.invoke((
            'inv', 'give', 'potion-of-healing', '--to', 'pc-tor', '--as', 'dm',
        ))
        self.assertEqual(code, 0, payload)
        code, used = self.invoke(('use', 'pc-tor', 'potion-of-healing', '--as', 'dm'))
        self.assertEqual(used['result']['effect'], 'heal')
        self.assertGreaterEqual(used['result']['amount'], 4)
        self.assertLessEqual(used['result']['amount'], 10)
        self.assertEqual(used['result']['hp'], 14)
        code, denied = self.invoke((
            'inv', 'transfer', 'gp', 'pc-tor', 'pc-pip', '--qty', '5', '--as', 'pc-pip',
        ))
        self.assertEqual(denied['error']['code'], 'NOT_AUTHORIZED')
        code, moved = self.invoke((
            'inv', 'transfer', 'gp', 'pc-tor', 'pc-pip', '--qty', '5', '--as', 'pc-tor',
        ))
        self.assertEqual(code, 0, moved)
        _code, tor = self.invoke(('inv', 'show', 'pc-tor', '--as', 'pc-tor'))
        _code, pip = self.invoke(('inv', 'show', 'pc-pip', '--as', 'dm'))
        self.assertEqual(tor['result']['coins']['gp'], 10)
        self.assertEqual(pip['result']['coins']['gp'], 20)

    def test_views_and_relay(self):
        self.create_party()
        code, public = self.invoke(('char', 'show', 'pc-mira', '--as', 'pc-pip'))
        self.assertNotIn('hp', public['result'])
        self.assertEqual(public['result']['status'], 'healthy')
        self.assertEqual(public['result']['class'], 'wizard')
        code, private = self.invoke(('char', 'show', 'pc-mira', '--as', 'dm'))
        self.assertEqual(private['result']['hp']['max'], 7)
        code, roster = self.invoke(('char', 'list', '--as', 'pc-tor'))
        self.assertEqual(
            [row['id'] for row in roster['result']['party']],
            ['pc-mira', 'pc-pip', 'pc-sel', 'pc-tor'],
        )
        self.invoke(('check', 'pc-mira', 'arcana', '--dc', '5', '--as', 'pc-mira'))
        _code, log = self.invoke(('log', '--as', 'dm', '--last', '1', '--type', 'check'))
        self.assertEqual(log['result']['events'][0].get('relayed_by'), 'dm')
        code, state = self.invoke(('state', 'show', '--section', 'party', '--as', 'pc-pip'))
        mira = next(row for row in state['result']['party'] if row['id'] == 'pc-mira')
        pip = next(row for row in state['result']['party'] if row['id'] == 'pc-pip')
        self.assertNotIn('hp', mira)
        self.assertIn('hp', pip)

    def test_paralyzed_auto_fails_strength_save(self):
        self.assertTrue(auto_fail_save({'conditions': ['paralyzed']}, 'str'))
        self.assertTrue(auto_fail_save({'conditions': ['stunned']}, 'dex'))
        self.assertFalse(auto_fail_save({'conditions': ['paralyzed']}, 'wis'))
        self.assertFalse(auto_fail_save({'conditions': []}, 'str'))

    def _mira(self, **overrides):
        document = json.loads((PARTY / 'pc-mira.json').read_text(encoding='utf-8'))
        document['id'] = 'pc-bad'
        document.update(overrides)
        return document

    def _fighter(self, **overrides):
        return {
            'id': 'pc-extra',
            'name': 'Extra',
            'player': 'bot:extra',
            'race': 'human',
            'class': 'fighter',
            'background': 'acolyte',
            'abilities': {'method': 'standard-array', 'str': 15, 'dex': 14, 'con': 13, 'int': 12, 'wis': 10, 'cha': 8},
            'skills': ['athletics', 'intimidation'],
            'languages': ['elvish', 'dwarvish', 'giant'],
            'fighting_style': 'defense',
            **overrides,
        }


if __name__ == '__main__':
    unittest.main()
