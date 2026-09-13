"""Observed isolated source-app QA; never points at a user's data directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port(kind):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--laps', type=int, default=25)
    parser.add_argument('--speed', type=float, default=25)
    parser.add_argument('--circuit', default='monza')
    parser.add_argument('--socks-proxy', action='store_true')
    parser.add_argument('--output-parent', type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='pitbox-strategy-e2e-', dir=args.output_parent))
    input_digest = hashlib.sha256(args.capture.read_bytes()).hexdigest() if args.capture else None
    data = root / 'data'
    data.mkdir()
    sentinel = data / 'qa-sentinel.txt'
    sentinel.write_text('isolated data must survive shutdown and reopen\n')
    web_port, udp_port = free_port(socket.SOCK_STREAM), free_port(socket.SOCK_DGRAM)
    env = {k: v for k, v in os.environ.items() if not (
        k.startswith('PITWALL_') or k.upper().endswith('_PROXY') or
        k in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY',
              'KIMI_API_KEY', 'MOONSHOT_API_KEY', 'CUSTOM_LLM_API_KEY'}
    )}
    env.update({
        'PYTHONPATH': str(REPO / 'src'), 'PITWALL_DATA_DIR': str(data),
        'PITWALL_WEB_HOST': '127.0.0.1', 'PITWALL_WEB_PORT': str(web_port),
        'PITWALL_UDP_BIND_HOST': '127.0.0.1', 'PITWALL_UDP_PORT': str(udp_port),
        'PITWALL_OPEN_BROWSER': 'false', 'PITWALL_NATIVE_VOICE': 'false',
        'PITWALL_WAKE_ENABLED': 'false', 'PITWALL_PROACTIVE_NARRATION_ENABLED': 'false',
    })
    if args.socks_proxy:
        env.update({'ALL_PROXY': 'socks5h://127.0.0.1:1',
                    'OPENAI_API_KEY': 'sk-test-no-billing-proxy-startup',
                    'PITWALL_PROACTIVE_ENABLED': 'false'})
    base = f'http://127.0.0.1:{web_port}'

    def get(path):
        with HTTP.open(base + path, timeout=5) as response:
            return json.load(response)

    def post(path, body):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        with HTTP.open(request, timeout=10) as response:
            return json.load(response)

    def launch(log):
        proc = subprocess.Popen([str(PYTHON), '-c', 'from pitwall.main import run; run()'],
                                cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f'app exited {proc.returncode}; see {root}')
            try:
                health = get('/api/health')
                assert Path(health['database']).parent == data
                assert health['udp_listener']
                return proc, health
            except (OSError, ValueError):
                time.sleep(.15)
        proc.terminate()
        proc.wait(timeout=10)
        raise RuntimeError(f'app startup timeout; see {root}')

    summary = {'root': str(root), 'violations': [], 'snapshots': 0, 'held_snapshots': 0,
               'projection_snapshots': 0, 'capture': str(args.capture) if args.capture else None,
               'socks_proxy': args.socks_proxy,
               'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
               'input_sha256': input_digest, 'http_latencies_s': [],
               'inventory_observed': 0, 'weather_snapshots': 0}
    with (root / 'server.log').open('w') as log, (root / 'telemetry.log').open('w') as traffic:
        app, health = launch(log)
        emitter = None
        try:
            summary['health_before'] = health
            assert not health['openai_key_configured'] or args.socks_proxy
            with HTTP.open(base + '/', timeout=5) as response:
                page = response.read().decode()
                assert response.status == 200 and 'Your Pit Box' in page
            if args.capture:
                command = [str(PYTHON), '-m', 'tools.replay_capture', str(args.capture),
                           '--port', str(udp_port), '--speed', str(args.speed)]
            else:
                command = [str(PYTHON), '-m', 'tools.replay_demo', '--port', str(udp_port),
                           '--laps', str(args.laps), '--speed', str(args.speed),
                           '--circuit', args.circuit]
            emitter = subprocess.Popen(command, cwd=REPO, env=env,
                                       stdout=traffic, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 360
            with (root / 'snapshots.jsonl').open('w') as snapshots:
                while emitter.poll() is None and time.monotonic() < deadline:
                    request_started = time.monotonic()
                    state = get('/api/state')
                    summary['http_latencies_s'].append(time.monotonic() - request_started)
                    strategy = state.get('strategy') or {}
                    rec = strategy.get('recommended') or {}
                    probs = rec.get('position_probabilities') or {}
                    summary['snapshots'] += 1
                    summary['held_snapshots'] += int(bool((strategy.get('stability') or {}).get('held')))
                    row = {'lap': state.get('current_lap'), 'strategy': strategy}
                    snapshots.write(json.dumps(row) + '\n')
                    if probs and rec.get('expected_finish_position') is not None:
                        summary['projection_snapshots'] += 1
                        mean = sum(float(k.lstrip('P')) * float(v) for k, v in probs.items())
                        expected = float(rec['expected_finish_position'])
                        if not math.isclose(mean, expected, abs_tol=.04):
                            summary['violations'].append({'lap': row['lap'], 'mean': mean,
                                                          'expected': expected, 'held': strategy.get('stability')})
                    inventory = strategy.get('tyre_inventory') or {}
                    summary['inventory_observed'] += int(inventory.get('status') == 'known')
                    summary['weather_snapshots'] += int(bool(strategy.get('weather_crossover')))
                    if rec:
                        if rec.get('finish_projection_valid') and (
                            not rec.get('feasible') or not rec.get('legal')):
                            summary['violations'].append({'lap': row['lap'], 'check': 'invalid finish marked valid'})
                        if rec.get('inventory_status') == 'unknown' and rec.get('confidence') != 'low':
                            summary['violations'].append({'lap': row['lap'], 'check': 'unknown stock overconfident'})
                        indices = [i for i in rec.get('tyre_set_indices', []) if i is not None]
                        if len(indices) != len(set(indices)):
                            summary['violations'].append({'lap': row['lap'], 'check': 'physical set reused'})
                        costs = rec.get('pit_stop_costs_s', [])
                        if costs and not math.isclose(sum(costs), rec.get('total_pit_cost_s', 0), abs_tol=.02):
                            summary['violations'].append({'lap': row['lap'], 'check': 'pit ledger mismatch'})
                    time.sleep(.4)
            if emitter.poll() is None:
                raise RuntimeError('telemetry replay timeout')
            assert emitter.returncode == 0
            time.sleep(2)
            state = get('/api/state')
            summary['state_final'] = {k: state.get(k) for k in [
                'track_name', 'session_type', 'current_lap', 'total_laps', 'player_position',
                'packet_format', 'packets_received', 'packets_dropped', 'telemetry_stale']}
            summary['drivers'] = len(state.get('drivers') or [])
            summary['health_after'] = get('/api/health')
            summary['sessions_before_shutdown'] = get('/api/v1/sessions')
            summary['tyre_answer'] = post('/api/ask', {'text': 'What tyres am I on?'})
            assert state['packets_received'] > 0
            assert state['packets_dropped'] == 0
            assert summary['drivers'] == 20
            assert not summary['violations'], summary['violations'][:5]
            assert post('/api/shutdown', {})['stopping']
            app.wait(timeout=25)
            assert app.returncode == 0
            assert sentinel.read_text() == 'isolated data must survive shutdown and reopen\n'
            app, _ = launch(log)
            summary['sessions_after_reopen'] = get('/api/v1/sessions')
            assert post('/api/shutdown', {})['stopping']
            app.wait(timeout=25)
            assert app.returncode == 0
            if args.capture:
                assert hashlib.sha256(args.capture.read_bytes()).hexdigest() == input_digest
            assert sentinel.read_text() == 'isolated data must survive shutdown and reopen\n'
            assert summary['sessions_after_reopen']['sessions'], 'persisted session catalog is empty'
            latencies = sorted(summary.pop('http_latencies_s'))
            summary['http_latency_max_s'] = round(max(latencies, default=0), 4)
            summary['http_latency_p95_s'] = round(latencies[int(.95 * (len(latencies)-1))], 4) if latencies else None
            summary['passed'] = True
        finally:
            if emitter is not None and emitter.poll() is None:
                emitter.terminate()
                emitter.wait(timeout=15)
            if app.poll() is None:
                try:
                    post('/api/shutdown', {})
                    app.wait(timeout=25)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    app.terminate()
                    app.wait(timeout=15)
            (root / 'summary.json').write_text(json.dumps(summary, indent=2))
            print(json.dumps({k:v for k,v in summary.items() if k not in {
                'health_before', 'health_after', 'sessions_before_shutdown', 'sessions_after_reopen'}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
