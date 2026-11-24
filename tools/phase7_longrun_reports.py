"""固定節目の評価契約を照合し、保存結果から集計を作る。"""

from __future__ import annotations

import csv
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from harness import evaluate, openings
from tools import evidence, phase7_eval
from tools import phase7_longrun_data as data
from tools.phase65_data import sha_file

if TYPE_CHECKING:
    from harness.contracts import _Game

_ZERO_REFERENCE = {
    '701-zero-vs-positional_v1': (
        'a1ab83515f254c2b98f62d89e74decaf',
        '3521d984f57a55394b72bc9a69aed55ea90ede4c22ddcac44cca928523431be3',
    ),
    '701-zero-vs-search_v1': (
        '0c03d0bec59345c9a4abce9406c78e84',
        '36916b78962e6733753123155a8e50019bcdcce7877236eec76a138f10e6692f',
    ),
    '702-zero-vs-positional_v1': (
        '83a5aa7ce50b4d4cb719ce5e7cec5212',
        'ccc259d3fcaccf91c6884d5b3aef10c9378894cb4d73ee2c2f9879d0132a8472',
    ),
    '702-zero-vs-search_v1': (
        '8c1363dc3bc14e45bb038f22870c118e',
        '5cbac74701fb4309d82355f925d487a0d821685d66a7a25973b54f88148036e6',
    ),
    '703-zero-vs-positional_v1': (
        '049c9137b6c04116a259410a28bd4e4d',
        '0e3baa85584f1062a59c5cd7a8546c925102127a786bb43b12091ec5f6cbd57b',
    ),
    '703-zero-vs-search_v1': (
        'a34c24558efc42d8adba6bbb39ad4565',
        '7956fa308b0f2c8bff47af0cd4cd4581211b49bc32091f69d3b269ba43e42662',
    ),
}


class StartupTimeout(data.RunError):
    """評価契約を満たした試行で、異常が対局開始前の応答待ちに限られている。"""


def _only_startup_timeouts(games: list[_Game], counts: object) -> bool:
    """集計値と対局の最初のコマンドから、再試行できる応答待ちだけを判定する。"""
    kinds = data.obj(counts)
    if not kinds.get('timeout') or kinds.keys() - {
        'timeout',
        'process_exit',
        'incomplete_color_pair',
    }:
        return False
    failures = [data.obj(game['error']) for game in games if game.get('error') is not None]
    if not failures:
        return False
    for failure in failures:
        if failure.get('kind') != 'timeout' or failure.get('command') != 'name':
            return False
        trace = data.obj(failure.get('diagnostics'))
        commands = trace.get('commands')
        if trace.get('stderr_tail') != [] or not isinstance(commands, list) or len(commands) != 1:
            return False
        probe = data.obj(commands[0])
        if (probe.get('command'), probe.get('startup_probe'), probe.get('error')) != (
            'name',
            True,
            'timeout',
        ):
            return False
    return True


@dataclass(frozen=True)
class Match:
    """1系列と固定相手、または同一seedの系列間対局。"""

    stage: int
    label: str
    opponent: str

    @property
    def key(self) -> str:
        """Phase 7と共通の評価キー。"""
        series, seed = self.label.split('-')
        return f'{seed}-{series}-vs-{self.opponent}'

    @property
    def opponent_mark(self) -> str | None:
        """モデル同士の対局で使う相手の保存点キー。"""
        if self.opponent == 'origin100k':
            return f'{self.label}@origin'
        if self.opponent == 'zero':
            return f'zero-{self.label[-3:]}'
        return None


def matches(plan: data.Plan, stage: int) -> list[Match]:
    """固定相手2版と3seedの直接対局を同じ順で列挙する。"""
    jobs = [
        Match(stage, label, side)
        for label in plan.labels
        for side in ('positional_v1', 'search_v1')
    ]
    if plan.zero500:
        if stage > plan.start:
            jobs.extend(Match(stage, label, 'origin100k') for label in plan.labels)
    elif plan.smoke is None:
        jobs.extend(Match(stage, f'pretrained-{seed}', 'zero') for seed in (701, 702, 703))
    return jobs


def match_inputs(job: Match, marks: dict[str, data.Checkpoint]) -> tuple[data.Checkpoint, str]:
    """固定相手の名前か同じseedの確定モデルを渡す。"""
    a = marks[job.label]
    if a.counts.games != job.stage or sha_file(a.model) != a.model_sha:
        raise data.RunError(f'{job.key} の評価モデルが違います。')
    name = job.opponent_mark
    if name is None:
        return a, job.opponent
    b = marks[name]
    expected = data.ZERO_START if job.opponent == 'origin100k' else job.stage
    if b.counts.games != expected or sha_file(b.model) != b.model_sha:
        raise data.RunError(f'{job.key} の対戦モデルが違います。')
    return a, str(b.model)


def stage_marks(
    plan: data.Plan, chains: dict[str, list[data.Checkpoint]], stage: int
) -> dict[str, data.Checkpoint]:
    """節目の凍結モデルと同じseedの起点を評価に渡す。"""
    marks = {label: c for label, chain in chains.items() for c in chain if c.counts.games == stage}
    if plan.zero500 and stage > plan.start:
        marks.update({f'{label}@origin': chain[0] for label, chain in chains.items()})
    return marks


def reference_path(plan: data.Plan, job: Match) -> Path:
    """原評価を固定する。10万局では受理済みの試行とsummaryのSHA-256も固定する。"""
    if not plan.zero500:
        return plan.phase7 / 'compare-10000' / job.key
    attempt, digest = _ZERO_REFERENCE[job.key]
    path = plan.assets / data.PHASE71 / 'evaluations' / '100000' / job.key
    path = path / f'attempt-{attempt}' / 'match'
    if sha_file(path / 'summary.json') != digest:
        raise data.RunError(f'{job.key} の10万局原評価が異なります。')
    return path


def _asset_hashes(record: object) -> set[tuple[str, str]]:
    """移動可能な証拠のファイル名とハッシュを取り出す。"""
    return {
        (Path(str(x['path'])).name, str(x['sha256']))
        for x in cast(list[dict[str, object]], data.obj(record)['assets'])
    }


def verified_match(
    job: Match,
    marks: dict[str, data.Checkpoint],
    directory: Path,
    population: Path,
) -> dict[str, object]:
    """元対局、確定checkpoint、先後・seed・相手・予算を検証し再計数する。"""
    candidate, opponent = match_inputs(job, marks)
    summary = data.read(directory / 'summary.json')
    raw = directory / 'report.json'
    if sha_file(raw) != summary.get('report_sha256') or sha_file(population) != summary.get(
        'population_sha256'
    ):
        raise data.RunError(f'{job.key} の評価ファイルが保存時と違います。')
    if summary.get('asset_changed') is not False:
        raise data.RunError(f'{job.key} に評価異常があります。')
    failed = bool(summary['errors'])
    plan = openings.load_population(population)
    selected, selection = openings.select_entries(plan, len(plan['entries']), phase7_eval.EVAL_SEED)
    case_ids = [c['case_id'] for c in selected]
    report = data.read(raw)
    conditions = data.obj(report['conditions'])
    status = data.obj(report['status'])
    if (
        conditions.get('selection_manifest') != selection
        or conditions.get('games') != len(case_ids) * 2
        or conditions.get('seed') != phase7_eval.EVAL_SEED
        or conditions.get('evaluation_profile') != 'descriptive'
        or conditions.get('budget_policy') != 'descriptive'
        or [conditions.get(k) for k in ('startup_timeout', 'command_timeout', 'genmove_timeout')]
        != [5, 1, 10]
        or status.get('execution') != ('failed' if failed else 'complete')
        or status.get('protocol') != ('failed' if failed else 'verified')
    ):
        raise data.RunError(f'{job.key} の評価条件または終了状態が違います。')
    budgets = data.obj(conditions['budget_configs'])
    if data.obj(budgets['A'])['budget'] != {'kind': 'unknown'}:
        raise data.RunError(f'{job.key} の1手選択の設定が違います。')
    targets = data.obj(report['targets'])
    runtime = {
        (name, sha_file(data.ROOT / 'reversi_player' / name)) for name in phase7_eval._RUNTIME
    }
    if (
        _asset_hashes(targets['A']) != runtime | {('model.json', candidate.model_sha)}
        or data.obj(summary['candidate'])['sha256'] != candidate.model_sha
    ):
        raise data.RunError(f'{job.key} の候補モデルまたは推論コードが違います。')
    if job.opponent in phase7_eval.BASELINES:
        script, assets, config = phase7_eval._fixed(job.opponent)
        budget = {'kind': 'depth', 'limit_depth': 2} if config else {'kind': 'unknown'}
        if (
            data.obj(budgets['B'])['budget'] != budget
            or _asset_hashes(targets['B']) != {(p.name, sha_file(p)) for p in assets}
            or data.obj(targets['B'])['sha256'] != sha_file(script)
        ):
            raise data.RunError(f'{job.key} の固定相手または予算が違います。')
    else:
        b_hash = sha_file(Path(opponent))
        if (
            _asset_hashes(targets['B']) != runtime | {('model.json', b_hash)}
            or data.obj(summary['opponent'])['sha256'] != b_hash
            or data.obj(budgets['B'])['budget'] != {'kind': 'unknown'}
        ):
            raise data.RunError(f'{job.key} の対戦モデルが違います。')
    games = cast(list['_Game'], report['games'])
    case_legs = [(case, leg) for case in case_ids for leg in (0, 1)]
    if [(g['case_id'], g['leg']) for g in games] != case_legs:
        raise data.RunError(f'{job.key} に欠落または重複対局があります。')
    for g in games:
        colors = {'B': 'A', 'W': 'B'} if g['leg'] == 0 else {'B': 'B', 'W': 'A'}
        seeds = {
            role: evaluate._seed_for(phase7_eval.EVAL_SEED, g['case_id'], role) for role in 'AB'
        }
        if g['colors'] != colors or g['seeds'] != seeds:
            raise data.RunError(f'{job.key} の先後・seed・実行状態が違います。')
    score = phase7_eval._game_stats(games, job.opponent == 'search_v1')
    if any(summary.get(k) != v for k, v in score.items()):
        raise data.RunError(f'{job.key} の集計が元対局と一致しません。')
    if failed and _only_startup_timeouts(games, score['errors']):
        raise StartupTimeout(f'{job.key} の起動時 name が期限内に応答しませんでした。')
    if failed or any(g.get('error') is not None for g in games):
        raise data.RunError(f'{job.key} に評価異常があります。')
    return {**summary, 'report': str(raw), 'state_sha256': candidate.sha}


def find_result(
    plan: data.Plan,
    job: Match,
    marks: dict[str, data.Checkpoint],
) -> tuple[dict[str, object] | None, list[dict[str, str]]]:
    """健全な評価を一つだけ採用し、不完全な過去試行を記録する。"""
    if job.stage == plan.start:
        path = reference_path(plan, job)
        source = plan.phase7 / 'opening-registration' / 'comparison.json'
        return verified_match(job, marks, path, source), []
    parent = plan.root / 'evaluations' / str(job.stage) / job.key
    complete = []
    errors = []
    for path in sorted(parent.glob('attempt-*')):
        try:
            if not data.skipped(path, 'match'):
                completed = verified_match(job, marks, path / 'match', plan.population)
                complete.append(completed)
        except StartupTimeout as failure:
            errors.append(
                {'attempt': str(path), 'problem': str(failure), 'cause': 'startup_timeout'}
            )
        except (data.RunError, OSError, ValueError, KeyError, TypeError) as failure:
            if plan.zero500 and (path / 'match' / 'summary.json').exists():
                raise data.RunError(
                    f'{job.key} の確定評価を検証できません（{path}）。'
                ) from failure
            errors.append({'attempt': str(path), 'problem': str(failure)})
        if plan.zero500:
            errors.extend(
                {'attempt': str(path), **issue}
                for issue in data.runtime_issues(path)
                if (path / 'process-exit.json').is_file() or not (path / 'process.json').is_file()
            )
    if len(complete) > 1:
        raise data.RunError(f'{job.key} に複数の正常な評価試行があります。重複を確認してください。')
    if plan.zero500 and complete:
        for error in errors:
            error.setdefault('recovery', 'verified_replacement')
    return (complete[0] if complete else None), errors


def _csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    """Excelで開きやすいUTF-8のCSVを発行する。"""
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})


def reports(
    plan: data.Plan,
    chains: dict[str, list[data.Checkpoint]],
    errors: list[dict[str, object]],
) -> Path:
    """検証済みの学習と評価を集計し、新しい報告世代を発行する。"""
    progress = []
    results = []
    for name, chain in chains.items():
        merged_outcomes: Counter[str] = Counter()
        total_seconds = 0.0
        for c in chain:
            m = c.metrics
            merged_outcomes.update(cast(dict[str, int], m['outcomes']))
            total_seconds += float(cast(float, m['seconds']))
            td = data.obj(m['td'])
            lengths = cast(dict[str, int], m['game_lengths'])
            progress.append(
                {
                    'series': name,
                    **c.as_status(),
                    'metrics': m,
                    'seconds': m['seconds'],
                    'post_origin_seconds': 0.0
                    if c.counts.games == plan.start
                    else total_seconds - float(cast(float, chain[0].metrics['seconds'])),
                    'saturation_rate': m['saturation_rate'],
                    'max_abs_delta': td['max_abs_delta'],
                    'mean_abs_delta': cast(float, td['abs_delta_sum'])
                    / cast(int, td['observations']),
                    'mean_game_length': sum(int(k) * v for k, v in lengths.items())
                    / cast(int, m['games']),
                    'model_save_seconds': m['model_save_seconds'],
                    'model_load_seconds': m['model_load_seconds'],
                    'peak_memory_bytes': m['peak_memory_bytes'],
                    'included_segment_outcomes': dict(merged_outcomes),
                }
            )
    for stage in plan.stages:
        marks = stage_marks(plan, chains, stage)
        groups: dict[tuple[str, str], list[float]] = {}
        for job in matches(plan, stage):
            series, seed = job.label.split('-')
            record: dict[str, object] = {
                'games_trained': stage,
                'series': series,
                'opponent': job.opponent,
                'seed': int(seed),
                'state': 'TRAINING_PENDING',
            }
            other = job.opponent_mark
            if job.label in marks and (other is None or other in marks):
                value, problems = find_result(plan, job, marks)
                record.update(
                    state='EVALUATION_FAILED' if problems else 'EVALUATION_PENDING',
                    attempts=problems,
                    model=marks[job.label].as_status(),
                )
                if value is not None:
                    wins, losses, draws = cast(list[int], value['wdl_a'])
                    rate = cast(float, value['point_rate'])
                    groups.setdefault((series, job.opponent), []).append(rate)
                    normal = data.obj(value['normal_move_seconds'])
                    record.update(
                        value,
                        state='VERIFIED',
                        wins=wins,
                        losses=losses,
                        draws=draws,
                        points=wins + draws / 2,
                        max_response_seconds=max(
                            cast(float, data.obj(a)['max']) for a in normal.values()
                        ),
                    )
                    if plan.zero500:
                        record['opponent_detail'] = record['opponent']
                        record['opponent'] = job.opponent
            results.append(record)
        if plan.smoke is None:
            for job in [k for k in matches(plan, stage) if k.label.endswith('-701')]:
                series = job.label.split('-')[0]
                values = groups.get((series, job.opponent), [])
                results.append(
                    {
                        'games_trained': stage,
                        'series': series,
                        'opponent': job.opponent,
                        'seed': 'mean',
                        'state': 'VERIFIED' if len(values) == 3 else 'INCOMPLETE',
                        'point_rate': statistics.mean(values) if len(values) == 3 else None,
                        'seed_stdev': statistics.stdev(values) if len(values) == 3 else None,
                    }
                )
    folder = plan.root / 'reports' / f'report-{uuid4().hex}'
    folder.mkdir()
    _csv(
        folder / 'results.csv',
        results,
        [
            'games_trained',
            'series',
            'opponent',
            'seed',
            'state',
            'games',
            'wins',
            'losses',
            'draws',
            'points',
            'point_rate',
            'seed_stdev',
            'mean_margin_a',
            'median_margin_a',
            'elapsed_seconds',
            'max_response_seconds',
            'report',
        ],
    )
    _csv(
        folder / 'progress.csv',
        progress,
        [
            'series',
            'games',
            'updates',
            'passes',
            'checkpoint',
            'state_sha256',
            'model_id',
            'model_sha256',
            'seconds',
            'post_origin_seconds',
            'saturation_rate',
            'mean_abs_delta',
            'max_abs_delta',
            'mean_game_length',
            'model_save_seconds',
            'model_load_seconds',
            'peak_memory_bytes',
        ],
    )
    lines = [
        f'# Phase {"7.2" if plan.zero500 else "7.1"} 自己対戦の結果',
        '',
        '固定development序盤の参考評価です。未評価の勝ち点率は空欄です。',
        '',
        '|累積局数|系列|相手|seed|状態|勝ち点率|',
        '|---:|---|---|---:|---|---:|',
    ]
    for row in results:
        shown_rate = row.get('point_rate')
        point = f'{shown_rate:.1%}' if isinstance(shown_rate, (int, float)) else ''
        lines.append(
            f'|{row["games_trained"]}|{row["series"]}|{row["opponent"]}|{row["seed"]}|{row["state"]}|{point}|'
        )
    evidence.save_new(folder / 'summary.md', '\n'.join(lines) + '\n')
    data.seal(
        folder / 'summary.json',
        {
            'run_id': plan.run_id,
            'evaluation': results,
            'training': progress,
            'checkpoint_issues': errors,
            'milestone_models': [
                p for p in progress if p['games'] in (plan.start, *plan.milestones)
            ],
        },
    )
    return folder
