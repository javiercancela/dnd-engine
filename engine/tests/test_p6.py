"""P6 table: seats, undo, extra attack, filtering, and concurrent shells."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent
BIN = ROOT / 'bin' / 'dnd'
PARTY = Path(__file__).resolve().parent / 'fixtures' / 'party'
YARD = Path(__file__).resolve().parent / 'fixtures' / 'briar-yard.json'
MILL = Path(__file__).resolve().parent / 'fixtures' / 'brackwater-mill.json'
HIDDEN_KEYS = {'dm_notes', 'dm_only', 'target_hp'}


def _keys(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            if key == 'ref' and isinstance(item, str) and item.startswith(('srd:', 'custom:')):
                if 'hp' in value:
                    found.add('MONSTER_HP')
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


class TableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'campaigns'
        self.home = Path(self.tmp.name) / 'home'
        self.root.mkdir()
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env['HOME'] = str(self.home)
        self.env['DND_CAMPAIGN'] = str(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def run_bin(self, args):
        proc = subprocess.run(
            [str(BIN), *args],
            cwd=self.tmp.name,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.stderr, '', proc.stderr)
        self.assertEqual(proc.stdout.count('\n'), 1, proc.stdout[:200])
        return proc.returncode, json.loads(proc.stdout)

    def test_wrapper_runs_from_another_directory(self):
        code, payload = self.run_bin(['help', '--about'])
        self.assertEqual(code, 0, payload)
        self.assertIn('SRD 5.1', payload['result']['about'])

    def test_extra_attack_is_two_swings(self):
        self.run_bin(['campaign', 'new', 'hall', '--adventure', str(YARD), '--seed', '5', '--as', 'dm'])
        self.run_bin(['char', 'create', '--json-in', str(PARTY / 'pc-tor.json'), '--as', 'dm'])
        self.run_bin(['xp', 'milestone', '5', '--as', 'dm'])
        self.run_bin(['level', 'up', 'pc-tor', '--as', 'pc-tor'])
        choice = Path(self.tmp.name) / 'sub.json'
        choice.write_text(json.dumps({'subclass': 'champion'}), encoding='utf-8')
        self.run_bin(['level', 'up', 'pc-tor', '--json-in', str(choice), '--as', 'pc-tor'])
        choice.write_text(json.dumps({'asi': {'str': 2}}), encoding='utf-8')
        self.run_bin(['level', 'up', 'pc-tor', '--json-in', str(choice), '--as', 'pc-tor'])
        self.run_bin(['level', 'up', 'pc-tor', '--as', 'pc-tor'])
        sheet = self.run_bin(['char', 'show', 'pc-tor', '--as', 'dm'])[1]['result']
        self.assertEqual(sheet['level'], 5)
        self.assertIn('extra-attack-1', [feature['id'] for feature in sheet['features']])
        self.run_bin(['encounter', 'spawn', 'lone-goblin', '--as', 'dm'])
        self.run_bin(['override', 'monsters.goblin-1.hp.current', '40', '--reason', 'durable', '--as', 'dm'])
        self.run_bin(['override', 'monsters.goblin-1.hp.max', '40', '--reason', 'durable', '--as', 'dm'])
        self.run_bin(['combat', 'start', '--combatants', 'pc-tor,goblin-1', '--as', 'dm'])
        status = self.run_bin(['combat', 'status', '--as', 'dm'])[1]['result']
        if status['current'] != 'pc-tor':
            self.run_bin(['turn', 'end', '--as', 'dm'])
        first = self.run_bin(['attack', 'pc-tor', 'goblin-1', '--weapon', 'battleaxe', '--range', '5', '--as', 'pc-tor'])
        second = self.run_bin(['attack', 'pc-tor', 'goblin-1', '--weapon', 'battleaxe', '--range', '5', '--as', 'pc-tor'])
        self.assertEqual(first[0], 0, first[1])
        self.assertEqual(second[0], 0, second[1])
        third = self.run_bin(['attack', 'pc-tor', 'goblin-1', '--weapon', 'battleaxe', '--range', '5', '--as', 'pc-tor'])
        self.assertEqual(third[1]['error']['code'], 'NO_ACTION_LEFT')

    def test_table_round_undo_and_filter(self):
        code, payload = self.run_bin([
            'campaign', 'new', 'table', '--adventure', str(YARD), '--seed', '11', '--as', 'dm',
        ])
        self.assertEqual(code, 0, payload)
        for seat, controller in (('bot:tor', 'bot:tor'), ('bot:pip', 'bot:pip'), ('javi', 'javi')):
            code, payload = self.run_bin(['player', 'add', seat, '--controller', controller, '--as', 'dm'])
            self.assertEqual(code, 0, payload)
        for char_id in ('pc-tor', 'pc-pip', 'pc-mira'):
            self.run_bin(['char', 'create', '--json-in', str(PARTY / f'{char_id}.json'), '--as', 'dm'])
        self.run_bin(['encounter', 'spawn', 'lone-goblin', '--as', 'dm'])
        self.run_bin([
            'combat', 'start', '--combatants', 'pc-tor,pc-pip,pc-mira,goblin-1', '--as', 'dm',
        ])
        status = self.run_bin(['combat', 'status', '--as', 'dm'])[1]['result']
        self.assertTrue(status['active'])
        current = status['current']
        goblin = next(row['id'] for row in status['combatants'] if row['id'].startswith('goblin'))
        weapons = {'pc-tor': 'battleaxe', 'pc-pip': 'rapier', 'pc-mira': 'quarterstaff'}

        def attack(actor, attacker):
            return self.run_bin([
                'attack', attacker, goblin, '--weapon', weapons[attacker],
                '--range', '5', '--as', actor,
            ])

        with ThreadPoolExecutor(max_workers=3) as pool:
            if current in weapons:
                others = [name for name in weapons if name != current]
                current_attack = pool.submit(attack, current, current)
                other_attack = pool.submit(attack, others[0], others[0])
            else:
                current_attack = pool.submit(self.run_bin, [
                    'attack', current, 'pc-tor', '--weapon', 'scimitar', '--range', '5', '--as', 'dm',
                ])
                other_attack = pool.submit(attack, 'pc-pip', 'pc-pip')
            rolled = pool.submit(self.run_bin, ['roll', '1d6', '--reason', 'clock', '--as', 'dm'])
        current_code, current_payload = current_attack.result()
        _other_code, other_payload = other_attack.result()
        roll_code, roll_payload = rolled.result()
        self.assertEqual(current_code, 0, current_payload)
        self.assertEqual(other_payload['error']['code'], 'NOT_YOUR_TURN')
        self.assertEqual(roll_code, 0, roll_payload)

        stolen = self.run_bin([
            'attack', 'pc-tor', goblin, '--weapon', 'battleaxe', '--range', '5', '--as', 'pc-pip',
        ])
        self.assertEqual(stolen[1]['error']['code'], 'NOT_AUTHORIZED')

        relay = self.run_bin(['check', 'pc-mira', 'perception', '--dc', '10', '--as', 'pc-mira'])
        self.assertEqual(relay[0], 0, relay[1])
        log = self.run_bin(['log', '--last', '1', '--as', 'dm'])[1]['result']['events'][0]
        self.assertEqual(log.get('relayed_by'), 'dm')

        rolled = self.run_bin(['roll', '1d20', '--reason', 'first', '--as', 'dm'])
        self.assertEqual(rolled[0], 0, rolled[1])
        first_counter = rolled[1]['rolls'][0]['counter']
        undone = self.run_bin(['undo', '--reason', 'miscount', '--as', 'dm'])
        self.assertEqual(undone[0], 0, undone[1])
        self.assertEqual(undone[1]['result']['reverts'], rolled[1]['event_ids'][0])
        again = self.run_bin(['roll', '1d20', '--reason', 'second', '--as', 'dm'])
        self.assertEqual(again[1]['rolls'][0]['counter'], first_counter + 1)
        history = self.run_bin(['log', '--last', '5', '--as', 'dm'])[1]['result']['events']
        types = [event['type'] for event in history]
        self.assertIn('roll', types)
        self.assertIn('revert', types)
        verified = self.run_bin(['verify'])
        self.assertEqual(verified[0], 0, verified[1])
        self.assertTrue(verified[1]['result']['ok'])

        code, payload = self.run_bin(['override', 'rng.counter', '0', '--reason', 'no', '--as', 'dm'])
        self.assertEqual(payload['error']['code'], 'RULE_VIOLATION')
        code, payload = self.run_bin([
            'override', 'characters.pc-tor.hp.current', '9', '--reason', 'ruling', '--as', 'dm',
        ])
        self.assertEqual(code, 0, payload)
        shown = self.run_bin(['char', 'show', 'pc-tor', '--as', 'dm'])[1]['result']
        self.assertEqual(shown['hp']['current'], 9)

    def test_player_output_hides_secrets(self):
        self.run_bin(['campaign', 'new', 'veil', '--adventure', str(MILL), '--seed', '4', '--as', 'dm'])
        self.run_bin(['char', 'create', '--json-in', str(PARTY / 'pc-pip.json'), '--as', 'dm'])
        self.run_bin(['char', 'create', '--json-in', str(PARTY / 'pc-tor.json'), '--as', 'dm'])
        self.run_bin(['roll', '1d20', '--secret', '--as', 'dm'])
        outputs = []
        for args in (
            ['look', '--as', 'pc-pip'],
            ['state', 'show', '--section', 'all', '--as', 'pc-pip'],
            ['char', 'show', 'pc-tor', '--as', 'pc-pip'],
            ['char', 'list', '--as', 'pc-pip'],
            ['log', '--as', 'pc-pip'],
            ['campaign', 'info'],
        ):
            code, payload = self.run_bin(args)
            self.assertEqual(code, 0, (args, payload))
            outputs.append(payload)
        self.run_bin(['go', 'mill-yard', '--as', 'dm'])
        for args in (
            ['combat', 'status', '--as', 'pc-pip'],
            ['look', '--as', 'pc-pip'],
            ['log', '--as', 'pc-pip'],
            ['state', 'show', '--section', 'combat', '--as', 'pc-pip'],
        ):
            code, payload = self.run_bin(args)
            self.assertEqual(code, 0, (args, payload))
            outputs.append(payload)
        # End the fight so the floor can be searched and the trap spotted.
        status = self.run_bin(['combat', 'status', '--as', 'dm'])[1]['result']
        for row in status['combatants']:
            if row['id'].startswith('goblin'):
                self.run_bin(['damage', row['id'], '20', '--type', 'slashing', '--as', 'dm'])
        self.run_bin(['combat', 'end', '--reason', 'victory', '--as', 'dm'])
        self.run_bin(['go', 'mill-floor', '--as', 'dm'])
        for args in (
            ['look', '--as', 'pc-pip'],
            ['search', 'pc-pip', '--as', 'pc-pip'],
            ['trap', 'spot', 'trap-millstone', 'pc-pip', '--as', 'pc-pip'],
            ['log', '--as', 'pc-pip'],
        ):
            code, payload = self.run_bin(args)
            self.assertEqual(code, 0, (args, payload))
            outputs.append(payload)
        for payload in outputs:
            found = set()
            _keys(payload, found)
            self.assertFalse(found.intersection(HIDDEN_KEYS), found.intersection(HIDDEN_KEYS))
            self.assertNotIn('MONSTER_HP', found)
        secret = self.run_bin(['log', '--type', 'roll', '--as', 'pc-pip'])[1]
        for event in secret['result']['events']:
            if event.get('secret'):
                for roll in event.get('rolls') or []:
                    self.assertNotIn('kept', roll)
                    self.assertNotIn('total', roll)
        spot = self.run_bin(['trap', 'spot', 'trap-millstone', 'pc-pip', '--as', 'pc-pip'])[1]
        self.assertNotIn('dc', spot['result'])


if __name__ == '__main__':
    unittest.main()
