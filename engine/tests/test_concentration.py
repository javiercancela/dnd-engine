"""Concentration replacement, termination, and persisted side effects."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dnd import cli, store
from dnd.errors import EngineError

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


class ConcentrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'campaigns'
        home = Path(temporary.name) / 'home'
        home.mkdir()
        adventure = json.loads((FIXTURES / 'briar-yard.json').read_text())
        adventure['traps'] = [
            {'id': 'small-trap', 'effect': {'damage': '1d1', 'type': 'force'}},
            {'id': 'large-trap', 'effect': {'damage': '1d1+49', 'type': 'force'}},
        ]
        path = Path(temporary.name) / 'adventure.json'
        path.write_text(json.dumps(adventure), encoding='utf-8')
        with patch('pathlib.Path.home', return_value=home):
            self.invoke('campaign', 'new', 'table', '--adventure', str(path), '--seed', '7', '--as', 'dm')
        self.directory = self.root / 'table'
        for char_id in ('pc-sel', 'pc-tor', 'pc-mira'):
            self.invoke('char', 'create', '--json-in', str(FIXTURES / 'party' / f'{char_id}.json'), '--as', 'dm')

    def invoke(self, *args):
        return cli.dispatch([*args, '--campaign', str(self.root)])

    def state(self):
        state, _head = store.fold(store.read_events(self.directory))
        self.assertEqual(state, store.read_state(self.directory))
        return state

    def bless(self, targets='pc-tor', caster='pc-sel'):
        return self.invoke('cast', caster, 'bless', '--targets', targets, '--as', caster)

    def override(self, path, value):
        return self.invoke('override', path, json.dumps(value), '--reason', 'test setup', '--as', 'dm')

    def assert_ended(self, caster='pc-sel', targets=('pc-tor',)):
        state = self.state()
        self.assertIsNone(state['characters'][caster]['spellcasting']['concentration'])
        for target_id in targets:
            creature = state['characters'].get(target_id) or state['monsters'][target_id]
            self.assertFalse(creature.get('blessed'), target_id)
        return state

    def changed_paths(self):
        return {change['path'] for change in store.read_events(self.directory)[-1]['changes']}

    def test_recast_keeps_overlapping_targets(self):
        self.bless('pc-tor,pc-sel')
        self.bless('pc-tor,pc-mira')
        state = self.state()
        self.assertTrue(state['characters']['pc-tor']['blessed'])
        self.assertTrue(state['characters']['pc-mira']['blessed'])
        self.assertFalse(state['characters']['pc-sel']['blessed'])
        self.assertEqual(state['characters']['pc-sel']['spellcasting']['concentration']['targets'], ['pc-tor', 'pc-mira'])

    def test_recast_logs_cleanup_of_previous_targets(self):
        self.bless('pc-tor')
        self.bless('pc-mira')
        state = self.state()
        self.assertFalse(state['characters']['pc-tor']['blessed'])
        self.assertTrue(state['characters']['pc-mira']['blessed'])
        self.assertIn('characters.pc-tor', self.changed_paths())

    def test_manual_concentration_spell_replaces_bless(self):
        self.bless()
        result = self.invoke('cast', 'pc-sel', 'shield-of-faith', '--targets', 'pc-mira', '--as', 'pc-sel')
        self.assertEqual(result['result']['effect'], 'manual')
        state = self.state()
        self.assertFalse(state['characters']['pc-tor']['blessed'])
        self.assertEqual(state['characters']['pc-sel']['spellcasting']['concentration']['spell'], 'shield-of-faith')

    def test_prefixed_spell_reference_records_canonical_concentration(self):
        self.invoke('cast', 'pc-sel', 'srd:bless', '--targets', 'pc-tor', '--as', 'pc-sel')
        concentration = self.state()['characters']['pc-sel']['spellcasting']['concentration']
        self.assertEqual(concentration['spell'], 'bless')
        self.invoke('condition', 'add', 'pc-sel', 'incapacitated', '--as', 'dm')
        self.assert_ended()

    def test_rejected_replacement_keeps_prior_concentration(self):
        self.bless()
        self.invoke('damage', 'pc-mira', '1000', '--type', 'force', '--as', 'dm')
        before = (self.directory / 'events.jsonl').read_bytes()
        with self.assertRaises(EngineError):
            self.bless('pc-mira')
        self.assertEqual(before, (self.directory / 'events.jsonl').read_bytes())
        state = self.state()
        self.assertTrue(state['characters']['pc-tor']['blessed'])
        self.assertEqual(state['characters']['pc-sel']['spellcasting']['concentration']['spell'], 'bless')

    def test_zero_hit_points_end_concentration_without_a_save(self):
        self.bless()
        before = self.state()
        result = self.invoke('damage', 'pc-sel', str(before['characters']['pc-sel']['hp']['max']), '--type', 'force', '--as', 'dm')
        state = self.assert_ended()
        self.assertEqual(state['characters']['pc-sel']['hp']['current'], 0)
        self.assertEqual(state['rng']['counter'], before['rng']['counter'])
        self.assertEqual(result['rolls'], [])
        self.assertTrue(result['result']['concentration']['broken'])

    def test_death_ends_concentration_without_a_save(self):
        self.bless()
        result = self.invoke('damage', 'pc-sel', '1000', '--type', 'force', '--as', 'dm')
        state = self.assert_ended()
        self.assertTrue(state['characters']['pc-sel']['dead'])
        self.assertEqual(result['rolls'], [])

    def test_incapacitating_conditions_end_concentration_without_rolling(self):
        for name in ('incapacitated', 'paralyzed', 'petrified', 'stunned', 'unconscious'):
            with self.subTest(condition=name):
                self.invoke('rest', 'long', '--as', 'dm')
                self.bless()
                counter = self.state()['rng']['counter']
                result = self.invoke('condition', 'add', 'pc-sel', name, '--as', 'dm')
                state = self.assert_ended()
                self.assertEqual(state['rng']['counter'], counter)
                self.assertEqual(result['rolls'], [])
                self.invoke('condition', 'remove', 'pc-sel', name, '--as', 'dm')

    def test_sleep_persists_cleanup_outside_its_targets(self):
        self.bless()
        self.override('characters.pc-sel.hp.current', 1)
        result = self.invoke('cast', 'pc-mira', 'sleep', '--targets', 'pc-sel', '--as', 'pc-mira')
        self.assertIn('pc-sel', result['result']['affected'])
        self.assert_ended()
        self.assertEqual(len(result['rolls']), 1)
        self.assertIn('characters.pc-tor', self.changed_paths())

    def test_spell_damage_persists_failed_concentration_cleanup(self):
        self.bless()
        self.invoke('temp-hp', 'pc-sel', '100', '--as', 'dm')
        result = self.invoke('cast', 'pc-mira', 'magic-missile', '--targets', 'pc-sel', '--as', 'pc-mira')
        state = self.assert_ended()
        self.assertEqual(state['characters']['pc-sel']['hp']['current'], state['characters']['pc-sel']['hp']['max'])
        self.assertEqual(len(result['rolls']), 4)
        self.assertIn('characters.pc-tor', self.changed_paths())

    def test_trap_damage_ends_concentration_at_zero_without_extra_save(self):
        self.bless()
        self.override('characters.pc-sel.hp.current', 1)
        result = self.invoke('trap', 'trigger', 'small-trap', '--target', 'pc-sel', '--as', 'dm')
        self.assert_ended()
        self.assertEqual(len(result['rolls']), 1)

    def test_trap_damage_requires_save_while_caster_is_conscious(self):
        self.bless()
        self.invoke('temp-hp', 'pc-sel', '100', '--as', 'dm')
        result = self.invoke('trap', 'trigger', 'large-trap', '--target', 'pc-sel', '--as', 'dm')
        state = self.assert_ended()
        self.assertEqual(state['characters']['pc-sel']['hp']['current'], state['characters']['pc-sel']['hp']['max'])
        self.assertEqual(len(result['rolls']), 2)
        self.assertEqual(result['rolls'][-1]['expr'], '1d20')
        self.assertIn('broken', result['result']['concentration'])

    def test_another_casters_bless_survives_when_one_concentration_ends(self):
        data = json.loads((FIXTURES / 'party' / 'pc-sel.json').read_text())
        data.update({'id': 'pc-sel-two', 'name': 'Second cleric', 'player': 'bot:second-cleric'})
        path = self.directory.parent / 'second-cleric.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        self.invoke('char', 'create', '--json-in', str(path), '--as', 'dm')
        self.bless()
        self.bless(caster='pc-sel-two')
        self.invoke('condition', 'add', 'pc-sel', 'incapacitated', '--as', 'dm')
        self.assertTrue(self.state()['characters']['pc-tor']['blessed'])
        self.invoke('condition', 'add', 'pc-sel-two', 'incapacitated', '--as', 'dm')
        self.assert_ended(caster='pc-sel-two')

    def test_long_rest_cleans_monster_targets_and_replays(self):
        self.invoke('encounter', 'spawn', 'lone-goblin', '--as', 'dm')
        self.bless('goblin-1')
        self.invoke('rest', 'long', '--as', 'dm')
        self.assert_ended(targets=('goblin-1',))


if __name__ == '__main__':
    unittest.main()
