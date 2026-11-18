"""Rangerの旧予算監査と7秒の参考診断。ローカル原証拠を入力とする。"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import importlib.util
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness import evaluate, openings, protocol, referee  # noqa: E402

OLD = ROOT / 'evaluation_results/depth5-7s-20261114'
PREV = ROOT / 'evaluation_results/depth5-runtime-verification-20261118'
INVESTIGATION = ROOT / 'evaluation_results/depth5-investigation-20261118'
OUT = ROOT / 'evaluation_results/depth5-budget-stability-20261118'
DEV = ROOT / 'benchmark_sets/phase0/development.json'
SEED = 20261118
FILES = ('reversi.py', 'ai_engine.py', 'timed_search.py', 'search_trace.py', 'response_config.json')
POLICY = {'target_ns': 7_000_000_000, 'new_depth_margin_ns': 1_000_000_000,
          'stop_margin_ns': 500_000_000}
UNKNOWN = {'format_version': 2, 'budget': {'kind': 'unknown'}, 'search': {}}
EVENT = 'RANGER_RUNTIME_DIAGNOSTIC_JSONL'
RUN = 'RANGER_RUNTIME_DIAGNOSTIC_RUN_ID'
EVAL_ENV = ('REVERSI_EVAL_CONFIG', 'REVERSI_EVAL_TRACE', 'REVERSI_EVAL_TRACE_CONTEXT')


def read(path):
    """保存済みJSONを読む。"""
    return json.loads(path.read_bytes())


def lines(path):
    """JSONLを読む。診断対象外の応答ではファイルが空でもよい。"""
    return [json.loads(line) for line in path.read_bytes().splitlines()] if path.exists() else []


def save(path, data):
    """原証拠と既存試行を上書きせず保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write('\n')


def sha(path):
    """ファイルの実バイトからSHA-256を求める。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prior(name):
    """既存の診断資産生成処理を、mainを起動せずに再利用する。"""
    spec = importlib.util.spec_from_file_location(name, PREV / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def time_stats(data):
    """秒の基本統計とnearest-rankのp95を返す。"""
    if not data:
        return None
    values = sorted(data)
    return {'n': len(values), 'mean': statistics.fmean(values), 'median': statistics.median(values),
            'p95': values[math.ceil(.95 * len(values)) - 1], 'max': values[-1],
            'over': {seconds: sum(v > seconds for v in values) for seconds in (7, 8, 10)}}


def audit():
    """旧7比較のCLI、全プレイヤー設定、全genmove外部期限を照合する。"""
    commands = {item['name']: item for item in lines(OLD / 'commands.jsonl')}
    completions = {item['name']: item for item in lines(OLD / 'completions.jsonl')}
    old_audit = read(OLD / 'final-audit.json')
    files = {}
    for label in ('a0', 'a1'):
        folder = OLD / 'players' / label
        files[label] = {name: sha(folder / name) for name in FILES}
        for name in FILES[:-1]:
            assert files[label][name] == old_audit['final_source_sha256'][f'reversi_player/{name}']
        assert read(folder / FILES[-1]) == dict(POLICY, max_depth=4 if label == 'a0' else 5)
    reports = []
    for path in sorted((OLD / 'results').glob('main-*.json')):
        entry = commands[path.stem]
        assert not any('config' in flag for flag in entry['argv'] if flag.startswith('--'))
        assert completions[path.stem]['exit_code'] == 0
        raw = path.read_bytes()
        report = json.loads(raw)
        conditions = report['conditions']
        assert all(conditions['budget_configs'][role] == UNKNOWN for role in ('A', 'B'))
        assert conditions['genmove_timeout'] == 10
        roles = {}
        for role, flag in (('A', '--a'), ('B', '--b')):
            target = ROOT / entry['argv'][entry['argv'].index(flag) + 1]
            assert report['targets'][role]['sha256'] == sha(target)
            assert Path(report['targets'][role]['path']).resolve() == target.resolve()
            recorded = [game['players'][role] for game in report['games']]
            assert all(p['config'] == UNKNOWN and p['budget_trace']['events'] == 0
                       and p['budget_trace']['artifact']['exists'] is False for p in recorded)
            moves = [c for p in recorded for c in p['commands'] if c['command'].startswith('genmove ')]
            assert all(c['limit_ns'] == 10_000_000_000 for c in moves)
            roles[role] = {'script': str(target), 'sha256': sha(target), 'child_config': UNKNOWN,
                           'trace_count': 0, 'external_ns': 10_000_000_000,
                           'times': time_stats([c['seconds'] for c in moves if 'error' not in c])}
        reports.append({'result': str(path), 'result_sha256': hashlib.sha256(raw).hexdigest(),
                        'argv_record': entry, 'completion': completions[path.stem],
                        'conditions': conditions, 'games': len(report['games']), 'roles': roles})
    assert len(reports) == 7 and sum(r['games'] for r in reports) == 540
    inputs = [OLD / name for name in ('commands.jsonl', 'completions.jsonl', 'final-audit.json',
                                    'pre-registration.json')]
    inputs += [INVESTIGATION / 'game-analysis/game-path-analysis.json',
               PREV / 'stage3-trial-summary-v4.json']
    save(OUT / 'historical-audit.json', {
        'inputs': {str(path): sha(path) for path in inputs}, 'reports': reports, 'assets': files,
        'path': ['evaluate._config_info(None) -> v2 unknown, search {}',
                 'evaluate._evaluate -> temporary config JSON',
                 'evaluate.play_game -> Player(evaluation_config, evaluation_trace, evaluation_context)',
                 'Player removes inherited evaluation variables and sets all three',
                 'search_trace.load -> None; REVERSI_EVAL_CONFIG remains set',
                 'reversi.main -> no TimedSelector; Board -> ordered fixed depth 2'],
        'limit_ns': None, 'limit_ns_note': 'search_iterative was not called',
        'historical_depth_events_available': False,
    })


def prepare():
    """前回の局面と中断例、序中終盤およびパス・終局を実行前に固定する。"""
    regen = prior('prepare_stage2')
    groups, _, _ = regen.regenerate()
    saved = {p['position_id']: p for p in read(OLD / 'positions.json')['positions']}
    candidates = {p['position_id']: p for group in groups.values() for p in group}
    assert candidates.keys() == saved.keys()
    for key, case in candidates.items():
        assert all(case[k] == saved[key][k] for k in ('rows', 'color', 'history') if k in saved[key])
    previous_path = PREV / 'stage3-cases-v2.json'
    ids = [p['position_id'] for p in read(previous_path)['cases']]
    stress_path = OLD / 'position-sweep-stress.json'
    stress = read(stress_path)
    sweep_rows = next(value for value in stress.values() if isinstance(value, list))
    pressured = [p['position_id'] for p in sweep_rows
                 if p['depth_setting'] == 5 and p['stop_reason'] == 'time_limit']
    ids.extend(key for key in pressured if key not in ids)
    for category, number in (('early', 1), ('middle', 1), ('late', 2), ('corner', 1),
                             ('pass', 1), ('terminal', 1)):
        picks = [p for p in groups[category] if p['position_id'] not in ids]
        picks.sort(key=lambda p: len(p['legal_moves']), reverse=category in ('middle', 'corner'))
        ids.extend(p['position_id'] for p in picks[:number])
    cases = [candidates[key] for key in ids]
    assert 10 <= len(cases) <= 20 and len(set(ids)) == len(ids)
    for case in cases:
        regen.verify_history(case)
    entries, selection = openings.select_entries(openings.load_population(DEV), 5, SEED)
    save(OUT / 'plan.json', {
        'argv': sys.argv, 'cwd': str(Path.cwd()), 'development_sha256': sha(DEV),
        'input_sha256': {p.name: sha(p) for p in (previous_path, stress_path, OLD / 'positions.json')},
        'policy': POLICY, 'time_limits_seconds': [5, 1, 10], 'seed': SEED, 'cases': cases,
        'selection_rule': 'previous three, old stressed depth-5 time stops, then legal-count picks by category',
        'conditions': ['normal', 'one_logical_cpu_plus_one_worker'], 'repeats': 3,
        'ordering': 'per position/repeat alternate depth orientation and reverse telemetry in second depth',
        'game_rule': 'Five fixed development openings, paired color swaps, A0 vs A1, normal load',
        'games_stop_rule': 'stop after a protocol/evidence failure or an 8-second response',
        'game_entries': entries, 'game_selection': selection,
    })
    instrument = prior('instrument_player_assets')
    manifest = {}
    for depth in (4, 5):
        for mode in ('plain', 'diag'):
            label = f'd{depth}-{mode}'
            source = OLD / 'players' / ('a0' if depth == 4 else 'a1')
            target = OUT / 'players' / label
            target.mkdir(parents=True, exist_ok=False)
            for name in FILES:
                (target / name).write_bytes((source / name).read_bytes())
            if mode == 'diag':
                path = target / 'timed_search.py'
                instrumented = instrument.instrument(path.read_text(encoding='utf-8'))
                anchor = "                    'command_received_ns': received,"
                assert instrumented.count(anchor) == 1
                path.write_text(instrumented.replace(anchor, anchor + '''
                    'limit_ns': self._limit_ns,
                    'policy_new_depth_deadline_ns': received + self._policy.target_ns - self._policy.new_depth_margin_ns,
                    'search_route': 'timed_selector','''), encoding='utf-8')
                path = target / 'reversi.py'
                data = path.read_text(encoding='utf-8')
                anchor = '    board = Board(selector=selector)\n'
                assert data.count(anchor) == 1
                path.write_text(data.replace(anchor, anchor + '''
    if diagnostic_path := os.environ.get('RANGER_RUNTIME_DIAGNOSTIC_JSONL'):
        import json
        with open(diagnostic_path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({
                'type': 'boot', 'pid': os.getpid(), 'path': __file__,
                'trace_active': trace is not None, 'timed_selector_active': controller is not None,
                'fixed_default_depth': ai_engine.DEFAULT_SEARCH_DEPTH if selector is None else None,
                'evaluation_config': os.environ.get('REVERSI_EVAL_CONFIG'),
                'run_id': os.environ.get('RANGER_RUNTIME_DIAGNOSTIC_RUN_ID'),
            }) + '\\n')
'''), encoding='utf-8')
            manifest[label] = {'original': {name: sha(source / name) for name in FILES},
                               'clone': {name: sha(target / name) for name in FILES}}
            for name in FILES[:-1]:
                compile((target / name).read_bytes(), name, 'exec')
    save(OUT / 'asset-manifest.json', manifest)


def affinity(handle, mask=None):
    """Windowsの実プロセスaffinityを取得し、指定時だけ変更する。"""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessAffinityMask.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    available, system = ctypes.c_size_t(), ctypes.c_size_t()
    if mask is not None and not kernel.SetProcessAffinityMask(handle, mask):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetProcessAffinityMask(handle, ctypes.byref(available), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    return available.value


@contextlib.contextmanager
def load_condition(stressed):
    """Phase 5に準じて親・子・競合workerを同じ論理CPUに置く。"""
    old = affinity(-1)
    if not stressed:
        yield {'parent_affinity_mask': old, 'worker': None}
        return
    one = old & -old
    worker = None
    args = [sys.executable, '-B', str(Path(__file__).resolve()), 'spin']
    try:
        affinity(-1, one)
        worker = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        assert affinity(worker._handle) == one
        time.sleep(.25)
        assert worker.poll() is None
        yield {'parent_affinity_mask': one, 'worker': {'pid': worker.pid, 'argv': args,
                                                     'affinity_mask': one}}
        assert worker.poll() is None
    finally:
        try:
            if worker is not None:
                worker.terminate()
                worker.wait(timeout=5)
        finally:
            affinity(-1, old)


@contextlib.contextmanager
def observe_launch(events, enabled, launches):
    """Popenの実引数と関係する環境だけを記録する。"""
    original = subprocess.Popen

    def spawn(argv, *args, **kwargs):
        process = original(argv, *args, **kwargs)
        env = kwargs['env']
        keys = (*EVAL_ENV, EVENT, RUN, 'PYTHONHASHSEED', 'PYTHONIOENCODING', 'PYTHONDONTWRITEBYTECODE')
        launches.append({'argv': argv, 'cwd': kwargs['cwd'], 'pid': process.pid,
                         'affinity_mask': affinity(process._handle),
                         'env': {key: env.get(key) for key in keys}})
        return process

    delta = {'PYTHONDONTWRITEBYTECODE': '1'}
    if enabled:
        delta.update({EVENT: str(events), RUN: events.stem})
    with patch.dict(os.environ, delta), patch.object(protocol.subprocess, 'Popen', spawn):
        if not enabled:
            os.environ.pop(EVENT, None)
            os.environ.pop(RUN, None)
        yield


def verify_event(event, launch, depth, response, rows, color):
    """完了反復・返却手・盤面・実ポリシーを親プロセスの証拠と照合する。"""
    serialized = json.dumps({'rows': rows, 'color': color}, sort_keys=True, separators=(',', ':'))
    assert event['position_sha256'] == hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    assert event['process_id'] == launch['pid'] and event['run_id'] == launch['env'][RUN]
    assert event['loaded_policy'] == dict(POLICY, max_depth=depth) and event['limit_ns'] is None
    assert event['config_sha256'] == sha(Path(launch['cwd']) / 'response_config.json')
    assert event['ai_engine_sha256'] == sha(Path(launch['cwd']) / 'ai_engine.py')
    assert event['timed_search_sha256'] == sha(Path(launch['cwd']) / 'timed_search.py')
    assert event['policy_new_depth_deadline_ns'] - event['command_received_ns'] == 6_000_000_000
    assert event['stop_deadline_ns'] - event['command_received_ns'] == 6_500_000_000
    assert event['returned_move'] == list(map(int, response.split()))
    assert 0 <= event['completed_depth'] <= event['max_depth_started'] <= depth
    completed = [it for it in event['iterations'] if it['status'] == 'completed']
    if completed:
        assert completed[-1]['depth'] == event['adopted_depth'] == event['completed_depth']
        assert completed[-1]['move'] == event['returned_move']
    else:
        assert event['fallback_used'] and event['adopted_depth'] is None


def probe(case, depth, mode, name, load, historical=False):
    """保存済み履歴を再生し、独立refereeと実子プロセスの応答を検査する。"""
    directory = OUT / 'probes'
    directory.mkdir(exist_ok=True)
    path = directory / f'{name}.json'
    events_path = directory / f'{name}.jsonl'
    assert not path.exists() and not events_path.exists()
    script = OUT / 'players' / f'd{depth}-{mode}' / 'reversi.py'
    launches = []
    record = {'name': name, 'position_id': case['position_id'], 'depth': depth, 'mode': mode,
              'historical_route_probe': historical, 'load': load, 'launches': launches}
    player = None
    try:
        kwargs = {}
        if historical:
            config = directory / 'old-normalized-config.json'
            kwargs = {'evaluation_config': config, 'evaluation_trace': directory / f'{name}-trace4.jsonl',
                      'evaluation_context': {'purpose': 'game'}}
        with observe_launch(events_path, mode == 'diag', launches):
            with protocol.Player(script, seed=SEED, **kwargs) as player:
                for move in case['history']:
                    if move['position'] is not None:
                        x, y = move['position']
                        assert player.command(f'play {move["color"]} {x} {y}') == ['OK']
                board, color = tuple(case['rows']), case['color']
                assert player.command('showboard') == list(board)
                legal = referee.legal_moves(board, color)
                record['legal_count'] = len(legal)
                command = f'genmove {color}'
                response, = player.command(command)
                record['genmove'] = dict(player.commands[-1])
                if legal:
                    move = tuple(map(int, response.split())) if response != 'NG' else None
                    if move not in legal:
                        raise protocol.HarnessError('illegal_move', repr(response), command)
                    board = referee.apply_move(board, color, move)
                else:
                    assert response == 'NG'
                assert player.command('showboard') == list(board)
                assert player.command('result') == [referee.result(board)]
        assert player.lifecycle['termination_kind'] == 'normal_quit'
        assert launches[0]['affinity_mask'] == load['parent_affinity_mask']
        events = lines(events_path)
        if mode == 'diag':
            boot, *searches = events
            assert boot['type'] == 'boot' and boot['pid'] == launches[0]['pid']
            assert boot['timed_selector_active'] is not historical and not boot['trace_active']
            if historical:
                assert boot['fixed_default_depth'] == 2 and not searches
            elif legal:
                event, = searches
                verify_event(event, launches[0], depth, response, case['rows'], color)
            else:
                assert not searches
        else:
            assert not events
        record.update(status='ok', events=events)
    except Exception as error:
        record.update(status='failed', error_type=getattr(error, 'kind', type(error).__name__),
                      error=str(error), diagnostics=getattr(error, 'diagnostics', None))
    finally:
        if player is not None:
            record['player'] = player.evidence()
        save(path, record)
    return record


def measure():
    """旧設定の最小再現後に、独立した7秒条件で固定反復を実施する。"""
    plan = read(OUT / 'plan.json')
    directory = OUT / 'probes'
    save(directory / 'old-normalized-config.json', UNKNOWN)
    with load_condition(False) as load:
        for depth in (4, 5):
            run = probe(plan['cases'][0], depth, 'diag', f'old-d{depth}', load, historical=True)
            assert run['status'] == 'ok', run.get('error')
    for condition in plan['conditions']:
        with load_condition(condition != 'normal') as load:
            save(OUT / f'load-{condition}.json', load)
            for index, case in enumerate(plan['cases']):
                for repeat in range(plan['repeats']):
                    reverse = (index + repeat) % 2
                    for depth in ((5, 4) if reverse else (4, 5)):
                        for mode in (('diag', 'plain') if (depth + reverse) % 2 else ('plain', 'diag')):
                            name = f'{condition}-{index:02}-{repeat}-d{depth}-{mode}'
                            run = probe(case, depth, mode, name, load)
                            if run['status'] != 'ok':
                                print(name, run.get('error_type'), run.get('error'), flush=True)
                print(condition, index, case['position_id'][:8], 'done', flush=True)


def game_trial(case, index, scripts, depths, event_path):
    """既存の対局ループを使い、実験内の起動引数だけを通常の7秒経路にする。"""
    assert not event_path.exists()
    launches = []
    original = evaluate.Player

    def launch(script, **kwargs):
        assert kwargs['evaluation_config'] is None
        kwargs.pop('evaluation_trace')
        kwargs.pop('evaluation_context')
        return original(script, **kwargs)

    hashes = {role: sha(script) for role, script in zip(('A', 'B'), scripts, strict=True)}
    seeds = {role: evaluate._seed_for(SEED, case['case_id'], role) for role in ('A', 'B')}
    with observe_launch(event_path, True, launches), patch.object(evaluate, 'Player', launch):
        result = evaluate.play_game(
            *scripts, index, SEED, case=case, pair_id=case['case_id'], leg=index % 2,
            seeds=seeds, configs={'A': UNKNOWN, 'B': UNKNOWN}, target_hashes=hashes,
            run_id=event_path.stem, trace_paths={
                role: event_path.with_suffix(f'.{role}.trace4.jsonl') for role in ('A', 'B')
            },
        )
    errors = []
    events = lines(event_path)
    raw = {'game': result, 'launches': launches, 'event_path': str(event_path),
           'event_errors': errors, 'depths': depths}
    if result['protocol_status'] == 'verified':
        try:
            for role, depth, spawned in zip(('A', 'B'), depths, launches, strict=True):
                own = [e for e in events if e.get('process_id') == spawned['pid']]
                boot = [e for e in events if e.get('pid') == spawned['pid']]
                assert all(spawned['env'][name] is None for name in EVAL_ENV)
                if depth is None:
                    assert not own and not boot
                    continue
                assert len(boot) == 1 and boot[0]['timed_selector_active']
                commands = [c for c in result['players'][role]['commands']
                            if c.get('phase') == 'normal_move' and 'error' not in c]
                moves = [m for m in result['moves'] if m['player'] == role and m['position'] is not None]
                assert len(commands) == len(own) == len(moves)
                rows = tuple(result['opening_start_board'])
                boards = []
                for move in result['moves']:
                    if move['player'] == role and move['position'] is not None:
                        boards.append(rows)
                    rows = tuple(move['board'])
                for event, command, move, board in zip(own, commands, moves, boards, strict=True):
                    verify_event(event, spawned, depth, command['response'][0], board, move['color'])
        except (AssertionError, KeyError, ValueError) as error:
            import traceback
            errors.append(f'{error!r}\n{traceback.format_exc()}')
    save(event_path.with_suffix('.json'), raw)
    return raw


def games(fixed=False):
    """先後交換の直接対局または同一相手の比較を一局ずつ保存する。"""
    directory = OUT / ('fixed-opponent-games' if fixed else 'games')
    plan_path = OUT / ('fixed-opponent-plan.json' if fixed else 'plan.json')
    plan = read(plan_path)
    baseline = ROOT / 'baselines/search_v1'
    if fixed:
        for name, digest in plan['opponent_assets'].items():
            assert sha(baseline / name) == digest
        schedule = [(plan['case'], leg, (depth, None)) for depth, leg in plan['games']]
    else:
        schedule = [(case, index, (4, 5)) for index, case in enumerate(
            case for case in plan['game_entries'] for _ in range(2))]
    directory.mkdir(exist_ok=False)
    save(directory / 'invocation.json', {
        'argv': [sys.executable, '-B', *sys.argv],
        'cwd': str(Path.cwd()),
        'driver_sha256': sha(Path(__file__)),
        'plan_sha256': sha(plan_path),
        'affinity_mask': affinity(-1),
    })
    for number, (case, index, depths) in enumerate(schedule):
        scripts = [baseline / 'reversi.py' if depth is None else
                   OUT / 'players' / f'd{depth}-diag' / 'reversi.py' for depth in depths]
        raw = game_trial(case, index, scripts, depths, directory / f'game-{number:02}.jsonl')
        game = raw['game']
        print(number, depths, game['result'], game['score_a'], game['protocol_status'],
              raw['event_errors'], flush=True)
        if (game['protocol_status'] != 'verified' or raw['event_errors'] or
            any(c['seconds'] > 8 for player in game['players'].values() for c in player['commands']
                if c['command'].startswith('genmove '))):
            return 1
    return 0


if __name__ == '__main__':
    action, = sys.argv[1:]
    if action == 'spin':
        value = 1
        while True:
            value = (value * 1664525 + 1013904223) & 0xFFFFFFFF
    if action in ('games', 'fixed-opponent-games'):
        sys.exit(games(fixed=action == 'fixed-opponent-games'))
    {'audit': audit, 'prepare': prepare, 'measure': measure}[action]()
