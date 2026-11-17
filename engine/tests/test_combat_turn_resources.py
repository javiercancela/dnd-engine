"""First-round reactions, once-per-turn death saves, and usage failures."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dnd import cli, combat, store
from dnd.errors import EngineError

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


class CombatTurnResourceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'campaigns'
        home = Path(temporary.name) / 'home'
        home.mkdir()
        with patch('pathlib.Path.home', return_value=home):
            self.invoke('campaign', 'new', 'table', '--adventure',
                        str(FIXTURES / 'briar-yard.json'), '--seed', '11', '--as', 'dm')
        self.directory = self.root / 'table'
        for char_id in ('pc-mira', 'pc-tor'):
            self.invoke('char', 'create', '--json-in',
                        str(FIXTURES / 'party' / f'{char_id}.json'), '--as', 'dm')
        self.invoke('encounter', 'spawn', 'lone-goblin', '--as', 'dm')

    def invoke(self, *args):
        return cli.dispatch([*args, '--campaign', str(self.root)])

    def state(self):
        return store.fold(store.read_events(self.directory))[0]

    def snapshot(self):
        return tuple((self.directory / name).read_bytes()
                     for name in ('events.jsonl', 'state.json'))

    def fail_unchanged(self, code, *args):
        before = self.snapshot()
        with self.assertRaises(EngineError) as caught:
            self.invoke(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.snapshot(), before)

    def start(self, *extra):
        return self.invoke('combat', 'start', '--combatants',
                           'pc-mira,pc-tor,goblin-1', *extra, '--as', 'dm')

    def end_current(self):
        state = self.state()
        current = store.turn_view(state)['current']
        actor = 'dm' if current in state['monsters'] else current
        return self.invoke('turn', 'end', '--as', actor)

    def until(self, creature_id):
        for _ in range(8):
            if store.turn_view(self.state())['current'] == creature_id:
                return
            self.end_current()
        self.fail(f'never reached {creature_id}')

    def test_initial_reaction_can_be_spent_then_refreshes_on_first_turn(self):
        self.start()
        self.assertNotEqual(store.turn_view(self.state())['current'], 'pc-mira')
        self.assertEqual(set(self.state()['combat']['economy']),
                         {'pc-mira', 'pc-tor', 'goblin-1'})
        self.invoke('cast', 'pc-mira', 'shield', '--reaction', '--as', 'pc-mira')
        self.fail_unchanged('NO_ACTION_LEFT', 'cast', 'pc-mira', 'shield',
                            '--reaction', '--as', 'pc-mira')
        self.until('pc-mira')
        self.invoke('cast', 'pc-mira', 'shield', '--reaction', '--as', 'pc-mira')
        self.fail_unchanged('NO_ACTION_LEFT', 'cast', 'pc-mira', 'shield',
                            '--reaction', '--as', 'pc-mira')

    def test_surprise_blocks_reaction_until_first_turn_ends(self):
        self.start('--surprised', 'pc-mira')
        self.fail_unchanged('NO_ACTION_LEFT', 'cast', 'pc-mira', 'shield',
                            '--reaction', '--as', 'pc-mira')
        self.until('pc-mira')
        self.fail_unchanged('NO_ACTION_LEFT', 'cast', 'pc-mira', 'shield',
                            '--reaction', '--as', 'pc-mira')
        self.end_current()
        self.invoke('cast', 'pc-mira', 'shield', '--reaction', '--as', 'pc-mira')
        self.fail_unchanged('NO_ACTION_LEFT', 'cast', 'pc-mira', 'shield',
                            '--reaction', '--as', 'pc-mira')

    def test_incapacitation_blocks_reaction_without_spending_its_token(self):
        self.invoke('condition', 'add', 'pc-mira', 'stunned', '--as', 'dm')
        self.start()
        self.fail_unchanged('INCAPACITATED', 'cast', 'pc-mira', 'shield',
                            '--reaction', '--as', 'pc-mira')
        self.invoke('condition', 'remove', 'pc-mira', 'stunned', '--as', 'dm')
        self.invoke('cast', 'pc-mira', 'shield', '--reaction', '--as', 'pc-mira')

    def test_combat_start_persists_first_turn_creature_resets(self):
        # Seed 11 puts Tor first. Previous temporary state must disappear
        # both from the cache and from subsequent event replay.
        for field in ('shield_spell', 'dodging', 'disengaged'):
            self.invoke('override', f'characters.pc-tor.{field}', 'true',
                        '--reason', 'previous fight', '--as', 'dm')
        self.start()
        self.assertEqual(store.turn_view(self.state())['current'], 'pc-tor')
        for field in ('shield_spell', 'dodging', 'disengaged'):
            self.assertFalse(self.state()['characters']['pc-tor'][field])
        self.assertEqual(self.state(), store.read_state(self.directory))

    def test_one_death_save_per_turn_and_reset_on_next_turn(self):
        self.start()
        self.until('pc-tor')
        hp = self.state()['characters']['pc-tor']['hp']['max']
        self.invoke('damage', 'pc-tor', str(hp), '--type', 'slashing', '--as', 'dm')
        first = self.invoke('death-save', 'pc-tor', '--as', 'pc-tor')
        self.assertEqual(first['result']['state'], 'dying')
        self.fail_unchanged('NO_ACTION_LEFT', 'death-save', 'pc-tor', '--as', 'pc-tor')
        self.end_current()
        self.until('pc-tor')
        self.invoke('death-save', 'pc-tor', '--as', 'pc-tor')
        self.assertEqual(self.state(), store.read_state(self.directory))

    def test_natural_twenty_keeps_action_but_not_another_death_save(self):
        # A two-combatant seed-39 fight uses counters 0 and 1 for initiative,
        # then counter 2 is a natural 20 on Tor's first death save.
        state = store.apply_changes({}, store.initial_changes(39, 'briar-yard', None))
        source = self.state()
        state['characters'] = {'pc-tor': source['characters']['pc-tor']}
        state['monsters'] = source['monsters']
        _result, event = combat.start_combat(state, ['pc-tor', 'goblin-1'], [])
        state = store.apply_changes(state, event['changes'])
        self.assertEqual(store.turn_view(state)['current'], 'pc-tor')
        hp = state['characters']['pc-tor']['hp']['max']
        _result, event = combat.deal_damage(state, 'pc-tor', str(hp), 'slashing', None)
        state = store.apply_changes(state, event['changes'])
        result, event = combat.death_save(state, 'pc-tor', 'pc-tor')
        state = store.apply_changes(state, event['changes'])
        self.assertEqual(result['natural'], 20)
        self.assertEqual(result['state'], 'conscious')
        self.assertEqual(state['characters']['pc-tor']['hp']['current'], 1)
        self.assertTrue(state['combat']['economy']['pc-tor']['action'])
        self.assertTrue(state['combat']['economy']['pc-tor']['death_save_done'])
        _result, event = combat.deal_damage(state, 'pc-tor', '1', 'slashing', None)
        state = store.apply_changes(state, event['changes'])
        with self.assertRaises(EngineError) as caught:
            combat.death_save(state, 'pc-tor', 'pc-tor')
        self.assertEqual(caught.exception.code, 'NO_ACTION_LEFT')

    def test_bad_duration_is_json_usage_error_without_writes(self):
        for duration in ('', 'rounds:nope', 'rounds:-1', 'rounds:0', 'rounds:1:2',
                         'save:dex', 'save:dex:nope', 'save:luck:10', 'save:dex:-1',
                         'until:end-of-turn:', 'until:end-of-turn:pc-tor:extra'):
            with self.subTest(duration=duration):
                before = self.snapshot()
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.main(['condition', 'add', 'pc-tor', 'poisoned',
                                     '--duration', duration, '--as', 'dm',
                                     '--campaign', str(self.root)])
                self.assertEqual(code, 2)
                self.assertEqual(out.getvalue().count('\n'), 1)
                self.assertEqual(json.loads(out.getvalue())['error']['code'], 'BAD_ARGS')
                self.assertEqual(self.snapshot(), before)

    def test_valid_durations_keep_the_documented_shape(self):
        for duration, parsed in (
            (None, None),
            ('rounds:2', {'type': 'rounds', 'rounds': 2}),
            ('save:dex:12', {'type': 'save', 'ability': 'dex', 'dc': 12}),
            ('until:end-of-turn:pc-tor', {'type': 'end-of-turn', 'id': 'pc-tor'}),
        ):
            with self.subTest(duration=duration):
                self.assertEqual(combat.parse_duration(duration), parsed)
        self.invoke('condition', 'add', 'pc-tor', 'poisoned',
                    '--duration', 'rounds:2', '--as', 'dm')
        condition = self.state()['characters']['pc-tor']['conditions'][0]
        self.assertEqual(condition['duration'], {'type': 'rounds', 'rounds': 2})

    def test_malformed_combat_lists_are_usage_errors_without_writes(self):
        for combatants, surprised in (
            ('', ''), (' ', ''), ('pc-tor,pc-tor', ''),
            (',pc-tor', ''), ('pc-tor,,goblin-1', ''), ('pc-tor,goblin-1,', ''),
            ('pc-tor,goblin-1', 'pc-mira'),
            ('pc-tor,goblin-1', 'pc-tor,pc-tor'),
            ('pc-tor,goblin-1', 'pc-tor,'),
        ):
            with self.subTest(combatants=combatants, surprised=surprised):
                self.fail_unchanged('BAD_ARGS', 'combat', 'start', '--combatants',
                                    combatants, '--surprised', surprised, '--as', 'dm')
        self.fail_unchanged('UNKNOWN_ID', 'combat', 'start', '--combatants',
                            'pc-tor,missing', '--as', 'dm')
        for ids in (None, 'pc-tor', [None], [[]]):
            with self.subTest(ids=ids):
                with self.assertRaises(EngineError) as caught:
                    combat.start_combat(self.state(), ids, [])
                self.assertEqual(caught.exception.code, 'BAD_ARGS')


if __name__ == '__main__':
    unittest.main()
