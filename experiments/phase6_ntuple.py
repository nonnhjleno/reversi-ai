"""Phase 6の初期資産・手計算資産と、推論および実プロセスの費用を記録する。"""

from __future__ import annotations

from argparse import ArgumentParser
from contextlib import redirect_stdout
from ctypes import POINTER, Structure, WinDLL, byref, c_size_t
from ctypes.wintypes import BOOL, DWORD, HANDLE
from functools import partial
from hashlib import sha256
from http.server import HTTPServer
import json
import os
from pathlib import Path
import platform
from random import Random
from statistics import median
import subprocess
import sys
from threading import Thread
from time import perf_counter_ns
import tracemalloc
from urllib.request import Request, urlopen

from harness import referee
from reversi_player import ai_engine, ntuple, ntuple_io, phase6_player, reversi, web


ROOT = Path(__file__).resolve().parents[1]
PLAYER = ROOT / 'reversi_player/phase6_player.py'
_ROUNDS = 11
_PROCESSES = 7


class _ProcessMemoryCounters(Structure):
    """Windows PROCESS_MEMORY_COUNTERSの定義。"""

    _fields_ = [
        ('cb', DWORD),
        ('PageFaultCount', DWORD),
        ('PeakWorkingSetSize', c_size_t),
        ('WorkingSetSize', c_size_t),
        ('QuotaPeakPagedPoolUsage', c_size_t),
        ('QuotaPagedPoolUsage', c_size_t),
        ('QuotaPeakNonPagedPoolUsage', c_size_t),
        ('QuotaNonPagedPoolUsage', c_size_t),
        ('PagefileUsage', c_size_t),
        ('PeakPagefileUsage', c_size_t),
    ]


def _process_memory(handle):
    """Windowsの子プロセスの常駐メモリとピークを測る。"""
    counters = _ProcessMemoryCounters()
    from ctypes import sizeof

    counters.cb = sizeof(counters)
    query = WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    query.argtypes = [HANDLE, POINTER(_ProcessMemoryCounters), DWORD]
    query.restype = BOOL
    if not query(handle, byref(counters), counters.cb):
        raise OSError('GetProcessMemoryInfo')
    return {
        'working_set_bytes': counters.WorkingSetSize,
        'peak_working_set_bytes': counters.PeakWorkingSetSize,
    }


def _record(action, repeat):
    """原サンプルnsと中央値・最大を返す。"""
    times = []
    for _ in range(repeat):
        start = perf_counter_ns()
        action()
        times.append(perf_counter_ns() - start)
    return _statistics(times)


def _statistics(times):
    """同じ単位で並べた原サンプルを集約する。"""
    return {'samples_ns': times, 'median_ns': median(times), 'max_ns': max(times)}


def _positions():
    """固定seedの合法棋譜から初期・中盤・終盤を選ぶ。"""
    rows = referee.INITIAL_ROWS
    turn = 'B'
    rng = Random(20261118)
    selected = {}
    while referee.result(rows) == 'NG':
        empty = ''.join(rows).count('E')
        if empty in (60, 32, 4):
            selected.setdefault({60: 'opening', 32: 'middle', 4: 'endgame'}[empty], (rows, turn))
        moves = referee.legal_moves(rows, turn)
        if moves:
            rows = referee.apply_move(rows, turn, rng.choice(moves))
        turn = 'W' if turn == 'B' else 'B'
    if set(selected) != {'opening', 'middle', 'endgame'}:
        raise RuntimeError('missing_benchmark_phase')
    return selected


def _child_runs(model_path, cwd):
    """複数回の起動におけるname、初回・通常着手、quitとメモリを測る。"""
    env = {**os.environ, phase6_player.MODEL_ENV: str(model_path), 'PYTHONIOENCODING': 'utf-8'}
    env.pop('REVERSI_EVAL_CONFIG', None)
    argv = [sys.executable, '-B', str(PLAYER)]
    samples = []
    for _ in range(_PROCESSES):
        start = perf_counter_ns()
        with subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            encoding='utf-8',
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as child:
            durations = {}
            transcript = []
            try:
                for label, command in [
                    ('spawn_to_name', 'name'),
                    ('first_genmove', 'genmove B'),
                    ('later_genmove', 'genmove W'),
                ]:
                    before = start if label == 'spawn_to_name' else perf_counter_ns()
                    child.stdin.write(command + '\n')
                    child.stdin.flush()
                    response = child.stdout.readline().rstrip('\r\n')
                    durations[label] = perf_counter_ns() - before
                    transcript.append([command, response])
                rows = referee.INITIAL_ROWS
                if transcript[0][1] != 'ranger':
                    raise RuntimeError('bad_model_child_name')
                for color, (_, response) in zip('BW', transcript[1:]):
                    move = tuple(map(int, response.split()))
                    rows = referee.apply_move(rows, color, move)
                memory = _process_memory(child._handle)
                before = perf_counter_ns()
                stdout, stderr = child.communicate('quit\n', timeout=5)
                durations['quit_to_exit'] = perf_counter_ns() - before
            finally:
                if child.poll() is None:
                    child.kill()
            samples.append(
                {
                    'elapsed_ns': durations,
                    'memory': memory,
                    'transcript': transcript,
                    'tail_stdout': stdout,
                    'stderr': stderr,
                    'returncode': child.returncode,
                }
            )
            if child.returncode or stdout or stderr:
                raise RuntimeError('bad_child_exit')
    return {
        'argv': argv,
        'cwd': str(cwd),
        'samples': samples,
        'summary': {
            key: _statistics([sample['elapsed_ns'][key] for sample in samples])
            for key in samples[0]['elapsed_ns']
        },
    }


def _gui_runs(model_path, log):
    """既存GUIのHTTP応答を子プロセスの再起動・再読込込みで測る。"""
    previous = os.environ.get(phase6_player.MODEL_ENV)
    os.environ[phase6_player.MODEL_ENV] = str(model_path)
    try:
        with HTTPServer(('127.0.0.1', 0), web.RequestHandler) as server:
            endpoint = f'http://127.0.0.1:{server.server_port}/genmove'
            results = []

            def request():
                """初期盤面のgenmoveをHTTPで実行する。"""
                with urlopen(Request(endpoint, data=b'{"positions":[]}'), timeout=10) as response:
                    result = json.load(response)
                    if response.status != 200 or result['positions'] != [[3, 2]]:
                        raise RuntimeError('bad_gui_response')
                    results.append(result)

            with log.open('x', encoding='utf-8') as stream, redirect_stdout(stream):
                worker = Thread(target=partial(server.serve_forever, poll_interval=0.01))
                worker.start()
                try:
                    return {
                        'endpoint': endpoint,
                        'timing': _record(request, _PROCESSES),
                        'responses': results,
                    }
                finally:
                    server.shutdown()
                    worker.join()
    finally:
        if previous is None:
            os.environ.pop(phase6_player.MODEL_ENV)
        else:
            os.environ[phase6_player.MODEL_ENV] = previous


def main():
    """独立した新規ディレクトリに資産と全試行サンプルを保存する。"""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir()
    zero = ntuple.NtupleModel.zeros()
    zero_path = output / 'untrained-zero.json'
    zero_id = ntuple_io.save_model(zero_path, zero, provenance={'kind': 'untrained_zero'})
    weights = [0.0] * ntuple.WEIGHT_COUNT
    weights[0], weights[9], weights[936] = 0.25, -0.125, -0.5
    hand_path = output / 'hand-calculated-fixture.json'
    hand_id = ntuple_io.save_model(
        hand_path, ntuple.NtupleModel(tuple(weights)), provenance={'kind': 'hand_fixture'}
    )
    model = ntuple_io.load_model(hand_path).model
    positions = _positions()
    start = perf_counter_ns()
    model.value(*positions['opening'])
    first_inference = perf_counter_ns() - start
    rates = {}
    iterations = 200
    for label, (rows, turn) in positions.items():

        def loop(fn):
            """同じ盤面で繰り返し測り、1回当たりの時間に換算する。"""
            elapsed = _record(lambda: [fn() for _ in range(iterations)], _ROUNDS)
            return {
                **elapsed,
                'calls_per_sample': iterations,
                'median_per_call_ns': elapsed['median_ns'] / iterations,
            }

        rates[label] = {
            'ntuple': loop(lambda: model.value(rows, turn)),
            'handmade': loop(
                lambda: ai_engine.evaluate_position(rows, turn, reversi.legal_moves_for)
            ),
            'fixture': {'rows': rows, 'turn': turn},
        }

    def batch():
        """複数の局面を順番に推論する。"""
        for rows, turn in positions.values():
            model.value(rows, turn)

    def save():
        """計測ごとに新規ファイルへ書き、保存時間だけを測れるようにする。"""
        ntuple_io.save_model(output / f'iteration-{next(iterations_for_save)}.json', zero)

    iterations_for_save = iter(range(_ROUNDS))
    saved = {
        'batch_three': _record(batch, _ROUNDS),
        'first_observed_inference': _statistics([first_inference]),
        'create_zero': _record(ntuple.NtupleModel.zeros, _ROUNDS),
        'load': _record(lambda: ntuple_io.load_model(zero_path), _ROUNDS),
        'save': _record(save, _ROUNDS),
    }
    tracemalloc.start()
    loaded = ntuple_io.load_model(zero_path)
    in_use, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    sources = (
        'experiments/phase6_ntuple.py',
        'reversi_player/ntuple.py',
        'reversi_player/ntuple_io.py',
        'reversi_player/phase6_player.py',
        'reversi_player/reversi.py',
        'reversi_player/ai_engine.py',
        'reversi_player/search_trace.py',
        'reversi_player/timed_search.py',
        'harness/referee.py',
        'reversi_player/web.py',
    )
    report = {
        'process': _child_runs(zero_path, output),
        'gui': _gui_runs(zero_path, output / 'gui-stdout.txt'),
        'in_process': saved,
        'inference': rates,
        'memory_tracemalloc': {'retained_bytes': in_use, 'peak_bytes': peak},
        'model_file_bytes': {'zero': zero_path.stat().st_size, 'fixture': hand_path.stat().st_size},
        'model_ids': {'zero': zero_id, 'fixture': hand_id},
        'file_hashes': {
            'zero': loaded.file_sha256,
            'fixture': sha256(hand_path.read_bytes()).hexdigest(),
        },
        'references_per_state': ntuple.REFERENCE_COUNT,
        'source_sha256': {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        'environment': {
            'python': sys.version,
            'executable': sys.executable,
            'os': platform.platform(),
            'threads_logical': os.cpu_count(),
            'processor': platform.processor(),
        },
        'argv': [sys.executable, '-B', '-m', 'experiments.phase6_ntuple', str(output)],
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    }
    with (output / 'measurements.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
