"""AI選択器の入出力契約とreversi.pyとの接続を検証する。"""

from __future__ import annotations

import ast
import contextlib
import io
import random
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from harness import referee
from reversi_player import ai_engine
from reversi_player.reversi import Board, Pos

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'reversi_player' / 'reversi.py'
INITIAL_ROWS = (
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEWBEEE',
    'EEEBWEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
)
INITIAL_BLACK_MOVES = ((3, 2), (2, 3), (5, 4), (4, 5))
EXPECTED_POSITION_WEIGHTS = (
    (120, -20, 20, 5, 5, 20, -20, 120),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (120, -20, 20, 5, 5, 20, -20, 120),
)


def _rows(board: Board) -> tuple[str, ...]:
    """実盤面を不変な8行へ変換する。"""
    return tuple(''.join(row) for row in board.cell)


def test_position_weights_match_the_fixed_8_by_8_table() -> None:
    """暫定固定値の各行・列が事前登録表と完全一致する。"""
    assert ai_engine.POSITION_WEIGHTS == EXPECTED_POSITION_WEIGHTS
    assert len(ai_engine.POSITION_WEIGHTS) == 8
    assert all(len(row) == 8 for row in ai_engine.POSITION_WEIGHTS)
    assert all(type(weight) is int for row in ai_engine.POSITION_WEIGHTS for weight in row)


def test_positional_selector_uses_y_then_x_for_weight_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(x,y)の座標をweights[y][x]で評価し、最大の候補を選ぶ。"""
    weights = [[0 for _ in range(8)] for _ in range(8)]
    weights[1][3] = 9
    monkeypatch.setattr(ai_engine, 'POSITION_WEIGHTS', tuple(tuple(row) for row in weights))
    legal_moves = ((1, 3), (3, 1))

    selected = ai_engine.select_positional_move(INITIAL_ROWS, 'B', legal_moves)

    assert selected == (3, 1)
    assert selected in legal_moves


def test_positional_selector_uses_y_then_x_as_deterministic_tie_break() -> None:
    """同じ表値なら入力順でなく行y、列xの昇順を選ぶ。"""
    legal_moves = ((0, 2), (2, 0))

    assert ai_engine.select_positional_move(INITIAL_ROWS, 'B', legal_moves) == (2, 0)
    assert ai_engine.select_positional_move(INITIAL_ROWS, 'W', tuple(reversed(legal_moves))) == (
        2,
        0,
    )


def test_positional_selector_is_deterministic_and_does_not_consume_random_state() -> None:
    """同じ入力で同じ手を返し、盤面・候補・乱数状態を変更しない。"""
    rows = INITIAL_ROWS
    legal_moves = ((4, 5), (2, 3), (5, 4), (3, 2))
    previous_state = random.getstate()
    random.seed(20261020)
    state_before = random.getstate()
    try:
        selected = ai_engine.select_positional_move(rows, 'B', legal_moves)
        repeated = ai_engine.select_positional_move(rows, 'B', legal_moves)

        assert selected == repeated == (3, 2)
        assert rows == INITIAL_ROWS
        assert legal_moves == ((4, 5), (2, 3), (5, 4), (3, 2))
        assert random.getstate() == state_before
    finally:
        random.setstate(previous_state)


@pytest.mark.parametrize('color', ['B', 'W'])
def test_positional_selector_only_returns_a_supplied_legal_candidate(color: str) -> None:
    """最高重みのマスでも候補に含まれなければ選択対象にならない。"""
    legal_moves = ((2, 2), (3, 2))

    selected = ai_engine.select_positional_move(INITIAL_ROWS, color, legal_moves)

    assert selected == (2, 2)
    assert selected in legal_moves


def test_positional_selector_handles_empty_and_singleton_candidates() -> None:
    """空候補はNone、単独候補は評価順位に関係なくそのまま返す。"""
    assert ai_engine.select_positional_move(INITIAL_ROWS, 'B', ()) is None
    assert ai_engine.select_positional_move(INITIAL_ROWS, 'W', ((1, 1),)) == (1, 1)


def test_default_board_uses_one_ply_selector_and_applies_one_move() -> None:
    """Board既定の初手は1手評価の対称な同点から決まり、実着手は一度だけ行う。"""
    board = Board()
    apply_move = Mock(wraps=board._play)
    board._play = apply_move
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        board.genmove('B')

    assert output.getvalue() == '3 2\n'
    apply_move.assert_called_once_with('B', Pos(3, 2))
    assert _rows(board) == referee.apply_move(INITIAL_ROWS, 'B', (3, 2))


def test_random_selector_shuffles_a_copy_once_and_preserves_inputs() -> None:
    """標準乱数の1回のshuffle結果を返し、入力tupleを変更しない。"""
    seed = 20261016
    rows = INITIAL_ROWS
    legal_moves = INITIAL_BLACK_MOVES
    expected = list(legal_moves)
    expected_rng = random.Random(seed)
    expected_rng.shuffle(expected)
    previous_state = random.getstate()

    try:
        random.seed(seed)
        with patch.object(ai_engine.random, 'shuffle', wraps=random.shuffle) as shuffle:
            selected = ai_engine.select_move(rows, 'B', legal_moves)

        assert shuffle.call_count == 1
        assert selected == expected[0]
        assert selected in legal_moves
        assert rows == INITIAL_ROWS
        assert legal_moves == INITIAL_BLACK_MOVES
        assert random.getstate() == expected_rng.getstate()
    finally:
        random.setstate(previous_state)


def test_random_selector_returns_none_without_shuffle_for_empty_moves() -> None:
    """空候補ならNoneを返し、乱数状態を進めない。"""
    previous_state = random.getstate()
    try:
        random.seed(73)
        state_before = random.getstate()
        with patch.object(ai_engine.random, 'shuffle') as shuffle:
            assert ai_engine.select_move(INITIAL_ROWS, 'B', ()) is None
        assert shuffle.call_count == 0
        assert random.getstate() == state_before
    finally:
        random.setstate(previous_state)


def test_board_passes_immutable_snapshot_and_ordered_root_moves_to_selector() -> None:
    """本体が盤面snapshotと完全なy/x順候補を渡し、返却座標だけを適用する。"""
    calls: list[tuple[tuple[str, ...], str, tuple[tuple[int, int], ...]]] = []

    def selector(
        rows: tuple[str, ...], color: str, legal_moves: tuple[tuple[int, int], ...]
    ) -> tuple[int, int]:
        calls.append((rows, color, legal_moves))
        return legal_moves[1]

    board = Board(selector=selector)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        board.genmove('B')

    selected = INITIAL_BLACK_MOVES[1]
    assert calls == [(INITIAL_ROWS, 'B', INITIAL_BLACK_MOVES)]
    assert output.getvalue() == '{} {}\n'.format(*selected)
    assert _rows(board) == referee.apply_move(INITIAL_ROWS, 'B', selected)


class _FalseySelector:
    """真偽値がFalseでもBoardへ注入できる選択器を表す。"""

    def __bool__(self) -> bool:
        """default選択器への誤置換を検出するためFalseを返す。"""
        return False

    def __call__(
        self, rows: tuple[str, ...], color: str, legal_moves: tuple[tuple[int, int], ...]
    ) -> tuple[int, int]:
        """受け取った候補の末尾を返す。"""
        return legal_moves[-1]


def test_falsey_selector_is_not_replaced_by_the_default() -> None:
    """None判定でselectorを選び、偽値のcallableも尊重する。"""
    selector = _FalseySelector()
    board = Board(selector=selector)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        board.genmove('B')

    selected = INITIAL_BLACK_MOVES[-1]
    assert output.getvalue() == '{} {}\n'.format(*selected)
    assert _rows(board) == referee.apply_move(INITIAL_ROWS, 'B', selected)


def test_no_legal_move_does_not_call_injected_selector() -> None:
    """合法手なしは選択器へ渡さずNGとし、盤面を保つ。"""
    calls = 0

    def selector(
        rows: tuple[str, ...], color: str, legal_moves: tuple[tuple[int, int], ...]
    ) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        raise AssertionError('合法手なしで選択器を呼んだ')

    board = Board(selector=selector)
    board.cell = [list('W' * 8) for _ in range(8)]
    before = _rows(board)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        board.genmove('B')

    assert calls == 0
    assert output.getvalue() == 'NG\n'
    assert _rows(board) == before


class _TupleMove(tuple):
    """通常tuple以外の戻り値を拒否するテスト用型。"""


@pytest.mark.parametrize(
    'invalid_move',
    [
        None,
        True,
        (True, 2),
        (2, 2.0),
        [3, 2],
        _TupleMove((3, 2)),
        (8, 0),
        (-1, 2),
        (0, 0),
        Pos(3, 2),
    ],
)
def test_invalid_selector_result_fails_before_board_application(invalid_move: object) -> None:
    """型・範囲・候補所属が不正なら盤面適用前に失敗する。"""
    board = Board(selector=lambda rows, color, legal_moves: invalid_move)
    before = _rows(board)
    apply_move = Mock(return_value=True)
    board._play = apply_move
    output = io.StringIO()

    with (
        contextlib.redirect_stdout(output),
        pytest.raises(
            RuntimeError,
            match='selector_result_invalid',
        ),
    ):
        board.genmove('B')

    assert not apply_move.called
    assert output.getvalue() == ''
    assert _rows(board) == before


def test_selector_exception_propagates_without_board_or_stdout_change() -> None:
    """選択器例外を置換せず伝播し、着手や応答を行わない。"""
    failure = LookupError('selector failure')

    def selector(
        rows: tuple[str, ...], color: str, legal_moves: tuple[tuple[int, int], ...]
    ) -> tuple[int, int]:
        raise failure

    board = Board(selector=selector)
    before = _rows(board)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), pytest.raises(LookupError) as raised:
        board.genmove('B')

    assert raised.value is failure
    assert output.getvalue() == ''
    assert _rows(board) == before


def test_failed_real_move_does_not_emit_a_successful_coordinate() -> None:
    """_playがTrue以外を返したときgenmove成功応答を出さない。"""
    board = Board(selector=lambda rows, color, legal_moves: legal_moves[0])
    before = _rows(board)
    apply_move = Mock(return_value=False)
    board._play = apply_move
    output = io.StringIO()

    with (
        contextlib.redirect_stdout(output),
        pytest.raises(
            RuntimeError,
            match='selector_apply_failed',
        ),
    ):
        board.genmove('B')

    apply_move.assert_called_once_with('B', Pos(3, 2))
    assert output.getvalue() == ''
    assert _rows(board) == before


def test_engine_has_no_reverse_import_to_board_gui_or_harness() -> None:
    """engineの依存を標準ライブラリに限定し、循環importを防ぐ。"""
    source = Path(ai_engine.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert not any(
        name == 'reversi_player' or name.startswith(('reversi_player.', 'harness.'))
        for name in imported
    )


def test_module_execution_keeps_the_existing_command_protocol() -> None:
    """packageの-m起動でもnameとquitの標準出力・終了状態を維持する。"""
    process = subprocess.run(
        [sys.executable, '-B', '-m', 'reversi_player.reversi'],
        input='name\nquit\n',
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=5,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert process.stdout == 'sample\n'
    assert process.stderr == ''
