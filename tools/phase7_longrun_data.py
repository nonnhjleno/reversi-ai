"""Phase 7.1の固定計画、6起点、局境界の連鎖を検証する。"""

from __future__ import annotations

import json
import os
import time
import platform
import shutil
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from reversi_player import ntuple_io
from tools import evidence, phase7_eval, phase7_td
from tools import phase7_train as train
from tools import phase7_longrun_os as windows
from tools.phase65_data import sha_file

ROOT = train.ROOT
RATE = 0.0000625
EPSILON = 0.20
START = 10_000
TARGETS = (20_000, 50_000, 100_000)
DEFAULT_ROOT = ROOT / 'evaluation_results' / 'phase7-longrun-100k'
PHASE7 = 'phase7-td0-20261119'
PHASE71 = 'phase7-longrun-100k-rerun-20261120'
ZERO500 = 'zero-500k'
ZERO_START = 100_000
ZERO_TARGETS = (150_000, 200_000, 300_000, 400_000, 500_000)
ZERO_ORIGINS = {
    'zero-701': (
        'e4a8fd9ad8c44cdf87ec09bf561a609f',
        'decef2f0bbc4d3efc2df530faa957211e55c07371cd3467f0aaa7f8d246396fa',
        '8d66fc82e95c4e52781391e0b05e5c36dab9d8fad26a690a4e4dfefc257c1e72',
    ),
    'zero-702': (
        'f7fcea8fbd424b8bba21cb0675206f4c',
        'f9b5a4c163d8798864b8ee9a3ed0a3e80feea8ce9ed46c4c15a2fd8c52d74273',
        '656a2ab1b3004d34945a63274f6bfc4d2321abf327b028a63bd11848e146a0a1',
    ),
    'zero-703': (
        '3ba999e0bd1d4fe38e1a9ab89004bf3f',
        '8c7b26e3c21d3fbc5be4fc4920340dc86c85fbc5cd33c4218dd91ffaafd8b4c4',
        '4b777638fec0aa6cabb695a996e864166edeedfec2e363f80a8c3021fd95ef1d',
    ),
}
_PHASE71_MANAGER = {
    'tools/phase7_longrun.py': '5693a1a4a75516e7c810c79a6ca47a17210f41277558e3cf3851282efeb6a74c',
    'tools/phase7_longrun_data.py': '52fe1b2713fff784f2b5c8bf606e41c6985c094309b8292af3ee12ebf38e04ac',
    'tools/phase7_longrun_os.py': 'a624f83150fd1b09917431798b6cdcd366e5d2f1f9e9db0efcb92918698dec87',
    'tools/phase7_longrun_reports.py': 'f4fe99a848bf28132a9a13f7f2e53669748482a8f550912b870c5a8ca63f4bca',
}
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


def read(path: Path, *, changing: bool = False) -> dict[str, object]:
    """JSONを読み、ファイルが分かるエラーにする。"""
    try:
        for _ in range(8):
            try:
                raw = windows.shared_bytes(path) if changing else path.read_bytes()
            except PermissionError:
                # 状態JSONを差し替える瞬間の一時的な共有競合だけを再試行する。
                time.sleep(0.03)
            else:
                return obj(json.loads(raw))
        return obj(json.loads(windows.shared_bytes(path) if changing else path.read_bytes()))
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
    profile: str = 'phase71'

    @property
    def zero500(self) -> bool:
        """10万局から継続する計画かを返す。"""
        return self.profile == ZERO500

    @property
    def start(self) -> int:
        """固定する開始局数。"""
        return ZERO_START if self.zero500 else START

    @property
    def labels(self) -> tuple[str, ...]:
        """決定的な系列順。"""
        if self.zero500 and self.smoke in (None, 'all'):
            return tuple(ZERO_ORIGINS)
        return tuple(ORIGIN_SHA) if self.smoke is None else (f'{self.smoke}-701',)

    @property
    def milestones(self) -> tuple[int, ...]:
        """評価を実行する累積局数。"""
        if self.smoke is not None:
            return (self.start + self.smoke_games,)
        return ZERO_TARGETS if self.zero500 else TARGETS

    @property
    def stages(self) -> tuple[int, ...]:
        """取り込む基準結果と新規評価の節目。"""
        return (self.start, *self.milestones) if self.smoke is None else self.milestones

    @property
    def max_workers(self) -> int:
        """系列数に応じた実行上限。"""
        return 3 if self.zero500 else 6

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
        return (1024 if self.zero500 else 512) * 2**20 if self.smoke is None else 32 * 2**20

    def next_stop(self, games: int) -> int:
        """定期保存と評価節目を越えない次境界。"""
        interval = 1_000 if self.smoke is None else 10
        boundary = (games // interval + 1) * interval
        return min(boundary, next(mark for mark in self.milestones if mark > games))


def payload(plan: Plan) -> dict[str, object]:
    """変更を検出する設定とソースの記録。"""
    result: dict[str, object] = {
        'version': 1,
        'run_id': plan.run_id,
        'assets': str(plan.assets),
        'smoke': plan.smoke,
        'smoke_games': plan.smoke_games,
        'population_sha256': plan.population_sha,
        'labels': list(plan.labels),
        'start': plan.start,
        'milestones': list(plan.milestones),
        'interval': 1000 if plan.smoke is None else 10,
        'rate': RATE,
        'epsilon': EPSILON,
        'gamma': 1,
        'runtime': train._runtime(),
        'sources': fingerprint(),
    }
    if plan.zero500:
        result.update(version=2, profile=ZERO500)
    return result


def load(root: Path) -> tuple[Plan, str]:
    """初期化済みの固定計画と現在のソース・環境を照合する。"""
    x, digest = unseal(root / 'config.json')
    smoke = x.get('smoke')
    limit = x.get('smoke_games')
    profile = x.get('profile', 'phase71')
    if (
        (profile != 'phase71' and profile != ZERO500)
        or smoke
        not in ((None, 'zero', 'all') if profile == ZERO500 else (None, 'zero', 'pretrained'))
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
        str(profile),
    )
    expected = payload(plan)
    old = {**expected, 'sources': {**obj(expected['sources']), **_PHASE71_MANAGER}}
    if (
        not assets.is_absolute()
        or (x != expected and (plan.zero500 or x != old))
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


def _zero_origins(plan: Plan) -> Iterator[tuple[str, Origin]]:
    """Phase 7.1の確定モデル・乱数・設定を元の保存点から復元する。"""
    root = plan.assets / PHASE71
    if (
        unseal(root / 'config.json')[1]
        != '05a9dab8ae38155ef983d09720d59a38c1e8d3c924d79bed6247f23fff577e5c'
        or unseal(root / 'manifest.json')[1]
        != '7de9dc9551234de3be2bc6a8b76615173fd25f273ca92b4b7f8ec1d24f988a4f'
    ):
        raise RunError('Phase 7.1の確定設定またはmanifestが異なります。')
    relative, expected = _INITIALS['zero']
    initial = plan.assets / relative
    model = ntuple_io.load_model(initial)
    if model.file_sha256 != expected:
        raise RunError('全ゼロの初期モデルが異なります。')
    sources, runtime = train.sources(), train._runtime()
    for label in plan.labels:
        attempt, state_sha, model_sha = ZERO_ORIGINS[label]
        config = phase7_td.Config(RATE, EPSILON, int(label.split('-')[1]))
        compatible = {
            'initial': {'model_id': model.model_id, 'sha256': expected},
            'settings': train._settings(config),
            'sources': sources,
            'runtime': runtime,
        }
        folder = root / 'checkpoints' / label / f'attempt-{attempt}'
        mark = checkpoint(folder / 'checkpoint', compatible)
        job, _ = unseal(folder / 'job.json')
        if (
            mark.sha != state_sha
            or mark.model_sha != model_sha
            or mark.counts.games != ZERO_START
            or mark.previous is None
            or job
            != {
                'label': label,
                'from': mark.before.games,
                'parent': mark.previous,
                'until': ZERO_START,
            }
        ):
            raise RunError(f'{label} の10万局checkpointが異なります。')
        yield label, Origin(label, initial, config, compatible, mark)


def plan_origins(plan: Plan, *, audit: bool = False) -> dict[str, Origin]:
    """通常の復元では固定起点を、事前確認では直前までの連鎖も検証する。"""
    if not plan.zero500:
        return origins(plan.assets)
    selected = dict(_zero_origins(plan))
    if audit:
        original, _ = load(plan.assets / PHASE71)
        earlier = origins(plan.assets)
        for label, origin in selected.items():
            chain, _ = scan(original, earlier[label])
            if chain[-1] != origin.checkpoint:
                raise RunError(f'{label} のPhase 7.1連鎖が10万局の起点につながりません。')
    return selected


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


DISPLAY_DEFERRED = '進捗表示の更新を次回に延期'


def runtime_issues(folder: Path) -> list[dict[str, str]]:
    """ログを保存したまま、継続できた表示競合と起動再試行を集計する。"""
    issues = []
    for path in sorted(folder.glob('process*.log')):
        with path.open(encoding='utf-8', errors='replace') as stream:
            count = sum(line.startswith(DISPLAY_DEFERRED) for line in stream)
        if count:
            issues.append(
                {
                    'path': str(path),
                    'problem': f'進捗表示を延期 {count}回',
                    'recovery': 'display_only',
                }
            )
    for path in sorted(folder.glob('launch-failure-*.json')):
        event, _ = unseal(path)
        issue = {'path': str(path), 'problem': str(event['failure'])}
        if (folder / 'process.json').is_file():
            issue['recovery'] = 'launch_retry'
        issues.append(issue)
    return issues


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
            if plan.zero500 and (
                (attempt / 'job.json').exists() or (attempt / 'checkpoint' / 'state.json').exists()
            ):
                raise RunError(f'{origin.label} の区間記録が不正です（{attempt}）。') from failure
            problems.append({'path': str(attempt), 'problem': str(failure)})
    chain = [origin.checkpoint]
    while chain[-1].counts.games < plan.target:
        begin = chain[-1]
        children = []
        for attempt in possible.pop(begin.sha, []):
            try:
                children.append(accept(plan, origin, attempt, begin))
            except (OSError, ValueError, KeyError, RunError) as failure:
                if plan.zero500 and (attempt / 'checkpoint' / 'state.json').exists():
                    raise RunError(
                        f'{origin.label} の確定保存点が不正です（{attempt}）。'
                    ) from failure
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
        if plan.zero500 and any((a / 'checkpoint' / 'state.json').exists() for a in attempts):
            raise RunError(f'{origin.label} に親を照合できない確定保存点があります。')
        for attempt in attempts:
            issue: dict[str, object] = {
                'path': str(attempt),
                'problem': '確定済み連鎖につながっていません。',
            }
            if plan.zero500:
                job, _ = unseal(attempt / 'job.json')
                issue.update({'from': job.get('from'), 'until': job.get('until')})
            problems.append(issue)
    if plan.zero500:
        for issue in problems:
            end = issue.get('until')
            if isinstance(end, int) and chain[-1].counts.games >= end:
                issue['recovery'] = 'verified_replacement'
        for attempt in sorted(directory.glob('attempt-*')):
            if (attempt / 'process-exit.json').is_file() or not (
                attempt / 'process.json'
            ).is_file():
                for runtime_issue in runtime_issues(attempt):
                    recorded: dict[str, object] = dict(runtime_issue)
                    if (attempt / 'job.json').is_file():
                        job, _ = unseal(attempt / 'job.json')
                        if 'recovery' not in recorded and job.get('until') in {
                            mark.counts.games for mark in chain
                        }:
                            recorded['recovery'] = 'verified_replacement'
                    problems.append(recorded)
    return chain, problems
