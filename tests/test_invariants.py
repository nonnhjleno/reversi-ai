"""合法対局から生成した局面で、手計算の固定試験とは別にルール不変条件を検査する。"""

import random

import pytest
from hypothesis import example, given, settings, strategies as st

from harness import referee
from reversi_player.reversi import Board, Pos


def _reachable_rows(choices: list[int]) -> tuple[referee.BoardRows, str]:
    """初期盤面から合法手だけを選ぶ。整数列は候補index、passは手番だけを進める。"""
    rows = referee.INITIAL_ROWS
    color = 'B'
    for choice in choices:
        legal = referee.legal_moves(rows, color)
        if not legal:
            color = 'W' if color == 'B' else 'B'
            legal = referee.legal_moves(rows, color)
            if not legal:
                break
        rows = referee.apply_move(rows, color, legal[choice % len(legal)])
        color = 'W' if color == 'B' else 'B'
    return rows, color


def _rows(board: Board) -> referee.BoardRows:
    """本体の可変cell[y][x]を比較用の不変な行tupleへ読み取る。"""
    return tuple(''.join(row) for row in board.cell)


@settings(max_examples=40, derandomize=True, database=None, deadline=None, print_blob=True)
@example(choices=[0] * 15)
@example(choices=[63] * 54)
@example(choices=[0] * 60)
@example(choices=[63] * 60)
@given(choices=st.lists(st.integers(min_value=0, max_value=63), max_size=60))
def test_reachable_moves_preserve_board_and_capture_invariants(choices: list[int]) -> None:
    """到達可能盤面で両色の合法性と全合法手の石数・反転・非破壊性を検査する。"""
    rows, _ = _reachable_rows(choices)
    occupied = sum(cell != 'E' for row in rows for cell in row)
    assert len(rows) == 8 and all(len(row) == 8 for row in rows)
    assert set(''.join(rows)) <= {'B', 'W', 'E'}
    assert 4 <= occupied <= 64
    for color in ('B', 'W'):
        board = Board()
        board.cell = [list(row) for row in rows]
        legal = referee.legal_moves(rows, color)
        for position_y in range(8):
            for position_x in range(8):
                position = (position_x, position_y)
                assert board._ask(color, Pos(*position)) == (position in legal)
                assert _rows(board) == rows
                if position not in legal:
                    assert not board._play(color, Pos(*position))
                    assert _rows(board) == rows
                    with pytest.raises(ValueError, match='Illegal move'):
                        referee.apply_move(rows, color, position)
                    assert _rows(board) == rows
        opponent = 'W' if color == 'B' else 'B'
        for position in legal:
            candidate = Board()
            candidate.cell = [list(row) for row in rows]
            updated = referee.apply_move(rows, color, position)
            assert candidate._play(color, Pos(*position))
            assert _rows(candidate) == updated
            assert rows[position[1]][position[0]] == 'E'
            assert updated[position[1]][position[0]] == color
            # 反転数ではなく配置石数だけが占有数を増やし、相手の石は必ず減る。
            assert sum(cell != 'E' for row in updated for cell in row) == occupied + 1
            assert ''.join(updated).count(opponent) < ''.join(rows).count(opponent)
            assert ''.join(updated).count(color) >= ''.join(rows).count(color) + 2
            assert set(''.join(updated)) <= {'B', 'W', 'E'}
        assert rows == _rows(board)


@settings(max_examples=40, derandomize=True, database=None, deadline=None, print_blob=True)
@example(choices=[0] * 15)
@example(choices=[63] * 54)
@example(choices=[0] * 60)
@example(choices=[63] * 60)
@given(choices=st.lists(st.integers(min_value=0, max_value=63), max_size=60))
def test_reachable_genmove_pass_and_result_are_consistent(choices: list[int]) -> None:
    """合法手があればpassせず、なければ不変なpassとなり、両色不可だけを終局にする。"""
    rows, _ = _reachable_rows(choices)
    board = Board()
    board.cell = [list(row) for row in rows]
    expected_result = referee.result(rows)
    assert board.result() == expected_result
    assert _rows(board) == rows
    if expected_result != 'NG':
        black, white = ''.join(rows).count('B'), ''.join(rows).count('W')
        assert expected_result == ('B' if black > white else 'W' if white > black else 'D')
    state = random.getstate()
    try:
        random.seed(0)
        for color in ('B', 'W'):
            board.cell = [list(row) for row in rows]
            legal = referee.legal_moves(rows, color)
            position = board._genmove(color)
            if legal:
                assert position is not None
                assert (position.x, position.y) in legal
                assert _rows(board) == referee.apply_move(rows, color, (position.x, position.y))
            else:
                assert position is None
                assert _rows(board) == rows
    finally:
        random.setstate(state)
