"""Phase 7.1のロック、連鎖、局境界停止、集計を短いケースで検証する。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import namedtuple
from dataclasses import replace
import pytest

from reversi_player import ntuple, ntuple_io
from tools import phase7_longrun as cli
from tools import phase7_longrun_data as data
from tools import phase7_longrun_os as windows
from tools import phase7_longrun_reports as reporting
from tools import phase7_td, phase7_train as train


def _state(path):
    """チェックポイントから比較対象を読む。"""
    return data.read(path / 'state.json')['payload']


def test_zero500_plan_and_fingerprint(tmp_path, monkeypatch):
    """開始・保存・評価の節目を固定し、再開時に変更を拒否する。"""
    plan = data.Plan(tmp_path, tmp_path, 'test', None, 40, '', data.ZERO500)
    plan.population.write_bytes(b'[]')
    plan = replace(plan, population_sha=data.sha_file(plan.population))
    assert plan.labels == ('zero-701', 'zero-702', 'zero-703')
    assert plan.stages == (100_000, 150_000, 200_000, 300_000, 400_000, 500_000)
    assert plan.next_stop(149_999) == 150_000
    assert plan.next_stop(150_000) == 151_000
    assert plan.max_workers == 3
    assert len(reporting.matches(plan, plan.start)) == 6
    assert len(reporting.matches(plan, plan.target)) == 9
    assert all(job.label.startswith('zero-') for job in reporting.matches(plan, plan.target))
    args = cli.parser().parse_args(['init', '--profile', 'zero-500k', '--smoke', 'all'])
    assert args.profile == data.ZERO500 and args.smoke == 'all'

    monkeypatch.setattr(data, 'fingerprint', lambda: {'test-source': 'aaa'})
    data.seal(plan.root / 'config.json', data.payload(plan))
    assert data.load(plan.root)[0] == plan
    monkeypatch.setattr(data, 'fingerprint', lambda: {'test-source': 'bbb'})
    with pytest.raises(data.RunError, match='ソース'):
        data.load(plan.root)


@pytest.mark.skipif(os.name != 'nt', reason='Windowsのファイル共有モードを検証する')
def test_display_sharing_conflict_is_bounded(tmp_path):
    """別プロセスが削除共有なしで保持すると、旧世代を残して処理が戻る。"""
    path = tmp_path / '進捗 live.json'
    windows.replace_json(path, {'games': 3})
    ready = tmp_path / 'ready'
    cmd = (
        'import sys; from pathlib import Path; '
        'f=open(sys.argv[1], "rb"); Path(sys.argv[2]).touch(); sys.stdin.read(); f.close()'
    )
    with subprocess.Popen(
        [sys.executable, '-B', '-c', cmd, str(path), str(ready)], stdin=subprocess.PIPE
    ) as blocker:
        try:
            deadline = time.monotonic() + 10
            while not ready.is_file():
                assert blocker.poll() is None and time.monotonic() < deadline
                time.sleep(0.025)
            start = time.monotonic()
            with pytest.raises(windows.DisplayBusy):
                windows.replace_json(path, {'games': 4})
            assert time.monotonic() - start < 8
            assert data.read(path, changing=True) == {'games': 3}
        finally:
            blocker.communicate(timeout=5)
    windows.replace_json(path, {'games': 5})
    assert data.read(path, changing=True) == {'games': 5}
    assert sorted(p.name for p in tmp_path.iterdir()) == ['ready', path.name]


def test_zero500_checkpoint_corruption_is_not_rollback(tmp_path, short_checkpoint):
    """確定ファイルの破損が欠落扱いされないことを確認する。"""
    plan = data.Plan(tmp_path, tmp_path, 'test', 'zero', 20, '', data.ZERO500)
    origin = short_checkpoint
    attempt = tmp_path / 'checkpoints' / origin.label / 'attempt-broken'
    folder = attempt / 'checkpoint'
    folder.mkdir(parents=True)
    data.seal(
        attempt / 'job.json',
        {
            'label': origin.label,
            'from': origin.checkpoint.counts.games,
            'parent': origin.checkpoint.sha,
            'until': plan.next_stop(origin.checkpoint.counts.games),
        },
    )
    chain, issues = data.scan(plan, origin)
    assert chain == [origin.checkpoint] and len(issues) == 1
    (folder / 'state.json').write_text('{}', encoding='utf-8')
    with pytest.raises(data.RunError, match='確定保存点'):
        data.scan(plan, origin)


def test_zero500_retries_only_a_child_that_never_started(tmp_path, short_checkpoint, monkeypatch):
    """既知の起動競合は復元を再照合し、子が起動するまで同じ区間を一度だけ投入する。"""
    origin = short_checkpoint
    plan = data.Plan(tmp_path, tmp_path, 'test', 'zero', 20, '', data.ZERO500)
    owner = {'pid': 123, 'created': 456}
    manager = cli.Manager(plan, 'config', tmp_path, owner, {origin.label: origin}, 1)
    attempt = tmp_path / 'attempt-1'
    attempt.mkdir()
    calls = []
    restored = []
    fake_process = namedtuple('Process', 'pid')(1234)

    def child(_args, _cwd, log):
        calls.append(log.name)
        if len(calls) == 1:
            raise windows.LaunchBusy('test sharing violation')
        return fake_process

    def checkpoint(path, compatibility):
        restored.append((path, compatibility))
        return origin.checkpoint

    monkeypatch.setattr(windows, 'child', child)
    monkeypatch.setattr(windows, 'identity', lambda _pid: None)
    monkeypatch.setattr(data, 'disk_check', lambda *_args: 0)
    monkeypatch.setattr(data, 'load', lambda _root: (plan, 'config'))
    monkeypatch.setattr(data, 'checkpoint', checkpoint)
    monkeypatch.setattr(cli.time, 'sleep', lambda _seconds: None)
    launched = manager.spawn(attempt, origin.label, {'kind': 'train'})
    assert launched.process == fake_process
    assert len(manager.active) == 1
    assert calls == ['process.log', 'process-retry-1.log']
    assert restored == [(origin.checkpoint.path, origin.compatible)]
    assert data.runtime_issues(attempt)[0]['recovery'] == 'launch_retry'

    attempt2 = tmp_path / 'attempt-2'
    attempt2.mkdir()
    monkeypatch.setattr(windows, 'child', lambda *_args: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError, match='disk'):
        manager.spawn(attempt2, origin.label, {'kind': 'train'})
    assert len(manager.active) == 1
    assert not list(attempt2.glob('launch-failure-*'))


def _variant(path, edit):
    """意図した破損内容だけを変え、内容ハッシュを付け直す。"""
    entry = path / 'state.json'
    doc = json.loads(entry.read_bytes())
    edit(doc['payload'])
    doc['payload_sha256'] = train._digest(doc['payload'])
    entry.write_text(json.dumps(doc), encoding='utf-8')


@pytest.fixture(scope='module')
def short_checkpoint(tmp_path_factory):
    """CIに本番の証拠を持ち込まず、同じ形式の小型checkpointを作る。"""
    root = tmp_path_factory.mktemp('td-small')
    initial = root / 'zero.json'
    ntuple_io.save_model(initial, ntuple.NtupleModel.zeros())
    config = phase7_td.Config(data.RATE, data.EPSILON, 701)
    checkpoint = root / 'two'
    saved = train.run(initial, checkpoint, config, 2)
    return data.Origin(
        'zero-701',
        initial,
        config,
        saved['compatibility'],
        data.checkpoint(checkpoint, saved['compatibility']),
    )


def test_cli_commands_and_worker_limit(tmp_path):
    """全公開コマンドと既定並列数、入力上限を照合する。"""
    parser = cli.parser()
    for command in (
        'doctor',
        'init',
        'start',
        'run',
        'status',
        'stop',
        'resume',
        'verify',
        'report',
    ):
        args = parser.parse_args([command, '--root', str(tmp_path)])
        assert args.root == tmp_path
        if command in ('doctor', 'start', 'resume', 'run'):
            assert args.max_workers == 2
    assert not parser.parse_args(['status']).verbose
    assert parser.parse_args(['status', '--verbose']).verbose
    for workers in ('0', '7', 'two'):
        with pytest.raises(SystemExit):
            parser.parse_args(['start', '--max-workers', workers])
    with pytest.raises(SystemExit):
        parser.parse_args(['init', '--smoke', 'zero', '--smoke-games', '1000'])


def test_status_default_is_compact_and_verbose_keeps_details(tmp_path, monkeypatch, capsys):
    """既定statusを読みやすくし、verboseに保存先とPIDを残す。"""
    snap = {
        'run_id': 'test-run',
        'state': 'RUNNING',
        'updated_at': '2026-11-20T00:00:00Z',
        'owner': {'pid': 123, 'created': 456},
        'session': 'a' * 32,
        'target': 100_000,
        'max_workers': 2,
        'games_per_second': 5.0,
        'estimated_remaining_seconds': 3600.0,
        'evaluation': '20,000局 701-zero-vs-search',
        'issues': [],
        'series': {
            'zero-701': {
                'games': 20_000,
                'live_games': 20_100,
                'updates': 12_345,
                'state': 'RUNNING',
                'checkpoint': r'C:\long\checkpoint\state.json',
                'segment_games_per_second': 4.0,
            },
            'pretrained-701': {
                'games': 10_000,
                'updates': 0,
                'state': 'WAITING',
                'checkpoint': r'C:\long\checkpoint\state.json',
                'segment_games_per_second': 0.0,
            },
        },
    }
    monkeypatch.setattr(cli, '_snapshot', lambda _root: snap)
    monkeypatch.setattr(cli, '_live', lambda _owner: True)
    monkeypatch.setattr(cli, '_session', lambda _root, _name: tmp_path)
    monkeypatch.setattr(cli.data, 'available', lambda: None)
    monkeypatch.setattr(cli.windows, 'extended', lambda path: path)
    monkeypatch.setattr(sys, 'argv', ['phase7_longrun.py', 'status', '--root', str(tmp_path)])

    assert cli.main() == 0
    compact = capsys.readouterr().out
    assert '[#-----------------------] 5.6%' in compact
    assert 'zero-701' in compact and 'pretrained-701' in compact
    assert '実行中' in compact and '待機中' in compact
    assert '同時稼働  zero-701' in compact
    assert '評価中  20,000局 701-zero-vs-search' in compact
    assert '評価時間を含みません' in compact
    assert 'エラー  なし' in compact
    assert 'state.json' not in compact and '123' not in compact and '12,345' not in compact

    monkeypatch.setattr(
        sys,
        'argv',
        ['phase7_longrun.py', 'status', '--root', str(tmp_path), '--verbose'],
    )
    assert cli.main() == 0
    verbose = capsys.readouterr().out
    assert 'state.json' in verbose and '管理PID 123' in verbose and '12,345' in verbose


def test_stop_before_first_game_creates_nothing(tmp_path, short_checkpoint):
    """最初の局前に停止した場合は空のcheckpointを発行しない。"""
    origin = short_checkpoint
    output = tmp_path / 'not-started'
    with pytest.raises(train.StoppedBeforeGame):
        train.run(
            origin.initial,
            output,
            origin.config,
            15,
            resume=origin.checkpoint.path,
            stop_requested=lambda: True,
        )
    assert not output.exists()


def test_game_boundary_and_after_save_are_reproducible(tmp_path, short_checkpoint):
    """完局の途中で止めた区間と連続実行で更新・乱数・棋譜が一致する。"""
    origin = short_checkpoint
    begin = origin.checkpoint.path
    seen = []
    early = tmp_path / 'early'
    train.run(
        origin.initial,
        early,
        origin.config,
        7,
        resume=begin,
        stop_requested=lambda: bool(seen),
        on_game=seen.append,
    )
    assert len(seen) == 1 and seen[0].games == 3
    saved = early / 'state.json'
    pristine = saved.read_bytes()
    split, nonstop = tmp_path / 'remaining', tmp_path / 'continuous'
    train.run(origin.initial, split, origin.config, 5, resume=early)
    train.run(origin.initial, nonstop, origin.config, 5, resume=begin)
    assert saved.read_bytes() == pristine
    assert (split / 'model.json').read_bytes() == (nonstop / 'model.json').read_bytes()
    assert (early / 'games.jsonl').read_bytes() + (split / 'games.jsonl').read_bytes() == (
        nonstop / 'games.jsonl'
    ).read_bytes()
    assert all(_state(split)[k] == _state(nonstop)[k] for k in ('rng', 'counts', 'model'))


@pytest.mark.parametrize(
    'field',
    ['seed', 'rate', 'epsilon', 'gamma', 'legacy-other-source', 'rng', 'games', 'model-only'],
)
def test_changed_resume_state_is_rejected(tmp_path, short_checkpoint, field):
    """ハッシュを再計算しても設定や乱数・保存内容の取り違えを通さない。"""
    import shutil

    origin = short_checkpoint
    path = tmp_path / 'copy'
    shutil.copytree(origin.checkpoint.path, path)
    if field == 'model-only':
        (path / 'state.json').unlink()
    elif field == 'games':
        with (path / 'games.jsonl').open('ab') as stream:
            stream.write(b'{}')
    elif field == 'rng':
        _variant(path, lambda x: x['rng'][1].__setitem__(-1, 625))
    elif field == 'legacy-other-source':

        def edit(x):
            src = x['compatibility']['sources']
            src['tools/phase7_train.py'] = train._PHASE7_TRAIN_SHA256
            src['harness/referee.py'] = 'f' * 64

        _variant(path, edit)
    else:
        _variant(path, lambda x: x['compatibility']['settings'].__setitem__(field, -1))
    with pytest.raises((ValueError, OSError)):
        train._restore(path, origin.compatible)


def test_only_known_legacy_trainer_hash_is_migrated(tmp_path, short_checkpoint):
    """限定した旧トレーナー来歴だけを次の正常な局境界へ継ぐ。"""
    import shutil

    origin = short_checkpoint
    old = tmp_path / 'legacy'
    shutil.copytree(origin.checkpoint.path, old)

    def update(x, sha):
        x['compatibility']['sources']['tools/phase7_train.py'] = sha

    _variant(old, lambda x: update(x, train._PHASE7_TRAIN_SHA256))
    new = tmp_path / 'continue'
    result = train.run(origin.initial, new, origin.config, 3, resume=old)
    assert result['compatibility'] == origin.compatible
    assert result['previous']['games'] == 2
    _variant(old, lambda x: update(x, 'a' * 64))
    with pytest.raises(ValueError, match='incompatible_td_checkpoint'):
        train._restore(old, origin.compatible)


def test_disk_and_write_probe_fail_closed(tmp_path, monkeypatch):
    """空き不足や権限エラーを起動前に返す。"""
    usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(data.shutil, 'disk_usage', lambda _: usage(100, 98, 2))
    with pytest.raises(data.RunError, match='空き容量'):
        data.disk_check(tmp_path, 10)

    def denied(**_):
        raise PermissionError('test drive')

    monkeypatch.setattr(data.tempfile, 'TemporaryDirectory', denied)
    with pytest.raises(PermissionError):
        data.disk_check(tmp_path, 1, probe=True)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windowsのプロセス契約')
def test_process_generation_lock_and_crash_recovery(tmp_path):
    """実際の子のロックは終了時にOSが解放し、古いPID世代を採用しない。"""
    identity = windows.identity(os.getpid())
    assert cli._live(identity)
    assert not cli._live({**identity, 'created': identity['created'] + 1})
    path = tmp_path / 'worker.lock'
    code = (
        'from tools.phase7_longrun_os import lease; from pathlib import Path; import time\n'
        'with lease(Path(__import__("sys").argv[1])):\n'
        ' print("locked",flush=True); time.sleep(30)\n'
    )
    proc = subprocess.Popen(
        [sys.executable, '-B', '-c', code, str(path)],
        cwd=data.ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == 'locked'
        with pytest.raises(windows.Busy), windows.lease(path):
            raise AssertionError('lock overlap')
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=5)
    assert path.exists()
    with windows.lease(path):
        assert windows.identity(proc.pid) is None


@pytest.mark.parametrize('maximum', [1, 2, 6])
def test_scheduler_deterministic_order_and_maximum(
    tmp_path, monkeypatch, short_checkpoint, maximum
):
    """管理の並列上限と系列順を実データなしで検証する。"""
    plan = data.Plan(tmp_path, tmp_path, 'unit', None, 40, 'unused')
    snapshot = short_checkpoint.checkpoint
    origins = {
        label: replace(
            short_checkpoint,
            label=label,
            checkpoint=replace(
                snapshot, sha=label, counts=train.Counts(10000, 600000, 595000, 5000)
            ),
        )
        for label in plan.labels
    }
    for label in plan.labels:
        (tmp_path / 'checkpoints' / label).mkdir(parents=True)
    manager = cli.Manager(plan, 'unit', tmp_path, {'pid': 1, 'created': 2}, origins, maximum)
    seen = []
    counts = []

    class Exited:
        def poll(self):
            return 0

    def spawn(folder, label, _request):
        seen.append(label)
        child = cli.Child(folder, Exited(), label)
        manager.active.append(child)
        counts.append(len(manager.active))
        child.folder.joinpath('checkpoint').mkdir()
        child.folder.joinpath('checkpoint', 'state.json').touch()
        return child

    def accept(_plan, _origin, attempt, begin):
        return replace(begin, path=attempt, counts=begin.counts._replace(games=11000))

    monkeypatch.setattr(manager, 'spawn', spawn)
    monkeypatch.setattr(manager, 'publish', lambda _: None)
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    monkeypatch.setattr(data, 'accept', accept)
    manager.train_to(11000)
    assert seen == list(plan.labels)
    assert max(counts) == maximum
    assert not manager.active
    assert all(c[-1].counts.games == 11000 for c in manager.chains.values())


def test_chain_keeps_partial_attempt_and_refuses_two_valid_children(tmp_path, short_checkpoint):
    """不完全な試行で最後の確定状態が消えず、正常な重複は拒否する。"""
    import shutil

    origin = short_checkpoint
    plan = data.Plan(tmp_path, tmp_path, 'unit', 'zero', 20, 'unused')
    parent = tmp_path / 'checkpoints' / origin.label
    a = parent / 'attempt-a'
    a.mkdir(parents=True)
    expected = {
        'label': origin.label,
        'from': 2,
        'parent': origin.checkpoint.sha,
        'until': plan.next_stop(2),
    }
    data.seal(a / 'job.json', expected)
    chain, issues = data.scan(plan, origin)
    assert chain == [origin.checkpoint] and issues[0]['from'] == 2 and issues[0]['until'] == 10
    b = parent / 'attempt-b'
    b.mkdir()
    data.seal(b / 'job.json', expected)
    train.run(origin.initial, b / 'checkpoint', origin.config, 3, resume=origin.checkpoint.path)
    chain, issues = data.scan(plan, origin)
    assert chain[-1].counts.games == 3 and len(issues) == 1
    shutil.copytree(b, parent / 'attempt-c')
    with pytest.raises(data.RunError, match='分岐'):
        data.scan(plan, origin)
    assert len(list(parent.iterdir())) == 3


def test_eval_retry_ignores_partial_and_rejects_duplicate(tmp_path, monkeypatch):
    """中断評価と再取得を一件だけ数え、完了した重複を拒否する。"""
    plan = data.Plan(tmp_path, tmp_path, 'unit', 'zero', 20, 'unused')
    job = reporting.Match(plan.target, 'zero-701', 'search_v1')
    parent = tmp_path / 'evaluations' / str(plan.target) / job.key
    first = parent / 'attempt-first' / 'match'
    first.mkdir(parents=True)

    def check(_job, _marks, path, _pop):
        if not (path / 'complete').exists():
            raise data.RunError('raw incomplete')
        return {'point_rate': 0.75}

    monkeypatch.setattr(reporting, 'verified_match', check)
    assert reporting.find_result(plan, job, {})[0] is None
    second = parent / 'attempt-second' / 'match'
    second.mkdir(parents=True)
    (second / 'complete').touch()
    accepted, errors = reporting.find_result(plan, job, {})
    assert accepted['point_rate'] == 0.75 and len(errors) == 1
    (first / 'complete').touch()
    with pytest.raises(data.RunError, match='複数'):
        reporting.find_result(plan, job, {})


def test_report_pending_has_empty_scores(tmp_path, short_checkpoint):
    """未到達は勝ち点0に変換せず、3形式の出力に残す。"""
    plan = data.Plan(tmp_path, tmp_path, 'unit', 'zero', 20, 'unused')
    (tmp_path / 'reports').mkdir()
    folder = reporting.reports(plan, {'zero-701': [short_checkpoint.checkpoint]}, [])
    value, _ = data.unseal(folder / 'summary.json')
    assert all(
        row['state'] == 'TRAINING_PENDING' and 'point_rate' not in row
        for row in value['evaluation']
    )
    assert (folder / 'progress.csv').is_file()
    assert 'TRAINING_PENDING' in (folder / 'results.csv').read_text(encoding='utf-8-sig')
    assert (folder / 'summary.md').read_text(encoding='utf-8').count('|TRAINING_PENDING||') == 2


def test_stop_before_work_is_not_corruption(tmp_path, short_checkpoint):
    """保存前の正常停止だけを除外し、不整合な停止記録を検出する。"""
    origin = short_checkpoint
    plan = data.Plan(tmp_path, tmp_path, 'unit', 'zero', 20, 'unused')
    attempt = tmp_path / 'checkpoints' / origin.label / 'attempt-stopped'
    attempt.mkdir(parents=True)
    data.seal(attempt / 'job.json', {'label': origin.label, 'parent': origin.checkpoint.sha})
    data.seal(attempt / 'dispatch.json', {'kind': 'train'})
    data.record_skipped(attempt)
    chain, issues = data.scan(plan, origin)
    assert chain == [origin.checkpoint] and not issues
    (attempt / 'checkpoint').mkdir()
    _, issues = data.scan(plan, origin)
    assert '停止記録' in issues[0]['problem']


def test_checkpoint_rollback_explains_missing_local_generation(short_checkpoint):
    """過去に保存表示があった世代を失った場合、再実行の局数を明示する。"""
    origin = short_checkpoint
    series = {origin.label: [origin.checkpoint]}
    assert cli._rollback_notices({'series': {origin.label: {'games': 2}}}, series) == []
    (notice,) = cli._rollback_notices({'series': {origin.label: {'games': 10}}}, series)
    assert (notice['series'], notice['from'], notice['until']) == (origin.label, 2, 10)
    assert '8局を再実行' in notice['problem']
