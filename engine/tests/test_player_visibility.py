"""Public history must not leak private values through alternate representations."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dnd import cli, store

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
PUBLIC_CHARACTER_KEYS = {'id', 'name', 'race', 'class', 'level', 'status'}


class PlayerVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        def active(root, name, _directory):
            (Path(root) / '.dnd_active').write_text(name + '\n', encoding='utf-8')

        self.pointer = patch('dnd.store.set_active', active)
        self.pointer.start()
        self.addCleanup(self.pointer.stop)
        self.invoke('campaign', 'new', 'veil', '--adventure', str(FIXTURES / 'briar-yard.json'),
                    '--seed', '184736251', '--as', 'dm')
        for char_id in ('pc-tor', 'pc-mira'):
            self.invoke('char', 'create', '--json-in', str(FIXTURES / 'party' / f'{char_id}.json'),
                        '--as', 'dm')

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([*args, '--campaign', str(self.root)])
        self.assertEqual(output.getvalue().count('\n'), 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0, payload)
        return payload

    def events(self, actor, *args):
        return self.invoke('log', *args, '--as', actor)['result']['events']

    def test_hp_dc_and_dm_prose_are_absent_from_every_public_representation(self):
        self.invoke('encounter', 'spawn', 'lone-goblin', '--as', 'dm')
        for field, number in (('max', '27183'), ('current', '27181')):
            self.invoke('override', f'monsters.goblin-1.hp.{field}', number,
                        '--reason', 'SECRET-RULING', '--as', 'dm')
        self.invoke('heal', 'goblin-1', '1', '--as', 'dm')
        self.invoke('override', 'dm_notes.SECRET-PATH', '{"reveal":"SECRET-VALUE"}',
                    '--reason', 'SECRET-RULING', '--as', 'dm')
        self.invoke('check', 'pc-tor', 'perception', '--dc', '7319', '--secret-dc', '--as', 'dm')
        public = self.events('pc-tor')
        encoded = json.dumps(public)
        for forbidden in ('27183', '27181', '27182', '7319', 'SECRET-RULING',
                          'SECRET-PATH', 'SECRET-VALUE', 'dm_notes'):
            self.assertNotIn(forbidden, encoded)
        check = public[-1]
        self.assertEqual(check['cmd'], 'check')
        self.assertIn('total', check['outcome'])
        self.assertNotIn('success', check['outcome'])
        healed = next(event for event in public if event['type'] == 'heal')
        self.assertEqual(healed['outcome']['amount'], 1)
        self.assertIn('target_status', healed['outcome'])

    def test_other_characters_are_public_summaries_and_owner_keeps_own_sheet(self):
        other = next(event for event in self.events('pc-tor')
                     if event['changes'] and event['changes'][0]['path'] == 'characters.pc-mira')
        self.assertEqual(set(other['changes'][0]['to']), PUBLIC_CHARACTER_KEYS)
        self.assertNotIn('spellcasting', json.dumps(other))
        self.assertNotIn('base_abilities', json.dumps(other))
        own = next(event for event in self.events('pc-mira')
                   if event['changes'] and event['changes'][0]['path'] == 'characters.pc-mira')
        self.assertIn('abilities', own['changes'][0]['to'])
        self.assertIn('spellcasting', own['changes'][0]['to'])
        self.invoke('rest', 'long', '--as', 'dm')
        rest = self.events('pc-tor', '--last', '1')[0]
        for change in rest['changes']:
            if change['path'] == 'characters.pc-mira':
                self.assertEqual(set(change['from']), PUBLIC_CHARACTER_KEYS)
                self.assertEqual(set(change['to']), PUBLIC_CHARACTER_KEYS)

    def test_secret_rolls_cannot_be_reconstructed_from_public_seed_or_hashes(self):
        self.invoke('roll', '1d20+7319', '--secret', '--reason', 'SECRET-ROLL', '--as', 'dm')
        public = self.events('pc-tor')
        encoded = json.dumps(public)
        self.assertNotIn('184736251', encoded)
        self.assertNotIn('rng', encoded)
        self.assertNotIn('7319', encoded)
        self.assertNotIn('SECRET-ROLL', encoded)
        for event in public:
            self.assertNotIn('hash', event)
            self.assertNotIn('prev_hash', event)
        self.assertEqual(public[-1]['rolls'], [{'counter': 0, 'secret': True}])
        self.assertEqual(public[-1]['cmd'], 'roll')

    def test_rolled_monster_hp_is_hidden_and_monster_snapshot_has_status(self):
        self.invoke('encounter', 'spawn', 'lone-goblin', '--roll-hp', '--as', 'dm')
        public = self.events('pc-tor', '--last', '1')[0]
        self.assertEqual(public['outcome']['spawned'], ['goblin-1'])
        self.assertEqual(public['rolls'], [{'counter': 0, 'secret': True}])
        monster = public['changes'][0]['to']['goblin-1']
        self.assertEqual(set(monster), {'id', 'name', 'status'})
        self.assertEqual(monster['status'], 'healthy')

    def test_dm_log_and_stored_events_remain_complete(self):
        self.invoke('roll', '1d20', '--secret', '--reason', 'SECRET-ROLL', '--as', 'dm')
        before = store.read_events(self.root / 'veil')
        original_bytes = (self.root / 'veil' / 'events.jsonl').read_bytes()
        saved = copy.deepcopy(before)
        self.events('pc-tor')
        self.assertEqual(self.events('dm'), saved)
        self.assertEqual(store.read_events(self.root / 'veil'), saved)
        self.assertEqual((self.root / 'veil' / 'events.jsonl').read_bytes(), original_bytes)
        rng = next(change['to'] for change in saved[0]['changes'] if change['path'] == 'rng')
        self.assertEqual(rng['seed'], 184736251)
        self.assertIn('total', saved[-1]['rolls'][0])
        self.assertEqual(saved[-1]['reason'], 'SECRET-ROLL')

    def test_unknown_nested_outcome_fields_are_not_public(self):
        event = {
            'id': 9, 'type': 'cast', 'actor': 'pc-mira', 'cmd': 'cast pc-mira fire-bolt',
            'changes': [], 'rolls': [],
            'outcome': {'effect': 'attack', 'targets': [
                {'id': 'goblin-1', 'hit': True, 'hp': 27183,
                 'dm_only': 'SECRET-NESTED', 'unexpected': {'label': 'SECRET-WRAPPED'}}]},
        }
        shown = cli._redact(event, 'pc-tor')
        self.assertEqual(shown['outcome']['targets'], [{'id': 'goblin-1', 'hit': True}])


if __name__ == '__main__':
    unittest.main()
