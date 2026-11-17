"""Command validation, event batches, and concurrent campaign snapshots."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dnd import cli, store
from dnd.errors import EngineError

ENGINE = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


class CampaignTransactionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'campaigns'
        home = Path(temporary.name) / 'home'
        home.mkdir()
        with patch('pathlib.Path.home', return_value=home):
            self.invoke(
                'campaign', 'new', 'table', '--adventure',
                str(FIXTURES / 'briar-yard.json'), '--seed', '11', '--as', 'dm',
            )
        self.directory = self.root / 'table'
        self.invoke(
            'char', 'create', '--json-in',
            str(FIXTURES / 'party' / 'pc-tor.json'), '--as', 'dm',
        )

    def invoke(self, *args):
        return cli.dispatch([*args, '--campaign', str(self.root)])

    def snapshot(self):
        return tuple((self.directory / name).read_bytes() for name in ('events.jsonl', 'state.json'))

    def test_rejected_spawn_during_combat_changes_nothing(self):
        self.invoke('encounter', 'spawn', 'lone-goblin', '--start-combat', '--as', 'dm')
        before = self.snapshot()
        with self.assertRaises(EngineError) as caught:
            self.invoke(
                'encounter', 'spawn', 'lone-goblin', '--roll-hp',
                '--start-combat', '--as', 'dm',
            )
        self.assertEqual(caught.exception.code, 'RULE_VIOLATION')
        self.assertEqual(self.snapshot(), before)

    def test_rejected_spawn_with_dead_participant_changes_nothing(self):
        self.invoke('damage', 'pc-tor', '1000', '--type', 'slashing', '--as', 'dm')
        before = self.snapshot()
        with self.assertRaises(EngineError) as caught:
            self.invoke('encounter', 'spawn', 'lone-goblin', '--start-combat', '--as', 'dm')
        self.assertEqual(caught.exception.code, 'INVALID_TARGET')
        self.assertEqual(self.snapshot(), before)

    def test_spawn_and_combat_commit_a_replayable_batch(self):
        result = self.invoke(
            'encounter', 'spawn', 'lone-goblin', '--roll-hp',
            '--start-combat', '--as', 'dm',
        )
        self.assertEqual(len(result['event_ids']), 2)
        self.assertTrue(result['turn']['combat'])
        events = store.read_events(self.directory)
        replayed, head = store.fold(events)
        self.assertEqual(replayed, store.read_state(self.directory))
        self.assertEqual(head, events[-1]['hash'])
        self.assertEqual([event['type'] for event in events[-2:]], ['encounter-spawn', 'combat-start'])
        self.assertEqual(
            [roll['counter'] for event in events[-2:] for roll in event['rolls']],
            list(range(replayed['rng']['counter'])),
        )

    def test_invalid_later_batch_event_appends_nothing(self):
        before = self.snapshot()
        good = {
            'actor': 'dm', 'cmd': 'first', 'type': 'override', 'rolls': [],
            'changes': [{'op': 'set', 'path': 'location', 'to': 'elsewhere'}],
        }
        bad = {
            'actor': 'dm', 'cmd': 'second', 'type': 'override', 'rolls': [],
            'changes': [{'op': 'set', 'path': 'bad..path', 'to': 1}],
        }
        with store.transaction(self.directory) as campaign:
            with self.assertRaises(EngineError) as caught:
                campaign.append_events([good, bad])
            self.assertEqual(caught.exception.code, 'LOG_TAMPERED')
        self.assertEqual(self.snapshot(), before)

    def test_undo_replays_history_and_preserves_spent_dice(self):
        rolled = self.invoke('roll', '1d20', '--as', 'dm')
        self.invoke('override', 'characters.pc-tor.hp.current', '1', '--reason', 'test', '--as', 'dm')
        self.invoke('undo', '--reason', 'test', '--as', 'dm')
        sheet = self.invoke('char', 'show', 'pc-tor', '--as', 'dm')['result']
        self.assertEqual(sheet['hp']['current'], sheet['hp']['max'])
        self.invoke('undo', '--reason', 'test', '--as', 'dm')
        next_roll = self.invoke('roll', '1d20', '--as', 'dm')
        self.assertEqual(next_roll['rolls'][0]['counter'], rolled['rolls'][0]['counter'] + 1)
        replayed, _head = store.fold(store.read_events(self.directory))
        self.assertEqual(replayed, store.read_state(self.directory))

    def test_information_uses_log_when_cache_is_stale(self):
        self.invoke('encounter', 'spawn', 'lone-goblin', '--start-combat', '--as', 'dm')
        (self.directory / 'state.json').write_text('{}', encoding='utf-8')
        info = self.invoke('campaign', 'info')
        self.assertEqual(info['result']['party'], ['pc-tor'])
        self.assertTrue(info['result']['combat'])
        log = self.invoke('log', '--last', '1', '--as', 'dm')
        self.assertEqual(log['turn'], info['turn'])

    def test_failed_cache_replacement_keeps_previous_cache(self):
        before = (self.directory / 'state.json').read_bytes()
        with patch('dnd.store.os.replace', side_effect=OSError('interrupted replace')):
            with self.assertRaises(OSError):
                store.write_state(self.directory, {'partial': True})
        self.assertEqual((self.directory / 'state.json').read_bytes(), before)
        self.assertEqual(list(self.directory.glob('.state-*')), [])

    def test_reader_waits_for_writer_before_reading_event_log(self):
        # Deliberately pause a writer midway through its final log line. The
        # reader must wait on the campaign lock rather than report tampering.
        events = store.read_events(self.directory)
        pending = {
            'actor': 'dm', 'cmd': 'override hp', 'type': 'override', 'rolls': [],
            'changes': [{'op': 'set', 'path': 'characters.pc-tor.hp.current', 'to': 1}],
            'id': events[-1]['id'] + 1, 'prev_hash': events[-1]['hash'],
        }
        pending['hash'] = store.event_hash(pending)
        pending['ts'] = store.timestamp()
        line = store.canonical(pending) + '\n'
        code = (
            'import sys\nfrom dnd.cli import main\n'
            'print("READY", flush=True)\n'
            'sys.exit(main(sys.argv[1:]))\n'
        )
        process = None
        try:
            with store.locked(self.directory):
                with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as handle:
                    midpoint = len(line) // 2
                    handle.write(line[:midpoint])
                    handle.flush()
                    process = subprocess.Popen(
                        [sys.executable, '-c', code, 'char', 'show', 'pc-tor',
                         '--as', 'dm', '--campaign', str(self.root)],
                        cwd=ENGINE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    self.assertEqual(process.stdout.readline(), 'READY\n')
                    with self.assertRaises(subprocess.TimeoutExpired):
                        process.communicate(timeout=0.5)
                    handle.write(line[midpoint:])
                    handle.flush()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stderr, '')
            self.assertEqual(json.loads(stdout)['result']['hp']['current'], 1)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


if __name__ == '__main__':
    unittest.main()
