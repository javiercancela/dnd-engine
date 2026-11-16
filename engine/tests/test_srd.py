"""Counts and spot values for the vendored SRD 5.1 data."""

import os
import tempfile
import unittest
from pathlib import Path

from dnd import srd


class SrdLoaderTest(unittest.TestCase):
    def test_vendored_counts(self):
        self.assertEqual(len(srd.all('class')), 12)
        self.assertEqual(len(srd.all('race')), 9)
        self.assertEqual(len(srd.all('monster')), 334)
        self.assertEqual(len(srd.all('spell')), 319)

    def test_every_kind_loads(self):
        self.assertGreater(len(srd.kinds()), 0)
        for kind in srd.kinds():
            records = srd.all(kind)
            self.assertGreater(len(records), 0, kind)
            self.assertIn('index', records[0])

    def test_goblin(self):
        goblin = srd.get('monster', 'goblin')
        self.assertEqual(goblin['armor_class'][0]['value'], 15)
        self.assertEqual(goblin['hit_points'], 7)
        self.assertEqual(goblin['hit_dice'], '2d6')
        self.assertEqual(goblin['hit_points_roll'], '2d6')
        scimitar = next(
            action for action in goblin['actions'] if action['name'] == 'Scimitar'
        )
        self.assertEqual(scimitar['attack_bonus'], 4)
        self.assertEqual(scimitar['damage'][0]['damage_dice'], '1d6+2')
        self.assertEqual(scimitar['damage'][0]['damage_type']['index'], 'slashing')
        self.assertIn('+4 to hit', scimitar['desc'])
        self.assertIn('slashing', scimitar['desc'])

    def test_magic_missile(self):
        spell = srd.get('spell', 'magic-missile')
        text = ' '.join(spell['desc'])
        self.assertIn('three glowing darts', text)
        self.assertIn('1d4 + 1 force damage', text)
        self.assertEqual(spell['damage'][0]['damage_type']['index'], 'force')

    def test_fireball(self):
        spell = srd.get('spell', 'fireball')
        text = ' '.join(spell['desc'])
        self.assertIn('8d6 fire damage', text)
        self.assertIn('20-foot-radius', text)
        self.assertEqual(spell['range'], '150 feet')
        self.assertEqual(spell['damage'][0]['damage_at_slot_level']['3'], '8d6')
        self.assertEqual(spell['damage'][0]['damage_type']['index'], 'fire')
        self.assertEqual(spell['dc']['dc_type']['index'], 'dex')
        self.assertEqual(spell['dc']['dc_success'], 'half')

    def test_srd_id_form(self):
        by_prefix = srd.get('monster', 'srd:goblin')
        self.assertIs(by_prefix, srd.get('monster', 'goblin'))
        self.assertEqual(srd.resolve('srd:magic-missile')['name'], 'Magic Missile')
        self.assertIs(
            srd.resolve('srd:monster:goblin'),
            srd.get('monster', 'goblin'),
        )
        with self.assertRaises(KeyError) as caught:
            srd.resolve('srd:goblin')
        message = str(caught.exception)
        self.assertIn('language', message)
        self.assertIn('monster', message)

    def test_missing_entries(self):
        with self.assertRaises(KeyError):
            srd.get('monster', 'not-a-real-monster')
        with self.assertRaises(KeyError):
            srd.get('not-a-kind', 'goblin')
        with self.assertRaises(KeyError):
            srd.resolve('magic-missile')
        with self.assertRaises(KeyError):
            srd.resolve('srd:no-such-slug')

    def test_lookup_ignores_working_directory(self):
        previous = os.getcwd()
        os.chdir(tempfile.gettempdir())
        try:
            self.assertEqual(srd.get('monster', 'goblin')['hit_points'], 7)
        finally:
            os.chdir(previous)

    def test_official_pdf_is_not_in_the_tree(self):
        data_dir = Path(__file__).resolve().parents[1] / 'data' / 'srd-5.1'
        self.assertEqual(list(data_dir.glob('*.pdf')), [])


if __name__ == '__main__':
    unittest.main()
