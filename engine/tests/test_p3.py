"""P3 combat: scripted goblin fight and the combat error codes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dnd.combat import _mitigate

ENGINE = Path(__file__).resolve().parents[1]
PARTY = Path(__file__).resolve().parent / 'fixtures' / 'party'
ADVENTURE = Path(__file__).resolve().parent / 'fixtures' / 'briar-yard.json'
SCRIPT = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'battle-script.json').read_text())


def _normalize(text):
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        event['ts'] = 'TS'
        lines.append(json.dumps(event, sort_keys=True, separators=(',', ':')))
    return '\n'.join(lines) + '\n'


class CombatTest(unittest.TestCase):
    def make_box(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'campaigns'
        home = Path(temp.name) / 'home'
        root.mkdir()
        home.mkdir()
        return root, home

    def invoke(self, root, home, args):
        env = os.environ.copy()
        env['HOME'] = str(home)
        env['DND_CAMPAIGN'] = str(root)
        expanded = []
        for part in args:
            if part == 'ADVENTURE':
                expanded.append(str(ADVENTURE))
            elif part.startswith('FIXTURE:'):
                expanded.append(str(PARTY / part.split(':', 1)[1]))
            else:
                expanded.append(part)
        proc = subprocess.run(
            [sys.executable, '-m', 'dnd', *expanded],
            cwd=ENGINE,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.stderr, '', proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(proc.stdout.count('\n'), 1, proc.stdout[:200])
        return proc.returncode, payload

    def play(self, root, home):
        last = None
        for args in SCRIPT:
            code, payload = self.invoke(root, home, args)
            self.assertEqual(code, 0, (args, payload))
            last = payload
        log = (root / 'fight' / 'events.jsonl').read_text(encoding='utf-8')
        return _normalize(log), last

    def test_scripted_battle_is_identical_three_times(self):
        logs = []
        endings = []
        for _ in range(3):
            root, home = self.make_box()
            log, ending = self.play(root, home)
            logs.append(log)
            endings.append(ending['result'])
        self.assertEqual(logs[0], logs[1])
        self.assertEqual(logs[1], logs[2])
        self.assertGreater(logs[0].count('\n'), 40)
        ending = endings[0]
        self.assertEqual(ending['winner'], 'party')
        self.assertEqual(ending['rounds'], 5)
        self.assertEqual(
            sorted((row['id'], row['xp']) for row in ending['xp']),
            [('pc-mira', 50), ('pc-pip', 50), ('pc-sel', 50), ('pc-tor', 50)],
        )

    def test_combat_errors_death_and_filtering(self):
        root, home = self.make_box()
        code, payload = self.invoke(root, home, [
            'campaign', 'new', 'skirmish', '--adventure', 'ADVENTURE', '--seed', '7', '--as', 'dm',
        ])
        self.assertEqual(code, 0, payload)
        for char_id in ('pc-mira', 'pc-pip'):
            code, payload = self.invoke(root, home, [
                'char', 'create', '--json-in', f'FIXTURE:{char_id}.json', '--as', 'dm',
            ])
            self.assertEqual(code, 0, payload)
        code, spawned = self.invoke(root, home, ['encounter', 'spawn', 'lone-goblin', '--as', 'dm'])
        self.assertEqual(spawned['result']['spawned'], ['goblin-1'])
        code, wolf = self.invoke(root, home, ['encounter', 'spawn', 'briar-wolf', '--as', 'dm'])
        self.assertEqual(wolf['result']['spawned'], ['briar-wolf-1'])
        code, state = self.invoke(root, home, ['state', 'show', '--section', 'all', '--as', 'pc-pip'])
        monster = next(row for row in state['result']['monsters'] if row['id'] == 'briar-wolf-1')
        self.assertEqual(monster['status'], 'healthy')
        self.assertNotIn('hp', monster)
        code, dm_state = self.invoke(root, home, ['state', 'show', '--section', 'all', '--as', 'dm'])
        wolf_hp = next(row for row in dm_state['result']['monsters'] if row['id'] == 'briar-wolf-1')
        self.assertEqual(wolf_hp['hp']['max'], 11)

        code, payload = self.invoke(root, home, [
            'combat', 'start', '--combatants', 'pc-mira,goblin-1', '--as', 'dm',
        ])
        self.assertEqual(code, 0, payload)
        for _ in range(4):
            _code, status = self.invoke(root, home, ['combat', 'status', '--as', 'dm'])
            current = status['result']['current']
            if current == 'pc-mira':
                break
            actor = 'dm' if current.startswith('goblin') else current
            code, payload = self.invoke(root, home, ['turn', 'end', '--as', actor])
            self.assertEqual(code, 0, payload)

        code, payload = self.invoke(root, home, [
            'attack', 'goblin-1', 'pc-mira', '--weapon', 'scimitar', '--range', '5', '--as', 'dm',
        ])
        self.assertEqual(payload['error']['code'], 'NOT_YOUR_TURN')
        code, payload = self.invoke(root, home, [
            'attack', 'pc-mira', 'goblin-1', '--weapon', 'quarterstaff', '--range', '5', '--as', 'pc-pip',
        ])
        self.assertEqual(payload['error']['code'], 'NOT_AUTHORIZED')
        code, payload = self.invoke(root, home, [
            'attack', 'pc-mira', 'goblin-1', '--weapon', 'quarterstaff', '--range', '30', '--as', 'pc-mira',
        ])
        self.assertEqual(payload['error']['code'], 'OUT_OF_RANGE')
        code, payload = self.invoke(root, home, [
            'attack', 'pc-mira', 'pc-mira', '--weapon', 'quarterstaff', '--range', '5', '--as', 'pc-mira',
        ])
        self.assertEqual(payload['error']['code'], 'INVALID_TARGET')
        code, payload = self.invoke(root, home, ['move', 'pc-mira', '--ft', '20', '--as', 'pc-mira'])
        self.assertEqual(code, 0, payload)
        code, payload = self.invoke(root, home, ['move', 'pc-mira', '--ft', '20', '--as', 'pc-mira'])
        self.assertEqual(payload['error']['code'], 'NO_ACTION_LEFT')
        code, payload = self.invoke(root, home, [
            'attack', 'pc-mira', 'goblin-1', '--weapon', 'quarterstaff', '--range', '5', '--as', 'pc-mira',
        ])
        self.assertEqual(code, 0, payload)
        code, payload = self.invoke(root, home, [
            'attack', 'pc-mira', 'goblin-1', '--weapon', 'quarterstaff', '--range', '5', '--as', 'pc-mira',
        ])
        self.assertEqual(payload['error']['code'], 'NO_ACTION_LEFT')
        code, payload = self.invoke(root, home, ['damage', 'pc-mira', '7', '--type', 'slashing', '--as', 'dm'])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload['result']['target_status'], 'dying')
        code, payload = self.invoke(root, home, [
            'attack', 'pc-mira', 'goblin-1', '--weapon', 'quarterstaff', '--range', '5', '--as', 'pc-mira',
        ])
        self.assertEqual(payload['error']['code'], 'INCAPACITATED')
        code, save = self.invoke(root, home, ['death-save', 'pc-mira', '--as', 'pc-mira'])
        self.assertEqual(code, 0, save)
        self.assertIn(save['result']['state'], ('dying', 'stable', 'dead', 'conscious'))
        code, hidden = self.invoke(root, home, ['combat', 'status', '--as', 'pc-pip'])
        goblin = next(row for row in hidden['result']['combatants'] if row['id'] == 'goblin-1')
        self.assertNotIn('hp', goblin)
        self.assertIn(goblin['status'], ('healthy', 'bloodied', 'dead'))

    def test_temp_hp_and_condition_duration(self):
        root, home = self.make_box()
        self.invoke(root, home, [
            'campaign', 'new', 'camp', '--adventure', 'ADVENTURE', '--seed', '3', '--as', 'dm',
        ])
        self.invoke(root, home, ['char', 'create', '--json-in', 'FIXTURE:pc-tor.json', '--as', 'dm'])
        self.invoke(root, home, ['encounter', 'spawn', 'lone-goblin', '--as', 'dm'])
        code, payload = self.invoke(root, home, ['temp-hp', 'goblin-1', '5', '--as', 'dm'])
        self.assertEqual(payload['result']['temp'], 5)
        code, payload = self.invoke(root, home, ['temp-hp', 'goblin-1', '3', '--as', 'dm'])
        self.assertEqual(payload['result']['temp'], 5)
        code, payload = self.invoke(root, home, [
            'condition', 'add', 'goblin-1', 'poisoned', '--duration', 'rounds:1', '--as', 'dm',
        ])
        self.assertEqual(code, 0, payload)
        self.invoke(root, home, [
            'combat', 'start', '--combatants', 'pc-tor,goblin-1', '--as', 'dm',
        ])
        seen_round_2 = False
        for _ in range(8):
            _code, status = self.invoke(root, home, ['combat', 'status', '--as', 'dm'])
            if status['result'].get('round') == 2:
                seen_round_2 = True
                break
            current = status['result']['current']
            actor = 'dm' if str(current).startswith('goblin') else current
            code, payload = self.invoke(root, home, ['turn', 'end', '--as', actor])
            self.assertEqual(code, 0, payload)
        self.assertTrue(seen_round_2)
        _code, state = self.invoke(root, home, ['state', 'show', '--section', 'all', '--as', 'dm'])
        goblin = next(row for row in state['result']['monsters'] if row['id'] == 'goblin-1')
        self.assertEqual(goblin['conditions'], [])
        self.assertEqual(_mitigate(
            {'damage_resistances': ['fire'], 'damage_vulnerabilities': [], 'damage_immunities': []},
            'fire', 5,
        ), 2)

    def test_action_and_heal(self):
        root, home = self.make_box()
        self.invoke(root, home, [
            'campaign', 'new', 'camp', '--adventure', 'ADVENTURE', '--seed', '9', '--as', 'dm',
        ])
        self.invoke(root, home, ['char', 'create', '--json-in', 'FIXTURE:pc-pip.json', '--as', 'dm'])
        self.invoke(root, home, ['encounter', 'spawn', 'lone-goblin', '--as', 'dm'])
        self.invoke(root, home, [
            'combat', 'start', '--combatants', 'pc-pip,goblin-1', '--as', 'dm',
        ])
        for _ in range(4):
            _code, status = self.invoke(root, home, ['combat', 'status', '--as', 'dm'])
            if status['result']['current'] == 'pc-pip':
                break
            self.invoke(root, home, ['turn', 'end', '--as', 'dm'])
        code, payload = self.invoke(root, home, ['action', 'pc-pip', 'dodge', '--as', 'pc-pip'])
        self.assertEqual(code, 0, payload)
        code, payload = self.invoke(root, home, ['action', 'pc-pip', 'dash', '--as', 'pc-pip'])
        self.assertEqual(payload['error']['code'], 'NO_ACTION_LEFT')
        code, payload = self.invoke(root, home, ['damage', 'pc-pip', '3', '--type', 'piercing', '--as', 'dm'])
        self.assertEqual(code, 0, payload)
        code, healed = self.invoke(root, home, ['heal', 'pc-pip', '2', '--as', 'dm'])
        self.assertEqual(healed['result']['amount'], 2)
        self.assertEqual(healed['result']['hp'], 9)


if __name__ == '__main__':
    unittest.main()
