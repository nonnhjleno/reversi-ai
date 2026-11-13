"""Phase 5の独立探索・偽時計全停止境界・実プロトコル。新規分はFull専用。"""

import contextlib
from functools import cache

from dataclasses import FrozenInstanceError

import hashlib

import io

import json

from pathlib import Path

import random

from unittest.mock import Mock, patch


import pytest


from harness import protocol, referee, search_budget

from reversi_player import ai_engine, reversi, search_trace, timed_search

from test_ai_phase3 import CASES, _oracle

from test_ai_phase4 import _context


def _legal(rows, color):
    """独立レフェリーの完全候補をAIのtuple契約へ接続する。"""

    return tuple(referee.legal_moves(rows, color))


@cache
def _expected(rows, color, depth):
    """独立Fractionオラクルの不変な結果だけを同一入力で再利用する。"""
    return _oracle(rows, color, depth)


@cache
def _fixed_reference(rows, color, depth, ordered):
    """保全した参照探索を入力ごとに実行し、その不変な計数を再利用する。"""
    return ai_engine.search_alpha_beta(
        rows, color, depth, _legal, referee.apply_move, ordered=ordered
    )


def _run(rows=referee.INITIAL_ROWS, color='B', depth=2, clock=lambda: 0, **kwargs):
    """実ルールと偽時計を分離し、固定した入力で時間探索を呼ぶ。"""

    return timed_search.search_iterative(
        rows,
        color,
        _legal(rows, color),
        _legal,
        referee.apply_move,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(max_depth=depth),
        clock=clock,
        **kwargs,
    )


@pytest.mark.parametrize('label', CASES)
@pytest.mark.parametrize('color', ['B', 'W'])
@pytest.mark.parametrize('depth', [1, 2, 3, 4])
@pytest.mark.parametrize('ordered', [False, True])
def test_completed_iterations_match_independent_oracle_and_preserved_reference(
    label, color, depth, ordered
):
    """完全反復の値・同点手と全反復の実計数を独立全列挙へ照合する。"""

    rows = tuple(CASES[label]['rows'])

    result = _run(rows, color, depth, ordered=ordered)

    if not _legal(rows, color):
        assert (result.move, result.value, result.nodes, result.completed_depth) == (
            None,
            None,
            0,
            0,
        )

        assert result.stop_reason == 'no_legal_move'

        return

    value, best, _ = _expected(rows, color, depth)

    assert result.value == float(value)

    assert result.move == min(best, key=lambda m: (m[1], m[0]))

    references = [_fixed_reference(rows, color, d, ordered) for d in range(1, depth + 1)]
    assert (result.move, result.value) == (references[-1].move, references[-1].value)

    assert (result.nodes, result.leaves, result.passes, result.cutoffs) == tuple(
        sum(getattr(r, name) for r in references)
        for name in ('nodes', 'leaves', 'passes', 'cutoffs')
    )

    assert result.completed_depth == result.max_depth_started == depth

    assert result.stop_reason == 'max_depth_completed'

    with pytest.raises(FrozenInstanceError):
        result.value = 0


class TripClock:
    """指定した確認回数から期限到達を返す決定的な単調時計。"""

    def __init__(self, trip):
        """0始まりの確認位置を固定する。"""

        self.calls, self.trip = 0, trip

    def __call__(self):
        """実時間を待たず同じ停止点を再現する。"""

        value = 7_000_000_000 if self.calls >= self.trip else 0

        self.calls += 1

        return value


def test_every_clock_boundary_preserves_only_completed_root_values():
    """初期盤面深度2の全確認位置で中断し、値は独立オラクルから判定する。"""

    counter = TripClock(10**9)

    complete = _run(clock=counter)

    expected = {d: _expected(referee.INITIAL_ROWS, 'B', d) for d in (1, 2)}

    seen = set()

    for trip in range(counter.calls + 1):
        clock = TripClock(trip)

        result = _run(clock=clock)

        seen.add((result.completed_depth, result.max_depth_started))

        assert result.move in _legal(referee.INITIAL_ROWS, 'B')

        assert 0 <= result.completed_depth <= result.max_depth_started <= 2

        assert 0 <= result.nodes <= complete.nodes and 0 <= result.leaves <= complete.leaves

        if result.completed_depth:
            value, best, _ = expected[result.completed_depth]

            assert result.value == float(value)

            assert result.move == min(best, key=lambda m: (m[1], m[0]))

        else:
            assert result.value is None and result.move == (3, 2)

        if result.stop_reason == 'time_limit':
            assert result.stop_requested_ns >= result.stop_deadline_ns

    assert {(0, 0), (0, 1), (1, 1), (1, 2), (2, 2)} <= seen


@pytest.mark.parametrize('root_index', [1, 2, 3, 4])
def test_abort_after_root_application_never_publishes_partial_depth(root_index):
    """最後のroot候補も含め、子評価前の中断を完了と誤認しない。"""

    calls = 0

    def apply(rows, color, move):
        """既知のroot候補数で時計だけを進め、子は独立レフェリーで作る。"""

        nonlocal calls

        calls += 1

        return referee.apply_move(rows, color, move)

    result = timed_search.search_iterative(
        referee.INITIAL_ROWS,
        'B',
        _legal(referee.INITIAL_ROWS, 'B'),
        _legal,
        apply,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(max_depth=1),
        clock=lambda: 6_500_000_000 if calls >= root_index else 0,
    )

    assert calls == root_index

    assert (result.completed_depth, result.max_depth_started, result.value, result.move) == (
        0,
        1,
        None,
        (3, 2),
    )


def test_complete_depth_one_then_new_depth_deadline_keeps_exact_result():
    """全root終了後の次反復開始限界では深度1の完全値を保持する。"""

    counter = TripClock(10**9)

    first = _run(depth=1, clock=counter)

    calls = 0

    def clock():
        """深度1の最終確認より後で新深度の開始だけを抑止する。"""

        nonlocal calls

        calls += 1

        return 6_000_000_000 if calls > counter.calls else 0

    result = _run(clock=clock)

    assert (result.move, result.value, result.completed_depth, result.max_depth_started) == (
        first.move,
        first.value,
        1,
        1,
    )

    assert result.stop_reason == 'new_depth_deadline'


@pytest.mark.parametrize('instant', [6_000_000_000, 6_499_999_999, 6_500_000_000, 7_000_000_000])
def test_expired_or_nearly_expired_command_uses_legal_depth_zero_candidate(instant):
    """受付起点を反復ごとに取り直さず、残り時間がなくても合法手を保持する。"""

    result = _run(clock=lambda: instant)

    assert (
        result.move,
        result.value,
        result.completed_depth,
        result.max_depth_started,
        result.nodes,
    ) == ((3, 2), None, 0, 0, 0)


def test_time_budget_and_command_deadline_take_earliest_stop():
    """search_onlyの予約分とコマンド予約分を独立な絶対時刻として照合する。"""

    short = _run(limit_ns=100_000_000)

    assert short.stop_deadline_ns == 20_000_000

    late = _run(limit_ns=1_000_000_000, clock=lambda: 5_900_000_000)

    assert late.stop_deadline_ns == 6_500_000_000


@pytest.mark.parametrize('label', ['pass', 'terminal_with_empty'])
def test_no_root_moves_do_not_search_or_consume_callbacks(label):
    """パス・終局は本体の合法手なし判定に従い、探索counterを作らない。"""

    result = timed_search.search_iterative(
        tuple(CASES[label]['rows']),
        CASES[label]['color'],
        (),
        Mock(side_effect=AssertionError),
        Mock(side_effect=AssertionError),
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(),
        clock=lambda: 0,
    )

    assert (
        result.nodes,
        result.leaves,
        result.passes,
        result.completed_depth,
        result.max_depth_started,
    ) == (0, 0, 0, 0, 0)

    assert result.move is result.value is None and result.stop_reason == 'no_legal_move'


def test_single_legal_move_and_internal_pass_keep_placement_semantics():
    """1候補と内部パスの値は独立オラクルに一致し、パスは深度を消費しない。"""

    rows = ('BWEEEEEE',) + ('BBBBBBBB',) * 7

    assert _legal(rows, 'B') == ((2, 0),)

    assert _run(rows, 'B', 4).move == (2, 0)

    rows, color = tuple(CASES['pass']['rows']), 'W'

    result = _run(rows, color, 4)

    value, best, _ = _expected(rows, color, 4)

    assert result.value == float(value) and result.move in best and result.passes > 0


@pytest.mark.parametrize('value', [True, -1, 0, 999999, 6500000001, 1.5, '1000000'])
def test_invalid_time_budgets_are_rejected(value):
    """対応値域外は無視せず拒否する。"""

    with pytest.raises(ValueError, match='budget'):
        _run(limit_ns=value)


@pytest.mark.parametrize(
    'field,value',
    [
        ('max_depth', 0),
        ('max_depth', 5),
        ('max_depth', True),
        ('target_ns', 10000000000),
        ('stop_margin_ns', 0),
        ('new_depth_margin_ns', 1),
        ('target_ns', '7'),
        ('stop_margin_ns', True),
    ],
)
def test_invalid_response_policy_is_rejected(field, value):
    """予約分の順序・通常int・深度上限を検査する。"""

    with pytest.raises(ValueError):
        timed_search.ResponsePolicy(**{field: value})


def test_bad_callbacks_clock_and_rule_exceptions_are_not_normal_interruptions():
    """通常例外を合法な安全応答やtime_limitへ変換しない。"""

    rows = referee.INITIAL_ROWS

    for legal, apply in [
        (Mock(side_effect=LookupError), referee.apply_move),
        (_legal, Mock(side_effect=LookupError)),
    ]:
        with pytest.raises(LookupError):
            timed_search.search_iterative(
                rows,
                'B',
                _legal(rows, 'B'),
                legal,
                apply,
                command_received_ns=0,
                policy=timed_search.ResponsePolicy(),
                clock=lambda: 0,
            )

    for clock in [False, lambda: True, iter([1, 0]).__next__]:
        with pytest.raises(ValueError):
            _run(clock=clock)

    with pytest.raises(ValueError):
        timed_search.search_iterative(
            rows,
            'B',
            ((True, 2),),
            _legal,
            referee.apply_move,
            command_received_ns=0,
            policy=timed_search.ResponsePolicy(),
            clock=lambda: 0,
        )

    with pytest.raises(ValueError):
        _run(ordered=1)


def test_snapshot_rng_and_streams_are_immutable_and_real_commit_is_once():
    """仮着手から実Boardを分離し、本体の確定と標準応答が1回だけになる。"""

    board = reversi.Board()

    snapshot, state = str(board), random.getstate()

    controller = timed_search.TimedSelector(
        reversi.legal_moves_for, reversi.apply_move, timed_search.ResponsePolicy(max_depth=1)
    )

    board._selector = controller.select

    output, error = io.StringIO(), io.StringIO()

    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        with patch.object(board, '_play', wraps=board._play) as commit:
            board.genmove('B')

    assert (
        commit.call_count == 1
        and len(output.getvalue().splitlines()) == 1
        and error.getvalue() == ''
    )

    assert random.getstate() == state and str(board) != snapshot

    assert controller.result.completed_depth == 1

    controller.begin(0)

    controller.select(referee.INITIAL_ROWS, 'B', _legal(referee.INITIAL_ROWS, 'B'))

    assert controller.result.completed_depth == 0

    controller.begin(0)

    controller._apply = Mock(side_effect=LookupError)

    # 実時計では開始前期限切れになるため、例外の状態分離は開始時刻も進める。

    import time

    controller.begin(time.perf_counter_ns())

    with pytest.raises(LookupError):
        controller.select(referee.INITIAL_ROWS, 'B', _legal(referee.INITIAL_ROWS, 'B'))

    assert controller.result is None and controller._received is None


def _time_config():
    """CIの実時計精度試験とは分け、十分な余裕でtrace経路を検証する。"""

    return {
        'format_version': 2,
        'budget': {'kind': 'time', 'limit_ns': 100000000},
        'search': {
            'algorithm': 'iterative',
            'max_depth': 1,
            'ordered': True,
            'deterministic': False,
            'counting_definition_id': 'minimax-call-v1',
        },
    }


def test_real_time_trace_is_acquired_with_complete_lifecycle(tmp_path):
    """本物の子探索端点を保存し、既存trace検証器と子lifecycleへ接続する。"""

    config = _time_config()

    path, raw = tmp_path / 'config.json', tmp_path / 'trace.jsonl'

    path.write_text(json.dumps(config), encoding='utf-8')

    context = _context(config)

    with protocol.Player(
        'reversi_player/reversi.py',
        evaluation_config=path,
        evaluation_trace=raw,
        evaluation_context=context,
    ) as player:
        assert player.command('showboard') == list(referee.INITIAL_ROWS)

        response = player.command('genmove B')

        player.commands[-1]['phase'] = 'normal_move'

    events = [json.loads(line) for line in raw.read_text(encoding='utf-8').splitlines()]

    assert (
        search_budget.validate_trace_events(
            events, config, player.commands, context['target_sha256'], player.evaluation_context
        )['status']
        == 'verified'
    )

    assert (
        search_budget.validate_child_lifecycle(
            events, player.commands, player.lifecycle, player.evaluation_context
        )['status']
        == 'verified'
    )

    event = next(e for e in events if e['type'] == 'genmove')

    assert response == ['3 2'] and event['value'] == float(
        _expected(referee.INITIAL_ROWS, 'B', 1)[0]
    )

    assert (
        event['nodes'],
        event['leaves'],
        event['max_depth_started'],
        event['completed_depth'],
    ) == (5, 4, 1, 1)

    assert event['fallback'] is False and event['model_load_status'] == 'not_required'

    assert not player.stderr_tail


@pytest.mark.parametrize(
    'change', ['true', 'algorithm', 'max_depth', 'extra', 'bool', 'small', 'large', 'model']
)
def test_invalid_time_settings_fail_before_trace_creation(tmp_path, change):
    """対応外time設定は実効一致を申告せず起動時に拒否する。"""

    config = _time_config()

    if change in ('bool', 'small', 'large'):
        config['budget']['limit_ns'] = {'bool': True, 'small': 1, 'large': 6500000001}[change]

    else:
        key, value = {
            'true': ('deterministic', True),
            'algorithm': ('algorithm', 'minimax'),
            'max_depth': ('max_depth', 5),
            'extra': ('tt', True),
            'model': ('model_id', 'not-supported'),
        }[change]

        config['search'][key] = value

    path, raw = tmp_path / 'config.json', tmp_path / 'trace.jsonl'

    path.write_text(json.dumps(config), encoding='utf-8')

    with pytest.raises(protocol.HarnessError):
        protocol.Player(
            'reversi_player/reversi.py',
            evaluation_config=path,
            evaluation_trace=raw,
            evaluation_context=_context(config),
        )

    assert not raw.exists()


def test_production_dependencies_and_reference_hash_are_preserved():
    """engineから本体/GUI/harnessへの逆依存と参照探索の改変を検出する。"""

    assert (
        hashlib.sha256(
            Path('reversi_player/ai_engine.py').read_bytes().replace(b'\r\n', b'\n')
        ).hexdigest()
        # Python 3.7対応のTypeAlias構文へ変更。探索本体の一致は別テストで検査する。
        == '2291da912920b64c53c314d89a056e6499220c737d923d9c9e4cacba2d6023cb'
    )

    source = Path(timed_search.__file__).read_text(encoding='utf-8')

    assert 'from harness' not in source and 'import harness' not in source

    assert 'import reversi' not in source and 'import web' not in source

    assert search_trace.clock_descriptor() == protocol.clock_descriptor()


@pytest.mark.parametrize('filename', ['ai_engine.py', 'timed_search.py', 'search_trace.py'])
def test_compatibility_fix_preserves_frozen_search_implementation(filename):
    """互換性修正で評価・探索・期限処理の関数やクラスを変更していないことを検査する。"""
    import ast

    class NormalizeCastTypes(ast.NodeTransformer):
        """castの型引数だけを同じ型式へ戻し、型の変更も検出できる形で比較する。"""

        def visit_Call(self, node):
            """旧Python向けに文字列化した型式を解析し、他の呼出引数は保持する。"""
            self.generic_visit(node)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == 'cast'
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                node.args[0] = ast.parse(node.args[0].value, mode='eval').body
            return node

    implementations = []
    for directory in ('baselines/search_v1', 'reversi_player'):
        tree = ast.parse(Path(directory, filename).read_text(encoding='utf-8'))
        tree = NormalizeCastTypes().visit(tree)
        implementations.append(
            [
                ast.dump(node, include_attributes=False)
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            ]
        )
    assert implementations[0] == implementations[1]


@pytest.mark.parametrize(
    'limit_ns,expected_stop',
    [
        (1000000, 200000),
        (100000000, 20000000),
        (150000000, 30000000),
        (250000000, 130000000),
        (500000000, 380000000),
        (1000000000, 880000000),
        (3000000000, 2850000000),
        (6500000000, 6175000000),
    ],
)
def test_time_reservation_has_empirical_scheduling_headroom(limit_ns, expected_stop):
    """実測後に先行確定した整数予約分を検査し、要求上限自体は変えない。"""
    result = _run(depth=1, limit_ns=limit_ns)
    assert result.stop_deadline_ns == expected_stop


@pytest.mark.parametrize(
    'at_ns,reason',
    [(19000000, 'new_depth_deadline'), (19999999, 'new_depth_deadline'), (20000000, 'time_limit')],
)
def test_short_time_soft_start_is_before_reserved_hard_stop(at_ns, reason):
    """独立に定めた19/20ms境界で完了深度1だけを保持する。"""
    counter = TripClock(10**9)
    _run(depth=1, limit_ns=100000000, clock=counter)
    calls = 0

    def clock():
        """深度1の完了後、次反復の開始判定だけで時刻を進める。"""
        nonlocal calls
        value = at_ns if calls >= counter.calls else 0
        calls += 1
        return value

    result = _run(limit_ns=100000000, clock=clock)
    value, best, _ = _expected(referee.INITIAL_ROWS, 'B', 1)
    assert result.completed_depth == result.max_depth_started == 1
    assert result.value == float(value) and result.move == min(best, key=lambda m: (m[1], m[0]))
    assert result.stop_reason == reason


def test_observed_long_pause_fits_reserved_100ms_headroom():
    """実測59.2659msの遅れを偽時計で再現し、要求100msは維持する。"""
    counter = TripClock(10**9)
    _run(depth=1, limit_ns=100000000, clock=counter)
    calls = 0

    def clock():
        """完了深度1の後で観測遅れを加え、部分結果を採用させない。"""
        nonlocal calls
        value = 20_000_000 + 59_265_900 if calls >= counter.calls else 0
        calls += 1
        return value

    result = _run(limit_ns=100000000, clock=clock)
    assert result.stop_reason == 'time_limit'
    assert result.stop_requested_ns < 100000000
    assert result.completed_depth == result.max_depth_started == 1
    assert result.stop_deadline_ns == 20_000_000
