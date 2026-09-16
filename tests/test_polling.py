import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import polling


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'offset-123'

    @patch('polling.handle_update')
    def test_checkpoint_precedes_execution_and_skips_duplicates(self, handle):
        def check(update):
            self.assertEqual(int(self.path.read_text()), update['update_id'] + 1)
        handle.side_effect = check
        result = polling.process_updates(
            [{'update_id': 9}, {'update_id': 10}, {'update_id': 10}], self.path, 10)
        self.assertEqual(result, 11)
        handle.assert_called_once_with({'update_id': 10})

    @patch('polling.handle_update', side_effect=RuntimeError('crash'))
    def test_crash_checkpoint_prevents_replay_after_restart(self, handle):
        with self.assertRaises(RuntimeError):
            polling.process_updates([{'update_id': 20}], self.path, None)
        handle.reset_mock()
        offset = int(self.path.read_text())
        self.assertEqual(polling.process_updates([{'update_id': 20}], self.path, offset), 21)
        handle.assert_not_called()

    @patch('polling.handle_update')
    @patch('polling.save_offset', side_effect=OSError('disk full'))
    def test_storage_failure_never_executes(self, save, handle):
        with self.assertRaises(OSError):
            polling.process_updates([{'update_id': 20}], self.path, None)
        handle.assert_not_called()

    @patch.dict('os.environ', {'BOT_TOKEN': '123:private-token'})
    @patch('polling.requests.post')
    def test_api_errors_do_not_expose_token(self, post):
        post.return_value = Mock(status_code=409)
        with self.assertRaises(polling.TelegramError) as caught:
            polling.telegram('getUpdates', {'timeout': 30})
        self.assertEqual(caught.exception.code, 409)
        self.assertNotIn('private-token', str(caught.exception))
        self.assertEqual(post.call_args.kwargs['timeout'], (5, 40))

    @patch('bot.reply')
    @patch.dict('os.environ', {'BOT_TOKEN': '123:fake'})
    def test_polling_id_needs_no_webhook_secret(self, reply):
        polling.process_updates([{'update_id': 30, 'message': {
            'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'},
            'text': '/id'}}], self.path, None)
        reply.assert_called_once_with(42, 'Your ID：42')

    def test_startup_switches_webhook_without_dropping_updates(self):
        stop = threading.Event()
        calls = []

        def api(method, payload):
            calls.append((method, payload))
            if method == 'deleteWebhook':
                return True
            stop.set()
            return [{'update_id': 40}]

        with patch.dict('os.environ', {'BOT_TOKEN': '123:fake',
                                     'DATA_DIR': self.directory.name}), \
                patch.dict('sys.modules', {'fcntl': Mock(LOCK_EX=2, LOCK_NB=4)}), \
                patch('polling.signal.signal'), \
                patch('polling.threading.Event', return_value=stop), \
                patch('polling.telegram', side_effect=api), \
                patch('polling.handle_update') as handle:
            self.assertEqual(polling.run(), 0)
        self.assertEqual(calls[0], ('deleteWebhook', {'drop_pending_updates': False}))
        self.assertEqual(calls[1][0], 'getUpdates')
        self.assertEqual(calls[1][1]['timeout'], 30)
        self.assertEqual(self.path.read_text(), '41')
        self.assertTrue((self.path.parent / 'heartbeat').exists())
        handle.assert_called_once_with({'update_id': 40})
