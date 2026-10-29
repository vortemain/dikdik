"""Atomic transcript persistence under Windows sharing contention."""
import unittest
from unittest.mock import patch

from dikdik.engine import _replace_checkpoint


class CheckpointIOTests(unittest.TestCase):
    def test_success_has_no_wait(self):
        with patch('dikdik.engine.os.replace') as replace, patch('dikdik.engine.time.sleep') as sleep:
            _replace_checkpoint('state.tmp', 'state.json')
        replace.assert_called_once_with('state.tmp', 'state.json')
        sleep.assert_not_called()

    def test_transient_windows_lock_retries_the_same_atomic_replace(self):
        with patch('dikdik.engine.os.name', 'nt'), \
                patch('dikdik.engine.os.replace', side_effect=[PermissionError('sharing lock'), None]) as replace, \
                patch('dikdik.engine.time.sleep') as sleep:
            _replace_checkpoint('state.tmp', 'state.json')
        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once_with(0.02)

    def test_permanent_windows_denial_remains_an_error(self):
        with patch('dikdik.engine.os.name', 'nt'), \
                patch('dikdik.engine.os.replace', side_effect=PermissionError('denied')) as replace, \
                patch('dikdik.engine.time.sleep') as sleep:
            with self.assertRaises(PermissionError):
                _replace_checkpoint('state.tmp', 'state.json')
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
