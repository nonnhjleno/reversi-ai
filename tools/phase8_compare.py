"""Phase 8の葉評価計測と参照Rangerとの先後交換対局。"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from harness import analysis, evaluate, openings, referee
from reversi_player import ai_engine, ntuple_io, reversi, search_trace, timed_search
from tools import evidence, phase7_eval
from tools.phase65_data import sha_file
from tools.phase65_train import peak_memory_bytes

if TYPE_CHECKING:
    from harness.contracts import _SourceReport

ROOT = Path(__file__).resolve().parents[1]
EVAL_SEED = phase7_eval.EVAL_SEED
POPULATION_SHA = 'ce747c9019a6661df549c3a9260af3a5c55229593515793be6ecd50aba08c7ff'
PACKAGE = {
    'ai_engine.py': '93850b2a98e4f4c222be4895606c7e50ba9da725bca79e6349547c87463b1168',
    'response_config.json': '092b2c88fdc3c341c3bbb72e88eb8df853135c882a0f8e0f7bd4535ee28526ef',
    'reversi.py': '908e79f62b7ae8496fe2ce4fb18af8b669e0f6201cd48be309d538a5dfef34e0',
    'search_trace.py': '4a038d2af58119358219e1de5d2834cd09faf54e6aca8b14aae4eabd01d6e6be',
    'timed_search.py': 'a866c8405feea25f7924dac76acf720a4b9d9454e45d40b0be20d6abf8fb1e69',
}


def _environment() -> dict[str, object]:
    """使用したインタープリタと取得コマンドを記録する。"""
    return {
        'argv': list(sys.orig_argv),
        'cwd': str(Path.cwd()),
        'python': sys.version,
        'platform': platform.platform(),
        'processor': platform.processor(),
        'git_head': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True
        ).strip(),
    }


def _distribution(values: list[int | float]) -> dict[str, object]:
    """nearest-rank p95 と測定件数を返す。"""
    if not values:
        return {'n': 0}
    ordered = sorted(values)
    return {
        'n': len(values),
        'mean': statistics.mean(values),
        'median': statistics.median(values),
        'p95': ordered[math.ceil(len(values) * 0.95) - 1],
        'max': ordered[-1],
    }


def _positions() -> dict[str, tuple[ai_engine.BoardRows, str]]:
    """独立refereeで短い固定棋譜を生成し、盤面段階ごとに取り出す。"""
    rng = random.Random(EVAL_SEED)
    positions: dict[str, tuple[ai_engine.BoardRows, str]] = {'initial': (referee.INITIAL_ROWS, 'B')}
    for _ in range(32):
        rows: ai_engine.BoardRows = referee.INITIAL_ROWS
        turn, placed = 'B', 0
        while True:
            moves = referee.legal_moves(rows, turn)
            other = 'W' if turn == 'B' else 'B'
            if not moves and not referee.legal_moves(rows, other):
                break
            if moves:
                label = {8: 'opening', 28: 'middle', 54: 'late'}.get(placed)
                if label:
                    positions.setdefault(label, (rows, turn))
                rows = referee.apply_move(rows, turn, rng.choice(moves))
                placed += 1
            else:
                positions.setdefault('pass', (rows, turn))
            turn = other
        if len(positions) == 5:
            return positions
    raise ValueError('phase8_benchmark_positions_missing')


def benchmark(model_path: Path, count: int, output: Path) -> None:
    """同じ局面で純粋推論、合法手込みの葉、深度4の代表費用を測る。"""
    start = time.perf_counter_ns()
    model = ntuple_io.load_model(model_path)
    load_ns = time.perf_counter_ns() - start
    positions = _positions()
    rows_result = {}
    for stage, (rows, turn) in positions.items():
        other = 'W' if turn == 'B' else 'B'

        def score_model() -> float:
            """実探索と同様に葉の合法手を取得してから評価する。"""
            moves = reversi.legal_moves_for(rows, turn)
            return ai_engine._evaluate_leaf(
                rows, turn, turn, moves, other, reversi.legal_moves_for, model.model.value
            )

        def score_manual() -> float:
            """手作業評価側でも探索の葉で実行する合法手取得を含める。"""
            reversi.legal_moves_for(rows, turn)
            return ai_engine.evaluate_position(rows, turn, reversi.legal_moves_for)

        def predict() -> float:
            """盤面をそのまま凍結モデルに渡す。"""
            return model.model.value(rows, turn)

        timings = {}
        callbacks: dict[str, Callable[[], float]] = {
            'model_ns': predict,
            'model_leaf_ns': score_model,
            'manual_leaf_ns': score_manual,
        }
        for callback in callbacks.values():
            for _ in range(10):
                callback()
        raw: dict[str, list[int]] = {name: [] for name in callbacks}
        for _ in range(count):
            for name, callback in callbacks.items():
                begin = time.perf_counter_ns()
                callback()
                raw[name].append(time.perf_counter_ns() - begin)
        for name, samples in raw.items():
            timings[name] = {
                **_distribution(list(samples)),
                'per_second': 1e9 / statistics.mean(samples),
            }
        searches = {}
        moves = reversi.legal_moves_for(rows, turn)
        for name, leaf in [('manual', None), ('ntuple', model.model.value)]:
            begin = time.perf_counter_ns()
            result = timed_search.search_iterative(
                rows,
                turn,
                moves,
                reversi.legal_moves_for,
                reversi.apply_move,
                command_received_ns=begin,
                policy=timed_search.load_policy(),
                limit_ns=6_500_000_000,
                leaf_evaluator=leaf,
            )
            searches[name] = {'elapsed_ns': time.perf_counter_ns() - begin, **vars(result)}
        rows_result[stage] = {
            'rows': rows,
            'turn': turn,
            'raw_ns': raw,
            'times': timings,
            'search': searches,
        }
    evidence.save_new(
        output,
        evidence.encoded_json(
            {
                **_environment(),
                'samples_per_position': count,
                'model_id': model.model_id,
                'model_sha256': model.file_sha256,
                'model_bytes': model_path.stat().st_size,
                'model_load_ns': load_ns,
                'peak_working_set_bytes': peak_memory_bytes(),
                'positions': rows_result,
            }
        ),
    )


def _opponent(name: str, baseline: Path) -> tuple[Path, list[Path], Path | None]:
    """提出版または固定相手の実バイトを照合する。"""
    if name == 'ranger':
        for file, digest in PACKAGE.items():
            if sha_file(baseline / file) != digest:
                raise ValueError('frozen_ranger_hash_mismatch:' + file)
        return baseline / 'reversi.py', [baseline / f for f in PACKAGE], None
    if name == 'random_v0':
        manifest = ROOT / 'baselines/random_v0/manifest.json'
        script = manifest.with_name('reversi.py')
        if sha_file(script) != 'a485400c335a8731a0cafd1d77d98fbbcda1289194c4e13147cbaf3135b12b41':
            raise ValueError('frozen_random_hash_mismatch')
        return script, [script, manifest], None
    return phase7_eval._fixed(name)


def _search_metrics(report: _SourceReport) -> dict[str, object]:
    """保存されたtrace4と通信から、予算と応答の分布を算出する。"""
    samples: dict[str, dict[str, list[int | float]]] = {
        role: {
            key: []
            for key in (
                'nodes',
                'leaves',
                'nps',
                'depth',
                'search_ns',
                'genmove_s',
                'startup_s',
                'command_s',
                'model_load_ns',
            )
        }
        for role in ('A', 'B')
    }
    for game in report['games']:
        for label, player in game['players'].items():
            target = samples[label]
            for command in player.get('commands', []):
                duration = command.get('seconds')
                if isinstance(duration, (int, float)):
                    key = 'startup_s' if command.get('command') == 'name' else 'command_s'
                    if command.get('phase') == 'normal_move':
                        key = 'genmove_s'
                    target[key].append(duration)
    for path in Path(report['evidence']['root']).rglob('*.jsonl'):
        if path.name not in ('A.jsonl', 'B.jsonl'):
            continue
        with path.open(encoding='utf-8') as stream:
            events = (json.loads(line) for line in stream)
            header = next(events, None)
            if not isinstance(header, dict) or header.get('type') != 'config':
                continue
            target = samples[header['player']]
            if header.get('model_load_status') == 'startup':
                target['model_load_ns'].append(header['model_load_elapsed_ns'])
            for event in events:
                if event.get('type') == 'genmove' and event['phase'] == 'normal_move':
                    target['nodes'].append(event['nodes'])
                    target['leaves'].append(event['leaves'])
                    target['depth'].append(event['completed_depth'])
                    target['search_ns'].append(event['search_elapsed_ns'])
                    if event['search_elapsed_ns']:
                        target['nps'].append(event['nodes'] * 1e9 / event['search_elapsed_ns'])
    return {
        role: {key: _distribution(values) for key, values in metrics.items()}
        for role, metrics in samples.items()
    }


def match(args: argparse.Namespace) -> None:
    """固定開発母集団を用い、設定・取得・解析を新規領域に保存する。"""
    if search_trace.MODEL_ENV in os.environ:
        raise ValueError('remove_REVERSI_SEARCH_MODEL_before_harness')
    population = openings.load_population(args.population)
    if population['purpose'] != 'development' or sha_file(args.population) != POPULATION_SHA:
        raise ValueError('phase8_requires_registered_development_population')
    if not 1 <= args.pairs <= len(population['entries']):
        raise ValueError('phase8_invalid_pair_count')
    if args.time and args.opponent != 'ranger':
        raise ValueError('phase8_equal_time_requires_ranger')
    if args.opponent == 'search_v1' and args.depth != 2:
        raise ValueError('phase8_search_v1_requires_depth2')
    b, assets_b, config_b = _opponent(args.opponent, args.baseline)
    model_path = args.model.resolve(strict=True)
    saved = ntuple_io.load_model(model_path)
    a = ROOT / 'reversi_player/reversi.py'
    assets_a = [
        model_path,
        *(a.with_name(file) for file in PACKAGE),
        a.with_name('ntuple.py'),
        a.with_name('ntuple_io.py'),
    ]
    budget = (
        {'kind': 'time', 'limit_ns': 6_500_000_000}
        if args.time
        else {'kind': 'depth', 'limit_depth': args.depth}
    )
    base_config: dict[str, object] = {'format_version': 2, 'budget': budget, 'search': {}}
    candidate_config = {
        **base_config,
        'search': {
            'model_id': saved.model_id,
            'model_sha256': saved.file_sha256,
            'model_path': str(model_path),
        },
    }
    args.output.mkdir()
    config_a = args.output / 'a-config.json'
    evidence.save_new(config_a, evidence.encoded_json(candidate_config))
    if args.opponent == 'ranger':
        config_b = args.output / 'b-config.json'
        evidence.save_new(config_b, evidence.encoded_json(base_config))
    policy = 'equal-time' if args.time else ('equal-work' if config_b else 'descriptive')
    profile = 'deployment' if args.time else 'descriptive'
    checks = {str(path.resolve()): sha_file(path) for path in [*assets_a, *assets_b]}
    evidence.save_new(
        args.output / 'run.json',
        evidence.encoded_json(
            {
                **_environment(),
                'source_and_asset_sha256': checks,
                'population_path': str(args.population.resolve()),
                'population_sha256': POPULATION_SHA,
                'evaluation_seed': EVAL_SEED,
                'pairs': args.pairs,
                'profile': profile,
                'budget_policy': policy,
                'opponent': args.opponent,
                'target_a': str(a),
                'target_b': str(b.resolve()),
                'timeouts_seconds': [5, 1, 10],
                'model_id': saved.model_id,
                'model_sha256': saved.file_sha256,
            }
        ),
    )
    started = time.perf_counter()
    report = evaluate.evaluate(
        a,
        b,
        games=2 * args.pairs,
        seed=EVAL_SEED,
        assets_a=assets_a,
        assets_b=assets_b,
        openings_file=args.population,
        selection_seed=EVAL_SEED,
        config_a=config_a,
        config_b=config_b,
        budget_policy=policy,
        evaluation_profile=profile,
        require_budget_verification=args.time,
        evidence_dir=args.output / 'raw',
    )
    elapsed = time.perf_counter() - started
    evidence.save_new(args.output / 'report.json', evidence.encoded_json(report))
    errors: Counter[str] = Counter()
    for game in report['games']:
        for role, player in game['players'].items():
            if (role == 'A' or config_b) and player.get('budget_trace', {}).get(
                'status'
            ) != 'verified':
                errors[role + '_trace_unverified'] += 1
    if any(sha_file(Path(file)) != digest for file, digest in checks.items()):
        errors['input_asset_changed'] += 1
    summary = phase7_eval._game_stats(report['games'], config_b is not None)
    verification = analysis.assess_evaluation(report, 0.95, args.time)
    evidence.save_new(
        args.output / 'summary.json',
        evidence.encoded_json(
            {
                **summary,
                'extra_errors': dict(errors),
                'evaluation_seconds': elapsed,
                'search': _search_metrics(report),
                'verification': verification,
            }
        ),
    )
    if (
        errors
        or summary['errors']
        or summary['valid_pairs'] != args.pairs
        or not verification['request_success']
    ):
        raise ValueError('phase8_evaluation_unverified_see_evidence')
    print(f'{args.output.name}: {summary["wdl_a"]} {summary["point_rate"]:.3f} {elapsed:.1f}s')


def main() -> None:
    """実験用CLI。入出力のパスは引数で明示する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    bench = commands.add_parser('benchmark')
    bench.add_argument('--model', type=Path, required=True)
    bench.add_argument('--count', type=int, default=100)
    bench.add_argument('--output', type=Path, required=True)
    play = commands.add_parser('match')
    play.add_argument('--model', type=Path, required=True)
    play.add_argument('--baseline', type=Path, required=True)
    play.add_argument('--population', type=Path, required=True)
    play.add_argument('--output', type=Path, required=True)
    play.add_argument('--pairs', type=int, default=12)
    play.add_argument(
        '--opponent',
        choices=('ranger', 'random_v0', 'positional_v1', 'search_v1'),
        default='ranger',
    )
    budget = play.add_mutually_exclusive_group(required=True)
    budget.add_argument('--depth', type=int, choices=(1, 2, 3, 4))
    budget.add_argument('--time', action='store_true')
    args = parser.parse_args()
    if args.command == 'benchmark':
        if args.count < 1:
            parser.error('--count must be positive')
        benchmark(args.model, args.count, args.output)
    else:
        match(args)


if __name__ == '__main__':
    main()
