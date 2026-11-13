"""独立refereeと有理数Negamaxで浅いMinimaxの意味・計数・接続を検証する。"""

import contextlib
from dataclasses import FrozenInstanceError
from fractions import Fraction
import io
import json
from pathlib import Path
import random
from unittest.mock import Mock, patch

import pytest

from harness import referee
from reversi_player import ai_engine, reversi

DATA = json.loads(
    (Path(__file__).parent / 'fixtures/phase2_positions.json').read_text(encoding='utf-8')
)
CASES = {case['label']: case for case in DATA['cases']}
# Phase 2の人手表を独立に固定する。対象engineから期待値を読み込まない。
WEIGHTS = (
    (0, -20, 20, 5, 5, 20, -20, 0),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (0, -20, 20, 5, 5, 20, -20, 0),
)
CORNERS = ((0, 0), (7, 0), (0, 7), (7, 7))


def _opponent(color):
    """独立オラクル用の手番交代。"""
    return 'W' if color == 'B' else 'B'


def _edge_stability_difference(rows, color):
    """独立辺走査で角接続石を数え、角の重複を避けて差を返す。"""
    other = _opponent(color)
    anchored = set()
    edges = (
        ((0, 0), ((1, 0), (0, 1))),
        ((7, 0), ((-1, 0), (0, 1))),
        ((0, 7), ((1, 0), (0, -1))),
        ((7, 7), ((-1, 0), (0, -1))),
    )
    for (cx, cy), directions in edges:
        owner = rows[cy][cx]
        if owner not in ('B', 'W'):
            continue
        for dx, dy in directions:
            x, y = cx + dx, cy + dy
            while 0 <= x < 8 and 0 <= y < 8 and rows[y][x] == owner:
                if (x, y) not in CORNERS:
                    anchored.add((x, y, owner))
                x, y = x + dx, y + dy
    return sum(owner == color for _, _, owner in anchored) - sum(
        owner == other for _, _, owner in anchored
    )


def _evaluate(rows, color):
    """独立ルールと候補の10%評価混合をFractionで計算する。"""
    other = _opponent(color)
    own, opposing = referee.legal_moves(rows, color), referee.legal_moves(rows, other)
    discs = sum(row.count(color) - row.count(other) for row in rows)
    if not own and not opposing:
        return Fraction(1000 + discs if discs > 0 else -1000 + discs if discs < 0 else 0)
    corner = sum((rows[y][x] == color) - (rows[y][x] == other) for x, y in CORNERS)
    position = 0
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            weight = WEIGHTS[y][x]
            if weight < 0 and any(
                abs(x - cx) <= 1 and abs(y - cy) <= 1 and rows[cy][cx] != 'E' for cx, cy in CORNERS
            ):
                weight = 0
            position += weight * ((cell == color) - (cell == other))
    base = (
        40 * Fraction(corner, 4)
        + 40 * Fraction(len(own) - len(opposing), 64)
        + 20 * Fraction(position, 696)
    )
    stable = 100 * Fraction(_edge_stability_difference(rows, color), 24)
    return Fraction(90, 100) * base + Fraction(10, 100) * stable


def _oracle(rows, turn, depth):
    """手番視点のNegamaxを全列挙する。本番のroot固定Minimaxとは符号処理を変える。"""
    moves = referee.legal_moves(rows, turn)
    other = _opponent(turn)
    if depth == 0 or (not moves and not referee.legal_moves(rows, other)):
        return _evaluate(rows, turn), (), (1, 1, 0)
    if not moves:
        value, _, (nodes, leaves, passes) = _oracle(rows, other, depth)
        return -value, (), (1 + nodes, leaves, 1 + passes)
    values, counts = {}, [1, 0, 0]
    for move in moves:
        value, _, child_counts = _oracle(referee.apply_move(rows, turn, move), other, depth - 1)
        values[move] = -value
        counts = [a + b for a, b in zip(counts, child_counts, strict=True)]
    best = max(values.values())
    return best, tuple(move for move, value in values.items() if value == best), tuple(counts)


def _search(rows, color, depth):
    """本番callbackを使って対象探索を呼ぶ。"""
    return ai_engine.search_minimax(rows, color, depth, reversi.legal_moves_for, reversi.apply_move)


@pytest.mark.parametrize('label', CASES)
@pytest.mark.parametrize('color', ['B', 'W'])
@pytest.mark.parametrize('depth', [1, 2])
def test_all_fixed_positions_match_independent_negamax(label, color, depth):
    """全区分の局面で白root・相手の最小化・最善手集合・全木計数を照合する。"""
    rows = tuple(CASES[label]['rows'])
    expected, best, counts = _oracle(rows, color, depth)
    actual = _search(rows, color, depth)
    assert actual.value == float(expected)
    assert actual.move == (min(best, key=lambda m: (m[1], m[0])) if best else None)
    assert (actual.nodes, actual.leaves, actual.passes) == counts
    assert actual.depth == depth


@pytest.mark.parametrize('label', CASES)
@pytest.mark.parametrize('depth', [0, 3, 4])
def test_depth_zero_through_four_and_terminal_early_stop(label, depth):
    """全区分の固定局面で深度4まで、終局は残深度にかかわらず正確に止める。"""
    case = CASES[label]
    rows, color = tuple(case['rows']), case['color']
    expected, best, counts = _oracle(rows, color, depth)
    actual = _search(rows, color, depth)
    assert actual.value == float(expected)
    assert actual.move in best if best else actual.move is None
    assert (actual.nodes, actual.leaves, actual.passes) == counts


@pytest.mark.parametrize('depth,nodes,leaves', [(0, 1, 1), (1, 5, 4), (2, 17, 12)])
def test_initial_tree_has_known_counts(depth, nodes, leaves):
    """初期盤面は4着手、各子に3応手。callback回数をnodeに含めない。"""
    result = _search(referee.INITIAL_ROWS, 'B', depth)
    assert (result.nodes, result.leaves, result.passes) == (nodes, leaves, 0)


def test_root_and_internal_pass_preserve_placement_depth():
    """固定合法棋譜の末尾で、rootと内部のパス後にも同じ予算で石を置く。"""
    trace = CASES['pass']['trace']
    for length in (len(trace) - 1, len(trace)):
        rows, color = referee.INITIAL_ROWS, 'B'
        for step in trace[:length]:
            assert step['color'] == color
            if step['move'] is not None:
                rows = referee.apply_move(rows, color, tuple(step['move']))
            else:
                assert not referee.legal_moves(rows, color)
            color = _opponent(color)
        expected, best, counts = _oracle(rows, color, 4)
        result = _search(rows, color, 4)
        assert result.value == float(expected)
        assert (result.nodes, result.leaves, result.passes) == counts
        assert result.passes > 0
        assert result.move in best if best else result.move is None
    root_pass = _search(tuple(CASES['pass']['rows']), 'B', 1)
    assert root_pass.move is None and root_pass.passes == 1
    # 白の2候補はいずれも終局ではない。1 placement後に通常評価で止める。
    assert (root_pass.nodes, root_pass.leaves) == (4, 2)
    expected, _, _ = _oracle(tuple(CASES['pass']['rows']), 'B', 1)
    assert root_pass.value == float(expected)
    assert abs(_search(tuple(CASES['pass']['rows']), 'B', 2).value) > 1000


@pytest.mark.parametrize('label', CASES)
def test_depth_one_matches_one_ply_and_order_is_deterministic(label):
    """深度1はPhase 2と同じで、候補順とseedで同点規則を変えない。"""
    case = CASES[label]
    rows, color = tuple(case['rows']), case['color']
    moves = tuple(referee.legal_moves(rows, color))
    one = ai_engine.make_one_ply_selector(reversi.legal_moves_for, reversi.apply_move)
    search = ai_engine.make_minimax_selector(reversi.legal_moves_for, reversi.apply_move, depth=1)
    assert search(rows, color, tuple(reversed(moves))) == one(rows, color, moves)


@pytest.mark.parametrize('value', [-1, 5, True, 1.5, '2', None])
def test_search_rejects_invalid_depth(value):
    """過大深度やboolを通常intと同一視しない。"""
    with pytest.raises(ValueError, match='depth'):
        _search(referee.INITIAL_ROWS, 'B', value)


@pytest.mark.parametrize('value', [0, -1, 5, True, 1.5, '2', None])
def test_factory_rejects_invalid_depth(value):
    """factoryは診断用depth0を着手選択として受理しない。"""
    with pytest.raises(ValueError, match='depth'):
        ai_engine.make_minimax_selector(reversi.legal_moves_for, reversi.apply_move, depth=value)


@pytest.mark.parametrize('rows,color', [((), 'B'), (referee.INITIAL_ROWS, 'X')])
def test_search_rejects_invalid_snapshot(rows, color):
    """不正snapshotと色をcallbackへ渡す前に拒否する。"""
    with pytest.raises(ValueError):
        _search(rows, color, 2)


def test_selector_empty_input_has_no_callbacks():
    """空候補で探索や仮着手を開始しない。"""
    legal, apply = Mock(side_effect=AssertionError), Mock(side_effect=AssertionError)
    selector = ai_engine.make_minimax_selector(legal, apply)
    assert selector(referee.INITIAL_ROWS, 'B', ()) is None
    legal.assert_not_called()
    apply.assert_not_called()


@pytest.mark.parametrize('failing', ['legal', 'apply'])
def test_callback_failures_propagate(failing):
    """ルールの故障を部分探索の成功へ変換しない。"""
    error = LookupError('search callback failure')
    legal = Mock(side_effect=error) if failing == 'legal' else reversi.legal_moves_for
    apply = Mock(side_effect=error) if failing == 'apply' else reversi.apply_move
    with pytest.raises(LookupError) as caught:
        ai_engine.search_minimax(referee.INITIAL_ROWS, 'B', 2, legal, apply)
    assert caught.value is error


def test_search_is_immutable_repeatable_and_silent():
    """結果と入力を保持し、繰り返しや診断呼出しで副作用を作らない。"""
    rows = tuple(CASES['after_first']['rows'])
    state, output, errors = random.getstate(), io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        first = _search(rows, 'W', 2)
        assert first == _search(rows, 'W', 2)
    assert rows == tuple(CASES['after_first']['rows'])
    assert random.getstate() == state and output.getvalue() == errors.getvalue() == ''
    with pytest.raises(FrozenInstanceError):
        first.value = 0


def test_default_board_searches_copies_and_commits_once():
    """探索途中の仮着手では実cellを保持し、選択座標だけを確定する。"""
    board = reversi.Board()
    rows = tuple(CASES['after_first']['rows'])
    board.cell = [list(row) for row in rows]
    real_rows = tuple(board.cell)
    expected, best, _ = _oracle(rows, 'W', 2)
    seen = []

    def checked_apply(snapshot, color, move):
        """仮着手の前後で実Boardの行の同一性と内容を監視する。"""
        assert tuple(''.join(r) for r in board.cell) == rows
        assert all(a is b for a, b in zip(real_rows, board.cell, strict=True))
        seen.append(move)
        return reversi.apply_move(snapshot, color, move)

    board._selector = ai_engine.make_minimax_selector(reversi.legal_moves_for, checked_apply)
    with patch.object(board, '_play', wraps=board._play) as play:
        move = board._genmove('W')
    assert (move.x, move.y) == min(best, key=lambda m: (m[1], m[0]))
    assert play.call_count == 1 and len(seen) > len(referee.legal_moves(rows, 'W'))
    assert tuple(''.join(r) for r in board.cell) == referee.apply_move(rows, 'W', (move.x, move.y))
    assert float(expected) == _search(rows, 'W', 2).value
