"""P4 magic and rests."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dnd import srd
from dnd import spells

ENGINE = Path(__file__).resolve().parents[1]
PARTY = Path(__file__).resolve().parent / 'fixtures' / 'party'
ADVENTURE = Path(__file__).resolve().parent / 'fixtures' / 'briar-yard.json'


class MagicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'campaigns'
        self.home = Path(self.tmp.name) / 'home'
        self.root.mkdir()
        self.home.mkdir()
        mira = json.loads((PARTY / 'pc-mira.json').read_text(encoding='utf-8'))
        mira['id'] = 'pc-bh'
        mira['name'] = 'Bh'
        mira['spells_prepared'] = ['magic-missile', 'shield', 'sleep', 'burning-hands']
        self.bh = Path(self.tmp.name) / 'bh.json'
        self.bh.write_text(json.dumps(mira), encoding='utf-8')

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

    def until(self, creature_id):
        for _ in range(8):
            current = self.ok(['combat', 'status', '--as', 'dm'])['result']['current']
            if current == creature_id:
                return
            actor = 'dm' if str(current).startswith('goblin') else current
            self.ok(['turn', 'end', '--as', actor])
        self.fail(f'never reached {creature_id}')

    def test_model_counts_and_upcast_dice(self):
        counts = spells.modelled_counts()
        self.assertEqual(len(counts['modelled']), 31)
        self.assertEqual(len(counts['manual']), 206)
        burning = srd.get('spell', 'burning-hands')
        self.assertEqual(spells._damage_expr(burning, 1, 1), '3d6')
        self.assertEqual(spells._damage_expr(burning, 2, 1), '4d6')
        cure = srd.get('spell', 'cure-wounds')
        self.assertIn('2d8', spells._slot_expr(cure['heal_at_slot_level'], 2, {
            'abilities': {'wis': 16}, 'spellcasting': {'ability': 'wis'},
        }))

    def test_spells_rests_and_no_slot(self):
        self.ok(['campaign', 'new', 'magic', '--adventure', str(ADVENTURE), '--seed', '21', '--as', 'dm'])
        for char_id in ('pc-mira', 'pc-sel', 'pc-tor'):
            self.ok(['char', 'create', '--json-in', str(PARTY / f'{char_id}.json'), '--as', 'dm'])
        self.ok(['char', 'create', '--json-in', str(self.bh), '--as', 'dm'])
        self.ok(['encounter', 'spawn', 'yard-goblins', '--as', 'dm'])

        code, payload = self.invoke([
            'cast', 'pc-mira', 'burning-hands', '--targets', 'goblin-1', '--as', 'pc-mira',
        ])
        self.assertEqual(payload['error']['code'], 'RULE_VIOLATION')

        burned = self.ok([
            'cast', 'pc-bh', 'burning-hands', '--targets', 'goblin-1', '--range', '15', '--as', 'pc-bh',
        ])
        row = burned['result']['targets'][0]
        rolled = next(item['total'] for item in burned['rolls'] if 'd6' in item['expr'])
        if row['saved']:
            self.assertEqual(row['damage'], rolled // 2)
        else:
            self.assertEqual(row['damage'], rolled)
        self.assertEqual(row['type'], 'fire')
        self.assertEqual(burned['result']['dc'], 13)

        sleep = self.ok(['cast', 'pc-mira', 'sleep', '--targets', 'goblin-2,goblin-3', '--as', 'pc-mira'])
        pool = sleep['result']['pool']
        state = self.ok(['state', 'show', '--section', 'combat', '--as', 'dm'])['result']
        asleep = {monster['id']: monster for monster in state['monsters']}
        affected = []
        remaining = pool
        for goblin_id in ('goblin-2', 'goblin-3'):
            if asleep[goblin_id]['hp']['current'] <= remaining and goblin_id not in affected:
                remaining -= asleep[goblin_id]['hp']['current']
                affected.append(goblin_id)
        self.assertEqual(sleep['result']['affected'], [gid for gid in ('goblin-2', 'goblin-3') if gid in sleep['result']['affected']])
        for goblin_id in sleep['result']['affected']:
            self.assertEqual(asleep[goblin_id]['status'], 'unconscious')
            self.assertEqual(asleep[goblin_id]['hp']['current'], 7)

        self.ok(['combat', 'start', '--combatants', 'pc-mira,pc-tor,goblin-4', '--as', 'dm'])
        self.until('pc-mira')
        shield = self.ok(['cast', 'pc-mira', 'shield', '--reaction', '--as', 'pc-mira'])
        self.assertEqual(shield['result']['effect'], 'shield')
        self.ok(['turn', 'end', '--as', 'pc-mira'])
        current = self.ok(['combat', 'status', '--as', 'dm'])['result']['current']
        if current == 'pc-tor':
            attack = self.ok(['attack', 'pc-tor', 'pc-mira', '--weapon', 'battleaxe', '--range', '5', '--as', 'pc-tor'])
        else:
            attack = self.ok(['attack', current, 'pc-mira', '--weapon', 'scimitar', '--range', '5', '--as', 'dm'])
        self.assertEqual(attack['result']['ac'], 18)
        self.ok(['combat', 'end', '--reason', 'fled', '--as', 'dm'])

        self.ok(['combat', 'start', '--combatants', 'pc-sel,goblin-4', '--as', 'dm'])
        self.until('pc-sel')
        bless = self.ok(['cast', 'pc-sel', 'bless', '--targets', 'pc-sel', '--as', 'pc-sel'])
        self.assertEqual(bless['result']['effect'], 'bless')
        broken = False
        for _ in range(20):
            damaged = self.ok(['damage', 'pc-sel', '1', '--type', 'bludgeoning', '--as', 'dm'])['result']
            if damaged.get('concentration', {}).get('broken'):
                broken = True
                self.assertFalse(damaged['concentration']['success'])
                break
        self.assertTrue(broken)
        sheet = self.ok(['char', 'show', 'pc-sel', '--as', 'dm'])['result']
        self.assertIsNone(sheet['spellcasting']['concentration'])
        self.assertFalse(sheet.get('blessed'))
        self.ok(['turn', 'end', '--as', 'pc-sel'])
        self.until('pc-sel')
        word = self.ok(['cast', 'pc-sel', 'healing-word', '--targets', 'pc-sel', '--as', 'pc-sel'])
        self.assertEqual(word['result']['slot'], 1)
        code, blocked = self.invoke(['cast', 'pc-sel', 'cure-wounds', '--targets', 'pc-sel', '--as', 'pc-sel'])
        self.assertEqual(blocked['error']['code'], 'RULE_VIOLATION')
        self.assertIn('bonus-action', blocked['error']['message'])
        flame = self.ok(['cast', 'pc-sel', 'sacred-flame', '--targets', 'goblin-4', '--as', 'pc-sel'])
        self.assertEqual(flame['result']['effect'], 'save')
        slots = self.ok(['char', 'show', 'pc-sel', '--as', 'dm'])['result']['spellcasting']['slots']['1']
        self.assertEqual(slots['used'], 2)
        self.ok(['combat', 'end', '--reason', 'fled', '--as', 'dm'])

        self.ok(['rest', 'long', '--as', 'dm'])
        self.ok(['damage', 'pc-sel', '9', '--type', 'slashing', '--as', 'dm'])
        cure = self.ok(['cast', 'pc-sel', 'cure-wounds', '--targets', 'pc-sel', '--as', 'pc-sel'])
        rolled = cure['rolls'][-1]['total']
        self.assertEqual(cure['result']['healing'][0]['amount'], rolled + 3)
        self.assertEqual(self.ok(['char', 'show', 'pc-sel', '--as', 'dm'])['result']['hp']['current'], min(10, 1 + rolled + 3))

        manual = self.ok(['cast', 'pc-sel', 'shield-of-faith', '--targets', 'pc-sel', '--as', 'pc-sel'])
        self.assertEqual(manual['result']['effect'], 'manual')
        self.assertEqual(manual['result']['slot'], 1)

        self.ok(['combat', 'start', '--combatants', 'pc-tor,goblin-4', '--as', 'dm'])
        self.until('pc-tor')
        self.ok(['action', 'pc-tor', 'second-wind', '--as', 'pc-tor'])
        self.assertEqual(self.ok(['char', 'show', 'pc-tor', '--as', 'dm'])['result']['features'][1]['uses'], 0)
        self.ok(['combat', 'end', '--reason', 'fled', '--as', 'dm'])
        self.ok(['cast', 'pc-mira', 'magic-missile', '--targets', 'goblin-4', '--as', 'pc-mira'])
        self.ok(['rest', 'short', '--spend', 'pc-tor:1', '--as', 'dm'])
        mira = self.ok(['char', 'show', 'pc-mira', '--as', 'dm'])['result']
        self.assertEqual(mira['spellcasting']['slots']['1']['used'], 0)
        self.assertEqual(mira['features'][1]['uses'], 0)
        tor = self.ok(['char', 'show', 'pc-tor', '--as', 'dm'])['result']
        self.assertEqual(next(row for row in tor['features'] if row['id'] == 'second-wind')['uses'], 1)
        self.assertEqual(tor['hit_dice']['d10'], 0)
        self.ok(['cast', 'pc-mira', 'magic-missile', '--targets', 'goblin-4', '--as', 'pc-mira'])
        self.ok(['rest', 'short', '--as', 'dm'])
        self.assertEqual(
            self.ok(['char', 'show', 'pc-mira', '--as', 'dm'])['result']['spellcasting']['slots']['1']['used'],
            1,
        )
        self.ok(['rest', 'long', '--as', 'dm'])
        mira = self.ok(['char', 'show', 'pc-mira', '--as', 'dm'])['result']
        self.assertEqual(mira['spellcasting']['slots']['1']['used'], 0)
        self.assertEqual(mira['features'][1]['uses'], 1)
        tor = self.ok(['char', 'show', 'pc-tor', '--as', 'dm'])['result']
        self.assertEqual(tor['hp']['current'], tor['hp']['max'])
        self.assertEqual(tor['hit_dice']['d10'], 1)

        self.ok(['cast', 'pc-mira', 'magic-missile', '--targets', 'goblin-4', '--as', 'pc-mira'])
        self.ok(['cast', 'pc-mira', 'mage-armor', '--as', 'pc-mira'])
        code, payload = self.invoke(['cast', 'pc-mira', 'sleep', '--targets', 'goblin-4', '--as', 'pc-mira'])
        self.assertEqual(payload['error']['code'], 'NO_SLOT')
        ritual = self.ok(['cast', 'pc-mira', 'detect-magic', '--ritual', '--as', 'pc-mira'])
        self.assertEqual(ritual['result']['effect'], 'manual')
        self.assertIsNone(ritual['result']['slot'])
        self.assertEqual(
            self.ok(['char', 'show', 'pc-mira', '--as', 'dm'])['result']['spellcasting']['slots']['1']['used'],
            2,
        )


if __name__ == '__main__':
    unittest.main()
