"""Phase 9-Bの深度別計測と先後交換の開発対局を再現する。"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from harness import analysis, evaluate, openings, protocol, referee, search_budget
from reversi_player import ai_engine, search_trace, timed_search
from tools import phase7_eval, phase8_compare, phase9a_compare, quality
from tools.phase65_data import sha_file

if TYPE_CHECKING:
    from harness.contracts import _SourceReport

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'reversi_player/reversi.py'
DEPTHS = (4, 5, 6)
LIMIT_NS = 6_500_000_000
SEED = phase8_compare.EVAL_SEED
PILOT_CASE = 'db5655112ae43f96c76faf43e7fb99fce70a366363455d637cf1986a21f3afe1'
REMAINDER_SHA = 'af652fb9087b93bd9c95c99a61dbe40ffa71ee8c8b7cff400e3708c9313ec18a'
_save = phase9a_compare._save


def _config(depth: int, model: Path | None, *, archived: bool = False) -> dict[str, object]:
    """実験は予算6.5秒を明示し、同一モデルなら設定差を深度だけにする。"""
    search: dict[str, object] = {} if archived else {'max_depth': depth, 'tt_capacity': 0}
    if model is not None:
        artifact = search_trace.load_search_model(str(model)).artifact
        search.update(
            model_path=str(model), model_id=artifact.model_id, model_sha256=artifact.file_sha256
        )
    return {'format_version': 2, 'budget': {'kind': 'time', 'limit_ns': LIMIT_NS}, 'search': search}


def _environment() -> dict[str, object]:
    """投入環境の意図しない上書きを拒否し、実ソースを記録する。"""
    for key in (search_trace.MODEL_ENV, timed_search.SEARCH_DEPTH_ENV, ai_engine.TT_CAPACITY_ENV):
        if key in os.environ:
            raise ValueError('remove_environment_override:' + key)
    return {**phase8_compare._environment(), 'sources': quality._source_hashes(ROOT)}


def _peak_bytes(pid: int) -> int | None:
    """生存中の子のピークworking setをWindowsから取得する。"""
    if sys.platform != 'win32':
        return None
    shell = shutil.which('powershell.exe')
    if shell is None:
        raise ValueError('windows_powershell_missing')
    return int(
        subprocess.check_output(
            [
                shell,
                '-NoProfile',
                '-NonInteractive',
                '-Command',
                f'(Get-Process -Id {pid} -ErrorAction Stop).PeakWorkingSet64',
            ],
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).strip()
    )


def _check_iterations(event: dict[str, object], limit: int) -> None:
    """深度と確定値の補助記録を既存要約と突き合わせる。"""
    completed = event['completed_depth']
    started = event['max_depth_started']
    if (
        type(completed) is not int
        or type(started) is not int
        or not 0 <= completed <= started <= limit
        or started - completed > 1
    ):
        raise ValueError('depth_summary_inconsistent')
    if event['phase'] != 'normal_move':
        if started != 0:
            raise ValueError('non_move_search_started')
        return
    entries = event['iterations']
    if not isinstance(entries, list) or len(entries) != started:
        raise ValueError('iteration_count_inconsistent')
    for index, item in enumerate(entries, 1):
        if item['depth'] != index or item['completed'] is not (index <= completed):
            raise ValueError('iteration_progress_inconsistent')
        if index > completed and (item['move'] is not None or item['value'] is not None):
            raise ValueError('incomplete_iteration_published')
    for field in ('nodes', 'leaves'):
        if sum(item[field] for item in entries) != event[field]:
            raise ValueError('iteration_work_inconsistent')
    if completed:
        last = entries[completed - 1]
        if event['value'] != last['value'] or event['response'] != ' '.join(map(str, last['move'])):
            raise ValueError('adopted_result_inconsistent')


def _sample(
    case: dict[str, object],
    case_dir: Path,
    config: dict[str, object],
    cap: int,
) -> dict[str, object]:
    """実プロセスで履歴を再生し、通信と探索の双方の原証拠を保存する。"""
    case_dir.mkdir()
    cfg, raw = case_dir / 'config.json', case_dir / 'trace.jsonl'
    _save(cfg, config)
    digest = sha_file(SCRIPT)
    context = {
        'run_id': case_dir.name,
        'purpose': 'game',
        'pair_id': case_dir.name,
        'leg': 0,
        'player': 'A',
        'target_sha256': digest,
        'requested_config_sha256': sha256(
            search_budget.canonical_json(config).encode()
        ).hexdigest(),
    }
    history, rows, color = case['history'], case['rows'], case['color']
    if not isinstance(rows, list) or not isinstance(history, list) or color not in ('B', 'W'):
        raise ValueError('invalid_position')
    with protocol.Player(
        SCRIPT, seed=SEED, evaluation_config=cfg, evaluation_trace=raw, evaluation_context=context
    ) as child:
        for step in history:
            if step['position'] is not None:
                reply = child.command('play {} {} {}'.format(step['color'], *step['position']))
                if reply != ['OK']:
                    raise ValueError('history_illegal_in_child')
        board = child.command('showboard')
        if board != rows:
            raise ValueError('position_replay_mismatch')
        response = child.command(f'genmove {color}')[0]
        rtt = child.commands[-1]['seconds']
        choices = referee.legal_moves(rows, str(color))
        other = 'W' if color == 'B' else 'B'
        child.commands[-1]['phase'] = (
            'normal_move'
            if choices
            else ('pass_probe' if referee.legal_moves(rows, other) else 'terminal_probe')
        )
        if not choices and response != 'NG':
            raise ValueError('pass_or_terminal_reply')
        if choices:
            a, b = map(int, response.split())
            if (a, b) not in choices:
                raise ValueError('genmove_illegal')
            if child.command('showboard') != list(referee.apply_move(rows, str(color), (a, b))):
                raise ValueError('updated_board_mismatch')
        rss = _peak_bytes(child.process.pid)
    _save(case_dir / 'wire.json', child.evidence())
    result = search_budget.validate_trace(
        raw, config, child.commands, digest, child.evaluation_context
    )
    if result['status'] != 'verified':
        raise ValueError('benchmark_trace_unverified:' + str(result['reason']))
    events = [json.loads(line) for line in raw.read_bytes().splitlines()]
    (event,) = [event for event in events if event['type'] == 'genmove']
    _check_iterations(event, cap)
    if event['board_sha256'] != search_budget.board_sha256(rows):
        raise ValueError('wrong_trace_board')
    return {
        **event,
        'max_rss_bytes': rss,
        'seconds': rtt,
        'startup_seconds': child.startup_seconds,
        'trace_status': result['status'],
        'trace_reason': result['reason'],
        'depth_cap': cap,
        'label': case['label'],
    }


def _bench(args: argparse.Namespace) -> None:
    """各評価・局面でD4/D5/D6を交替順に計測し、未加工の結果を保存する。"""
    env = _environment()
    models: dict[str, Path | None] = {'manual': None}
    models.update({f'zero-{s}': p for s, p in phase9a_compare._models(args.registry).items()})
    positions = json.loads(args.positions.read_bytes())
    output: Path = args.output
    output.mkdir()
    _save(
        output / 'run.json',
        {
            **env,
            'positions_sha256': sha_file(args.positions),
            'repeats': 2,
            'warmups': 1,
            'model_sha256': {k: sha_file(p) for k, p in models.items() if p is not None},
            'ordering': 'rotate by position and repetition; reverse odd repetitions',
            'candidate_depths': DEPTHS,
            'time_limit_ns': LIMIT_NS,
        },
    )
    for label, model in models.items():
        dest = output / label
        dest.mkdir()
        configs = {d: _config(d, model) for d in DEPTHS}
        samples = []
        for index, case in enumerate(positions):
            for repeat in range(3):
                shift = (index + repeat) % 3
                order = DEPTHS[shift:] + DEPTHS[:shift]
                for depth in order if repeat % 2 == 0 else order[::-1]:
                    path = dest / f'{index:02d}-{repeat}-{depth}'
                    sample = {**_sample(case, path, configs[depth], depth), 'repeat': repeat}
                    _save(path / 'result.json', sample)
                    if repeat:
                        samples.append(sample)
            print(f'{label} {index + 1}/{len(positions)}', flush=True)
        _save(dest / 'summary-input.json', samples)


def _match_summary(report: _SourceReport, raw: Path, archived: bool, cap: int) -> dict[str, object]:
    """開発対局の集計と、実際の開始・完了・応答・中断を保存する。"""
    details: dict[str, Counter[str]] = {'A': Counter(), 'B': Counter()}
    for path in raw.rglob('*.jsonl'):
        if path.name not in ('A.jsonl', 'B.jsonl'):
            continue
        for line in path.read_bytes().splitlines():
            event = json.loads(line)
            if event.get('type') != 'genmove':
                continue
            role = path.stem
            if role == 'A' or not archived:
                _check_iterations(event, cap if role == 'A' else 4)
            item = details[role]
            item['genmove'] += 1
            if event['phase'] == 'normal_move':
                item['normal_move'] += 1
                item[f'started/{event["max_depth_started"]}'] += 1
                item[f'completed/{event["completed_depth"]}'] += 1
                item[f'stop/{event["stop_reason"]}'] += 1
    max_seconds = {role: 0.0 for role in details}
    for game in report['games']:
        for role, player in game['players'].items():
            for command in player.get('commands', []):
                if command.get('command', '').startswith('genmove '):
                    seconds = command.get('seconds')
                    if isinstance(seconds, (int, float)):
                        max_seconds[role] = max(max_seconds[role], seconds)
                        if seconds > 7:
                            details[role]['over_7s'] += 1
    return {
        'trace_counts': details,
        'max_genmove_seconds': max_seconds,
        'search': phase8_compare._search_metrics(report),
    }


def _match(args: argparse.Namespace) -> None:
    """凍結開発序盤と同一時間で試走1ペアまたは残り11ペアを取得する。"""
    env = _environment()
    population: Path = args.population.resolve(strict=True)
    expected = phase8_compare.POPULATION_SHA if args.scope == 'pilot' else REMAINDER_SHA
    if sha_file(population) != expected:
        raise ValueError('phase8_openings_mismatch')
    if openings.load_population(population)['purpose'] != 'development':
        raise ValueError('requires_development_population')
    pairs = 1 if args.scope == 'pilot' else 11
    path_model = args.model.resolve(strict=True) if args.model else None
    if args.submission and path_model is None:
        raise ValueError('submission_comparison_requires_model')
    a, b = SCRIPT, SCRIPT
    assets_a = [
        *(a.with_name(name) for name in phase8_compare.PACKAGE),
        a.with_name('ntuple.py'),
        a.with_name('ntuple_io.py'),
    ]
    if path_model:
        assets_a.append(path_model)
    assets_b = assets_a
    if args.submission:
        b, assets_b, _ = phase8_compare._opponent('ranger', args.submission)
    output: Path = args.output
    output.mkdir()
    config_a, config_b = output / 'a-config.json', output / 'b-config.json'
    _save(config_a, _config(args.depth, path_model))
    _save(
        config_b,
        _config(4, None if args.submission else path_model, archived=bool(args.submission)),
    )
    digests = {str(p): sha_file(p) for p in [*assets_a, *assets_b, config_a, config_b, population]}
    _save(
        output / 'run.json',
        {
            **env,
            'source_and_asset_sha256': digests,
            'seed': SEED,
            'pairs': pairs,
            'time_ns': LIMIT_NS,
            'external_seconds': [5, 1, 10],
            'max_depth_a': args.depth,
            'max_depth_b': 4,
        },
    )
    begin = time.perf_counter()
    report = evaluate.evaluate(
        a,
        b,
        games=2 * pairs,
        seed=SEED,
        assets_a=assets_a,
        assets_b=assets_b,
        openings_file=population,
        selection_seed=SEED,
        config_a=config_a,
        config_b=config_b,
        budget_policy='equal-time',
        evaluation_profile='deployment',
        require_budget_verification=True,
        evidence_dir=output / 'raw',
    )
    elapsed = time.perf_counter() - begin
    _save(output / 'report.json', report)
    verify = analysis.assess_evaluation(report, 0.95, True)
    results = phase7_eval._game_stats(report['games'], True)
    cases = {game['case_id'] for game in report['games']}
    actual_cases = (
        cases == {PILOT_CASE} if pairs == 1 else len(cases) == 11 and PILOT_CASE not in cases
    )
    unchanged = all(sha_file(Path(p)) == before for p, before in digests.items())
    _save(
        output / 'summary.json',
        {
            **results,
            **_match_summary(report, output / 'raw', bool(args.submission), args.depth),
            'verified': verify,
            'assets_unchanged': unchanged,
            'evaluation_seconds': elapsed,
            'raw_bytes': sum(p.stat().st_size for p in (output / 'raw').rglob('*') if p.is_file()),
        },
    )
    if (
        not unchanged
        or not actual_cases
        or not verify['request_success']
        or results['errors']
        or results['valid_pairs'] != pairs
    ):
        raise ValueError('match_verification_failed_see_artifacts')
    print(f'{output.name}: {results["wdl_a"]}, {elapsed:.1f}s', flush=True)


def main() -> None:
    """代表局面の測定と対局のCLI。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    bench = commands.add_parser('bench')
    bench.add_argument('--positions', required=True, type=Path)
    bench.add_argument('--registry', required=True, type=Path)
    bench.add_argument('--output', required=True, type=Path)
    match = commands.add_parser('match')
    match.add_argument('--scope', required=True, choices=('pilot', 'remaining'))
    match.add_argument('--depth', choices=(5, 6), required=True, type=int)
    match.add_argument('--population', required=True, type=Path)
    match.add_argument('--model', type=Path)
    match.add_argument('--submission', type=Path)
    match.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'bench':
        _bench(args)
    else:
        _match(args)


if __name__ == '__main__':
    main()
