"""P1 core: envelopes, campaign log, seeded rolls, verify."""

import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dnd.dice import parse_expr, roll_expr
from dnd.errors import ALL_CODES, DEFERRED_CODES
from dnd import store
from dnd.cli import SRD_ATTRIBUTION

ENGINE = Path(__file__).resolve().parents[1]
REPO = ENGINE.parent
ADVENTURE = Path(__file__).resolve().parent / 'fixtures' / 'waystone.json'
NOTICE = REPO / 'NOTICE.md'

SCRIPT = (
    ('campaign', 'new', 'waystone', '--adventure', str(ADVENTURE), '--seed', '4242', '--as', 'dm'),
    ('roll', '1d20+5', '--as', 'dm'),
    ('roll', '4d6kh3', '--as', 'dm', '--reason', 'stats'),
    ('roll', '2d6kl1', '--as', 'pc-mira'),
    ('roll', 'd%', '--as', 'dm'),
    ('roll', '1d20', '--adv', '--as', 'dm'),
    ('roll', '1d20', '--dis', '--as', 'dm'),
    ('roll', '1d20', '--adv', '--dis', '--as', 'dm'),
    ('roll', '1d20+1', '--secret', '--as', 'dm', '--reason', 'hidden'),
)


def _normalize(text):
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        event['ts'] = 'TS'
        lines.append(json.dumps(event, sort_keys=True, separators=(',', ':')))
    return '\n'.join(lines) + '\n'


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'campaigns'
        self.home = Path(self.tmp.name) / 'home'
        self.root.mkdir()
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, args, *, root=None, env_root=None):
        env = os.environ.copy()
        env['HOME'] = str(self.home)
        env['DND_CAMPAIGN'] = str(self.root if env_root is None else env_root)
        proc = subprocess.run(
            [sys.executable, '-m', 'dnd', *args],
            cwd=ENGINE,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.stderr, '', proc.stderr)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f'{exc}: stdout={proc.stdout!r}')
        self.assertTrue(proc.stdout.endswith('\n'), proc.stdout)
        self.assertEqual(proc.stdout.count('\n'), 1, proc.stdout)
        return proc.returncode, payload

    def play(self, root, home):
        saved_root, saved_home = self.root, self.home
        self.root, self.home = root, home
        try:
            for args in SCRIPT:
                code, payload = self.invoke(args)
                self.assertEqual(code, 0, payload)
                self.assertTrue(payload['ok'])
        finally:
            self.root, self.home = saved_root, saved_home
        return (root / 'waystone' / 'events.jsonl').read_text(encoding='utf-8')

    def test_same_seed_is_byte_identical_ignoring_ts(self):
        first_root = Path(self.tmp.name) / 'a' / 'campaigns'
        second_root = Path(self.tmp.name) / 'b' / 'campaigns'
        first_home = Path(self.tmp.name) / 'a' / 'home'
        second_home = Path(self.tmp.name) / 'b' / 'home'
        for path in (first_root, second_root, first_home, second_home):
            path.mkdir(parents=True)
        first = self.play(first_root, first_home)
        second = self.play(second_root, second_home)
        self.assertEqual(_normalize(first), _normalize(second))
        self.assertEqual(re.sub(r'"ts":"[^"]*"', '"ts":"TS"', first), re.sub(r'"ts":"[^"]*"', '"ts":"TS"', second))
        self.assertEqual(
            [json.loads(line)['hash'] for line in first.splitlines()],
            [json.loads(line)['hash'] for line in second.splitlines()],
        )
        events = [json.loads(line) for line in first.splitlines()]
        self.assertEqual([event['type'] for event in events], ['campaign'] + ['roll'] * 8)
        rolls = [event['rolls'][0] for event in events[1:]]
        self.assertEqual([roll['counter'] for roll in rolls], list(range(8)))
        rng = random.Random(hashlib.sha256(b'4242:0').digest())
        face = rng.randint(1, 20)
        self.assertEqual(rolls[0]['dice'], [face])
        self.assertEqual(rolls[0]['total'], face + 5)
        self.assertEqual(len(rolls[1]['dice']), 4)
        self.assertEqual(len(rolls[1]['kept']), 3)
        self.assertEqual(rolls[1]['total'], sum(rolls[1]['kept']))
        self.assertEqual(rolls[2]['kept'], [min(rolls[2]['dice'])])
        self.assertEqual(rolls[4]['adv'], 'adv')
        self.assertEqual(rolls[4]['kept'], [max(rolls[4]['dice'])])
        self.assertEqual(len(rolls[4]['dice']), 2)
        self.assertEqual(rolls[5]['adv'], 'dis')
        self.assertEqual(rolls[5]['kept'], [min(rolls[5]['dice'])])
        self.assertEqual(rolls[6]['adv'], 'none')
        self.assertEqual(len(rolls[6]['dice']), 1)
        self.assertTrue(events[-1]['secret'])
        self.assertTrue(1 <= rolls[3]['dice'][0] <= 100)

    def test_verify_ok_tamper_and_rebuild(self):
        code, payload = self.invoke(SCRIPT[0])
        self.assertEqual(code, 0, payload)
        code, payload = self.invoke(SCRIPT[1])
        self.assertEqual(code, 0, payload)
        directory = self.root / 'waystone'
        log_path = directory / 'events.jsonl'
        state_path = directory / 'state.json'
        code, payload = self.invoke(('verify',))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload['result']['ok'], True)
        self.assertEqual(payload['result']['events'], 2)
        head = payload['result']['head_hash']
        self.assertEqual(head, json.loads(log_path.read_text().splitlines()[-1])['hash'])
        before = state_path.read_bytes()
        code, payload = self.invoke(('verify',))
        self.assertEqual(code, 0, payload)
        self.assertEqual(state_path.read_bytes(), before)

        original = log_path.read_text(encoding='utf-8')
        self.assertIn('1d20+5', original)
        tampered = original.replace('1d20+5', '1d21+5', 1)
        log_path.write_text(tampered, encoding='utf-8')
        code, payload = self.invoke(('verify',))
        self.assertEqual(code, 1)
        self.assertEqual(payload['error']['code'], 'LOG_TAMPERED')
        self.assertEqual(log_path.read_text(encoding='utf-8'), tampered)
        self.assertEqual(state_path.read_bytes(), before)
        code, payload = self.invoke(('roll', '1d6', '--as', 'dm'))
        self.assertEqual(code, 1)
        self.assertEqual(payload['error']['code'], 'LOG_TAMPERED')
        self.assertEqual(log_path.read_text(encoding='utf-8'), tampered)

        log_path.write_text(original, encoding='utf-8')
        state_path.write_text('{', encoding='utf-8')
        code, payload = self.invoke(('verify',))
        self.assertEqual(code, 0, payload)
        repaired = json.loads(state_path.read_text(encoding='utf-8'))
        self.assertEqual(repaired['rng']['counter'], 1)
        self.assertEqual(repaired['rng']['seed'], 4242)
        self.assertEqual(repaired['location'], 'trailhead')

    def test_timestamp_is_outside_the_hash(self):
        self.invoke(SCRIPT[0])
        log_path = self.root / 'waystone' / 'events.jsonl'
        text = log_path.read_text(encoding='utf-8')
        shifted = re.sub(r'"ts":"[^"]*"', '"ts":"2000-01-01T00:00:00+00:00"', text, count=1)
        self.assertNotEqual(shifted, text)
        log_path.write_text(shifted, encoding='utf-8')
        code, payload = self.invoke(('verify',))
        self.assertEqual(code, 0, payload)

    def test_rejected_commands_write_no_event(self):
        code, payload = self.invoke((
            'campaign', 'new', 'waystone', '--adventure', str(ADVENTURE), '--as', 'pc-mira',
        ))
        self.assertEqual(code, 1)
        self.assertEqual(payload['error']['code'], 'NOT_AUTHORIZED')
        self.assertEqual(payload['cmd'], 'campaign new')
        self.assertFalse((self.root / 'waystone').exists())

        self.invoke(SCRIPT[0])
        log_path = self.root / 'waystone' / 'events.jsonl'
        before = log_path.read_bytes()
        code, payload = self.invoke(('roll', 'nope', '--as', 'dm'))
        self.assertEqual(code, 2)
        self.assertEqual(payload['error']['code'], 'BAD_ARGS')
        self.assertEqual(log_path.read_bytes(), before)
        code, payload = self.invoke(('roll', '1d20', '--secret', '--as', 'pc-mira'))
        self.assertEqual(code, 1)
        self.assertEqual(payload['error']['code'], 'NOT_AUTHORIZED')
        self.assertEqual(log_path.read_bytes(), before)
        code, payload = self.invoke(('roll', '2d6', '--adv', '--as', 'dm'))
        self.assertEqual(code, 2)
        self.assertEqual(payload['error']['code'], 'BAD_ARGS')
        self.assertEqual(log_path.read_bytes(), before)

    def test_reachable_error_codes(self):
        code, payload = self.invoke(())
        self.assertEqual(code, 2)
        self.assertEqual(payload['error']['code'], 'BAD_ARGS')

        code, payload = self.invoke(('roll', '1d20', '--as', 'dm'))
        self.assertEqual(code, 1)
        self.assertEqual(payload['error']['code'], 'NO_CAMPAIGN')

        code, payload = self.invoke(('campaign', 'load', 'missing', '--as', 'dm'))
        self.assertEqual(code, 1)
        self.assertEqual(payload['error']['code'], 'UNKNOWN_ID')

        bad = Path(self.tmp.name) / 'other-rules.json'
        bad.write_text(json.dumps({
            'format': 'dnd-adventure/1',
            'id': 'other',
            'title': 'Other Rules',
            'ruleset': 'srd-5.2',
            'start': 'gate',
        }), encoding='utf-8')
        code, payload = self.invoke((
            'campaign', 'new', 'other', '--adventure', str(bad), '--as', 'dm',
        ))
        self.assertEqual(code, 1)
        self.assertEqual(payload['error']['code'], 'RULE_VIOLATION')
        self.assertFalse((self.root / 'other').exists())

        reached = {
            'BAD_ARGS', 'NO_CAMPAIGN', 'UNKNOWN_ID', 'RULE_VIOLATION',
            'NOT_AUTHORIZED', 'LOG_TAMPERED', 'NOT_YOUR_TURN', 'NO_ACTION_LEFT',
            'OUT_OF_RANGE', 'INVALID_TARGET', 'INCAPACITATED', 'NO_SLOT',
        }
        self.assertEqual(set(ALL_CODES) - set(DEFERRED_CODES), reached)

    def test_secret_rolls_are_hidden_from_players(self):
        for args in SCRIPT:
            code, payload = self.invoke(args)
            self.assertEqual(code, 0, payload)
        _code, dm_log = self.invoke(('log', '--as', 'dm', '--type', 'roll', '--last', '1'))
        dm_roll = dm_log['result']['events'][0]['rolls'][0]
        self.assertIn('total', dm_roll)
        self.assertEqual(dm_roll['expr'], '1d20+1')
        _code, player_log = self.invoke(('log', '--as', 'pc-mira', '--type', 'roll', '--last', '1'))
        hidden = player_log['result']['events'][0]['rolls'][0]
        self.assertEqual(hidden, {'counter': 7, 'secret': True})
        self.assertNotIn('total', hidden)
        _code, since = self.invoke(('log', '--as', 'dm', '--since', '1', '--type', 'campaign'))
        self.assertEqual(since['result']['events'], [])

    def test_campaign_info_load_and_active_pointer(self):
        code, payload = self.invoke(SCRIPT[0])
        self.assertEqual(payload['result'], {
            'campaign': 'waystone',
            'seed': 4242,
            'start_location': 'trailhead',
        })
        self.assertEqual(payload['event_ids'], [1])
        self.assertEqual((self.root / '.dnd_active').read_text(encoding='utf-8').strip(), 'waystone')
        self.assertEqual(
            (self.home / '.dnd_active').read_text(encoding='utf-8').strip(),
            str((self.root / 'waystone').resolve()),
        )
        meta = json.loads((self.root / 'waystone' / 'campaign.json').read_text(encoding='utf-8'))
        raw = ADVENTURE.read_bytes()
        self.assertEqual((self.root / 'waystone' / 'adventure.json').read_bytes(), raw)
        self.assertEqual(meta['adventure_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(meta['ruleset'], 'srd-5.1')
        code, info = self.invoke(('campaign', 'info'))
        self.assertEqual(code, 0, info)
        self.assertEqual(info['result']['party'], [])
        self.assertEqual(info['result']['location'], 'trailhead')
        self.assertEqual(info['result']['time'], {'day': 1, 'minute': 0})
        self.assertFalse(info['result']['combat'])
        self.invoke((
            'campaign', 'new', 'other', '--adventure', str(ADVENTURE),
            '--seed', '1', '--as', 'dm',
        ))
        code, loaded = self.invoke(('campaign', 'load', 'waystone', '--as', 'pc-mira'))
        self.assertEqual(code, 0, loaded)
        self.assertEqual(loaded['result']['campaign'], 'waystone')
        self.assertEqual(loaded['result']['location'], 'trailhead')
        self.assertFalse(loaded['result']['combat'])
        _code, info = self.invoke(('campaign', 'info'))
        self.assertEqual(info['result']['campaign'], 'waystone')

    def test_campaign_flag_overrides_env(self):
        other = Path(self.tmp.name) / 'other-root'
        other.mkdir()
        code, payload = self.invoke((
            'campaign', 'new', 'waystone', '--adventure', str(ADVENTURE),
            '--seed', '7', '--as', 'dm', '--campaign', str(other),
        ))
        self.assertEqual(code, 0, payload)
        self.assertTrue((other / 'waystone' / 'campaign.json').is_file())
        self.assertFalse((self.root / 'waystone').exists())

    def test_help_about_and_command_list(self):
        before = list(self.root.iterdir())
        code, payload = self.invoke(('help', '--about'))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload['result']['about'], SRD_ATTRIBUTION)
        self.assertIn(SRD_ATTRIBUTION, NOTICE.read_text(encoding='utf-8'))
        self.assertEqual(list(self.root.iterdir()), before)
        code, listed = self.invoke(('help',))
        names = [command['name'] for command in listed['result']['commands']]
        for name in (
            'campaign new', 'campaign load', 'campaign info', 'verify', 'roll', 'log', 'help',
        ):
            self.assertIn(name, names)
        code, one = self.invoke(('help', 'roll'))
        self.assertEqual(code, 0)
        self.assertEqual(one['result']['commands'][0]['name'], 'roll')
        code, payload = self.invoke(('help', 'player'))
        self.assertEqual(code, 2)
        self.assertEqual(payload['error']['code'], 'BAD_ARGS')

    def test_default_root_lookup_creates_nothing(self):
        root, explicit = store.locate_root(None, environ={})
        existed = root.exists()
        again, explicit_again = store.locate_root(None, environ={})
        self.assertFalse(explicit)
        self.assertFalse(explicit_again)
        self.assertEqual(again, REPO / 'campaigns')
        self.assertEqual(root.exists(), existed)

    def test_dice_parser(self):
        parsed = parse_expr('1d20+5')
        self.assertEqual(parsed.count, 1)
        self.assertEqual(parsed.sides, 20)
        self.assertEqual(parsed.mod, 5)
        self.assertIsNone(parsed.keep)
        parsed = parse_expr('d%')
        self.assertEqual((parsed.count, parsed.sides, parsed.mod), (1, 100, 0))
        parsed = parse_expr('4d6kh3')
        self.assertEqual((parsed.keep, parsed.keep_n), ('kh', 3))
        parsed = parse_expr('2d6kl1-1')
        self.assertEqual((parsed.keep, parsed.keep_n, parsed.mod), ('kl', 1, -1))
        for expr in ('', '1d20+5kh1', '0d6', '2d6kh5', 'drop', '3d6+'):
            with self.assertRaises(ValueError):
                parse_expr(expr)
        rolled = roll_expr('1d20+5', 4242, 0, 'none')
        rng = random.Random(hashlib.sha256(b'4242:0').digest())
        self.assertEqual(rolled['total'], rng.randint(1, 20) + 5)


if __name__ == '__main__':
    unittest.main()
