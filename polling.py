"""Single-process Telegram polling with a durable, pre-execution checkpoint."""
import logging
import os
from pathlib import Path
import signal
import threading
import time

import requests

from bot import handle_update

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Telegram request failed')


def telegram(method, payload):
    response = requests.post(
        f'https://api.telegram.org/bot{os.environ["BOT_TOKEN"]}/{method}',
        json=payload, timeout=(5, 40))
    if response.status_code != 200:
        raise TelegramError(response.status_code)
    data = response.json()
    if not data.get('ok'):
        raise TelegramError(data.get('error_code'))
    return data['result']


def save_offset(path, offset):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(str(offset))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    # Persist the rename as well as the file contents on Linux.
    if os.name == 'posix':
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def process_updates(updates, path, offset):
    for update in updates:
        update_id = update.get('update_id')
        if type(update_id) is not int or update_id < 0:
            raise ValueError('Invalid update ID')
        if offset is not None and update_id < offset:
            continue
        offset = update_id + 1
        # Prefer a possibly missed command over replaying a real-money trade.
        save_offset(path, offset)
        handle_update(update)
    return offset


def run():
    if not os.environ.get('BOT_TOKEN'):
        log.error('BOT_TOKEN is required.')
        return 1
    directory = Path(os.environ.get('DATA_DIR', '/app/data'))
    directory.mkdir(parents=True, exist_ok=True)
    # Only Linux containers are supported; lock prevents two readers on one volume.
    import fcntl
    with (directory / 'polling.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error('Another polling process is using this data directory.')
            return 1
        path = directory / ('offset-' + os.environ['BOT_TOKEN'].split(':')[0])
        offset = int(path.read_text()) if path.exists() else None
        if offset is not None and offset < 0:
            raise ValueError('Invalid stored offset')
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        initialized = False
        heartbeat = directory / 'heartbeat'
        while not stop.is_set():
            try:
                if not initialized:
                    telegram('deleteWebhook', {'drop_pending_updates': False})
                    initialized = True
                    log.info('Long polling started (Bybit MAINNET).')
                updates = telegram('getUpdates', {
                    'offset': offset, 'timeout': 30, 'limit': 1,
                    'allowed_updates': ['message']})
            except TelegramError as error:
                if error.code in (401, 404, 409):
                    log.error('Telegram authentication or polling conflict; stopping.')
                    return 1
                log.warning('Telegram temporarily unavailable; retrying in 5 seconds.')
                stop.wait(5)
                continue
            except (requests.RequestException, ValueError):
                log.warning('Network or response error; retrying in 5 seconds.')
                stop.wait(5)
                continue
            # Processing failures terminate the process. Restart reads the durable
            # checkpoint rather than replaying with an outdated in-memory offset.
            offset = process_updates(updates, path, offset)
            heartbeat.write_text(str(time.time()), encoding='utf-8')
        return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        raise SystemExit(run())
    except Exception:
        # Request exceptions may contain the token in a URL: never print traceback.
        log.error('Polling stopped unexpectedly; check configuration and data permissions.')
        raise SystemExit(1)
