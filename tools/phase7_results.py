"""Phase 7の共通相手比較、seed分散、生成棋譜と開発評価の重複を集計する。"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import cast

from harness import referee
from tools import evidence
from tools.phase65_data import canonical, parse_game, sha_file
from tools.phase7_eval import paired_interval

_SEEDS = (701, 702, 703)
_MATCHES = (
    'zero-vs-positional_v1',
    'pretrained-vs-positional_v1',
    'pretrained-vs-zero',
    'zero-vs-search_v1',
    'pretrained-vs-search_v1',
)


def _read(path: Path) -> dict[str, object]:
    """証拠JSONのオブジェクトを読む。"""
    data: object = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError('invalid_td_evidence')
    return cast(dict[str, object], data)


def _obj(value: object) -> dict[str, object]:
    """証拠内の入れ子オブジェクトを取り出す。"""
    if not isinstance(value, dict):
        raise ValueError('invalid_td_nested_evidence')
    return cast(dict[str, object], value)


def _prefix(moves: bytes) -> bytes:
    """パスを保った最初の8 placementsのD4代表を返す。"""
    count = 0
    for index, move in enumerate(moves, 1):
        if move != 64:
            count += 1
            if count == 8:
                return canonical(moves[:index])
    return canonical(moves)


def _scores(summary: dict[str, object]) -> dict[str, float]:
    """健全な対局の十分な先後交換ペアだけを読めるようにする。"""
    raw = _obj(summary['pair_scores'])
    if summary['errors'] or summary['valid_pairs'] != 12 or len(raw) != 12:
        raise ValueError('incomplete_td_comparison')
    if any(type(v) not in (int, float) or v not in (0, 0.25, 0.5, 0.75, 1) for v in raw.values()):
        raise ValueError('invalid_td_pair_score')
    return {case: float(cast(float, score)) for case, score in raw.items()}


def _contrast(vectors: dict[int, dict[str, float]]) -> dict[str, object]:
    """同じ序盤とseedの比較。序盤の再標本化とseed間分散を別々に計算する。"""
    if not vectors:
        raise ValueError('missing_td_seed_scores')
    cases = sorted(next(iter(vectors.values())))
    if any(set(scores) != set(cases) for scores in vectors.values()):
        raise ValueError('td_comparison_case_mismatch')
    sample = {seed: statistics.mean(scores.values()) for seed, scores in vectors.items()}
    paired = {case: statistics.mean(scores[case] for scores in vectors.values()) for case in cases}
    return {
        'by_seed': sample,
        'mean': statistics.mean(sample.values()),
        'seed_sample_stdev': statistics.stdev(sample.values()) if len(sample) > 1 else None,
        'by_case': paired,
        'opening_pair_bootstrap_95_fixed_seeds': paired_interval(list(paired.values())),
    }


def _behavior(game: dict[str, object], counters: Counter[str]) -> None:
    """実戦の両色について、角を取れた機会と行・列順選択を数える。"""
    rows = tuple(cast(list[str], game['opening_start_board']))
    for move in cast(list[dict[str, object]], game['moves']):
        turn = cast(str, move['color'])
        label = f'{move["player"]}:{turn}'
        action = move['position']
        if action is not None:
            chosen = tuple(cast(list[int], action))
            available = referee.legal_moves(rows, turn)
            if len(available) > 1:
                counters[f'{label}:choices'] += 1
                counters[f'{label}:row_first'] += chosen == available[0]
            corners = {(0, 0), (0, 7), (7, 0), (7, 7)}
            if corners.intersection(available):
                counters[f'{label}:corner_opportunities'] += 1
                counters[f'{label}:corners_taken'] += chosen in corners
            counters[f'{label}:edge_moves'] += chosen[0] in (0, 7) or chosen[1] in (0, 7)
        rows = tuple(cast(list[str], move['board']))


def _read_match(path: Path, behavior: Counter[str], eval_games: set[bytes]) -> dict[str, object]:
    """結果に結びついた原証拠を照合し、評価の着手列を取り出す。"""
    summary = _read(path / 'summary.json')
    report = path / 'report.json'
    if sha_file(report) != summary['report_sha256'] or summary['asset_changed']:
        raise ValueError('td_match_hash_mismatch')
    players = [_obj(summary[x]) for x in ('candidate', 'opponent')]
    for player in players:
        if 'path' in player and sha_file(Path(str(player['path']))) != player['sha256']:
            raise ValueError('td_final_evaluation_model_changed')
    full = _read(report)
    for game in cast(list[dict[str, object]], full['games']):
        moves = [
            move['position'] or [-1, -1]
            for field in ('opening_moves', 'moves')
            for move in cast(list[dict[str, object]], game[field])
        ]
        sequence, _ = parse_game([moves, None])
        eval_games.add(canonical(sequence))
        _behavior(game, behavior)
    return summary


def summarize(root: Path, stops: list[int], compared: list[int], output: Path) -> dict[str, object]:
    """時点ごとの成績と学習曲線、凍結評価とのD4一致を新規ファイルに保存する。"""
    if not compared or compared[0] != 0 or set(compared[1:]) - set(stops):
        raise ValueError('invalid_td_comparison_stages')
    reference_hash = _obj(
        _obj(_read(root / 'opening-registration/manifest.json')['subsets'])['comparison']
    )['sha256']
    by_stop = {}
    eval_games: set[bytes] = set()
    initial_scores: dict[str, dict[str, float]] = {}
    for stop in compared:
        directory = root / f'compare-{stop}'
        if _read(directory / 'execution.json')['passed'] is not True:
            raise ValueError('td_comparison_execution_failed')
        summaries: dict[str, object] = {}
        comparisons: dict[str, object] = {}
        seeds = _SEEDS if stop else (0,)
        for match in _MATCHES:
            absolute: dict[int, dict[str, float]] = {}
            for seed in seeds:
                behavior: Counter[str] = Counter()
                suite = directory / f'{seed}-{match}'
                summary = _read_match(suite, behavior, eval_games)
                if summary['population_sha256'] != reference_hash:
                    raise ValueError('td_comparison_population_mismatch')
                absolute[seed] = _scores(summary)
                summaries[f'{seed}-{match}'] = {**summary, 'behavior': behavior}
            if stop == 0:
                initial_scores[match] = absolute[0]
            changes = {
                seed: {case: score - initial_scores[match][case] for case, score in row.items()}
                for seed, row in absolute.items()
            }
            comparisons[match] = {
                'absolute': _contrast(absolute),
                'change_from_initial': _contrast(changes),
            }
        for opponent in ('positional_v1', 'search_v1'):
            zero = _obj(comparisons[f'zero-vs-{opponent}'])
            pre = _obj(comparisons[f'pretrained-vs-{opponent}'])
            pre_by_seed = {
                seed: _scores(_obj(summaries[f'{seed}-pretrained-vs-{opponent}'])) for seed in seeds
            }
            zero_by_seed = {
                seed: _scores(_obj(summaries[f'{seed}-zero-vs-{opponent}'])) for seed in seeds
            }
            initial = {
                case: score - initial_scores[f'zero-vs-{opponent}'][case]
                for case, score in initial_scores[f'pretrained-vs-{opponent}'].items()
            }
            gaps = {
                seed: {case: score - zero_by_seed[seed][case] for case, score in row.items()}
                for seed, row in pre_by_seed.items()
            }
            increments = {
                seed: {case: value - initial[case] for case, value in gap.items()}
                for seed, gap in gaps.items()
            }
            comparisons[f'initialization-gap-vs-{opponent}'] = {
                'absolute': _contrast(gaps),
                'change_from_initial_gap': _contrast(increments),
                'zero_change_from_initial': zero['change_from_initial'],
                'pretrained_change_from_initial': pre['change_from_initial'],
            }
        by_stop[str(stop)] = {'matches': summaries, 'comparisons': comparisons}
    train = {}
    # 完全棋譜D4代表の先頭8手もD4で再計算するため、初期の向きに依存しない。
    eval_prefixes = {_prefix(game) for game in eval_games}
    for stop in stops:
        directory = root / f'train-{stop}'
        execution = _read(directory / 'execution.json')
        if execution['passed'] is not True:
            raise ValueError('td_training_execution_failed')
        records = {}
        for label in ('zero', 'pretrained'):
            for seed in _SEEDS:
                name = f'{label}-{seed}'
                segment = directory / name
                checkpoint = _obj(_read(segment / 'state.json')['payload'])
                games_path = segment / 'games.jsonl'
                if (
                    _obj(checkpoint['segment'])['sha256'] != sha_file(games_path)
                    or _obj(checkpoint['model'])['sha256'] != sha_file(segment / 'model.json')
                    or _obj(checkpoint['counts'])['games'] != stop
                ):
                    raise ValueError('td_training_output_mismatch')
                prefixes: Counter[str] = Counter()
                full = 0
                total = 0
                with games_path.open('rb') as stream:
                    for line in stream:
                        row = json.loads(line)
                        moves, _ = parse_game([row['moves'], row['margin']])
                        total += 1
                        if canonical(moves) in eval_games:
                            full += 1
                        p = _prefix(moves)
                        if p in eval_prefixes:
                            prefixes[p.hex()] += 1
                metrics = _obj(checkpoint['metrics'])
                if total != metrics['games']:
                    raise ValueError('td_training_game_count_mismatch')
                records[name] = {
                    'counts': checkpoint['counts'],
                    'settings': _obj(checkpoint['compatibility'])['settings'],
                    'checkpoint': str(segment),
                    'state_sha256': sha_file(segment / 'state.json'),
                    'model': checkpoint['model'],
                    'metrics': metrics,
                    'training_prefixes_seen_in_evaluation': prefixes,
                    'training_games_equal_to_evaluation': full,
                }
        train[str(stop)] = {'elapsed_seconds': execution['seconds'], 'runs': records}
    result = {
        'evaluation': by_stop,
        'training': train,
        'unique_evaluation_complete_games_d4': len(eval_games),
        'unique_evaluation_prefixes_d4': len(eval_prefixes),
        'phase0_held_out_opened': False,
        'phase65_test_reused': False,
        'scope': 'descriptive_development_only',
    }
    evidence.save_new(output, evidence.encoded_json(result))
    return result


def main() -> int:
    """保存済み試行を集計するCLI。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence_root', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--training', type=int, nargs='+', required=True)
    parser.add_argument('--compared', type=int, nargs='+', required=True)
    args = parser.parse_args()
    result = summarize(args.evidence_root, args.training, args.compared, args.output)
    print(
        evidence.encoded_json(
            {
                'unique_evaluation_complete_games_d4': result[
                    'unique_evaluation_complete_games_d4'
                ],
                'unique_evaluation_prefixes_d4': result['unique_evaluation_prefixes_d4'],
            }
        )
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
