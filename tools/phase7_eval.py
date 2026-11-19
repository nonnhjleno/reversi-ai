"""Phase 7の凍結モデルで既存ハーネスの参考対局を実行する。"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from harness import evaluate, openings
from reversi_player import ntuple_io
from tools import evidence
from tools.phase65_data import parse_game, canonical, sha_file

if TYPE_CHECKING:
    from harness.contracts import _Game

ROOT = Path(__file__).resolve().parents[1]
EVAL_SEED = 20261119
BASELINES = {'positional_v1', 'search_v1'}
_DEVELOPMENT_SHA = '59dcc82a4365dfbad5403a834558fe66d8522c3e9d8d3a4cfaee7f388db34c13'
_RUNTIME = (
    'phase6_player.py',
    'ntuple.py',
    'ntuple_io.py',
    'ai_engine.py',
    'reversi.py',
    'timed_search.py',
    'search_trace.py',
    'response_config.json',
)


def prepare(output: Path) -> dict[str, object]:
    """固定developmentから重複しない4＋12ペアを登録する。"""
    source = ROOT / 'benchmark_sets/phase0/development.json'
    if sha_file(source) != _DEVELOPMENT_SHA:
        raise ValueError('td_development_file_mismatch')
    original = openings.load_population(source)
    if original['purpose'] != 'development' or original['placements'] != 8:
        raise ValueError('td_requires_development_openings')
    selected, selection = openings.select_entries(original, 16, EVAL_SEED)
    keys = {
        canonical(parse_game([[move['position'] for move in case['moves']], None])[0])
        for case in selected
    }
    if len(keys) != len(selected):
        raise ValueError('td_development_selection_move_orbit_overlap')
    output.mkdir()
    subsets = {}
    for name, entries in [('pilot', selected[:4]), ('comparison', selected[4:])]:
        derived: openings._Population = {
            **original,
            'entries': entries,
            'population_size': len(entries),
        }
        derived.pop('population_hash', None)
        derived['population_hash'] = openings.digest(derived)
        openings.validate_population(derived)
        path = output / f'{name}.json'
        evidence.save_new(path, evidence.encoded_json(derived))
        subsets[name] = {
            'path': str(path),
            'sha256': sha_file(path),
            'cases': [entry['case_id'] for entry in entries],
        }
    manifest = {
        'source_sha256': _DEVELOPMENT_SHA,
        'selection': selection,
        'subsets': subsets,
        'eval_seed': EVAL_SEED,
        'held_out_used': False,
        'phase65_test_used': False,
    }
    evidence.save_new(output / 'manifest.json', evidence.encoded_json(manifest))
    return manifest


def _adapter(output: Path, model: Path) -> tuple[list[Path], dict[str, object]]:
    """資産ごとに別プロセスで既存Phase 6入口を呼び出す。"""
    model = model.resolve(strict=True)
    saved = ntuple_io.load_model(model)
    code = (
        'import os\nimport sys\n'
        f'sys.path.insert(0, {str(ROOT)!r})\n'
        'from reversi_player.phase6_player import MODEL_ENV, main\n'
        f'os.environ[MODEL_ENV] = {str(model)!r}\n'
        'sys.exit(main())\n'
    )
    evidence.save_new(output, code)
    launch_assets = [model, *(ROOT / 'reversi_player' / f for f in _RUNTIME)]
    return launch_assets, {
        'path': str(model),
        'model_id': saved.model_id,
        'sha256': saved.file_sha256,
        'bytes': model.stat().st_size,
    }


def _fixed(label: str) -> tuple[Path, list[Path], Path | None]:
    """固定相手のmanifestを実bytesと照合し、searchは明示深度2を渡す。"""
    directory = ROOT / 'baselines' / label
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    if manifest['baseline_id'] != label:
        raise ValueError('invalid_td_fixed_baseline')
    assets = [manifest_path]
    checks = (
        manifest['assets']
        if label == 'search_v1'
        else {
            Path(record['path']).name: record['sha256']
            for record in manifest['frozen_copy'].values()
        }
    )
    for file, digest in checks.items():
        path = directory / file
        if sha_file(path) != digest:
            raise ValueError('td_fixed_baseline_hash_mismatch')
        assets.append(path)
    config = directory / 'eval-depth2.json' if label == 'search_v1' else None
    return directory / manifest['entrypoint'], assets, config


def _game_stats(games: list[_Game], search: bool) -> dict[str, object]:
    """先後交換ペアと対局健全性の評価を行う。"""
    by_case: dict[str, list[tuple[str, float]]] = defaultdict(list)
    intervals: dict[str, list[float]] = defaultdict(list)
    margins: list[int] = []
    durations: dict[str, list[float]] = defaultdict(list)
    outcomes: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    passes = 0
    lengths: list[int] = []
    for game in games:
        label_a = 'B' if game['colors']['B'] == 'A' else 'W'
        score = game['score_a']
        if game['protocol_status'] != 'verified' or game['game_status'] != 'complete':
            error = game.get('error')
            errors[str(error.get('kind', 'unclassified') if error else 'unclassified')] += 1
        if score is not None and game['protocol_status'] == 'verified':
            by_case[game['case_id']].append((label_a, score))
            board = game['final_board']
            diff = sum(r.count('B') - r.count('W') for r in board)
            margins.append(diff if label_a == 'B' else -diff)
            outcomes[f'{label_a}:{score:g}'] += 1
            lengths.append(sum(move['position'] is not None for move in game['moves']))
            passes += sum(move['position'] is None for move in game['moves'])
        for role, player in game['players'].items():
            if player.get('stderr_tail'):
                errors['unexpected_stderr'] += 1
            if player.get('exit_code') != 0:
                errors['process_exit'] += 1
            trace = player.get('budget_trace', {})
            if search and role == 'B' and trace.get('status') != 'verified':
                errors['search_depth_unverified'] += 1
            for cmd in player.get('commands', []):
                seconds = cmd.get('seconds')
                if isinstance(seconds, (int, float)):
                    if cmd.get('phase') == 'normal_move':
                        durations[role].append(seconds)
                    elif cmd.get('command') == 'name':
                        intervals[role].append(seconds)
    pair_scores = {}
    for case, legs in by_case.items():
        if sorted(turn for turn, _ in legs) != ['B', 'W']:
            errors['incomplete_color_pair'] += 1
        else:
            pair_scores[case] = statistics.mean(score for _, score in legs)
    wdl = [sum(outcomes[f'{turn}:{score}'] for turn in 'BW') for score in ('1', '0', '0.5')]
    return {
        'games': len(games),
        'valid_pairs': len(pair_scores),
        'errors': errors,
        'wdl_a': wdl,
        'by_color_a': outcomes,
        'pair_scores': pair_scores,
        'point_rate': statistics.mean(pair_scores.values()) if pair_scores else None,
        'mean_margin_a': statistics.mean(margins) if margins else None,
        'median_margin_a': statistics.median(margins) if margins else None,
        'mean_placements_after_opening': statistics.mean(lengths) if lengths else None,
        'passes_after_opening': passes,
        'startup_probe_max_seconds': {role: max(vals) for role, vals in intervals.items()},
        'normal_move_seconds': {
            role: {
                'n': len(vals),
                'mean': statistics.mean(vals),
                'median': statistics.median(vals),
                'max': max(vals),
            }
            for role, vals in durations.items()
        },
    }


def paired_interval(values: list[float], seed: int = EVAL_SEED) -> list[float]:
    """開発序盤ペアを再標本化した参考95%区間を返す。"""
    if not values:
        raise ValueError('no_td_pairs_for_interval')
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(2000))
    return [means[50], means[1949]]


def match(a: Path, opponent: str, population: Path, output: Path) -> dict[str, object]:
    """固定開発ペアの独立対局と通信を新しい領域に保存する。"""
    cases = openings.load_population(population)
    if cases['purpose'] != 'development' or cases['placements'] != 8:
        raise ValueError('td_match_requires_development')
    output.mkdir()
    script_a = output / 'a.py'
    assets_a, info_a = _adapter(script_a, a)
    config_b = None
    if opponent in BASELINES:
        script_b, assets_b, config_b = _fixed(opponent)
        info_b: dict[str, object] = {
            'baseline': opponent,
            'script_sha256': sha_file(script_b),
            'config_sha256': sha_file(config_b) if config_b else None,
        }
    else:
        script_b = output / 'b.py'
        assets_b, info_b = _adapter(script_b, Path(opponent))
    start = time.perf_counter()
    report = evaluate.evaluate(
        script_a,
        script_b,
        games=2 * len(cases['entries']),
        seed=EVAL_SEED,
        startup_timeout=5.0,
        command_timeout=1.0,
        genmove_timeout=10.0,
        assets_a=assets_a,
        assets_b=assets_b,
        openings_file=population,
        selection_seed=EVAL_SEED,
        config_b=config_b,
        budget_policy='descriptive',
        evaluation_profile='descriptive',
        evidence_dir=output / 'raw',
    )
    evidence.save_new(output / 'report.json', evidence.encoded_json(report))
    summary = _game_stats(report['games'], opponent == 'search_v1')
    changed = any(
        sha_file(Path(str(info['path']))) != info['sha256']
        for info in (info_a, info_b)
        if 'path' in info
    )
    summary.update(
        {
            'candidate': info_a,
            'opponent': info_b,
            'elapsed_seconds': time.perf_counter() - start,
            'population_sha256': sha_file(population),
            'report_sha256': sha_file(output / 'report.json'),
            'asset_changed': changed,
            'settings': {'eval_seed': EVAL_SEED, 'timeouts': [5, 1, 10], 'profile': 'descriptive'},
            'argv': [sys.executable, *sys.orig_argv[1:]],
            'platform': platform.platform(),
        }
    )
    if isinstance(scores := summary['pair_scores'], dict) and len(scores) == len(cases['entries']):
        summary['pair_bootstrap_95'] = paired_interval(list(scores.values()))
    evidence.save_new(output / 'summary.json', evidence.encoded_json(summary))
    if changed or summary['errors'] or summary['valid_pairs'] != len(cases['entries']):
        raise ValueError('td_evaluation_unhealthy_see_report')
    return summary


def main() -> int:
    """序盤固定と参考対局のCLI。"""
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('output', type=Path)
    play = sub.add_parser('match')
    play.add_argument('candidate', type=Path)
    play.add_argument('opponent')
    play.add_argument('population', type=Path)
    play.add_argument('output', type=Path)
    args = cli.parse_args()
    result = (
        prepare(args.output)
        if args.command == 'prepare'
        else (match(args.candidate, args.opponent, args.population, args.output))
    )
    print(evidence.encoded_json(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
