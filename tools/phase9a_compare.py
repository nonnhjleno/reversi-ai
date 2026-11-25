"""Phase 9-Aの固定局面比較と同一時間のTT有無対局。"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import NamedTuple

from harness import analysis, evaluate, openings, referee
from reversi_player import ai_engine, reversi, search_trace, timed_search
from tools import evidence, phase7_eval, phase8_compare, quality
from tools.phase65_data import sha_file
from tools.phase65_train import peak_memory_bytes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/phase2_positions.json'
CAPACITIES = (0, 1024, 4096, 16384)
REPEATS = 3


class Position(NamedTuple):
    """凍結済み局面の識別子、盤面、手番。"""

    label: str
    rows: ai_engine.BoardRows
    turn: str


def _save(path: Path, body: object) -> None:
    """既存証拠を上書きせずJSONを保存する。"""
    evidence.save_new(path, evidence.encoded_json(body))


def _positions() -> list[Position]:
    """Phase 4で使った代表棋譜を凍結fixtureから取得する。"""
    cases = json.loads(FIXTURE.read_bytes())['cases']
    return [Position(case['label'], tuple(case['rows']), case['color']) for case in cases]


def _transposition(positions: list[Position]) -> dict[str, object]:
    """独立refereeで同じ盤面・手番・残り深さへ合流する棋譜を探す。"""
    for position in positions:
        seen: dict[tuple[ai_engine.BoardRows, str, int], tuple[ai_engine.Move | None, ...]] = {}

        def walk(
            rows: ai_engine.BoardRows,
            turn: str,
            depth: int,
            path: tuple[ai_engine.Move | None, ...],
        ) -> dict[str, object] | None:
            """初めて見つかった転置について両経路を返す。"""
            key = (rows, turn, depth)
            old = seen.setdefault(key, path)
            if old != path:
                return {'case': position.label, 'state': key, 'histories': [old, path]}
            if depth == 0:
                return None
            opponent = 'W' if turn == 'B' else 'B'
            moves = referee.legal_moves(rows, turn)
            if not moves and referee.legal_moves(rows, opponent):
                return walk(rows, opponent, depth, (*path, None))
            for move in moves:
                found = walk(
                    referee.apply_move(rows, turn, move), opponent, depth - 1, (*path, move)
                )
                if found:
                    return found
            return None

        witness = walk(position.rows, position.turn, 4, ())
        if witness:
            return witness
    raise ValueError('no_transposition_in_registered_positions')


def _models(registry: Path) -> dict[int, Path]:
    """Phase 8が照合したZero-TD 10万局の各seedを特定し現物を検証する。"""
    records = json.loads(registry.read_bytes())['models']
    models = {}
    for seed in (701, 702, 703):
        (row,) = (
            item
            for item in records
            if (
                item['series'],
                item['seed'],
                item['games'],
                item.get('experiment'),
            )
            == ('zero', seed, 100000, 'phase7-longrun-100k-rerun-20261120')
        )
        path = Path(row['path'])
        model = search_trace.load_search_model(str(path))
        if (model.artifact.model_id, model.artifact.file_sha256) != (
            row['model_id'],
            row['sha256'],
        ):
            raise ValueError(f'model_fingerprint_mismatch_{seed}')
        models[seed] = path
    return models


def _model(path: Path | None) -> search_trace.SearchModel | None:
    """手作業評価を選ぶか、指定されたモデルの互換性とhashを検証する。"""
    return search_trace.load_search_model(str(path.resolve(strict=True))) if path else None


def _search(
    position: Position, depth: int, capacity: int, model: search_trace.SearchModel | None
) -> tuple[ai_engine.AlphaBetaResult, ai_engine.TTDiagnostics, int]:
    """固定深度探索を空の表から開始し、実時間を記録する。"""
    stats = ai_engine.TTDiagnostics()
    begin = time.perf_counter_ns()
    score = ai_engine.search_alpha_beta(
        position.rows,
        position.turn,
        depth,
        reversi.legal_moves_for,
        reversi.apply_move,
        leaf_evaluator=model.artifact.model.value if model else None,
        tt_capacity=capacity,
        tt_stats=stats,
    )
    return score, stats, time.perf_counter_ns() - begin


def _timed(
    position: Position, capacity: int, model: search_trace.SearchModel | None
) -> dict[str, object]:
    """実際の反復深化と時間予算を使い、第4反復後の余裕を計測する。"""
    stats = ai_engine.TTDiagnostics()
    begin = time.perf_counter_ns()
    answer = timed_search.search_iterative(
        position.rows,
        position.turn,
        reversi.legal_moves_for(position.rows, position.turn),
        reversi.legal_moves_for,
        reversi.apply_move,
        command_received_ns=begin,
        policy=timed_search.load_policy(),
        leaf_evaluator=model.artifact.model.value if model else None,
        tt_capacity=capacity,
        tt_stats=stats,
    )
    end = time.perf_counter_ns()
    return {
        'position': position.label,
        'capacity': capacity,
        **asdict(answer),
        'elapsed_ns': end - begin,
        'spare_ns': answer.stop_deadline_ns - end if answer.completed_depth == 4 else None,
        'tt': asdict(stats),
    }


def _overhead(position: Position, capacity: int) -> dict[str, float]:
    """同じ空き表の初期化、キーと添字、ヒット、同局面登録の費用を計測する。"""
    count = 30_000
    start = time.perf_counter_ns()
    for _ in range(1000):
        table = ai_engine._TranspositionTable(capacity, None)
    allocated = time.perf_counter_ns()
    for _ in range(count):
        key = (position.rows, position.turn)
        index = table.index(key)
    indexed = time.perf_counter_ns()
    for _ in range(count):
        table.store(index, key, 0, 0.0, None)
    stored = time.perf_counter_ns()
    for _ in range(count):
        table.probe(index, key, 0, -1.0, 1.0)
    end = time.perf_counter_ns()
    return {
        'init_ns': (allocated - start) / 1000,
        'key_index_ns': (indexed - allocated) / count,
        'store_ns': (stored - indexed) / count,
        'hit_ns': (end - stored) / count,
    }


class Sample(NamedTuple):
    """同じ局面・深度・反復番号で対応付ける性能計測値。"""

    position: str
    depth: int
    repeat: int
    elapsed_ns: int
    search: ai_engine.AlphaBetaResult
    tt: ai_engine.TTDiagnostics


def _summary(samples: list[Sample], baseline: list[Sample], rss: int | None) -> dict[str, object]:
    """代表40条件の全体値と、深度4の対応する時間比を集計する。"""
    actual: list[int | float] = [sample.elapsed_ns for sample in samples]
    by_case = {
        position: statistics.median(
            sample.elapsed_ns / before.elapsed_ns
            for sample, before in zip(samples, baseline, strict=True)
            if sample.depth == 4 and sample.position == position
        )
        for position in sorted({sample.position for sample in samples})
    }
    nodes = sum(sample.search.nodes for sample in samples)
    tt = {
        metric: sum(getattr(sample.tt, metric) for sample in samples)
        for metric in (
            'probes',
            'state_hits',
            'depth_hits',
            'depth_misses',
            'exact',
            'lower',
            'upper',
            'collisions',
            'stores',
            'replacements',
        )
    }
    return {
        'nodes': nodes,
        'leaves': sum(sample.search.leaves for sample in samples),
        'cutoffs': sum(sample.search.cutoffs for sample in samples),
        'search_ns': phase8_compare._distribution(actual),
        'depth4_ns': phase8_compare._distribution(
            [item.elapsed_ns for item in samples if item.depth == 4]
        ),
        'depth4_paired_median_ratio': statistics.median(by_case.values()),
        'depth4_ratios_by_case': by_case,
        'nps': nodes * 1e9 / sum(actual),
        'peak_rss_bytes': rss,
        'peak_entries': max(s.tt.peak_entries for s in samples),
        'tt': tt,
    }


def _benchmark(registry: Path, output: Path) -> None:
    """指定された4容量を固定局面で測定し、全候補の値と着手を照合する。"""
    model_paths = _models(registry)
    positions = _positions()
    output.mkdir()
    _save(
        output / 'run.json',
        {
            **phase8_compare._environment(),
            'sources': quality._source_hashes(ROOT),
            'registry_sha256': sha_file(registry),
            'fixture_sha256': sha_file(FIXTURE),
            'transposition': _transposition(positions),
            'positions': [position._asdict() for position in positions],
            'python_hash_seed': os.environ.get('PYTHONHASHSEED'),
            'repeats': REPEATS,
            'warmups': 1,
            'order': 'rotate and alternate reverse; all fresh tables',
            'capacities': CAPACITIES,
            'model_paths': {seed: str(path) for seed, path in model_paths.items()},
            'model_sha256': {seed: sha_file(path) for seed, path in model_paths.items()},
            'model_id': {
                seed: search_trace.load_search_model(str(path)).artifact.model_id
                for seed, path in model_paths.items()
            },
        },
    )
    result: dict[str, object] = {}
    for name, path in [('manual', None), ('zero-701', model_paths[701])]:
        model = _model(path)
        samples: dict[int, list[Sample]] = {capacity: [] for capacity in CAPACITIES}
        timed: list[dict[str, object]] = []
        for i, position in enumerate(positions):
            for depth in range(1, 5):
                for capacity in CAPACITIES:
                    _search(position, depth, capacity, model)
                for repeat in range(REPEATS):
                    shift = (i + depth + repeat) % len(CAPACITIES)
                    order = CAPACITIES[shift:] + CAPACITIES[:shift]
                    answers = {}
                    for capacity in order if repeat % 2 == 0 else order[::-1]:
                        search, stats, elapsed = _search(position, depth, capacity, model)
                        answers[capacity] = (search.move, search.value)
                        samples[capacity].append(
                            Sample(position.label, depth, repeat, elapsed, search, stats)
                        )
                    if len(set(answers.values())) != 1:
                        raise ValueError(f'fixed_depth_mismatch_{name}_{position.label}_{depth}')
            for capacity in (0, 4096) if i % 2 == 0 else (4096, 0):
                timed.append(_timed(position, capacity, model))
        memory = {}
        for capacity in CAPACITIES:
            command = [
                sys.executable,
                '-B',
                '-m',
                'tools.phase9a_compare',
                'memory',
                '--capacity',
                str(capacity),
            ]
            if path:
                command += ['--model', str(path)]
            memory[capacity] = json.loads(subprocess.check_output(command, cwd=ROOT))
        _save(
            output / (name + '.json'),
            {
                'fixed': {
                    capacity: [
                        {
                            **sample._asdict(),
                            'search': asdict(sample.search),
                            'tt': asdict(sample.tt),
                        }
                        for sample in rows
                    ]
                    for capacity, rows in samples.items()
                },
                'timed': timed,
                'memory': memory,
                'overhead': {c: _overhead(positions[0], c) for c in CAPACITIES if c},
            },
        )
        result[name] = {
            capacity: _summary(rows, samples[0], memory[capacity]['peak_rss_bytes'])
            for capacity, rows in samples.items()
        }
    extra = []
    for seed in (702, 703):
        model = _model(model_paths[seed])
        for position in positions:
            for depth in range(1, 5):
                baseline, _, _ = _search(position, depth, 0, model)
                candidate, _, _ = _search(position, depth, 4096, model)
                if (baseline.move, baseline.value) != (candidate.move, candidate.value):
                    raise ValueError(f'fixed_depth_mismatch_{seed}_{position.label}_{depth}')
                extra.append(
                    {
                        'seed': seed,
                        'case': position.label,
                        'depth': depth,
                        'move': candidate.move,
                        'value': candidate.value,
                    }
                )
    _models(registry)
    _save(output / 'extra-equivalence.json', extra)
    _save(output / 'summary.json', result)
    print(f'{output}  fixed-depth comparisons successful')


def _match(args: argparse.Namespace) -> None:
    """同一モデル・同一時間で先後を交換し、棋譜と予算証拠を保存する。"""
    if {search_trace.MODEL_ENV, ai_engine.TT_CAPACITY_ENV} & os.environ.keys():
        raise ValueError('remove_REVERSI_SEARCH_MODEL_and_REVERSI_TT_CAPACITY')
    population = args.population.resolve(strict=True)
    if sha_file(population) != phase8_compare.POPULATION_SHA:
        raise ValueError('phase8_population_sha256_mismatch')
    if openings.load_population(population)['purpose'] != 'development':
        raise ValueError('requires_development_population')
    model_path = args.model.resolve(strict=True) if args.model else None
    model = _model(model_path)
    target = ROOT / 'reversi_player/reversi.py'
    assets = [
        *(target.with_name(file) for file in phase8_compare.PACKAGE),
        target.with_name('ntuple.py'),
        target.with_name('ntuple_io.py'),
    ]
    search: dict[str, object] = {}
    if model and model_path:
        assets.append(model_path)
        search |= {
            'model_id': model.artifact.model_id,
            'model_sha256': model.artifact.file_sha256,
            'model_path': str(model_path),
        }
    output: Path = args.output
    output.mkdir()
    a, b = output / 'a-config.json', output / 'b-config.json'
    for path, capacity in [(a, args.capacity), (b, 0)]:
        _save(
            path,
            {
                'format_version': 2,
                'budget': {'kind': 'time', 'limit_ns': 6_500_000_000},
                'search': {**search, 'tt_capacity': capacity},
            },
        )
    digests = {str(path): sha_file(path) for path in [*assets, population, a, b]}
    _save(
        output / 'run.json',
        {
            **phase8_compare._environment(),
            'sources': quality._source_hashes(ROOT),
            'asset_sha256': digests,
            'pairs': args.pairs,
            'evaluation_seed': phase8_compare.EVAL_SEED,
            'manager_python_hash_seed': os.environ.get('PYTHONHASHSEED'),
            'capacity_a': args.capacity,
            'capacity_b': 0,
            'timeouts_seconds': [5, 1, 10],
            'model_id': model.artifact.model_id if model else None,
        },
    )
    start = time.perf_counter()
    report = evaluate.evaluate(
        target,
        target,
        games=2 * args.pairs,
        seed=phase8_compare.EVAL_SEED,
        assets_a=assets,
        assets_b=assets,
        openings_file=population,
        selection_seed=phase8_compare.EVAL_SEED,
        config_a=a,
        config_b=b,
        budget_policy='equal-time',
        evaluation_profile='deployment',
        require_budget_verification=True,
        evidence_dir=output / 'raw',
    )
    elapsed = time.perf_counter() - start
    _save(output / 'report.json', report)
    stats = phase7_eval._game_stats(report['games'], True)
    verified = analysis.assess_evaluation(report, 0.95, True)
    diagnostic: Counter[str] = Counter()
    depths: Counter[str] = Counter()
    for path in (output / 'raw').rglob('*.jsonl'):
        if path.name in ('A.jsonl', 'B.jsonl'):
            for line in path.read_bytes().splitlines():
                event = json.loads(line)
                if event.get('type') == 'genmove' and event['phase'] == 'normal_move':
                    depths[f'{path.stem}/{event["completed_depth"]}'] += 1
                    tt = event.get('tt_diagnostics', {})
                    if path.stem == 'A' and tt.get('capacity') != args.capacity:
                        raise ValueError('candidate_trace_missing_tt_capacity')
                    if path.stem == 'B' and tt:
                        raise ValueError('baseline_trace_has_tt_diagnostics')
                    diagnostic.update(
                        {
                            key: value
                            for key, value in tt.items()
                            if key not in {'capacity', 'peak_entries'}
                        }
                    )
    unchanged = all(sha_file(Path(file)) == digest for file, digest in digests.items())
    _save(
        output / 'summary.json',
        {
            **stats,
            'verification': verified,
            'assets_unchanged': unchanged,
            'evaluation_seconds': elapsed,
            'search': phase8_compare._search_metrics(report),
            'completed_depth_counts': dict(depths),
            'tt_diagnostics': dict(diagnostic),
        },
    )
    if (
        not unchanged
        or not verified['request_success']
        or stats['errors']
        or stats['valid_pairs'] != args.pairs
    ):
        raise ValueError('match_verification_failed_see_evidence')
    print(f'{output}  {stats["wdl_a"]}  {elapsed:.1f}s')


def main() -> None:
    """計測・参考対局と、比較用の独立プロセスメモリ計測を起動する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    bench = commands.add_parser('benchmark')
    bench.add_argument('--registry', required=True, type=Path)
    bench.add_argument('--output', required=True, type=Path)
    match = commands.add_parser('match')
    match.add_argument('--population', required=True, type=Path)
    match.add_argument('--output', required=True, type=Path)
    match.add_argument('--model', type=Path)
    match.add_argument('--pairs', type=int, choices=range(1, 13), default=1)
    match.add_argument('--capacity', type=int, choices=CAPACITIES[1:], default=4096)
    memory = commands.add_parser('memory')
    memory.add_argument('--capacity', type=int, required=True, choices=CAPACITIES)
    memory.add_argument('--model', type=Path)
    args = parser.parse_args()
    if args.command == 'benchmark':
        _benchmark(args.registry, args.output)
    elif args.command == 'match':
        _match(args)
    else:
        model = _model(args.model)
        base_rss = peak_memory_bytes()
        for position in _positions():
            _search(position, 4, args.capacity, model)
        print(
            json.dumps(
                {
                    'argv': sys.orig_argv,
                    'pre_search_peak_rss_bytes': base_rss,
                    'peak_rss_bytes': peak_memory_bytes(),
                }
            )
        )


if __name__ == '__main__':
    main()
