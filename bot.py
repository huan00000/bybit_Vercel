import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import urlencode

import requests
from flask import Flask, request

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024
BYBIT_URL = 'https://api.bybit.com'


class ApiError(Exception):
    pass


def bybit(method, path, params):
    key = os.environ.get('API_KEY', '')
    secret = os.environ.get('API_SECRET', '')
    if not key or not secret:
        raise ApiError('未配置 Bybit 正式网 API_KEY / API_SECRET')
    timestamp = str(int(time.time() * 1000))
    payload = urlencode(params) if method == 'GET' else json.dumps(params, separators=(',', ':'))
    signature = hmac.new(secret.encode(), (timestamp + key + '5000' + payload).encode(), hashlib.sha256).hexdigest()
    headers = {'X-BAPI-API-KEY': key, 'X-BAPI-TIMESTAMP': timestamp,
               'X-BAPI-RECV-WINDOW': '5000', 'X-BAPI-SIGN': signature,
               'Content-Type': 'application/json'}
    url = BYBIT_URL + path + ('?' + payload if method == 'GET' else '')
    response = requests.request(method, url, headers=headers,
                                data=payload if method == 'POST' else None, timeout=(3, 10))
    response.raise_for_status()
    data = response.json()
    if data.get('retCode') != 0:
        # Never echo arbitrary upstream text: it may contain request credentials.
        reasons = {10001: '参数不符合要求，请检查交易对及最小下单数量',
                   10003: 'API Key 无效，请确认使用正式网密钥',
                   10004: '签名错误，请检查 API Secret',
                   10005: 'API Key 权限不足', 10006: '请求过于频繁',
                   10014: '重复请求，请到 Bybit 核实原订单',
                   110072: '订单标识已存在，请到 Bybit 核实原订单',
                   170121: '交易对无效', 170130: '下单参数不符合要求',
                   170131: '可用余额不足', 170136: '订单数量低于最小值',
                   170140: '订单金额低于最小值',
                   170141: '订单标识已存在，请到 Bybit 核实原订单',
                   170007: '交易所响应超时，结果待确认，请先核实订单',
                   170146: '订单创建超时，结果待确认，请先核实订单',
                   170151: '交易对尚未开放交易',
                   170157: '交易对不支持 API 交易'}
        code = data.get('retCode')
        raise ApiError(f'Bybit 错误 {code if isinstance(code, int) else "未知"}：' + reasons.get(code, '请求被拒绝，请在 Bybit 正式网检查账户与交易规则'))
    return data.get('result', {})


def reply(chat_id, text):
    token = os.environ['BOT_TOKEN']
    response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                             json={'chat_id': chat_id, 'text': text}, timeout=(3, 10))
    response.raise_for_status()
    if response.json().get('ok') is not True:
        raise ApiError('Telegram 回复失败')


def execute(update, message, command):
    if command == 'id':
        return f'你的 ID：{message["from"]["id"]}'
    ids = re.split(r'[\s,]+', os.environ.get('ALLOWED_TELEGRAM_IDS', '').strip())
    if not ids or any(not re.fullmatch(r'[1-9]\d*', item) for item in ids):
        return '❌ 白名单未配置或格式错误，操作已拒绝。'
    if str(message['from']['id']) not in ids:
        return '❌ 你没有操作权限。请用 /id 查询自己的 ID。'
    if message['chat'].get('type') != 'private':
        return '❌ 请在与机器人的私聊中操作。'
    if command == 'status':
        try:
            bybit('GET', '/v5/user/query-api', {})
            return '🟢 Bot 正常\n💰 Bybit MAINNET（真实资金）\n📡 API：认证已连接\n💰 USDTUSD，每次 1 USDT\n交易对及余额是否满足下单要求，以交易所返回为准。'
        except ApiError as error:
            return f'🟢 Bot 正常\n💰 Bybit MAINNET（真实资金）\n📡 API：{error}'
        except (requests.RequestException, ValueError):
            return '🟢 Bot 正常\n💰 Bybit MAINNET（真实资金）\n📡 API：连接异常，请稍后重试。'
    if command not in ('buy', 'sell'):
        return '支持 /id、/status、/buy、/sell。买卖固定为正式网 USDTUSD 的 1 USDT。'
    date = message.get('date')
    if not isinstance(date, int) or not 0 <= time.time() - date <= 120:
        return '❌ 交易指令已过期或时间无效，请重新发送。'
    update_id = update.get('update_id')
    if type(update_id) is not int or update_id < 0:
        return '❌ 无效消息，未下单。'
    bot_id = os.environ['BOT_TOKEN'].split(':')[0]
    # Stable across instances, cold starts and Telegram retries; never retry with a new ID.
    order_link_id = 'tg-' + hashlib.sha256(f'{bot_id}:{update_id}'.encode()).hexdigest()[:32]
    side = 'Buy' if command == 'buy' else 'Sell'
    try:
        result = bybit('POST', '/v5/order/create', {
            'category': 'spot', 'symbol': 'USDTUSD', 'side': side,
            'orderType': 'Market', 'qty': '1', 'marketUnit': 'baseCoin',
            'isLeverage': 0, 'orderLinkId': order_link_id})
        return (f'💰 MAINNET（真实资金）\n{"买入" if side == "Buy" else "卖出"} USDTUSD\n数量：1 USDT\n'
                f'✅ 订单已受理（不代表已成交）\n订单 ID：{result.get("orderId", "未返回")}\n订单标识：{order_link_id}')
    except ApiError as error:
        return f'💰 MAINNET（真实资金）\n{error}\n固定 USDTUSD / 1 USDT，未更换交易对或增加数量。\n订单标识：{order_link_id}'
    except (requests.RequestException, ValueError):
        return f'⚠️ 订单结果待确认，请先到 Bybit 正式网核实，避免重新下单。\n订单标识：{order_link_id}'


@app.get('/')
def home():
    return {'service': 'Telegram Bybit Bot', 'environment': 'MAINNET'}, 200


@app.post('/webhook')
def webhook():
    secret = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')
    if not secret or not os.environ.get('BOT_TOKEN'):
        return 'Service not configured', 503
    supplied = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not hmac.compare_digest(supplied.encode(), secret.encode()):
        return 'Forbidden', 403
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return 'Invalid JSON', 400
    message = update.get('message')
    if not isinstance(message, dict):
        return 'OK', 200
    user, chat = message.get('from'), message.get('chat')
    if (not isinstance(user, dict) or not isinstance(chat, dict) or user.get('is_bot')
            or type(user.get('id')) is not int or type(chat.get('id')) is not int):
        return 'OK', 200
    text = message.get('text')
    if not isinstance(text, str):
        return 'OK', 200
    match = re.fullmatch(r'/([a-z]+)(?:@([A-Za-z0-9_]+))?', text.strip(), re.I)
    if not match:
        return 'OK', 200
    command, target = match.groups()
    if target and target.lower() != os.environ.get('BOT_USERNAME', '').lstrip('@').lower():
        return 'OK', 200
    answer = execute(update, message, command.lower())
    try:
        reply(chat['id'], answer)
    except (requests.RequestException, ValueError, ApiError):
        # A failed notification must not request Telegram to replay a trade.
        app.logger.warning('Telegram reply failed; check Telegram availability and Bybit order history.')
    return 'OK', 200
