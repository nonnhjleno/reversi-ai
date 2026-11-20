"""Phase 7の6系列を完局境界で止め、評価まで進めるWindows用CLI。"""

from __future__ import annotations

import argparse
import os
import signal
import statistics
import subprocess
import sys
import time
import traceback
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import uuid4

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import openings
from tools import evidence, phase7_eval, phase7_train as train
from tools import phase7_longrun_data as data
from tools import phase7_longrun_os as windows
from tools import phase7_longrun_reports as reporting
from tools.phase65_data import sha_file

_SCRIPT = Path(__file__).resolve()
_ACTIVE = {'STARTING', 'RUNNING', 'EVALUATING', 'STOPPING'}
_PROBLEM = (data.RunError, windows.Busy, ValueError, OSError, KeyError, TypeError)


def _timestamp() -> str:
    """証拠保存用にUTCの現在時刻を返す。"""
    return datetime.now(timezone.utc).isoformat()


def _argument(command: str, root: Path, location: Path, *args: str) -> list[str]:
    """同じ直接CPythonで内部プロセスを起動するargv。"""
    return [sys.executable, '-B', str(_SCRIPT), command, str(root), str(location), *args]


def _prepare(
    root: Path, assets: Path, smoke: str | None, limit: int, profile: str = 'phase71'
) -> tuple[dict[str, data.Origin], dict[str, str]]:
    """初期化前に起点、評価の固定資産、出力先を検査する。"""
    if root in (windows.extended(data.ROOT), assets) or any(
        root.is_relative_to(assets / name) or (assets / name).is_relative_to(root)
        for name in (
            data.PHASE7,
            data.PHASE71,
            'phase6-ntuple-20261118',
            'phase6-5-supervised-20261118',
        )
    ):
        raise data.RunError('出力はPhase 6・6.5・7の原証拠とは別の新しいフォルダにしてください。')
    if smoke and root == windows.extended(data.DEFAULT_ROOT):
        raise data.RunError('試験は --root で本番とは別の出力先を指定してください。')
    if profile == data.ZERO500:
        if root.is_relative_to(windows.extended(data.DEFAULT_ROOT)):
            raise data.RunError('50万局用に別の --root を指定してください。')
        if root.exists():
            raise data.RunError('実験先は既に存在します。既存実験は verify で確認してください。')
    needed = ((8 if profile == data.ZERO500 else 3) * 2**30) if smoke is None else (64 * 2**20)
    free = data.disk_check(root, needed, probe=True)
    probe = data.Plan(root, assets, 'preflight', smoke, limit, data.POP_SHA, profile)
    origins = data.plan_origins(probe, audit=True)
    source = assets / data.PHASE7 / 'opening-registration' / 'comparison.json'
    if sha_file(source) != data.POP_SHA:
        raise data.RunError('Phase 7の開発序盤とハッシュが一致しません。')
    if len(openings.load_population(source)['entries']) != 12:
        raise data.RunError('Phase 7の開発序盤12対が必要です。')
    for name in phase7_eval.BASELINES:
        phase7_eval._fixed(name)
    reference: dict[str, str] = {}
    if smoke is None:
        marks = {n: o.checkpoint for n, o in origins.items()}
        for job in reporting.matches(probe, probe.start):
            reporting.find_result(probe, job, marks)
            summary = reporting.reference_path(probe, job) / 'summary.json'
            reference[job.key] = sha_file(summary)
    print(f'空き容量 {free / 2**30:.2f} GiB。直接CPython 3.13.5。')
    for name, origin in origins.items():
        mark = origin.checkpoint
        print(
            f'{name} {mark.counts.games:,}局 照合済み model={mark.model_sha} state={mark.sha} ID={mark.model_id}'
        )
    return origins, reference


def init(root: Path, assets: Path, smoke: str | None, limit: int, profile: str = 'phase71') -> None:
    """新しい実験領域を発行する。"""
    if root.exists():
        raise data.RunError(
            f'出力先が既にあります（{root}）。その実験は status または resume を使ってください。'
        )
    origins, reference = _prepare(root, assets, smoke, limit, profile)
    root.mkdir(parents=True)
    for name in ('sessions', 'locks', 'checkpoints', 'evaluations', 'reports'):
        (root / name).mkdir()
    source = assets / data.PHASE7 / 'opening-registration' / 'comparison.json'
    opening = root / 'openings.json'
    if smoke is None:
        with opening.open('xb') as stream:
            stream.write(source.read_bytes())
    else:
        population = openings.load_population(source)
        population['entries'] = population['entries'][:1]
        population['population_size'] = 1
        population.pop('population_hash', None)
        population['population_hash'] = openings.digest(population)
        evidence.save_new(opening, evidence.encoded_json(openings.validate_population(population)))
    plan = data.Plan(root, assets, uuid4().hex, smoke, limit, sha_file(opening), profile)
    for label in plan.labels:
        (root / 'checkpoints' / label).mkdir()
    config = data.payload(plan)
    digest = train._digest(config)
    data.seal(
        root / 'manifest.json',
        {
            'config_sha256': digest,
            'origins': {name: origins[name].checkpoint.as_status() for name in plan.labels},
            'reference_summary_sha256': reference,
            'python_executable': sys.executable,
            'created_at_utc': _timestamp(),
        },
    )
    windows.replace_json(
        root / 'status.json',
        {
            'run_id': plan.run_id,
            'config_sha256': digest,
            'state': 'READY',
            'updated_at': _timestamp(),
            'target': plan.target,
            'start': plan.start,
            'mode': 'test' if smoke else 'production',
            'series': {label: origins[label].checkpoint.as_status() for label in plan.labels},
        },
    )
    data.seal(root / 'config.json', config)
    print(f'準備完了 READY。実験先 {windows.display(root)}')


def _snapshot(root: Path) -> dict[str, object]:
    """状態表示のハッシュを初期化済みの計画と照合する。"""
    snap = data.read(root / 'status.json', changing=True)
    config, digest = data.unseal(root / 'config.json')
    if snap.get('config_sha256') != digest or snap.get('run_id') != config.get('run_id'):
        raise data.RunError('status.jsonと実験の設定が一致しません。')
    return snap


def _live(owner: object) -> bool:
    """PIDと生成時刻が同じプロセスの生存を調べる。"""
    if owner is None:
        return False
    record = data.obj(owner)
    pid = record.get('pid')
    if type(pid) is not int or pid <= 0:
        raise data.RunError('管理PIDの記録が不正です。')
    return windows.identity(pid) == record


def _stop(session: Path) -> None:
    """この起動世代にだけ停止を要求する。"""
    try:
        (session / 'stop.request').touch(exist_ok=False)
    except FileExistsError:
        pass


def _session(root: Path, name: object) -> Path:
    """保存された起動識別子から専用ディレクトリを求める。"""
    if (
        not isinstance(name, str)
        or len(name) != 32
        or any(ch not in '0123456789abcdef' for ch in name)
    ):
        raise data.RunError('実行の識別子が不正です。')
    return root / 'sessions' / name


def _simple_status(snap: dict[str, object], state: str) -> None:
    """状態表示から必要な進捗と操作だけを選んで表示する。"""
    target = cast(int, snap['target'])
    start = cast(int, snap.get('start', data.START))
    if start == data.ZERO_START:
        print('Ranger Zero-TD 50万局実験')
    rows = data.obj(snap['series'])
    completed = 0
    active = []
    labels = {
        'RUNNING': '実行中',
        'STOPPING': '停止中',
        'COMPLETED': '完了',
        'WAITING': '待機中',
    }
    print(f'状態  {state}')
    for name, item in rows.items():
        row = data.obj(item)
        saved = cast(int, row['games'])
        current = cast(int, row.get('live_games', saved))
        completed += current - start if start == data.ZERO_START else saved - start
        row_state = str(row.get('state', 'WAITING'))
        if row_state in ('RUNNING', 'STOPPING'):
            active.append(name)
        if start == data.ZERO_START:
            ratio = (current - start) / (target - start)
            print(
                f'{name:<15} {current:>7,} / {target:,}局  保存 {saved:,}局  {ratio:>5.1%}  {labels.get(row_state, row_state)}'
            )
            continue
        print(
            f'{name:<15} 現在 {current:,}局  保存 {saved:,}局  {labels.get(row_state, row_state)}'
        )

    fraction = completed / (len(rows) * (target - start))
    filled = max(0, min(24, round(fraction * 24)))
    print(f'全体進捗  [{"#" * filled}{"-" * (24 - filled)}] {fraction:.1%}')
    print(f'同時稼働  {", ".join(active) if active else "なし"}')
    if snap.get('evaluation'):
        print(f'評価中  {snap["evaluation"]}')
    speed = snap.get('games_per_second')
    if start == data.ZERO_START and isinstance(stage := snap.get('latest_evaluation'), int):
        means = data.obj(snap.get('latest_scores', {}))
        scores = ' / '.join(f'{name} {cast(float, rate):.1%}' for name, rate in means.items())
        print(f'最後に完了した評価  {stage:,}局  {scores}')
    eta = snap.get('estimated_remaining_seconds')
    if isinstance(speed, (int, float)) and isinstance(eta, (int, float)):
        print(f'全体速度  {speed:.2f}局/秒')
        print(f'残り時間  約{eta / 3600:.1f}時間（学習分の推定。評価時間を含みません）')
    else:
        print('残り時間  学習速度を実測後に表示します。評価時間は別途かかります。')
    issues = cast(list[dict[str, object]], snap.get('issues', []))
    if start == data.ZERO_START:
        recovered = sum(bool(issue.get('recovery')) for issue in issues)
        if recovered:
            print(f'復旧済み  {recovered}件（詳細は status --verbose）')
        issues = [issue for issue in issues if not issue.get('recovery')]
    error = snap.get('error')
    if issues or error:
        print(f'エラー  あり（要確認 {len(issues)}件）')
        if error:
            print(f'  最後のエラー  {error}')
    else:
        print('エラー  なし')
    print('操作  停止: stop  |  再開: verify の後に resume  |  詳細: status --verbose')


def status(root: Path, *, verbose: bool = False) -> None:
    """状態表示と実プロセスの一致を確認する。読み取り専用。"""
    snap = _snapshot(root)
    state = str(snap['state'])
    live = _live(snap.get('owner'))
    if state in _ACTIVE:
        if not live:
            state = 'INTERRUPTED'
        elif (_session(root, snap['session']) / 'stop.request').is_file():
            state = 'STOPPING'
    elif live:
        state = 'FINISHING'
    if not verbose:
        _simple_status(snap, state)
        return
    print(f'実験 {snap["run_id"]}  {state}  更新 {snap["updated_at"]}')
    if snap.get('owner'):
        print(f'管理PID {data.obj(snap["owner"])["pid"]}  生存 {"あり" if live else "なし"}')
    target = cast(int, snap['target'])
    rows = data.obj(snap['series'])
    complete = 0
    for name, item in rows.items():
        row = data.obj(item)
        games = cast(int, row['games'])
        complete += games - cast(int, snap.get('start', data.START))
        observed = cast(int, row.get('live_games', games))
        print(
            f'{name} 完局 {observed:,}  保存 {games:,}/{target:,} ({games / target:.1%})  更新 {row["updates"]:,}'
        )
        print(f'  {row.get("state", "WAITING")}  {row["checkpoint"]}')
        rate = row.get('segment_games_per_second')
        if isinstance(rate, (int, float)):
            print(f'  直近確定区間 {rate:.2f}局/秒')
    if snap.get('max_workers') is not None:
        print(f'同時実行上限 {snap["max_workers"]}')
    if snap.get('evaluation'):
        print(f'評価 {snap["evaluation"]}')
    print(f'追加学習の全体進捗 {complete / (len(rows) * (target - data.START)):.1%}')
    speed = snap.get('games_per_second')
    eta = snap.get('estimated_remaining_seconds')
    if isinstance(speed, (int, float)) and isinstance(eta, (int, float)):
        print(f'実測 {speed:.2f}局/秒。学習の残り約 {eta / 3600:.2f}時間（評価を除く推定）。')
    if snap.get('issues'):
        print(
            f'中断・要確認 {len(cast(list[object], snap["issues"]))}件。詳細は verify と各試行ログ。'
        )
    if snap.get('error'):
        print(f'最後の異常 {snap["error"]}')
    if state == 'INTERRUPTED':
        print(
            '管理プロセスが終了しています。子の処理が終わってから verify と resume を実行してください。'
        )


def stop(root: Path) -> None:
    """停止要求の受付と停止済みを区別して返す。"""
    snap = _snapshot(root)
    if not _live(snap.get('owner')):
        if snap['state'] in _ACTIVE:
            print(
                '管理プロセスが既に終了しています。status と verify で保存状態を確認してください。'
            )
        else:
            print(f'動作中の管理プロセスはありません（{snap["state"]}）。')
        return
    session = _session(root, snap['session'])
    _stop(session)
    print('STOPPING 停止を要求しました。status で停止完了とPID終了を確認してください。')


def _chains(
    plan: data.Plan, origins: dict[str, data.Origin]
) -> tuple[dict[str, list[data.Checkpoint]], list[dict[str, object]]]:
    """系列ごとの確定連鎖と、採用できなかった試行を集める。"""
    chains = {}
    issues: list[dict[str, object]] = []
    for label in plan.labels:
        chain, problems = data.scan(plan, origins[label])
        chains[label] = chain
        issues.extend({'series': label, **problem} for problem in problems)
    return chains, issues


def _rollback_notices(
    previous: dict[str, object], chains: dict[str, list[data.Checkpoint]]
) -> list[dict[str, object]]:
    """欠落・破損で確定局数が表示上の保存点より戻る場合に範囲を示す。"""
    notices = []
    rows = data.obj(previous['series'])
    for label, chain in chains.items():
        checkpoint = chain[-1]
        saved = data.obj(rows[label])['games']
        if type(saved) is int and saved > checkpoint.counts.games:
            notices.append(
                {
                    'series': label,
                    'path': windows.display(checkpoint.path),
                    'from': checkpoint.counts.games,
                    'until': saved,
                    'problem': f'前回の保存表示は{saved:,}局。直前の確定状態から{saved - checkpoint.counts.games:,}局を再実行します。',
                }
            )
    return notices


def _worker_request(root: Path, folder: Path) -> tuple[data.Plan, dict[str, object], Path]:
    """別プロセスに渡された固定計画と起動世代を照合する。"""
    plan, digest = data.load(root)
    request, _ = data.unseal(folder / 'dispatch.json')
    if request['config'] != digest:
        raise data.RunError('子プロセスの実験設定が違います。')
    session = _session(root, request['session'])
    if data.unseal(session / 'owner.json')[0] != request['owner']:
        raise data.RunError('管理プロセスの記録が違います。')
    return plan, request, session


def _worker(root: Path, folder: Path) -> None:
    """系列または評価のロックを保持して区間を一度実行する。"""
    plan, request, session = _worker_request(root, folder)
    label = str(request['label'])
    kind = request['kind']
    if label not in plan.labels or kind not in ('train', 'eval'):
        raise data.RunError('子プロセスの系列または処理が不正です。')
    lock = label if kind == 'train' else 'evaluation'

    def requested() -> bool:
        """停止要求または管理プロセスの消失を完局ごとに調べる。"""
        return (session / 'stop.request').exists() or not _live(request['owner'])

    with windows.lease(root / 'locks' / lock):
        if requested():
            data.record_skipped(folder)
            return
        data.disk_check(root, plan.minimum_free)
        checked = data.plan_origins(plan)
        if kind == 'train':
            origin = checked[label]
            begin = data.checkpoint(Path(str(request['begin'])), origin.compatible)
            job, _ = data.unseal(folder / 'job.json')
            if (
                begin.sha != job['parent']
                or begin.counts.games != job['from']
                or job['label'] != label
                or plan.next_stop(begin.counts.games) != job['until']
            ):
                raise data.RunError(f'{label} の学習要求と親checkpointが一致しません。')
            last = time.monotonic()

            def progress(count: train.Counts) -> None:
                """一定間隔で未確定の完局数を通知する。"""
                nonlocal last
                if time.monotonic() - last >= 1:
                    data.disk_check(root, plan.minimum_free)
                    try:
                        windows.replace_json(folder / 'live.json', count._asdict())
                    except windows.DisplayBusy as failure:
                        print(
                            f'{data.DISPLAY_DEFERRED} {count.games:,}局 {failure}',
                            file=sys.stderr,
                            flush=True,
                        )
                    last = time.monotonic()

            try:
                train.run(
                    origin.initial,
                    folder / 'checkpoint',
                    origin.config,
                    job['until'],
                    resume=begin.path,
                    stop_requested=requested,
                    on_game=progress,
                    longrun=plan.zero500,
                )
            except train.StoppedBeforeGame:
                data.record_skipped(folder)
                return
        else:
            stage = cast(int, request['stage'])
            match = reporting.Match(stage, label, str(request['opponent']))
            if match not in reporting.matches(plan, stage):
                raise data.RunError('登録されていない評価です。')
            source = data.obj(request['marks'])
            marks = {
                name: data.checkpoint(
                    Path(str(path)), checked[name.removesuffix('@origin')].compatible
                )
                for name, path in source.items()
            }
            if (
                match.opponent == 'origin100k'
                and marks[f'{label}@origin'] != checked[label].checkpoint
            ):
                raise data.RunError(f'{label} の評価相手が登録済みの10万局モデルと異なります。')
            a, b = reporting.match_inputs(match, marks)
            if requested():
                data.record_skipped(folder)
            else:
                phase7_eval.match(a.model, b, plan.population, folder / 'match')


@dataclass
class Child:
    """管理プロセスが起動した処理。"""

    folder: Path
    process: subprocess.Popen[bytes]
    label: str


class Manager:
    """単一の管理プロセスで学習区間、節目評価と停止を順序づける。"""

    def __init__(
        self,
        plan: data.Plan,
        digest: str,
        session: Path,
        owner: windows.Identity,
        origins: dict[str, data.Origin],
        workers: int,
    ) -> None:
        """検証済みの実験と並列上限を保持する。"""
        self.plan, self.digest, self.session, self.owner = plan, digest, session, owner
        self.origins, self.workers = origins, workers
        self.chains, self.issues = _chains(plan, origins)
        self.active: list[Child] = []
        self.started = time.monotonic()
        self.evaluation_seconds = 0.0
        self.evaluation_started: float | None = None
        self.initial_games = sum(c[-1].counts.games for c in self.chains.values())
        self.evaluating: str | None = None
        self.latest_evaluation: int | None = None
        self.latest_scores: dict[str, float] = {}
        self.error: str | None = None

    @property
    def stopping(self) -> bool:
        """この起動世代への停止要求を返す。"""
        return (self.session / 'stop.request').exists()

    def publish(self, state: str) -> None:
        """確定済みと実行中の局数を区別して状態表示に保存する。"""
        rows = {}
        for name, chain in self.chains.items():
            latest = chain[-1]
            row = latest.as_status()
            row['state'] = 'COMPLETED' if latest.counts.games == self.plan.target else 'WAITING'
            row['segment_games_per_second'] = latest.metrics['games_per_second']
            row['post_origin_seconds'] = sum(cast(float, c.metrics['seconds']) for c in chain[1:])
            rows[name] = row
        if self.evaluating is None:
            for child in self.active:
                live = child.folder / 'live.json'
                rows[child.label]['state'] = 'STOPPING' if self.stopping else 'RUNNING'
                if live.is_file():
                    try:
                        rows[child.label]['live_games'] = data.read(live, changing=True)['games']
                    except data.RunError as failure:
                        cause = failure.__cause__
                        if not isinstance(cause, OSError) or not windows.sharing_error(cause):
                            raise
        games = sum(cast(int, row.get('live_games', row['games'])) for row in rows.values())
        elapsed = time.monotonic() - self.started
        evaluating = self.evaluation_started
        training_seconds = elapsed - self.evaluation_seconds
        if evaluating is not None:
            training_seconds -= time.monotonic() - evaluating
        speed = (games - self.initial_games) / training_seconds if training_seconds > 1 else 0.0
        remaining = self.plan.target * len(rows) - games
        windows.replace_json(
            self.plan.root / 'status.json',
            {
                'run_id': self.plan.run_id,
                'config_sha256': self.digest,
                'updated_at': _timestamp(),
                'state': state,
                'target': self.plan.target,
                'start': self.plan.start,
                'owner': self.owner,
                'session': self.session.name,
                'mode': 'test' if self.plan.smoke else 'production',
                'max_workers': self.workers,
                'series': rows,
                'elapsed_seconds': elapsed,
                'games_per_second': speed if speed > 0 else None,
                'estimated_remaining_seconds': remaining / speed if speed > 0 else None,
                'evaluation': self.evaluating,
                'latest_evaluation': self.latest_evaluation,
                'latest_scores': self.latest_scores,
                'issues': self.issues,
                'error': self.error,
            },
        )

    def spawn(self, folder: Path, label: str, request: dict[str, object]) -> Child:
        """試行固有のログと保存先で子プロセスを起動する。"""
        data.disk_check(self.plan.root, self.plan.minimum_free)
        args = _argument('_worker', self.plan.root, folder)
        data.seal(
            folder / 'dispatch.json',
            {
                **request,
                'session': self.session.name,
                'owner': self.owner,
                'label': label,
                'config': self.digest,
                'argv': args,
            },
        )
        retries: list[str] = []
        for seconds in (0.25, 0.75, None):
            name = 'process.log' if not retries else f'process-retry-{len(retries)}.log'
            try:
                proc = windows.child(args, data.ROOT, folder / name)
            except windows.LaunchBusy as failure:
                if not self.plan.zero500:
                    raise
                retries.append(str(failure))
                data.seal(
                    folder / f'launch-failure-{len(retries)}.json',
                    {'failure': str(failure), 'at': _timestamp()},
                )
                if seconds is None or self.stopping:
                    raise
                # 再試行前に全設定と、次に読む確定保存点を照合する。
                if data.load(self.plan.root)[1] != self.digest:
                    raise data.RunError('子プロセスの起動再試行前に実験設定が変化しました。')
                if request['kind'] == 'train':
                    begin = self.chains[label][-1]
                    if data.checkpoint(begin.path, self.origins[label].compatible) != begin:
                        raise data.RunError(f'{label} の保存点が子プロセス再起動前に変化しました。')
                else:
                    for key, path in data.obj(request['marks']).items():
                        series = key.removesuffix('@origin')
                        expected = next(c for c in self.chains[series] if str(c.path) == path)
                        if (
                            data.checkpoint(expected.path, self.origins[series].compatible)
                            != expected
                        ):
                            raise data.RunError(
                                f'{series} の評価モデルが子プロセス再起動前に変化しました。'
                            )
                time.sleep(seconds)
            else:
                break
        item = Child(folder, proc, label)
        self.active.append(item)
        data.seal(
            folder / 'process.json', {'pid': proc.pid, 'identity': windows.identity(proc.pid)}
        )
        return item

    def outcome(self, child: Child) -> int:
        """終了済み子プロセスの実終了値を記録する。"""
        rc = child.process.poll()
        if rc is None:
            raise data.RunError('実行中の子プロセスは確定できません。')
        self.active.remove(child)
        data.seal(
            child.folder / 'process-exit.json', {'exit_code': rc, 'finished_at': _timestamp()}
        )
        if self.plan.zero500:
            for issue in data.runtime_issues(child.folder):
                self.issues.append(dict(issue))
        return rc

    def train_to(self, target: int) -> None:
        """次の節目まで、系列を決定的な順序で同時実行する。"""
        while True:
            for child in self.active[:]:
                if child.process.poll() is None:
                    continue
                rc = self.outcome(child)
                file = child.folder / 'checkpoint' / 'state.json'
                if rc != 0 or (not file.exists() and not self.stopping):
                    raise data.RunError(
                        f'{child.label} の学習が異常終了しました（終了値 {rc}、{child.folder}）。'
                    )
                if file.exists():
                    name = child.label
                    chain = self.chains[name]
                    chain.append(
                        data.accept(self.plan, self.origins[name], child.folder, chain[-1])
                    )
            if self.stopping:
                self.publish('STOPPING')
                if not self.active:
                    return
            else:
                for label, chain in self.chains.items():
                    if len(self.active) >= self.workers or self.stopping:
                        break
                    begin = chain[-1]
                    if begin.counts.games >= target or any(c.label == label for c in self.active):
                        continue
                    folder = self.plan.root / 'checkpoints' / label / f'attempt-{uuid4().hex}'
                    folder.mkdir()
                    data.seal(
                        folder / 'job.json',
                        {
                            'label': label,
                            'from': begin.counts.games,
                            'parent': begin.sha,
                            'until': self.plan.next_stop(begin.counts.games),
                        },
                    )
                    self.spawn(folder, label, {'kind': 'train', 'begin': str(begin.path)})
                if not self.active and all(
                    c[-1].counts.games >= target for c in self.chains.values()
                ):
                    return
                self.publish('RUNNING')
            time.sleep(0.5)

    def evaluate(self, stage: int) -> None:
        """1つの評価だけを起動し、未完了試行は新しい出力で再取得する。"""
        self.evaluation_started = time.monotonic()
        marks = reporting.stage_marks(self.plan, self.chains, stage)
        scores: dict[str, list[float]] = {}
        for job in reporting.matches(self.plan, stage):
            if self.stopping:
                break
            existing, issues = reporting.find_result(self.plan, job, marks)
            self.issues.extend({'evaluation': job.key, **item} for item in issues)
            if existing is not None:
                scores.setdefault(job.opponent, []).append(cast(float, existing['point_rate']))
                continue
            self.evaluating = f'{stage} {job.key}'
            folder = (
                self.plan.root / 'evaluations' / str(stage) / job.key / f'attempt-{uuid4().hex}'
            )
            folder.mkdir(parents=True)
            paths = {job.label: str(marks[job.label].path)}
            if (name := job.opponent_mark) is not None:
                paths[name] = str(marks[name].path)
            child = self.spawn(
                folder,
                job.label,
                {
                    'kind': 'eval',
                    'stage': stage,
                    'opponent': job.opponent,
                    'marks': paths,
                },
            )
            while child.process.poll() is None:
                self.publish('STOPPING' if self.stopping else 'EVALUATING')
                time.sleep(0.5)
            rc = self.outcome(child)
            if rc != 0:
                raise data.RunError(f'評価 {job.key} が異常終了しました（終了値 {rc}、{folder}）。')
            if not (folder / 'match' / 'summary.json').is_file() and self.stopping:
                break
            verified = reporting.verified_match(job, marks, folder / 'match', self.plan.population)
            scores.setdefault(job.opponent, []).append(cast(float, verified['point_rate']))
        self.evaluating = None
        self.evaluation_seconds += time.monotonic() - self.evaluation_started
        self.evaluation_started = None
        if not self.stopping:
            self.latest_evaluation = stage
            self.latest_scores = {name: statistics.mean(values) for name, values in scores.items()}

    def drain(self) -> None:
        """異常時に新しい仕事を止め、起動済み処理の解放を待つ。"""
        _stop(self.session)
        for child in self.active[:]:
            child.process.wait()
            self.outcome(child)


def _validate_manifest(plan: data.Plan, digest: str, origins: dict[str, data.Origin]) -> None:
    """実験の起点が初期化時と同じであることを確かめる。"""
    manifest, _ = data.unseal(plan.root / 'manifest.json')
    expected = {name: origins[name].checkpoint.as_status() for name in plan.labels}
    if manifest['config_sha256'] != digest or manifest['origins'] != expected:
        raise data.RunError('manifest.jsonの起点または設定ハッシュが一致しません。')
    reference = {}
    if plan.smoke is None:
        for match in reporting.matches(plan, plan.start):
            path = reporting.reference_path(plan, match) / 'summary.json'
            reference[match.key] = sha_file(path)
    if manifest['reference_summary_sha256'] != reference:
        raise data.RunError(f'{plan.start:,}局の基準評価が初期化時と違います。')


def _manage(root: Path, session: Path, workers: int, intent: str) -> None:
    """全系列の排他を確認してから新しい起動世代を公開する。"""
    plan, digest = data.load(root)
    with windows.lease(root / 'locks' / 'manager'):
        with ExitStack() as locks:
            for name in (*plan.labels, 'evaluation'):
                locks.enter_context(windows.lease(root / 'locks' / name))
        previous = _snapshot(root)
        if intent == 'start' and previous['state'] != 'READY':
            raise data.RunError(
                '初期化済みの新規実験に start を使います。途中からは resume を使ってください。'
            )
        owner = windows.identity(os.getpid())
        if owner is None:
            raise data.RunError('現在の管理プロセスを識別できません。')
        data.seal(session / 'owner.json', owner)
        origins = data.plan_origins(plan)
        _validate_manifest(plan, digest, origins)
        manager = Manager(plan, digest, session, owner, origins, workers)
        notices = _rollback_notices(previous, manager.chains)
        if plan.zero500 and notices:
            raise data.RunError(
                '以前の進捗に対応する確定保存点が欠けています。verify で確認してください。'
            )
        manager.issues.extend(notices)
        if plan.zero500:
            for label, chain in manager.chains.items():
                observed = data.obj(data.obj(previous['series'])[label])
                if not any(
                    all(observed.get(k) == v for k, v in c.as_status().items()) for c in chain
                ):
                    raise data.RunError(f'{label} の保存済み進捗が確定連鎖と一致しません。')
        for issue in manager.issues:
            start = issue.get('from')
            end = issue.get('until')
            span = f'{start}局→{end}局' if start is not None else '履歴'
            print(f'過去の試行 {span} {issue["path"]} {issue["problem"]}', flush=True)

        def interrupt(signum: int, frame: object) -> None:
            """前景運転のCtrl+Cを安全停止へ変換する。"""
            _stop(session)

        old_handler = signal.signal(signal.SIGINT, interrupt)
        try:
            manager.publish('STARTING')
            data.seal(
                session / 'ready.json',
                {
                    'pid': owner['pid'],
                    'created': owner['created'],
                    'run_id': plan.run_id,
                    'root': str(root),
                },
            )
            if plan.zero500 and plan.smoke is None:
                manager.evaluate(plan.start)
            for stage in plan.milestones:
                manager.train_to(stage)
                if manager.stopping:
                    break
                manager.evaluate(stage)
                if manager.stopping:
                    break
                if plan.zero500 and stage != plan.target:
                    reporting.reports(plan, manager.chains, manager.issues)
            manager.evaluating = None
            if not manager.stopping:
                reporting.reports(plan, manager.chains, manager.issues)
            manager.publish('STOPPED' if manager.stopping else 'COMPLETED')
        except Exception as failure:
            manager.error = f'{type(failure).__name__} {failure}'
            manager.drain()
            # 失敗時も保存まで到達した子の確定状態を見せる。
            try:
                manager.chains, more = _chains(plan, origins)
                manager.issues.extend(more)
            except _PROBLEM as scan_failure:
                manager.error += f' / 保存状態の再確認にも失敗 {scan_failure}'
            manager.publish('FAILED')
            raise
        finally:
            signal.signal(signal.SIGINT, old_handler)


def _launch(root: Path, workers: int, intent: str) -> None:
    """前景またはコンソールから独立する管理プロセスを起動する。"""
    snap = _snapshot(root)
    if _live(snap.get('owner')):
        raise windows.Busy('管理プロセスは既に動作中です。status で確認してください。')
    if intent == 'start' and snap['state'] != 'READY':
        raise data.RunError('途中の実験には resume を使ってください。')
    session = _session(root, uuid4().hex)
    session.mkdir()
    if intent == 'run':
        print(f'前景運転 PID {os.getpid()}、実験先 {windows.display(root)}', flush=True)
        _manage(root, session, workers, intent)
        status(root)
        return
    log = session / 'manager.log'
    args = _argument('_manager', root, session, str(workers), intent)
    data.seal(
        session / 'launch.json', {'argv': args, 'created_at': _timestamp(), 'workers': workers}
    )
    process = windows.child(args, data.ROOT, log, detached=True)
    until = time.monotonic() + 90
    while time.monotonic() < until:
        if (session / 'ready.json').is_file():
            record, _ = data.unseal(session / 'ready.json')
            owner = {'pid': record['pid'], 'created': record['created']}
            if (
                process.pid == owner['pid']
                and _live(owner)
                and _snapshot(root).get('session') == session.name
            ):
                print(
                    f'管理PID {process.pid} 起動確認済み。実験先 {windows.display(root)}。ログ {windows.display(log)}'
                )
                return
        rc = process.poll()
        if rc is not None:
            raise data.RunError(
                f'管理プロセスの起動後確認に失敗しました（終了値 {rc}）。{log} を見てください。'
            )
        time.sleep(0.1)
    raise data.RunError(
        f'初期化の応答がまだありません。再度 start する前に status と {log} を確認してください。'
    )


def verify(root: Path) -> int:
    """確定連鎖、進捗、評価の整合性を照合して問題の範囲を示す。"""
    snap = _snapshot(root)
    plan, digest = data.load(root)
    origins = data.plan_origins(plan)
    _validate_manifest(plan, digest, origins)
    chains, errors = _chains(plan, origins)
    all_current = True
    for name, chain in chains.items():
        row = data.obj(data.obj(snap['series'])[name])
        valid = any(
            all(row.get(key) == value for key, value in c.as_status().items()) for c in chain
        )
        if not valid:
            errors.append({'series': name, 'problem': '進捗が確定済みの連鎖と一致しません。'})
        elif row['state_sha256'] != chain[-1].sha:
            all_current = False
        print(f'{name} 最終確定 {chain[-1].counts.games:,}局 SHA-256 {chain[-1].model_sha}')
    incomplete = 0
    for stage in plan.stages:
        marks = reporting.stage_marks(plan, chains, stage)
        if not all(label in marks for label in plan.labels):
            incomplete += len(reporting.matches(plan, stage))
            continue
        for job in reporting.matches(plan, stage):
            value, issues = reporting.find_result(plan, job, marks)
            if value is None:
                incomplete += 1
            errors.extend({'evaluation': job.key, **issue} for issue in issues)
    if not all_current:
        print('進捗表示より新しい正常checkpointがあります。再開時は確定状態を使います。')
    if snap['state'] == 'COMPLETED' and (
        incomplete or any(c[-1].counts.games != plan.target for c in chains.values())
    ):
        errors.append({'problem': 'COMPLETEDの表示に対応する学習または評価が欠けています。'})
    for issue in errors:
        print(evidence.encoded_json(issue).strip())
    unresolved = [issue for issue in errors if not (plan.zero500 and issue.get('recovery'))]
    if plan.zero500:
        print(f'過去の復旧済み記録 {len(errors) - len(unresolved)}件。')
    print(f'確定状態を検証しました。未評価 {incomplete}件、要確認 {len(unresolved)}件。')
    return int(bool(unresolved))


def report(root: Path) -> None:
    """学習を行わず、保存状態から新しい集計を作る。"""
    plan, digest = data.load(root)
    origins = data.plan_origins(plan)
    _validate_manifest(plan, digest, origins)
    chains, errors = _chains(plan, origins)
    folder = reporting.reports(plan, chains, errors)
    print(f'結果を保存しました。{windows.display(folder)}')


def _range(text: str, low: int, high: int) -> int:
    """CLIの整数範囲を制限する。"""
    try:
        value = int(text)
    except ValueError as failure:
        raise argparse.ArgumentTypeError('整数を指定してください。') from failure
    if not low <= value <= high:
        raise argparse.ArgumentTypeError(f'{low}〜{high}を指定してください。')
    return value


def parser() -> argparse.ArgumentParser:
    """公開コマンドとパス指定を定義する。"""
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='command', required=True)
    for name, help_text in (
        ('doctor', '環境と6起点を確認'),
        ('init', '新しい実験を準備'),
        ('start', '独立して開始'),
        ('run', 'この画面で実行'),
        ('status', '進捗を表示'),
        ('stop', '安全停止を要求'),
        ('resume', '最後の確定状態から再開'),
        ('report', '保存結果を集計'),
        ('verify', '保存内容を照合'),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument(
            '--root',
            type=Path,
            default=data.DEFAULT_ROOT,
            help='実験フォルダ。相対パスは現在のフォルダから。',
        )
        if name in ('doctor', 'init'):
            command.add_argument('--assets', type=Path, help='元の evaluation_results フォルダ。')
            command.add_argument(
                '--smoke',
                choices=['zero', 'pretrained', 'all'],
                help='短い試験。all は50万局計画専用。',
            )
            command.add_argument('--smoke-games', type=lambda s: _range(s, 20, 100), default=40)
            command.add_argument('--profile', choices=['phase71', data.ZERO500], default='phase71')
        if name in ('doctor', 'start', 'resume', 'run'):
            command.add_argument('--max-workers', type=lambda s: _range(s, 1, 6), default=2)
        if name == 'status':
            command.add_argument('--verbose', action='store_true', help='保存先、PIDなども表示')
    return cli


def main() -> int:
    """日本語の操作結果と意味のある終了コードを返す。"""
    internal = len(sys.argv) > 1 and sys.argv[1] in ('_worker', '_manager')
    try:
        if internal:
            data.available()
            mode, root_text, location, *extra = sys.argv[1:]
            if mode == '_worker':
                _worker(Path(root_text), Path(location))
            else:
                _manage(Path(root_text), Path(location), _range(extra[0], 1, 6), extra[1])
            return 0
        args = parser().parse_args()
        data.available()
        root = windows.extended(args.root)
        command = args.command
        if command in ('doctor', 'init'):
            assets = windows.extended(args.assets or data.asset_directory())
            zero500 = args.profile == data.ZERO500
            if (zero500 and args.smoke == 'pretrained') or (not zero500 and args.smoke == 'all'):
                raise data.RunError('指定された試験系列はこの実験計画では使えません。')
            args.max_workers = getattr(args, 'max_workers', 2)
            if zero500 and args.max_workers > 3:
                raise data.RunError('50万局計画の並列数は1〜3です。')
            if command == 'doctor':
                _prepare(root, assets, args.smoke, args.smoke_games, args.profile)
                print(
                    f'同時実行上限 {args.max_workers}。論理CPU {os.cpu_count()}。確認完了。実験先 {root}'
                )
            else:
                init(root, assets, args.smoke, args.smoke_games, args.profile)
        elif command in ('start', 'resume', 'run'):
            plan, _ = data.load(root)
            workers = args.max_workers
            if workers > plan.max_workers:
                raise data.RunError(f'この実験の並列数は1〜{plan.max_workers}です。')
            _launch(root, workers, command)
        elif command == 'verify':
            return verify(root)
        elif command == 'status':
            status(root, verbose=args.verbose)
        else:
            {'stop': stop, 'report': report}[command](root)
        return 0
    except _PROBLEM as failure:
        print(f'操作できません。{failure}', file=sys.stderr, flush=True)
        if internal:
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
