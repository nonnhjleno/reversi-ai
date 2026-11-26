"""完了主変化の寿命、順序、根の最善手と時間中断を独立に検査する。"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from harness import protocol, referee
from reversi_player import ntuple, ntuple_io, reversi, search_trace, timed_search, web
from tests.test_ai_phase3 import _oracle as fraction_oracle
from tests.test_ai_phase4 import CASES, _context
from tests.test_ai_phase8 import _oracle as ntuple_oracle
from tests.test_ai_phase9b import _legal, _near_end, _search

MODEL = ntuple.NtupleModel(tuple((i % 11 - 5) / 2000 for i in range(ntuple.WEIGHT_COUNT)))


def _validate_line(rows, turn, depth, line):
    """refereeで主変化の根・パス・手番を一手ずつ再生する。"""
    placed = 0
    for move in line:
        available = _legal(rows, turn)
        other = 'W' if turn == 'B' else 'B'
        if move is None:
            assert not available and _legal(rows, other)
        else:
            assert move in available
            rows = referee.apply_move(rows, turn, move)
            placed += 1
        turn = other
    if placed < depth:
        assert not _legal(rows, 'B') and not _legal(rows, 'W')
    else:
        assert placed == depth


@pytest.mark.parametrize('depth', [5, 6])
@pytest.mark.parametrize('turn', ['B', 'W'])
@pytest.mark.parametrize('feature', ['manual', 'ntuple'])
def test_pv_values_and_moves_match_independent_deep_oracle(depth, turn, feature):
    """深い固定探索の同点規則と価値を別実装の全幅探索と合わせる。"""
    rows = _near_end()
    state_before = tuple(rows)
    opts = {'leaf_evaluator': MODEL.value} if feature == 'ntuple' else {}
    base = _search(rows, turn, depth, **opts)
    stats = timed_search.OrderingDiagnostics()
    traces = []
    new = _search(
        rows, turn, depth, move_ordering='pv', ordering_stats=stats, iterations=traces, **opts
    )
    if feature == 'manual':
        value, tied, _ = fraction_oracle(rows, turn, depth)
        expected = (tied[0] if tied else None, float(value))
    else:
        value, move = ntuple_oracle(rows, turn, depth, MODEL.value)
        expected = (move, value if turn == 'B' else -value)
    if _legal(rows, turn):
        assert (new.move, new.value) == (base.move, base.value) == expected
        assert new.completed_depth == depth
        assert len(traces) == len(stats.completed_lines) == depth
        for trace, line in zip(traces, stats.completed_lines, strict=True):
            assert line[0] == trace.move
            _validate_line(rows, turn, trace.depth, line)
        assert 0 < stats.pv_legal <= stats.pv_candidates
        assert 0 <= stats.pv_exact_best <= stats.pv_exact_nodes <= stats.pv_legal
        assert stats.first_cutoffs <= new.cutoffs
    else:
        assert new == base and not stats.completed_lines
    assert rows == state_before


@pytest.mark.parametrize('label', ['pass', 'terminal_with_empty', 'wipe', 'initial'])
def test_pass_terminal_single_and_root_ties(label):
    """パス、終局、全滅と根同点で現在の公開着手を保つ。"""
    rows = (
        ('BWEEEEEE',) + ('EEEEEEEE',) * 7
        if label == 'wipe'
        else referee.INITIAL_ROWS
        if label == 'initial'
        else tuple(CASES[label]['rows'])
    )
    cap = 3 if label == 'initial' else 6
    for turn in ('B', 'W'):
        stats = timed_search.OrderingDiagnostics()
        opts = {'leaf_evaluator': lambda _rows, _turn: 0.0}
        test = _search(rows, turn, cap, move_ordering='pv', ordering_stats=stats, **opts)
        base = _search(rows, turn, cap, **opts)
        assert (test.move, test.value, test.completed_depth) == (
            base.move,
            base.value,
            base.completed_depth,
        )
        if label == 'initial':
            assert test.move == min(_legal(rows, turn), key=lambda m: (m[1], m[0]))
        for depth, line in enumerate(stats.completed_lines, 1):
            _validate_line(rows, turn, depth, line)


def test_illegal_hint_is_ignored_after_callback_changes():
    """合法手の列が変わった場合は古い候補を仮着手しない。"""
    rows, turn = referee.INITIAL_ROWS, 'B'
    stats = timed_search.OrderingDiagnostics()
    applied = 0

    def counting(r, color, position):
        """2反復までに行った仮着手回数を測る。"""
        nonlocal applied
        applied += 1
        return referee.apply_move(r, color, position)

    _search(rows, turn, 2, move_ordering='pv', apply=counting, ordering_stats=stats)
    threshold = applied
    first, outdated = stats.completed_lines[-1]
    assert first is not None and outdated is not None
    reached = referee.apply_move(rows, turn, first)
    applied = 0

    def hiding(snapshot, color):
        """3反復目から1箇所だけ合法手列から旧候補を取り除く。"""
        moves = _legal(snapshot, color)
        if applied > threshold and snapshot == reached and color == 'W':
            return tuple(m for m in moves if m != outdated)
        return moves

    def guarded(snapshot, color, move):
        """無効になった候補を実際に適用しないことを確認する。"""
        assert move in hiding(snapshot, color)
        return counting(snapshot, color, move)

    diagnostics = timed_search.OrderingDiagnostics()
    result = timed_search.search_iterative(
        rows,
        turn,
        _legal(rows, turn),
        hiding,
        guarded,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(max_depth=3),
        clock=lambda: 0,
        move_ordering='pv',
        ordering_stats=diagnostics,
    )
    assert result.completed_depth == 3
    assert diagnostics.pv_candidates > diagnostics.pv_legal


@pytest.mark.parametrize('depth', [5, 6])
def test_interrupted_line_is_never_committed(depth):
    """D5・D6の探索途中で失効しても直前の着手と主変化だけを残す。"""
    rows = _near_end()
    turn = 'B' if _legal(rows, 'B') else 'W'
    count = 0

    def apply(r, color, move):
        """実際の仮着手を数えて中断位置を決める。"""
        nonlocal count
        count += 1
        return referee.apply_move(r, color, move)

    completed = timed_search.OrderingDiagnostics()
    first = _search(
        rows, turn, depth - 1, apply=apply, move_ordering='pv', ordering_stats=completed
    )
    end = count + 2
    count = 0
    trials = timed_search.OrderingDiagnostics()
    steps = []
    after = _search(
        rows,
        turn,
        depth,
        apply=apply,
        move_ordering='pv',
        ordering_stats=trials,
        iterations=steps,
        clock=lambda: 6_500_000_000 if count >= end else 0,
    )
    assert (after.move, after.value, after.completed_depth) == (first.move, first.value, depth - 1)
    assert after.stop_reason == 'time_limit'
    assert after.max_depth_started == depth and not steps[-1].completed
    assert trials.completed_lines == completed.completed_lines


@pytest.mark.parametrize('setting', ['bad', False, None])
def test_invalid_ordering(setting):
    """未知値を従来設定へ読み替えない。"""
    with pytest.raises(ValueError, match='move_ordering'):
        _search(_near_end(), 'B', 4, move_ordering=setting)


@pytest.mark.parametrize('opts', [{'tt_capacity': 4}, {'ordered': False}])
def test_pv_rejects_other_ordering_experiments(opts):
    """置換表や順序無効をPV比較に混ぜない。"""
    with pytest.raises(ValueError, match='pv_requires'):
        _search(_near_end(), 'B', 4, move_ordering='pv', **opts)


@pytest.mark.parametrize(
    'kind,mode,success',
    [
        ('time', 'pv', True),
        ('time', 'static', True),
        ('time', 'unknown', False),
        ('depth', 'pv', False),
    ],
)
def test_trace_setting(kind, mode, success, tmp_path):
    """時間設定を独立プロセスに渡し、実効設定と整合するgenmoveを記録する。"""
    budget = (
        {'kind': kind, 'limit_ns': 6_500_000_000}
        if kind == 'time'
        else {'kind': kind, 'limit_depth': 4}
    )
    search = {'move_ordering': mode}
    if kind == 'time':
        search['max_depth'] = 5
    cfg = {'format_version': 2, 'budget': budget, 'search': search}
    path = tmp_path / 'config.json'
    trace = tmp_path / 'trace.jsonl'
    path.write_text(json.dumps(cfg), encoding='utf-8')
    script = Path(reversi.__file__).resolve()

    def connect():
        """外部traceの検証用コンテキストで子を起動する。"""
        return protocol.Player(
            script, evaluation_config=path, evaluation_trace=trace, evaluation_context=_context(cfg)
        )

    if not success:
        with pytest.raises(protocol.HarnessError):
            connect()
        assert not trace.exists()
        return
    with connect() as player:
        response = player.command('genmove B')
        assert tuple(map(int, response[0].split())) in _legal(referee.INITIAL_ROWS, 'B')
    events = [json.loads(line) for line in trace.read_bytes().splitlines()]
    assert events[0]['effective_config'] == cfg
    assert sum(e['type'] == 'genmove' for e in events) == 1


def test_standalone_pv_cli_and_no_state_leak(monkeypatch):
    """GUIと同じ通常起動経路で連続genmoveが合法に進む。"""
    monkeypatch.setenv(timed_search.MOVE_ORDERING_ENV, 'pv')
    monkeypatch.setenv(timed_search.SEARCH_DEPTH_ENV, '3')
    result = subprocess.run(
        [sys.executable, '-B', str(Path(reversi.__file__).resolve())],
        input='name\ngenmove B\ngenmove W\nshowboard\nquit\n',
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert len(lines) == 11 and lines[0] == 'ranger' and not result.stderr
    b, w = (tuple(map(int, line.split())) for line in lines[1:3])
    rows = referee.apply_move(referee.INITIAL_ROWS, 'B', b)
    assert tuple(lines[3:]) == referee.apply_move(rows, 'W', w)

    selector = timed_search.TimedSelector(
        _legal, referee.apply_move, timed_search.ResponsePolicy(max_depth=3), move_ordering='pv'
    )
    selector.select(referee.INITIAL_ROWS, 'B', _legal(referee.INITIAL_ROWS, 'B'))
    selector.select(rows, 'W', _legal(rows, 'W'))
    reference = _search(rows, 'W', 3, move_ordering='pv')
    assert selector.result is not None
    assert (selector.result.move, selector.result.value, selector.result.nodes) == (
        reference.move,
        reference.value,
        reference.nodes,
    )


@pytest.mark.parametrize('use_model', [False, True])
def test_gui_starts_pv_selector_with_both_evaluators(tmp_path, monkeypatch, use_model):
    """GUIの通常起動でPVを指定し、手作業とモデルの両方を独立refereeで照合する。"""
    monkeypatch.setenv(timed_search.MOVE_ORDERING_ENV, 'pv')
    monkeypatch.setenv(timed_search.SEARCH_DEPTH_ENV, '3')
    if use_model:
        path = tmp_path / 'model.json'
        ntuple_io.save_model(path, MODEL)
        monkeypatch.setenv(search_trace.MODEL_ENV, str(path))
    with web.Process() as gui:
        assert gui.command('name') == 'ranger'
        answer = tuple(map(int, gui.command('genmove B').split()))
        assert answer in referee.legal_moves(referee.INITIAL_ROWS, 'B')
