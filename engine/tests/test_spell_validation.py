"""Spell target bounds and class-specific preparation regressions."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from dnd import cli, combat, levelup, party, spells, store
from dnd.errors import EngineError

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def fixture_state():
    state = store.apply_changes({}, store.initial_changes(11, 'yard', None))
    for char_id in ('pc-mira', 'pc-sel', 'pc-tor'):
        data = json.loads((FIXTURES / 'party' / f'{char_id}.json').read_text())
        _result, event = party.create_character(state, data)
        store.apply_changes(state, event['changes'])
    adventure = json.loads((FIXTURES / 'briar-yard.json').read_text())
    _result, event = combat.spawn_encounter(state, adventure, 'lone-goblin', False)
    store.apply_changes(state, event['changes'])
    goblin = state['monsters']['goblin-1']
    goblin['hp'] = {'max': 1000, 'current': 1000, 'temp': 0}
    for number in range(2, 8):
        monster = copy.deepcopy(goblin)
        monster['id'] = f'goblin-{number}'
        state['monsters'][monster['id']] = monster
    return state


class SpellTargetTest(unittest.TestCase):
    def setUp(self):
        self.state = fixture_state()
        for char_id in ('pc-mira', 'pc-sel'):
            character = self.state['characters'][char_id]
            character['level'] = 5
            character['spellcasting']['slots'] = {
                str(level): {'max': 10, 'used': 0} for level in range(1, 6)
            }

    def allow(self, char_id, spell):
        casting = self.state['characters'][char_id]['spellcasting']
        for key in ('known', 'prepared', 'cantrips'):
            if spell not in casting[key]:
                casting[key].append(spell)

    def cast(self, char_id, spell, targets, slot=None):
        self.allow(char_id, spell)
        return spells.cast_spell(self.state, char_id, spell, targets, slot, None, char_id, False, False)

    def test_invalid_targets_do_not_spend_resources(self):
        cases = (
            ('pc-mira', 'fire-bolt', ['goblin-1'] * 3),
            ('pc-mira', 'fire-bolt', ['goblin-1', 'goblin-2']),
            ('pc-sel', 'cure-wounds', ['pc-tor', 'pc-sel']),
            ('pc-sel', 'cure-wounds', ['pc-tor', 'pc-tor']),
            ('pc-mira', 'false-life', ['pc-tor']),
            ('pc-mira', 'false-life', ['pc-mira', 'pc-tor']),
            ('pc-mira', 'acid-splash', ['goblin-1', 'goblin-2', 'goblin-3']),
            ('pc-mira', 'burning-hands', ['goblin-1', 'goblin-1']),
            ('pc-sel', 'bless', ['pc-tor', 'pc-tor']),
            ('pc-sel', 'bless', [f'goblin-{n}' for n in range(1, 5)]),
            ('pc-sel', 'prayer-of-healing', [f'goblin-{n}' for n in range(1, 8)]),
            ('pc-mira', 'magic-missile', [f'goblin-{n}' for n in range(1, 5)]),
            ('pc-mira', 'eldritch-blast', ['goblin-1', 'goblin-2', 'goblin-3']),
        )
        for char_id, spell, targets in cases:
            with self.subTest(spell=spell, targets=targets):
                self.allow(char_id, spell)
                before = copy.deepcopy(self.state)
                with self.assertRaises(EngineError):
                    self.cast(char_id, spell, targets)
                self.assertEqual(self.state, before)

    def test_area_and_bounded_multiple_targets_are_preserved(self):
        goblins = [f'goblin-{number}' for number in range(1, 5)]
        result, _event = self.cast('pc-mira', 'burning-hands', goblins)
        self.assertEqual(len(result['targets']), 4)
        result, _event = self.cast('pc-mira', 'acid-splash', goblins[:2])
        self.assertEqual(len(result['targets']), 2)
        result, _event = self.cast('pc-sel', 'bless', goblins, slot=2)
        self.assertEqual(result['targets'], goblins)
        result, _event = self.cast('pc-sel', 'prayer-of-healing', [f'goblin-{n}' for n in range(1, 7)])
        self.assertEqual(len(result['healing']), 6)

    def test_eldritch_blast_resolves_exactly_the_level_beam_count(self):
        for level, targets, expected in (
            (1, ['goblin-1'], ['goblin-1']),
            (5, ['goblin-1'], ['goblin-1', 'goblin-1']),
            (5, ['goblin-1', 'goblin-2'], ['goblin-1', 'goblin-2']),
            (5, ['goblin-1', 'goblin-1'], ['goblin-1', 'goblin-1']),
        ):
            with self.subTest(level=level, targets=targets):
                self.state['characters']['pc-mira']['level'] = level
                result, event = self.cast('pc-mira', 'eldritch-blast', targets)
                self.assertEqual([row['id'] for row in result['targets']], expected)
                self.assertEqual(sum(roll['expr'] == '1d20' for roll in event['rolls']), len(expected))

    def test_magic_missile_keeps_bounded_dart_allocation(self):
        for targets in (['goblin-1'], ['goblin-1', 'goblin-2', 'goblin-3'], ['goblin-1', 'goblin-1', 'goblin-2']):
            with self.subTest(targets=targets):
                result, event = self.cast('pc-mira', 'magic-missile', targets)
                self.assertEqual({row['id'] for row in result['targets']}, set(targets))
                self.assertEqual(len(event['rolls']), 3)
                self.assertTrue(all(row['damage'] > 0 for row in result['targets']))

    def test_later_beam_does_not_reject_a_cast_when_first_beam_kills(self):
        goblin = self.state['monsters']['goblin-1']
        goblin['hp']['current'] = 1
        goblin['ac'] = 1
        result, event = self.cast('pc-mira', 'eldritch-blast', ['goblin-1'])
        self.assertTrue(result['targets'][0]['hit'])
        self.assertEqual(result['targets'][1]['skipped'], 'target-dead')
        store.apply_changes(self.state, event['changes'])
        self.assertTrue(self.state['monsters']['goblin-1']['dead'])

    def test_self_default_single_target_and_manual_fallback(self):
        result, _event = self.cast('pc-mira', 'false-life', [])
        self.assertEqual([row['id'] for row in result['targets']], ['pc-mira'])
        result, _event = self.cast('pc-sel', 'cure-wounds', ['pc-tor'])
        self.assertEqual(len(result['healing']), 1)
        result, _event = self.cast('pc-mira', 'scorching-ray', ['goblin-1'] * 4)
        self.assertEqual(result['effect'], 'manual')

    def test_shield_rejects_another_target_instead_of_silently_replacing_it(self):
        _result, event = combat.start_combat(self.state, ['pc-mira'], [])
        store.apply_changes(self.state, event['changes'])
        before = copy.deepcopy(self.state)
        with self.assertRaises(EngineError):
            spells.cast_spell(self.state, 'pc-mira', 'shield', ['pc-tor'], None, None, 'pc-mira', False, True)
        self.assertEqual(self.state, before)
        result, _event = spells.cast_spell(self.state, 'pc-mira', 'shield', [], None, None, 'pc-mira', False, True)
        self.assertEqual(result['effect'], 'shield')

    def test_cli_rejects_duplicate_fire_bolt_without_appending_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'review'
            directory.mkdir()
            (root / '.dnd_active').write_text('review\n')
            (directory / 'campaign.json').write_text('{}')
            store.append_event(directory, {
                'actor': 'dm', 'type': 'campaign', 'cmd': 'test setup', 'rolls': [],
                'changes': [{'op': 'set', 'path': key, 'to': value} for key, value in self.state.items()],
            })
            before = (directory / 'events.jsonl').read_bytes()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(['cast', 'pc-mira', 'fire-bolt', '--targets', 'goblin-1,goblin-1', '--as', 'pc-mira', '--campaign', str(root)])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(out.getvalue())['ok'])
            self.assertEqual((directory / 'events.jsonl').read_bytes(), before)


class SpellPreparationTest(unittest.TestCase):
    def setUp(self):
        self.state = fixture_state()
        self.state['progression'] = {'available': 5}

    def test_cleric_rejects_off_list_but_accepts_class_and_domain_spells(self):
        with self.assertRaises(EngineError):
            levelup.level_up(self.state, 'pc-sel', {'prepared': ['magic-missile']}, False, 'pc-sel')
        _result, event = levelup.level_up(self.state, 'pc-sel', {'prepared': ['guiding-bolt', 'bless']}, False, 'pc-sel')
        store.apply_changes(self.state, event['changes'])
        spells.cast_spell(self.state, 'pc-sel', 'bless', ['pc-tor'], None, None, 'pc-sel', False, False)
        self.state['characters']['pc-sel']['spellcasting']['prepared'] = ['magic-missile']
        with self.assertRaises(EngineError):
            spells.cast_spell(self.state, 'pc-sel', 'magic-missile', ['goblin-1'], None, None, 'pc-sel', False, False)

    def test_wizard_must_prepare_from_spellbook(self):
        choices = {'spells': ['grease', 'thunderwave'], 'prepared': ['charm-person']}
        with self.assertRaises(EngineError):
            levelup.level_up(self.state, 'pc-mira', choices, False, 'pc-mira')
        choices['prepared'] = ['grease', 'magic-missile']
        levelup.level_up(self.state, 'pc-mira', choices, False, 'pc-mira')

    def test_domain_exception_and_known_casters(self):
        character = copy.deepcopy(self.state['characters']['pc-sel'])
        character['class'] = 'druid'
        casting = character['spellcasting']
        casting.update({'known': [], 'cantrips': ['guidance', 'produce-flame'], 'domain': ['misty-step']})
        row = {'spellcasting': {'cantrips_known': 2, 'spell_slots_level_1': 4, 'spell_slots_level_2': 2}}
        casting['slots']['2'] = {'max': 2, 'used': 0}
        levelup._learn_spells(character, 'druid', row, {'prepared': ['misty-step', 'cure-wounds']})
        self.assertTrue(spells._available(character, spells.srd.get('spell', 'misty-step'), False))
        character['class'] = 'ranger'
        casting.update({'known': ['cure-wounds'], 'prepared': []})
        self.assertTrue(spells._available(character, spells.srd.get('spell', 'cure-wounds'), False))
        row['spellcasting']['spells_known'] = 1
        with self.assertRaisesRegex(EngineError, 'does not prepare spells'):
            levelup._learn_spells(character, 'ranger', row, {'prepared': ['cure-wounds']})


if __name__ == '__main__':
    unittest.main()
