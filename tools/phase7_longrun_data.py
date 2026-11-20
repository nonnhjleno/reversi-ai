"""Phase 7.1の固定計画、6起点、局境界の連鎖を検証する。"""

from __future__ import annotations

import json
import os
import time
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from reversi_player import ntuple_io
from tools import evidence, phase7_eval, phase7_td
from tools import phase7_train as train
from tools.phase65_data import sha_file

ROOT = train.ROOT
RATE = 0.0000625
EPSILON = 0.20
START = 10_000
TARGETS = (20_000, 50_000, 100_000)
DEFAULT_ROOT = ROOT / 'evaluation_results' / 'phase7-longrun-100k'
PHASE7 = 'phase7-td0-20261119'
POP_SHA = 'ce747c9019a6661df549c3a9260af3a5c55229593515793be6ecd50aba08c7ff'
ORIGIN_SHA = {
    'zero-701': '1a83e7e4c415ac25dd2e2944b0bdd5f2a5ade48d71408be48bed85ede064d8e2',
    'zero-702': 'c6999c294b75028df65fea40e3d70f5c4265b2e4423818134ebb76be665f8587',
    'zero-703': '941469de38b5f3309c8e44d8934749a642b240a7b28388036fbb5e9e7d648275',
    'pretrained-701': 'd9069ee07bd8c4fea9efcd4ae883b44ebf210fc6abc43ac0a7afe01d232aa4b0',
    'pretrained-702': '5eeb84e7e589a568a44cb5ff6415f26d7302da725690730e4c2a577a0527cf61',
    'pretrained-703': 'f20ebc04386086181d001f4034afcb38700cc36bb5a1e174ffd9e46ff99152da',
}
_INITIALS = {
    'zero': (
        'phase6-ntuple-20261118/measurements-final/untrained-zero.json',
        '28c60077b012ae322b5d299019c97cafc43206edf31ea3830df7a0a93a21cb6d',
    ),
    'pretrained': (
        'phase6-5-supervised-20261118/stages/fit-81919-0.000125-65/epoch-4.json',
        '76d7be731a4944725d08a45b1f3a678cb7d5e274c040f7495027518352a61331',
    ),
}
_LOCAL = (
    'tools/phase7_longrun.py',
    'tools/phase7_longrun_data.py',
    'tools/phase7_longrun_os.py',
    'tools/phase7_longrun_reports.py',
    'tools/phase7_eval.py',
    'reversi_player/phase6_player.py',
    'harness/evaluate.py',
    'harness/contracts.py',
    'harness/analysis.py',
    'harness/openings.py',
    'harness/protocol.py',
    'harness/search_budget.py',
    'harness/statistics.py',
    'harness/seeded_player.py',
    'reversi_player/response_config.json',
    'reversi_player/timed_search.py',
    'reversi_player/search_trace.py',
)


class RunError(RuntimeError):
    """利用者が安全に対処できる状態不整合。"""


def obj(value: object) -> dict[str, object]:
    """JSONオブジェクトの形を検査する。"""
    if not isinstance(value, dict):
        raise RunError('JSONの形式が違います。保存済みファイルを確認してください。')
    return cast(dict[str, object], value)


def read(path: Path) -> dict[str, object]:
    """JSONを読み、ファイルが分かるエラーにする。"""
    try:
        for _ in range(8):
            try:
                raw = path.read_bytes()
            except PermissionError:
                # 状態JSONを差し替える瞬間の一時的な共有競合だけを再試行する。
                time.sleep(0.03)
            else:
                return obj(json.loads(raw))
        return obj(json.loads(path.read_bytes()))
    except (OSError, ValueError) as failure:
        raise RunError(f'読み込めません（{path}）。{failure}') from failure


def seal(path: Path, payload: object) -> None:
    """ハッシュを付けて新規の確定記録を発行する。"""
    evidence.save_new(
        path, evidence.encoded_json({'payload': payload, 'sha256': train._digest(payload)})
    )


def unseal(path: Path) -> tuple[dict[str, object], str]:
    """保存内容をハッシュと照合して返す。"""
    x = read(path)
    payload = obj(x.get('payload'))
    digest = train._digest(payload)
    if x.get('sha256') != digest:
        raise RunError(f'ハッシュが一致しません（{path}）。')
    return payload, digest


def available() -> None:
    """検証済みのWindows直接CPythonだけを受け入れる。"""
    if (
        sys.platform != 'win32'
        or platform.python_implementation() != 'CPython'
        or sys.version_info[:3] != (3, 13, 5)
        or sys.prefix != sys.base_prefix
        or sys.maxsize <= 2**32
    ):
        raise RunError('Windowsの直接インストール版CPython 3.13.5（64ビット）を使ってください。')


def asset_directory() -> Path:
    """同じリポジトリの作業ツリー階層から既存証拠を探す。"""
    for candidate in (ROOT, *ROOT.parents):
        path = candidate / 'evaluation_results'
        if (path / PHASE7).is_dir():
            return path
    return ROOT / 'evaluation_results'


def fingerprint() -> dict[str, str]:
    """実験の処理と評価に使うファイルのSHA-256を返す。"""
    paths = [ROOT / name for name in _LOCAL]
    for label in sorted(phase7_eval.BASELINES):
        _, assets, _ = phase7_eval._fixed(label)
        paths.extend(assets)
    return {**train.sources(), **{p.relative_to(ROOT).as_posix(): sha_file(p) for p in paths}}


@dataclass(frozen=True)
class Plan:
    """固定実験の入力。smokeは開始局数を維持し、目標と評価数だけを減らす。"""

    root: Path
    assets: Path
    run_id: str
    smoke: str | None
    smoke_games: int
    population_sha: str

    @property
    def labels(self) -> tuple[str, ...]:
        """決定的な系列順。"""
        return tuple(ORIGIN_SHA) if self.smoke is None else (f'{self.smoke}-701',)

    @property
    def milestones(self) -> tuple[int, ...]:
        """評価を実行する累積局数。"""
        return TARGETS if self.smoke is None else (START + self.smoke_games,)

    @property
    def target(self) -> int:
        """学習を終える累積局数。"""
        return self.milestones[-1]

    @property
    def population(self) -> Path:
        """比較に使う開発序盤のファイル名。"""
        return self.root / 'openings.json'

    @property
    def phase7(self) -> Path:
        """Phase 7の原証拠の参照先。"""
        return self.assets / PHASE7

    @property
    def minimum_free(self) -> int:
        """実行中の1区間と保存の余白。"""
        return (512 if self.smoke is None else 32) * 2**20

    def next_stop(self, games: int) -> int:
        """定期保存と評価節目を越えない次境界。"""
        interval = 1_000 if self.smoke is None else 10
        boundary = (games // interval + 1) * interval
        return min(boundary, next(mark for mark in self.milestones if mark > games))


def payload(plan: Plan) -> dict[str, object]:
    """変更を検出する設定とソースの記録。"""
    return {
        'version': 1,
        'run_id': plan.run_id,
        'assets': str(plan.assets),
        'smoke': plan.smoke,
        'smoke_games': plan.smoke_games,
        'population_sha256': plan.population_sha,
        'labels': list(plan.labels),
        'start': START,
        'milestones': list(plan.milestones),
        'interval': 1000 if plan.smoke is None else 10,
        'rate': RATE,
        'epsilon': EPSILON,
        'gamma': 1,
        'runtime': train._runtime(),
        'sources': fingerprint(),
    }


def load(root: Path) -> tuple[Plan, str]:
    """初期化済みの固定計画と現在のソース・環境を照合する。"""
    x, digest = unseal(root / 'config.json')
    smoke = x.get('smoke')
    limit = x.get('smoke_games')
    if (
        smoke not in (None, 'zero', 'pretrained')
        or type(limit) is not int
        or not 20 <= limit <= 100
    ):
        raise RunError('試験設定が不正です。')
    assets = Path(str(x.get('assets')))
    plan = Plan(
        root,
        assets,
        str(x.get('run_id')),
        smoke,
        limit,
        str(x.get('population_sha256')),
    )
    if (
        not assets.is_absolute()
        or x != payload(plan)
        or sha_file(plan.population) != plan.population_sha
    ):
        raise RunError('設定、開発序盤、ソース、またはPython環境が初期化時と違います。')
    return plan, digest


def disk_check(path: Path, required: int, *, probe: bool = False) -> int:
    """利用可能容量を検査し、事前確認で書き込みも試す。"""
    base = path
    while not base.exists():
        base = base.parent
    free = shutil.disk_usage(base).free
    if free < required:
        raise RunError(
            f'空き容量が不足しています（空き {free / 2**30:.2f} GiB、必要 {required / 2**30:.2f} GiB）。'
        )
    if probe:
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            target = Path(temporary)
            source = target / 'before'
            source.write_bytes(b'Ranger')
            os.link(source, target / 'after')
            (target / 'after').replace(target / 'published')
    return free


@dataclass(frozen=True)
class Checkpoint:
    """検証済みの保存状態とその直前の状態。"""

    path: Path
    sha: str
    counts: train.Counts
    before: train.Counts
    previous: str | None
    model_id: str
    model_sha: str
    metrics: dict[str, object]

    @property
    def model(self) -> Path:
        """Phase 6の推論用モデル。"""
        return self.path / 'model.json'

    def as_status(self) -> dict[str, object]:
        """進捗表示に使う小さな写し。"""
        return {
            'checkpoint': str(self.path),
            'state_sha256': self.sha,
            'games': self.counts.games,
            'updates': self.counts.updates,
            'passes': self.counts.passes,
            'model_id': self.model_id,
            'model_sha256': self.model_sha,
        }


@dataclass(frozen=True)
class Origin:
    """1系列の初期モデル、Phase 7の起点と固定設定。"""

    label: str
    initial: Path
    config: phase7_td.Config
    compatible: dict[str, object]
    checkpoint: Checkpoint


def checkpoint(path: Path, compatibility: dict[str, object]) -> Checkpoint:
    """既存トレーナーの復元検査を行い、保存済みの連鎖情報を取得する。"""
    restored = train._restore(path, compatibility)
    x = obj(read(path / 'state.json')['payload'])
    current = obj(x['model'])
    parent = obj(x['previous']) if x['previous'] is not None else {}
    if restored.parent is None:
        raise RunError(f'checkpointの親情報を発行できません（{path}）。')
    return Checkpoint(
        path,
        str(restored.parent['state_sha256']),
        restored.counts,
        train._counts(x['from_counts']),
        str(parent['state_sha256']) if parent else None,
        str(current['model_id']),
        str(current['sha256']),
        obj(x['metrics']),
    )


def origins(assets: Path) -> dict[str, Origin]:
    """既知の6起点と初期モデルを照合して実際に再開状態を復元する。"""
    sources = train.sources()
    runtime = train._runtime()
    out = {}
    for label, state_sha in ORIGIN_SHA.items():
        series, seed = label.split('-')
        relative, expected = _INITIALS[series]
        initial = assets / relative
        directory = assets / PHASE7 / 'train-10000' / label
        try:
            model = ntuple_io.load_model(initial)
            if model.file_sha256 != expected or sha_file(directory / 'state.json') != state_sha:
                raise RunError('初期モデルまたはPhase 7のstate.jsonのSHA-256が違います。')
            config = phase7_td.Config(RATE, EPSILON, int(seed))
            compatibility = {
                'initial': {'model_id': model.model_id, 'sha256': expected},
                'settings': train._settings(config),
                'sources': sources,
                'runtime': runtime,
            }
            c = checkpoint(directory, compatibility)
            if c.counts.games != START or c.sha != state_sha:
                raise RunError('10,000局起点の保存状態が違います。')
        except (OSError, ValueError, RunError) as failure:
            raise RunError(
                f'{label} の再開起点を確認できません。{directory} を確認してください。{failure}'
            ) from failure
        out[label] = Origin(label, initial, config, compatibility, c)
    return out


def accept(plan: Plan, origin: Origin, attempt: Path, begin: Checkpoint) -> Checkpoint:
    """正しい系列と親から作られた完了済みの区間だけを採用する。"""
    job, _ = unseal(attempt / 'job.json')
    end = plan.next_stop(begin.counts.games)
    expected = {
        'label': origin.label,
        'from': begin.counts.games,
        'parent': begin.sha,
        'until': end,
    }
    if job != expected:
        raise RunError(f'系列、親または保存予定が違います（{attempt.name}）。')
    c = checkpoint(attempt / 'checkpoint', origin.compatible)
    if (
        c.previous != begin.sha
        or c.before != begin.counts
        or not begin.counts.games < c.counts.games <= end
    ):
        raise RunError(f'checkpointの連鎖が違います（{attempt.name}）。')
    return c


def record_skipped(folder: Path) -> None:
    """作業を始める前に停止要求を受けた試行を確定する。"""
    _, dispatch_sha = unseal(folder / 'dispatch.json')
    seal(folder / 'skipped.json', {'dispatch': dispatch_sha})


def skipped(folder: Path, output: str) -> bool:
    """作業を開始しなかった正当な停止を中途終了と区別する。"""
    marker = folder / 'skipped.json'
    if not marker.is_file():
        return False
    contents, _ = unseal(marker)
    _, dispatch_sha = unseal(folder / 'dispatch.json')
    if contents != {'dispatch': dispatch_sha} or (folder / output).exists():
        raise RunError(f'停止記録と試行の内容が違います（{folder}）。')
    return True


def scan(plan: Plan, origin: Origin) -> tuple[list[Checkpoint], list[dict[str, object]]]:
    """健全な連鎖と再実行が必要な区間を保存済み試行から構成する。"""
    directory = plan.root / 'checkpoints' / origin.label
    possible: dict[str, list[Path]] = {}
    problems: list[dict[str, object]] = []
    for attempt in sorted(directory.glob('attempt-*')):
        try:
            job, _ = unseal(attempt / 'job.json')
            if job.get('label') != origin.label:
                raise RunError('系列が一致しません。')
            if not skipped(attempt, 'checkpoint'):
                possible.setdefault(str(job['parent']), []).append(attempt)
        except (OSError, ValueError, KeyError, RunError) as failure:
            problems.append({'path': str(attempt), 'problem': str(failure)})
    chain = [origin.checkpoint]
    while chain[-1].counts.games < plan.target:
        begin = chain[-1]
        children = []
        for attempt in possible.pop(begin.sha, []):
            try:
                children.append(accept(plan, origin, attempt, begin))
            except (OSError, ValueError, KeyError, RunError) as failure:
                problems.append(
                    {
                        'path': str(attempt),
                        'problem': str(failure),
                        'from': begin.counts.games,
                        'until': plan.next_stop(begin.counts.games),
                    }
                )
        if len(children) > 1:
            raise RunError(
                f'{origin.label} の{begin.counts.games}局から確定checkpointが分岐しています。'
            )
        if not children:
            break
        chain.append(children[0])
    for attempts in possible.values():
        problems.extend(
            {'path': str(a), 'problem': '確定済み連鎖につながっていません。'} for a in attempts
        )
    return chain, problems
