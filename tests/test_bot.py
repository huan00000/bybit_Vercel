import hashlib
import hmac
import os
import time
import unittest
from unittest.mock import patch, Mock

import requests
import bot

ENV = {'BOT_TOKEN': '123:test-token', 'TELEGRAM_WEBHOOK_SECRET': 'test-secret',
       'ALLOWED_TELEGRAM_IDS': '42,43', 'API_KEY': 'test-key', 'API_SECRET': 'test-api-secret',
       'BOT_USERNAME': 'example_bot'}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = bot.app.test_client()
        self.reply = patch('bot.reply').start()
        self.api = patch('bot.bybit').start()
        self.addCleanup(patch.stopall)
        self.api.return_value = {'orderId': 'order-1'}

    def send(self, text='/buy', user=42, **changes):
        message = {'from': {'id': user}, 'chat': {'id': user, 'type': 'private'},
                   'text': text, 'date': int(time.time())}
        message.update(changes)
        return self.client.post('/webhook', json={'update_id': 123, 'message': message},
                                headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'})

    def answer(self):
        return self.reply.call_args.args[1]

    def test_home_reports_mainnet(self):
        self.assertEqual(self.client.get('/').json['environment'], 'MAINNET')

    def test_forged_webhook(self):
        self.assertEqual(self.client.post('/webhook', json={}).status_code, 403)
        self.api.assert_not_called()

    def test_fail_closed_configuration(self):
        os.environ.pop('TELEGRAM_WEBHOOK_SECRET')
        self.assertEqual(self.send().status_code, 503)
        self.api.assert_not_called()

    def test_public_id(self):
        self.send('/id', user=99)
        self.assertIn('99', self.answer())
        self.api.assert_not_called()

    def test_denied_commands(self):
        for command in ['/buy', '/sell', '/status']:
            self.send(command, user=99)
            self.assertIn('没有操作权限', self.answer())
        self.api.assert_not_called()

    def test_invalid_allowlist(self):
        for value in ['', '42,bad']:
            os.environ['ALLOWED_TELEGRAM_IDS'] = value
            self.send()
            self.assertIn('白名单', self.answer())
        self.api.assert_not_called()

    def test_group_and_stale_trade(self):
        self.send(chat={'id': -100, 'type': 'supergroup'})
        self.assertIn('私聊', self.answer())
        self.send(date=int(time.time()) - 121)
        self.assertIn('过期', self.answer())
        self.api.assert_not_called()

    def test_buy_sell_fixed_and_duplicate_id(self):
        self.send('/buy')
        first = self.api.call_args.args[2]
        self.send('/buy')
        self.assertEqual(first, self.api.call_args.args[2])
        self.assertLessEqual(len(first['orderLinkId']), 36)
        self.assertEqual({k: first[k] for k in ['category', 'symbol', 'qty', 'marketUnit', 'side']},
                         {'category': 'spot', 'symbol': 'USDTUSD', 'qty': '1', 'marketUnit': 'baseCoin', 'side': 'Buy'})
        self.assertIn('不代表已成交', self.answer())
        self.assertIn('MAINNET（真实资金）', self.answer())
        self.send('/sell')
        self.assertEqual(self.api.call_args.args[2]['side'], 'Sell')

    def test_status_authenticated(self):
        self.send('/status')
        self.api.assert_called_once_with('GET', '/v5/user/query-api', {})
        self.assertIn('认证已连接', self.answer())

    def test_status_failure(self):
        self.api.side_effect = requests.Timeout('secret')
        self.send('/status')
        self.assertIn('连接异常', self.answer())
        self.assertNotIn('secret', self.answer())

    def test_order_unknown(self):
        self.api.side_effect = requests.Timeout('test-api-secret')
        self.send()
        self.assertIn('待确认', self.answer())
        self.assertNotIn('test-api-secret', self.answer())
        self.api.assert_called_once()

    def test_rejected_order(self):
        self.api.side_effect = bot.ApiError('订单数量低于最小值')
        self.send()
        self.assertIn('低于最小值', self.answer())
        self.assertNotIn('✅', self.answer())
        self.api.assert_called_once()

    def test_notification_failure_acknowledges(self):
        self.reply.side_effect = requests.Timeout()
        self.assertEqual(self.send().status_code, 200)
        self.api.assert_called_once()

    def test_other_bot_and_arguments_ignored(self):
        self.send('/buy@other_bot')
        self.send('/buy 100')
        self.api.assert_not_called()
        self.reply.assert_not_called()
        self.send('/id@example_bot')
        self.assertIn('42', self.answer())

    def test_malformed_update(self):
        for value in [[], None, {'message': []}, {'message': {'from': []}}]:
            response = self.client.post('/webhook', json=value,
                headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'})
            self.assertIn(response.status_code, [200, 400])
        self.api.assert_not_called()


class BybitTests(unittest.TestCase):
    @patch.dict(os.environ, ENV, clear=True)
    @patch('bot.requests.request')
    @patch('bot.time.time', return_value=1000)
    def test_signature_and_mainnet(self, clock, request):
        request.return_value.json.return_value = {'retCode': 0, 'result': {'orderId': '1'}}
        result = bot.bybit('POST', '/v5/order/create', {'qty': '1'})
        args, kwargs = request.call_args
        self.assertEqual(args, ('POST', 'https://api.bybit.com/v5/order/create'))
        payload = '1000000test-key5000' + kwargs['data']
        signature = hmac.new(b'test-api-secret', payload.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(kwargs['headers']['X-BAPI-SIGN'], signature)
        self.assertEqual(result['orderId'], '1')
        bot.bybit('GET', '/v5/user/query-api', {})
        self.assertIsNone(request.call_args.kwargs['data'])

    @patch.dict(os.environ, ENV, clear=True)
    @patch('bot.requests.request')
    def test_error_redacts_upstream(self, request):
        request.return_value.json.return_value = {'retCode': 170140, 'retMsg': 'test-api-secret'}
        with self.assertRaisesRegex(bot.ApiError, '金额低于最小值') as caught:
            bot.bybit('POST', '/v5/order/create', {})
        self.assertNotIn('test-api-secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
