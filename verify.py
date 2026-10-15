#!/usr/bin/env python3
"""Test with fake chats and temporary storage."""
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import io
import zipfile
import hashlib
import urllib.error
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
IDENTITY = '00000000-0000-4000-8000-000000000001'


def request(url, body=None, origin=None):
    headers = {'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = origin
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def verify_updates():
    spec = importlib.util.spec_from_file_location('updater', ROOT / 'update.py')
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)

    def package(version, extra=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for filename in updater.FILES:
                archive.writestr('CodexMap/' + filename, version + '\n' if filename == 'VERSION' else (ROOT / filename).read_bytes())
            if extra:
                for entry, body in extra:
                    archive.writestr(entry, body)
        return stream.getvalue()

    assert updater.version_tuple('v1.2.10') > updater.version_tuple('1.2.9')
    for invalid in ('1.2.3-beta', 'v01.2.3', '../1.2.3', '1.2'):
        try:
            updater.version_tuple(invalid)
            raise AssertionError('Invalid release version accepted')
        except ValueError:
            pass
    data = package('1.1.0')
    release = {'tag_name': 'v1.1.0', 'assets': [
        {'name': 'CodexMap-source.zip', 'browser_download_url': 'https://github.com/source'},
        {'name': 'SHA256SUMS.txt', 'browser_download_url': 'https://github.com/checksum'},
    ]}
    def fetch(url, limit):
        return (hashlib.sha256(data).hexdigest() + '  CodexMap-source.zip\n').encode() if url.endswith('checksum') else data
    assert updater.verified_source(release, fetch) == ('1.1.0', data)
    for bad in ({**release, 'prerelease': True}, {**release, 'draft': True}, {**release, 'assets': []}):
        try:
            updater.verified_source(bad, fetch)
            raise AssertionError('Unsafe release accepted')
        except ValueError:
            pass
    try:
        updater.verified_source(release, lambda url, limit: b'0' * 64 + b'  CodexMap-source.zip\n' if url.endswith('checksum') else data)
        raise AssertionError('Checksum mismatch accepted')
    except ValueError:
        pass
    with tempfile.TemporaryDirectory(prefix='codexmap-update-check-') as temporary:
        base = Path(temporary)
        old = base / 'releases/1.0.0'
        old.mkdir(parents=True)
        (old / 'VERSION').write_text('1.0.0\n')
        updater.replace_current(base, old)
        state = base / 'dashboard.sqlite3'
        state.write_bytes(b'fixture saved state')
        (base / 'config.json').write_text('fixture config')
        calls = []
        updater.apply_release(base, '1.1.0', data, lambda: calls.append('restart'), lambda root, v: calls.append(v))
        assert (base / 'current/VERSION').read_text().strip() == '1.1.0'
        assert calls == ['restart', '1.1.0'] and old.exists()
        assert state.read_bytes() == b'fixture saved state'
        assert (base / 'config.json').read_text() == 'fixture config'
        calls.clear()
        def fail_new(root, version):
            calls.append(version)
            if version == '1.2.0':
                raise RuntimeError('fixture health failure')
        try:
            updater.apply_release(base, '1.2.0', package('1.2.0'), lambda: calls.append('restart'), fail_new)
            raise AssertionError('Failed health check accepted')
        except RuntimeError:
            pass
        assert (base / 'current/VERSION').read_text().strip() == '1.1.0'
        assert calls == ['restart', '1.2.0', 'restart', '1.1.0']
        assert state.read_bytes() == b'fixture saved state'
        for entry in ('../escape', '/absolute', 'CodexMap/../../escape', 'CodexMap/app\\evil'):
            try:
                updater.extract_source(package('1.3.0', [(entry, 'bad')]), base / 'invalid')
                raise AssertionError('Unsafe ZIP path accepted')
            except ValueError:
                pass
        link = zipfile.ZipInfo('CodexMap/app/symlink')
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        try:
            updater.extract_source(package('1.3.0', [(link, '/tmp/elsewhere')]), base / 'invalid')
            raise AssertionError('ZIP symlink accepted')
        except ValueError:
            pass
        with updater.exclusive_lock(base):
            try:
                with updater.exclusive_lock(base):
                    raise AssertionError('Concurrent updater accepted')
            except RuntimeError:
                pass
    print('PASS: updater checks, rollback, and locking.')


def verify_installer_lifecycle():
    spec = importlib.util.spec_from_file_location('lifecycle_installer', ROOT / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory(prefix='codexmap-installer-check-') as temporary:
        base = Path(temporary).resolve()
        path = base / 'dashboard.plist'
        old_content = b'fixture existing agent'
        path.write_bytes(old_content)
        calls = []
        def same_run(command, **kwargs):
            calls.append(command[1])
            return SimpleNamespace(returncode=0)
        installer.configure_agent('fixture.dashboard', path, old_content, same_run, lambda _: None)
        assert calls == ['print', 'kickstart']
        assert 'bootout' not in calls and 'bootstrap' not in calls
        loaded_probes = iter((0, 0, 0, 1))
        calls.clear()
        def delayed_stop(command, **kwargs):
            calls.append(command[1])
            return SimpleNamespace(returncode=next(loaded_probes) if command[1] == 'print' else 0)
        installer.stop_agent('fixture.dashboard', delayed_stop, lambda _: None)
        assert calls.count('print') == 4 and calls.count('bootout') == 1
        attempts = iter((5, 37, 0))
        calls.clear()
        def retry_bootstrap(command, **kwargs):
            calls.append(command[1])
            return SimpleNamespace(returncode=next(attempts))
        installer.bootstrap_agent('fixture.dashboard', path, retry_bootstrap, lambda _: None)
        assert calls == ['bootstrap', 'bootstrap', 'bootstrap']
        try:
            installer.bootstrap_agent('fixture.dashboard', path, lambda *a, **kw: SimpleNamespace(returncode=5), lambda _: None, attempts=2)
            raise AssertionError('Permanent bootstrap failure accepted')
        except RuntimeError as error:
            assert 'Could not start' in str(error) and 'root' not in str(error)
        old = base / 'releases/1.0.0'
        old.mkdir(parents=True)
        (old / 'VERSION').write_text('1.0.0\n')
        installer.replace_current(base, old)
        new = installer.stage_release(base, '1.0.0', True)
        assert new != old and (old / 'VERSION').read_text() == '1.0.0\n'
        config_path = base / 'config.json'
        config_path.write_bytes(b'{"port":4399,"fixture":"previous"}\n')
        original_config = config_path.read_bytes()
        state = base / 'dashboard.sqlite3'
        state.write_bytes(b'fixture positions and done marks')
        loaded = installer.agent_loaded
        configure = installer.configure_agent
        try:
            installer.agent_loaded = lambda label: True
            restored = []
            installer.configure_agent = lambda label, p, content: (p.write_bytes(content), restored.append(label))
            def activate():
                path.write_bytes(b'new agent configuration')
            def failed_health():
                raise RuntimeError('Fixture service failed to start')
            try:
                installer.installation_transaction(base, {'port': 4400}, [(installer.LABEL, path, b'new')], new, activate, failed_health, lambda: restored.append('health-verified'))
                raise AssertionError('Failed installation accepted')
            except RuntimeError as error:
                assert 'Previous setup restored.' in str(error)
            assert (base / 'current').resolve() == old
            assert config_path.read_bytes() == original_config
            assert path.read_bytes() == old_content and restored == [installer.LABEL, 'health-verified']
            assert state.read_bytes() == b'fixture positions and done marks'
        finally:
            installer.agent_loaded = loaded
            installer.configure_agent = configure
    print('PASS: reinstall kickstart, delayed bootout, bootstrap retries, fresh staging, and failed-install rollback of code, config, agent, and saved data.')


def run():
    spec = importlib.util.spec_from_file_location('installer', ROOT / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    updater_agent = installer.updater_plist(Path('/tmp/codexmap'), Path('/tmp/logs'), '/usr/bin/python3')
    assert updater_agent['StartInterval'] == 21600 and updater_agent['RunAtLoad']
    assert 'KeepAlive' not in updater_agent
    service = installer.service_plist(Path('/tmp/codexmap'), Path('/tmp/logs'), '/usr/bin/python3', False)
    assert service['EnvironmentVariables']['CODEXMAP_DATA_DIR'] == '/tmp/codexmap'
    assert service['ProgramArguments'][-1] == '/tmp/codexmap/current/app/server.py'
    origins, remote = installer.tailscale_settings({'BackendState': 'Running', 'Self': {
        'DNSName': 'test-mac.example.ts.net.', 'HostName': 'test-mac', 'TailscaleIPs': ['100.64.0.1']
    }}, 4400)
    assert 'http://100.64.0.1:4400' in origins and remote == 'https://test-mac.example.ts.net/'
    with tempfile.TemporaryDirectory(prefix='chat-map-check-') as temp:
        folder = Path(temp)
        shutil.copytree(ROOT / 'app', folder / 'app')
        codex = folder / 'fake-codex'
        codex.mkdir()
        rollout = codex / 'fake-rollout.jsonl'
        events = [
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Test chat.'}]}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'test-turn'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Fake task finished.\n<oai-mem-citation>Hidden metadata</oai-mem-citation>'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<codex_internal_context>Hidden automatic prompt</codex_internal_context>'}]}},
        ]
        rollout.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
        # Test schema discovery and optional columns.
        with sqlite3.connect(codex / 'state_7.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT,title TEXT,rollout_path TEXT,updated_at INTEGER,archived INTEGER,agent_path TEXT)')
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', (IDENTITY, 'Fixture chat', str(rollout), int(time.time()), 0, '/root'))
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', ('00000000-0000-4000-8000-000000000002', 'Child agent', str(rollout), int(time.time()), 0, '/root/child'))
        sqlite3.connect(codex / 'state_8.sqlite').close()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        config = {'port': port, 'codexHome': str(codex), 'allowedOrigins': []}
        (folder / 'config.json').write_text(json.dumps(config))
        base = f'http://127.0.0.1:{port}'
        process = None

        def start():
            worker = subprocess.Popen([sys.executable, str(folder / 'app/server.py')], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for _ in range(40):
                try:
                    request(base + '/health')
                    return worker
                except OSError:
                    if worker.poll() is not None:
                        raise RuntimeError(worker.stderr.read().decode())
                    time.sleep(.1)
            worker.terminate()
            worker.wait()
            raise RuntimeError('Fixture server did not start')

        def stop(worker):
            worker.terminate()
            worker.wait(timeout=5)
            worker.stderr.close()

        def update(patch):
            return request(base + '/api/state', {'updates': {IDENTITY: patch}}, base)['state'][IDENTITY]

        try:
            process = start()
            assert request(base + '/health')['version'] == 'development'
            evil_host = urllib.request.Request(base + '/health', headers={'Host': 'untrusted.example'})
            try:
                urllib.request.urlopen(evil_host, timeout=3)
                raise AssertionError('Unexpected Host accepted')
            except urllib.error.HTTPError as error:
                assert error.code == 403
            data = request(base + '/api/chats')
            assert len(data['threads']) == 1 and data['threads'][0]['title'] == 'Fixture chat'
            assert data['threads'][0]['status'] == 'finished' and len(data['threads'][0]['messages']) == 2
            assert [m['role'] for m in data['threads'][0]['messages']] == ['user', 'assistant']
            assert data['threads'][0]['messages'][-1]['text'] == 'Fake task finished.'
            first = update({'x': .2, 'y': .8, 'done': True})
            assert first['doneAt'] > 0
            assert not request(base + '/api/chats')['threads'][0]['archived']
            second = update({'x': .4, 'done': True})
            assert second['doneAt'] == first['doneAt'] and second['y'] == .8
            try:
                request(base + '/api/state', {'updates': {IDENTITY: {'done': False}}}, 'https://untrusted.example')
                raise AssertionError('Unexpected cross-origin write accepted')
            except urllib.error.HTTPError as error:
                assert error.code == 403
            try:
                update({'x': 2})
                raise AssertionError('Invalid coordinate accepted')
            except urllib.error.HTTPError as error:
                assert error.code == 400
            state_db = folder / 'dashboard.sqlite3'
            second['doneAt'] = time.time() - 601
            with sqlite3.connect(state_db) as db:
                db.execute('UPDATE node_state SET data=? WHERE id=?', (json.dumps(second), IDENTITY))
            assert request(base + '/api/chats')['threads'][0]['archived']
            stop(process)
            process = start()
            assert request(base + '/api/chats')['threads'][0]['archived']
            assert update({'done': False}) == {'x': .4, 'y': .8, 'done': False}
            assert not request(base + '/api/chats')['threads'][0]['archived']
            assert update({'done': True})['doneAt'] > first['doneAt']
            assert not request(base + '/api/chats')['threads'][0]['archived']
            print('PASS: chat data, state, permissions, and archive timing.')
            print('PASS: Tailscale origins.')
        finally:
            if process and process.poll() is None:
                stop(process)


if __name__ == '__main__':
    run()
    verify_updates()
    verify_installer_lifecycle()
