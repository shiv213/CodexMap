"""Serve the Codex chat map."""
import json
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import threading
import math
import os

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get('CODEXMAP_DATA_DIR', str(ROOT.parent)))
VERSION_PATH = ROOT.parent / 'VERSION'
VERSION = VERSION_PATH.read_text().strip() if VERSION_PATH.exists() else 'development'
CONFIG_PATH = DATA_ROOT / 'config.json'
CONFIG = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
CODEX = Path(CONFIG.get('codexHome', str(Path.home() / '.codex'))).expanduser()
PORT = int(CONFIG.get('port', 4399))
CACHE = {}
LOCK = threading.Lock()
STATE_DB = DATA_ROOT / 'dashboard.sqlite3'


def initialize_state():
    with sqlite3.connect(STATE_DB) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS node_state (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS state_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO state_version VALUES (1, 0)')
        for identity, raw in db.execute('SELECT id,data FROM node_state').fetchall():
            data = json.loads(raw)
            if data.get('done') and 'doneAt' not in data:
                data['doneAt'] = time.time()
                db.execute('UPDATE node_state SET data=? WHERE id=?', (json.dumps(data), identity))
    STATE_DB.chmod(0o600)


def read_state():
    with sqlite3.connect(STATE_DB, timeout=5) as db:
        db.execute('BEGIN')
        version = db.execute('SELECT version FROM state_version WHERE id=1').fetchone()[0]
        state = {row[0]: json.loads(row[1]) for row in db.execute('SELECT id,data FROM node_state')}
    return {'state': state, 'stateVersion': version}


def write_state(updates, initialize=False):
    if not isinstance(updates, dict) or len(updates) > 2000:
        raise ValueError('Invalid state updates')
    for identity, patch in updates.items():
        if not re.fullmatch(r'[0-9a-f-]{36}', identity) or not isinstance(patch, dict):
            raise ValueError('Invalid node')
        for key, value in patch.items():
            if key in ('x', 'y'):
                valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
            elif key == 'done':
                valid = isinstance(value, bool)
            elif key == 'seen':
                valid = value is None or isinstance(value, str) and len(value) <= 128
            elif key == 'mobile':
                valid = value is None or isinstance(value, dict) and set(value) == {'x', 'y', 'manual'} and value['manual'] is True and all(type(value[k]) in (int, float) and math.isfinite(value[k]) and 0 <= value[k] <= 1 for k in ('x', 'y'))
            else:
                valid = False
            if not valid:
                raise ValueError('Invalid state field')
    with sqlite3.connect(STATE_DB, timeout=5) as db:
        db.execute('BEGIN IMMEDIATE')
        for identity, patch in updates.items():
            existing = db.execute('SELECT data FROM node_state WHERE id=?', (identity,)).fetchone()
            if initialize and existing:
                continue
            data = json.loads(existing[0]) if existing else {}
            was_done = bool(data.get('done'))
            for key, value in patch.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            if data.get('done'):
                if not was_done or 'doneAt' not in data:
                    data['doneAt'] = time.time()
            else:
                data.pop('doneAt', None)
            db.execute('INSERT INTO node_state VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (identity, json.dumps(data)))
        db.execute('UPDATE state_version SET version=version+1 WHERE id=1')
    return read_state()


def restore_resumed_chats(threads, now):
    with sqlite3.connect(STATE_DB, timeout=5) as db:
        db.execute('BEGIN IMMEDIATE')
        changed = False
        for chat in threads:
            if chat['status'] != 'running' or not chat.get('runStartedAt'):
                continue
            row = db.execute('SELECT data FROM node_state WHERE id=?', (chat['id'],)).fetchone()
            if not row:
                continue
            node = json.loads(row[0])
            done_at = node.get('doneAt')
            if not node.get('done') or done_at is None or now - done_at < 600 or chat['runStartedAt'] <= done_at:
                continue
            node['done'] = False
            node.pop('doneAt', None)
            node.pop('seen', None)
            db.execute('UPDATE node_state SET data=? WHERE id=?', (json.dumps(node), chat['id']))
            changed = True
        if changed:
            db.execute('UPDATE state_version SET version=version+1 WHERE id=1')
    return read_state()


ALLOWED_ORIGINS = {
    f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}',
    *CONFIG.get('allowedOrigins', []),
}

ALLOWED_HOSTS = {urlparse(origin).netloc.lower() for origin in ALLOWED_ORIGINS}


def codex_database():
    if CONFIG.get('codexStateDb'):
        return Path(CONFIG['codexStateDb']).expanduser()
    candidates = [p for p in CODEX.glob('state_*.sqlite') if re.fullmatch(r'state_\d+\.sqlite', p.name)]
    for path in sorted(candidates, key=lambda p: int(p.stem.split('_')[1]), reverse=True):
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
            columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
        if {'id', 'title', 'rollout_path', 'updated_at', 'archived'} <= columns:
            return path
    raise RuntimeError('No compatible Codex database. Start a chat or update CodexMap.')


def reverse_lines(path):
    with path.open('rb') as handle:
        offset = handle.seek(0, 2)
        carry = b''
        while offset:
            size = min(offset, 512 * 1024)
            offset -= size
            handle.seek(offset)
            parts = (handle.read(size) + carry).split(b'\n')
            carry = parts.pop(0)
            yield from reversed(parts)
        if carry:
            yield carry


def read_chat(path):
    stamp = (path.stat().st_size, path.stat().st_mtime_ns)
    key = str(path)
    if key in CACHE and CACHE[key][0] == stamp:
        return CACHE[key][1]
    messages, seen = [], set()
    status, turn, started_at = 'idle', '', None
    found_status = False
    for line in reverse_lines(path):
        if not any(token in line for token in (b'"task_started"', b'"task_complete"', b'"turn_aborted"', b'"task_failed"', b'"message"', b'"agent_message"')):
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        p = event.get('payload', {})
        if event.get('type') == 'event_msg' and not found_status:
            kind = p.get('type')
            if kind in ('task_started', 'task_complete', 'turn_aborted', 'task_failed'):
                status = {'task_started': 'running', 'task_complete': 'finished', 'turn_aborted': 'interrupted', 'task_failed': 'failed'}[kind]
                turn = p.get('turn_id', p.get('root_turn_id', ''))
                if kind == 'task_started':
                    try:
                        started_at = datetime.fromisoformat(str(event['timestamp']).replace('Z', '+00:00')).timestamp()
                    except (KeyError, TypeError, ValueError):
                        pass
                found_status = True
        if event.get('type') == 'response_item' and p.get('type') == 'message' and p.get('role') in ('user', 'assistant'):
            text = '\n'.join(c.get('text', '') for c in p.get('content', []) if c.get('type') in ('input_text', 'output_text', 'text')).strip()
            text = re.sub(r'<(codex_internal_context|oai-mem-citation)\b[^>]*>.*?</\1>', '', text, flags=re.S).strip()
            if text.startswith('<send_user_message_question_reply>'):
                try:
                    replies = json.loads(text.split('>', 1)[1].rsplit('</', 1)[0])
                    text = '\n'.join(str(reply.get('answer', '')) for reply in replies if isinstance(reply, dict)).strip()
                except (ValueError, TypeError):
                    text = ''
            if p.get('role') == 'user':
                text = re.sub(r'<(?:in-app-browser-context|environment_context|app_context|permissions instructions|system-reminder)\b[^>]*>.*?</[^>]+>', '', text, flags=re.S)
                if '## My request:' in text:
                    text = text.split('## My request:', 1)[1].strip()
                if text.startswith(('# AGENTS.md instructions', '<INSTRUCTIONS>', '<environment_context>')):
                    text = ''
            identity = p.get('id') or (p.get('role'), text)
            if text and identity not in seen and len(messages) < 8:
                seen.add(identity)
                messages.append({'role': p['role'], 'text': text[:6000], 'time': event.get('timestamp', '')})
        if found_status and len(messages) >= 8:
            break
    result = {'status': status, 'turnId': turn, 'runStartedAt': started_at, 'messages': list(reversed(messages))}
    CACHE[key] = (stamp, result)
    return result


def snapshot():
    state = read_state()
    now = time.time()
    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        pinned = set(json.loads((CODEX / '.codex-global-state.json').read_text()).get('pinned-thread-ids', []))
    except (OSError, ValueError):
        pinned = set()
    with sqlite3.connect(f'file:{codex_database()}?mode=ro', uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
        name = 'name' if 'name' in columns else 'NULL AS name'
        pinned_column = 'is_pinned' if 'is_pinned' in columns else '0 AS is_pinned'
        roots = " AND (agent_path IS NULL OR agent_path IN ('','/root'))" if 'agent_path' in columns else ''
        rows = db.execute(f'SELECT id,{name},title,rollout_path,updated_at,{pinned_column} FROM threads WHERE archived=0{roots} ORDER BY updated_at DESC').fetchall()
    threads = []
    with LOCK:
        for row in rows:
            path = Path(row['rollout_path'])
            try:
                # Resumed chats update their logs.
                modified = path.stat().st_mtime
                if modified < today and row['id'] not in TRACKED and row['id'] not in state['state']:
                    continue
                chat = read_chat(path)
            except OSError:
                continue
            TRACKED.add(row['id'])
            threads.append({'id': row['id'], 'title': row['name'] or row['title'], 'pinned': bool(row['is_pinned']) or row['id'] in pinned, 'updatedAt': modified, **chat})
    state = restore_resumed_chats(threads, now)
    for chat in threads:
        node = state['state'].get(chat['id'], {})
        chat['archived'] = bool(node.get('done') and node.get('doneAt') is not None and now - node['doneAt'] >= 600)
    return {'threads': threads, 'updatedAt': now, **state}


TRACKED = set()


class Handler(BaseHTTPRequestHandler):
    def response(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        if self.headers.get('Host', '').lower() not in ALLOWED_HOSTS:
            self.response(403, b'Forbidden host', 'text/plain')
            return False
        return True

    def do_GET(self):
        if not self.valid_host():
            return
        path = urlparse(self.path).path
        try:
            if path == '/health':
                self.response(200, json.dumps({'service': 'codex-chat-map', 'status': 'ok', 'version': VERSION}).encode(), 'application/json')
            elif path == '/api/chats':
                self.response(200, json.dumps(snapshot()).encode(), 'application/json')
            elif path in ('/', '/index.html', '/app.js', '/style.css'):
                file = ROOT / ('index.html' if path == '/' else path[1:])
                mime = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8'}[file.suffix[1:]]
                self.response(200, file.read_bytes(), mime)
            else:
                self.response(404, b'Not found', 'text/plain')
        except Exception as error:
            self.response(503, json.dumps({'error': str(error)}).encode(), 'application/json')

    def do_POST(self):
        if not self.valid_host():
            return
        # Restrict writes to configured origins.
        origin = self.headers.get('Origin')
        if origin not in ALLOWED_ORIGINS:
            self.response(403, b'Forbidden', 'text/plain')
            return
        if self.path == '/api/state':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 256 * 1024:
                    raise ValueError('Invalid state size')
                payload = json.loads(self.rfile.read(length))
                result = write_state(payload['updates'], payload.get('initialize') is True)
                self.response(200, json.dumps(result).encode(), 'application/json')
            except (ValueError, KeyError, TypeError) as error:
                self.response(400, json.dumps({'error': str(error)}).encode(), 'application/json')
            except sqlite3.Error:
                self.response(503, b'{"error":"State save failed"}', 'application/json')
            return
        match = re.fullmatch(r'/api/open/([0-9a-f-]{36})', self.path)
        if not match or match[1] not in TRACKED:
            self.response(404, b'Chat not found', 'text/plain')
            return
        result = subprocess.run(['open', f'codex://threads/{match[1]}'], capture_output=True)
        self.response(200 if result.returncode == 0 else 500, json.dumps({'opened': result.returncode == 0}).encode(), 'application/json')

    def log_message(self, *_):
        pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    if args.port != PORT:
        ALLOWED_ORIGINS.difference_update({f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'})
        ALLOWED_ORIGINS.update({f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}'})
    ALLOWED_HOSTS.clear()
    ALLOWED_HOSTS.update(urlparse(origin).netloc.lower() for origin in ALLOWED_ORIGINS)
    initialize_state()
    print(f'Chat map: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
