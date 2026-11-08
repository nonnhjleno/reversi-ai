"""Phase 4専用の独立探索・境界値・実プロセスtrace回帰。Fullで実行する。"""

import contextlib
import hashlib
import io
import json
import random
from pathlib import Path
from functools import cache
from unittest.mock import Mock, patch

import pytest

from harness import protocol, referee, search_budget
from reversi_player import ai_engine, reversi, search_trace
from test_ai_phase3 import CASES, _oracle


@cache
def _comparison_reference(rows, color, depth):
    """不変入力ごとの独立期待値とMinimaxだけを共有し、候補探索は毎回実行する。"""
    expected = _oracle(rows, color, depth)
    reference = ai_engine.search_minimax(
        rows, color, depth, referee.legal_moves, referee.apply_move
    )
    return expected, reference


@pytest.mark.parametrize('label', CASES)
@pytest.mark.parametrize('depth', range(5))
@pytest.mark.parametrize('ordered', [False, True])
@pytest.mark.parametrize('color', ['B', 'W'])
def test_alpha_beta_matches_independent_oracle_and_reference(label, depth, ordered, color):
    """有理数Negamaxと保全Minimaxを使い、値・最善集合・同点規則を照合する。"""
    rows = tuple(CASES[label]['rows'])
    (expected, best, _), reference = _comparison_reference(rows, color, depth)
    result = ai_engine.search_alpha_beta(
        rows, color, depth, referee.legal_moves, referee.apply_move, ordered=ordered
    )
    assert result.value == float(expected) == reference.value
    canonical = min(best, key=lambda m: (m[1], m[0])) if best else None
    assert result.move == reference.move == canonical
    assert 1 <= result.nodes <= reference.nodes
    assert result.leaves <= reference.leaves
    assert result.passes <= reference.passes
    assert result.cutoffs >= 0


def test_fail_low_equal_bound_does_not_replace_exact_best_move():
    """後続候補の真値0・上限5を、先行候補の厳密値5との同点と誤認しない。"""
    states = [tuple(['B' * i + 'E' * (8 - i)] + ['EEEEEEEE'] * 7) for i in range(7)]
    a, b = (0, 0), (1, 0)
    edges = {0: {a: 1, b: 2}, 1: {a: 3, b: 4}, 2: {a: 5, b: 6}}
    scores = {3: 5.0, 4: 9.0, 5: 5.0, 6: 0.0}

    def legal(rows, color):
        """実リバーシとは別の既知有限木を列挙する。"""
        return tuple(edges.get(states.index(rows), {}))

    def apply(rows, color, move):
        """入力を変更せず、既知の子状態を返す。"""
        return states[edges[states.index(rows)][move]]

    def score(rows, color, callback):
        """手で定義した独立leaf値を返す。"""
        return scores[states.index(rows)]

    with patch.object(ai_engine, 'evaluate_position', side_effect=score):
        for ordered in (False, True):
            result = ai_engine.search_alpha_beta(states[0], 'B', 2, legal, apply, ordered=ordered)
            assert (result.move, result.value) == (a, 5)
            assert (result.nodes, result.leaves, result.passes, result.cutoffs) == (6, 3, 0, 1)


def test_pass_known_count_and_terminal_leaf():
    """rootパスは残深度を保ち、終局はroot1node・leaf1とする。"""
    result = ai_engine.search_alpha_beta(
        tuple(CASES['pass']['rows']), 'B', 1, referee.legal_moves, referee.apply_move
    )
    assert (result.move, result.nodes, result.leaves, result.passes) == (None, 4, 2, 1)
    terminal = ('BBBBBBBB',) * 8
    result = ai_engine.search_alpha_beta(terminal, 'W', 4, referee.legal_moves, referee.apply_move)
    assert (result.value, result.nodes, result.leaves, result.passes) == (-1064, 1, 1, 0)


def test_search_is_silent_immutable_and_board_commits_once():
    """探索コピーと実Board、乱数状態を分離し、確定回数を監視する。"""
    board = reversi.Board()
    rows, state = tuple(''.join(row) for row in board.cell), random.getstate()
    output, errors = io.StringIO(), io.StringIO()
    seen = []

    def apply(snapshot, color, move):
        """仮着手の間は実盤面が開始時のままであることを検査する。"""
        assert tuple(''.join(row) for row in board.cell) == rows
        seen.append(move)
        return reversi.apply_move(snapshot, color, move)

    board._selector = ai_engine.make_alpha_beta_selector(reversi.legal_moves_for, apply)
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        with patch.object(board, '_play', wraps=board._play) as commit:
            move = board._genmove('B')
    assert commit.call_count == 1 and seen and (move.x, move.y) == (3, 2)
    assert random.getstate() == state and output.getvalue() == errors.getvalue() == ''


@pytest.mark.parametrize('depth', [-1, 5, True, 1.5, '2', None])
def test_invalid_search_depth(depth):
    """通常int以外や未対応深度を拒否する。"""
    with pytest.raises(ValueError):
        ai_engine.search_alpha_beta(
            referee.INITIAL_ROWS, 'B', depth, referee.legal_moves, referee.apply_move
        )


@pytest.mark.parametrize('depth', [0, 5, True])
def test_invalid_selector_depth(depth):
    """selectorには深度0診断を許可しない。"""
    with pytest.raises(ValueError):
        ai_engine.make_alpha_beta_selector(referee.legal_moves, referee.apply_move, depth=depth)


def test_bad_inputs_and_callback_failures_are_not_success():
    """入力違反とルール故障を部分探索の成功へ変えない。"""
    for rows, color, ordered in [
        ((), 'B', True),
        (referee.INITIAL_ROWS, 'X', True),
        (referee.INITIAL_ROWS, 'B', 1),
    ]:
        with pytest.raises(ValueError):
            ai_engine.search_alpha_beta(
                rows, color, 2, referee.legal_moves, referee.apply_move, ordered=ordered
            )
    for legal, apply in [
        (Mock(side_effect=LookupError), referee.apply_move),
        (referee.legal_moves, Mock(side_effect=LookupError)),
    ]:
        with pytest.raises(LookupError):
            ai_engine.search_alpha_beta(referee.INITIAL_ROWS, 'B', 2, legal, apply)
    legal, apply = Mock(side_effect=AssertionError), Mock(side_effect=AssertionError)
    selector = ai_engine.make_alpha_beta_selector(legal, apply)
    assert selector(referee.INITIAL_ROWS, 'B', ()) is None


def _config():
    """実AIが対応する最小の明示的設定を返す。"""
    return {
        'format_version': 2,
        'budget': {'kind': 'depth', 'limit_depth': 2},
        'search': {
            'algorithm': 'alphabeta',
            'deterministic': True,
            'counting_definition_id': 'minimax-call-v1',
        },
    }


def _context(config):
    """親プロセスとして設定hashとidentityを作る。"""
    return {
        'run_id': 'phase4-test',
        'purpose': 'game',
        'pair_id': 'pair',
        'leg': 0,
        'player': 'A',
        'requested_config_sha256': hashlib.sha256(
            search_budget.canonical_json(config).encode()
        ).hexdigest(),
        'target_sha256': hashlib.sha256(Path('reversi_player/reversi.py').read_bytes()).hexdigest(),
    }


def test_real_process_trace_has_actual_search_and_complete_lifecycle(tmp_path):
    """全コマンドを実行し、模擬counterでない探索結果と正式trace検査を照合する。"""
    config = _config()
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
        assert player.command('ask B 3 2') == ['OK']
        assert player.command('result') == ['NG']
        assert player.command('genmove B') == ['3 2']
        player.commands[-1]['phase'] = 'normal_move'
        assert player.command('play W 2 2') == ['OK']
    events = [json.loads(line) for line in raw.read_text(encoding='utf-8').splitlines()]
    identity = player.evaluation_context
    validated = search_budget.validate_trace_events(
        events, config, player.commands, context['target_sha256'], identity
    )
    assert validated['status'] == 'verified', validated
    assert (
        search_budget.validate_child_lifecycle(events, player.commands, player.lifecycle, identity)[
            'status'
        ]
        == 'verified'
    )
    event = next(e for e in events if e['type'] == 'genmove')
    expected = ai_engine.search_alpha_beta(
        referee.INITIAL_ROWS, 'B', 2, reversi.legal_moves_for, reversi.apply_move
    )
    assert (event['nodes'], event['completed_depth'], event['max_depth_started']) == (
        expected.nodes,
        2,
        2,
    )
    assert event['search_elapsed_ns'] == event['search_end_ns'] - event['search_start_ns'] > 0
    assert events[-1]['type'] == 'process_exit_ready'
    assert all(e.get('model_id') is None for e in events)
    assert not player.stderr_tail


@pytest.mark.parametrize('label', ['pass', 'terminal_with_empty'])
def test_real_protocol_pass_and_terminal_are_no_search(tmp_path, label):
    """合法棋譜を実プロセスへ再生し、探索なしの問い合わせを通常着手と分ける。"""
    case = CASES[label]
    config, path, raw = _config(), tmp_path / 'config.json', tmp_path / 'trace.jsonl'
    path.write_text(json.dumps(config), encoding='utf-8')
    with protocol.Player(
        'reversi_player/reversi.py',
        evaluation_config=path,
        evaluation_trace=raw,
        evaluation_context=_context(config),
    ) as player:
        for step in case['trace']:
            if step['move'] is not None:
                assert player.command('play {} {} {}'.format(step['color'], *step['move'])) == [
                    'OK'
                ]
        assert player.command('genmove ' + case['color']) == ['NG']
    events = [json.loads(line) for line in raw.read_text(encoding='utf-8').splitlines()]
    event = next(e for e in events if e['type'] == 'genmove')
    assert event['phase'] == ('pass_probe' if label == 'pass' else 'terminal_probe')
    assert (event['nodes'], event['completed_depth'], event['max_depth_started']) == (0, 0, 0)
    assert event['stop_reason'] == 'no_legal_move'


@pytest.mark.parametrize(
    'change',
    [
        'time',
        'nodes',
        'none',
        'depth0',
        'depth5',
        'bool',
        'model',
        'unknown_key',
        'nondeterministic',
        'definition',
    ],
)
def test_unsupported_config_is_rejected_without_trace_or_stdout(tmp_path, change):
    """未対応設定は起動前に拒否し、実効一致を申告しない。"""
    config = _config()
    if change in ('time', 'nodes', 'none'):
        config['budget'] = {'kind': change}
    elif change in ('depth0', 'depth5', 'bool'):
        config['budget']['limit_depth'] = {'depth0': 0, 'depth5': 5, 'bool': True}[change]
    else:
        key, value = {
            'model': ('model_id', 'bad'),
            'unknown_key': ('tt', True),
            'nondeterministic': ('deterministic', False),
            'definition': ('counting_definition_id', 'fake'),
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


def test_clock_matches_contract_without_reverse_import():
    """時計属性を現行契約と照合し、AIからharnessへの依存を防ぐ。"""
    assert search_trace.clock_descriptor() == protocol.clock_descriptor()
    for path in Path('reversi_player').glob('*.py'):
        if path.name != 'web.py':
            assert 'from harness' not in path.read_text(encoding='utf-8')
            assert 'import harness' not in path.read_text(encoding='utf-8')
