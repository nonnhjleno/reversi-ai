"""独立referee・固定棋譜・有理数の期待値でPhase 2の内部境界を検証する。"""

import contextlib
import hashlib
import io
import json
import math
import random
from fractions import Fraction
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from harness import referee
from reversi_player import ai_engine
from reversi_player import reversi

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / 'tests/fixtures/phase2_positions.json').read_text(encoding='utf-8'))
CASES = {case['label']: case for case in DATA['cases']}
CORNERS = ((0, 0), (7, 0), (0, 7), (7, 7))
ORACLE_WEIGHTS = (
    (0, -20, 20, 5, 5, 20, -20, 0),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (0, -20, 20, 5, 5, 20, -20, 0),
)


def _rows(board):
    """実盤面を観測するだけのsnapshotを返す。"""
    return tuple(''.join(row) for row in board.cell)


def _oracle(rows, color):
    """本番関数を使わず、固定表・独立ルール・有理数の元の式から計算する。"""
    other = 'W' if color == 'B' else 'B'
    counts = [len(referee.legal_moves(rows, side)) for side in (color, other)]
    corner = sum((rows[y][x] == color) - (rows[y][x] == other) for x, y in CORNERS)
    position = 0
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            weight = ORACLE_WEIGHTS[y][x]
            occupied_adjacent_corner = any(
                abs(x - cx) <= 1 and abs(y - cy) <= 1 and rows[cy][cx] != 'E' for cx, cy in CORNERS
            )
            if weight < 0 and occupied_adjacent_corner:
                weight = 0
            position += weight * ((cell == color) - (cell == other))
    discs = sum(row.count(color) - row.count(other) for row in rows)
    terminal = not any(counts)
    if terminal:
        score = Fraction(1000 + discs if discs > 0 else -1000 + discs if discs < 0 else 0)
    else:
        score = 40 * Fraction(corner, 4)
        score += 40 * Fraction(counts[0] - counts[1], 64) + 20 * Fraction(position, 696)
    return (corner, counts[0] - counts[1], position, discs, terminal), score


def _replay(case):
    """固定棋譜の交互手番・パス・合法性から到達盤面を独立に復元する。"""
    rows = referee.INITIAL_ROWS
    color = 'B'
    for turn in case['trace']:
        assert turn['color'] == color
        if turn['move'] is None:
            assert not referee.legal_moves(rows, color)
            assert referee.legal_moves(rows, 'W' if color == 'B' else 'B')
        else:
            rows = referee.apply_move(rows, color, tuple(turn['move']))
        color = 'W' if color == 'B' else 'B'
    assert color == case['color']
    return rows


def _transform(rows, rotation, reflected):
    """8対称の盤面を独立に構築する。選択座標の同変性は要求しない。"""
    result = [list(row) for row in rows]
    if reflected:
        result = [list(reversed(row)) for row in result]
    for _ in range(rotation):
        result = [[result[7 - x][y] for x in range(8)] for y in range(8)]
    return tuple(''.join(row) for row in result)


@pytest.mark.parametrize('label', CASES)
def test_fixed_positions_replay_and_independent_expected_values(label):
    """局面IDと保存期待値を本番AIから独立に監査する。"""
    case = CASES[label]
    rows = _replay(case)
    assert rows == tuple(case['rows'])
    assert hashlib.sha256(''.join(rows).encode()).hexdigest() == case['position_id']
    raw, score = _oracle(rows, case['color'])
    expected = case['expected']
    assert raw == tuple(
        expected[key]
        for key in (
            'corner_difference',
            'mobility_difference',
            'position_difference',
            'disc_difference',
            'terminal',
        )
    )
    assert score == Fraction(expected['score_fraction'])


@pytest.mark.parametrize('label', CASES)
def test_snapshot_callbacks_match_independent_rules_without_mutation(label):
    """仮着手の反転・合法手・連続呼出しが入力を変更しないことを確認する。"""
    rows = tuple(CASES[label]['rows'])
    initial = rows
    state = random.getstate()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        for color in ('B', 'W'):
            moves = reversi.legal_moves_for(rows, color)
            assert type(moves) is tuple
            assert moves == tuple(referee.legal_moves(rows, color))
            for move in moves:
                after = reversi.apply_move(rows, color, move)
                assert after == referee.apply_move(rows, color, move)
                assert all(type(row) is str for row in after)
                assert type(after) is tuple
                assert rows == initial
                assert reversi.legal_moves_for(after, color) == tuple(
                    referee.legal_moves(after, color)
                )
    assert rows == initial and random.getstate() == state
    assert output.getvalue() == ''


def test_snapshot_copy_cells_are_separate_and_constructor_is_not_recursive():
    """作業用Boardを明示selectorで構築し、実盤面・別コピーとの行共有を防ぐ。"""
    actual = reversi.Board()
    rows = _rows(actual)
    with patch.object(ai_engine, 'make_one_ply_selector', side_effect=AssertionError('recursive')):
        one = reversi._board_from_rows(rows, 'B')
        two = reversi._board_from_rows(rows, 'W')
        assert reversi.legal_moves_for(rows, 'B')
    for original, first, second in zip(actual.cell, one.cell, two.cell, strict=True):
        assert original is not first and first is not second and original is not second
    one.cell[0][0] = 'B'
    assert _rows(actual) == rows and _rows(two) == rows


@pytest.mark.parametrize(
    'rows',
    [
        None,
        [],
        (),
        ('EEEEEEEE',) * 7,
        ('EEEEEEEE',) * 9,
        ('EEEEEEE',) * 8,
        ('EEEEEEEE?',) * 8,
        ('EEEEEEEX',) * 8,
        (None,) * 8,
    ],
)
@pytest.mark.parametrize('operation', ['legal', 'apply', 'evaluate'])
def test_snapshot_boundary_rejects_invalid_shape_or_cells(rows, operation):
    """共有境界の違反をIndexErrorや補修へ変えずValueErrorにする。"""
    with pytest.raises(ValueError):
        if operation == 'legal':
            reversi.legal_moves_for(rows, 'B')
        elif operation == 'apply':
            reversi.apply_move(rows, 'B', (3, 2))
        else:
            ai_engine.evaluate_position(rows, 'B', reversi.legal_moves_for)


@pytest.mark.parametrize('color', [None, '', 'E', 'b', 'BW', True, 1])
@pytest.mark.parametrize('operation', ['legal', 'apply', 'evaluate'])
def test_snapshot_boundary_rejects_invalid_color(color, operation):
    """合法手がない盤面でも色の値域を省略しない。"""
    rows = referee.INITIAL_ROWS
    with pytest.raises(ValueError):
        if operation == 'legal':
            reversi.legal_moves_for(rows, color)
        elif operation == 'apply':
            reversi.apply_move(rows, color, (3, 2))
        else:
            ai_engine.evaluate_position(rows, color, reversi.legal_moves_for)


@pytest.mark.parametrize(
    'move',
    [
        None,
        (),
        (3,),
        (3, 2, 1),
        [3, 2],
        (True, 2),
        (3.0, 2),
        (-1, 2),
        (8, 2),
        (3, 8),
        (0, 0),
        (3, 3),
        ('3', 2),
    ],
)
def test_apply_rejects_invalid_or_illegal_move_without_mutation(move):
    """Pythonのbool添字や負添字を合法な座標として受理しない。"""
    rows = referee.INITIAL_ROWS
    with pytest.raises(ValueError):
        reversi.apply_move(rows, 'B', move)
    assert rows == referee.INITIAL_ROWS


@pytest.mark.parametrize('label', CASES)
def test_features_evaluation_and_each_candidate_match_independent_oracle(label):
    """保存した生特徴・各候補・正規化値の根拠を独立計算で検証する。"""
    case = CASES[label]
    rows = tuple(case['rows'])
    for color in ('B', 'W'):
        raw, score = _oracle(rows, color)
        features = ai_engine._extract_features(rows, color, reversi.legal_moves_for)
        assert (
            features.corner_difference,
            features.mobility_difference,
            features.position_difference,
            features.disc_difference,
            features.terminal,
        ) == raw
        value = ai_engine.evaluate_position(rows, color, reversi.legal_moves_for)
        assert value == pytest.approx(float(score), abs=1e-12, rel=0)
        assert math.isfinite(value)
        if not features.terminal:
            assert abs(value) <= 100
            assert all(abs(v) <= 1 for v in features.normalized)
    for candidate in case['candidates']:
        after = referee.apply_move(rows, case['color'], tuple(candidate['move']))
        value = ai_engine.evaluate_position(after, case['color'], reversi.legal_moves_for)
        assert value == pytest.approx(
            float(Fraction(candidate['features']['score_fraction'])), abs=1e-12, rel=0
        )


def test_initial_features_and_hand_calculated_first_move():
    """黒(3,2)後は角0・合法手数3対3・位置差9なので20*9/696=15/58。"""
    initial = ai_engine._extract_features(referee.INITIAL_ROWS, 'B', reversi.legal_moves_for)
    assert initial.normalized == (0, 0, 0)
    rows = referee.apply_move(referee.INITIAL_ROWS, 'B', (3, 2))
    features = ai_engine._extract_features(rows, 'B', reversi.legal_moves_for)
    assert (
        features.corner_difference,
        features.mobility_difference,
        features.position_difference,
    ) == (0, 0, 9)
    assert features.normalized == (0, 0, 9 / 696)
    assert ai_engine.evaluate_position(rows, 'B', reversi.legal_moves_for) == float(
        Fraction(15, 58)
    )
    assert features.contributions == (0, 0, float(Fraction(15, 58)))


@pytest.mark.parametrize('corner', CORNERS)
@pytest.mark.parametrize('owner', ['B', 'W'])
def test_corner_adjacency_mask_is_local_and_does_not_mutate_phase1_table(corner, owner):
    """局所の人工盤面で隣接3マスの-80が取得後に0になることを手計算で確認する。"""
    cx, cy = corner
    nx, ny = (1 if cx == 0 else 6), (1 if cy == 0 else 6)
    cells = [['E'] * 8 for _ in range(8)]
    for x, y in ((nx, cy), (cx, ny), (nx, ny)):
        cells[y][x] = 'B'
    empty_corner = tuple(''.join(row) for row in cells)
    cells[cy][cx] = owner
    occupied_corner = tuple(''.join(row) for row in cells)
    original = ai_engine.POSITION_WEIGHTS
    before = ai_engine._extract_features(empty_corner, 'B', reversi.legal_moves_for)
    after = ai_engine._extract_features(occupied_corner, 'B', reversi.legal_moves_for)
    assert before.position_difference == -80
    assert after.position_difference == 0
    assert after.corner_difference == (1 if owner == 'B' else -1)
    assert ai_engine.POSITION_WEIGHTS is original
    assert ai_engine.POSITION_WEIGHTS[cy][cx] == 120


@pytest.mark.parametrize(
    ('rows', 'expected'),
    [
        (('BBBBBBBB',) * 8, 1064),
        (('WWWWWWWW',) * 8, -1064),
        (('BBBBBBBB',) * 4 + ('WWWWWWWW',) * 4, 0),
        (('EEEEEEEE',) * 8, 0),
    ],
)
def test_terminal_outcome_dominates_nonterminal_heuristic(rows, expected):
    """人工の終局盤面は石差を含む確定勝敗にし、満杯だけを終了条件にしない。"""
    assert ai_engine.evaluate_position(rows, 'B', reversi.legal_moves_for) == expected
    assert ai_engine.evaluate_position(rows, 'W', reversi.legal_moves_for) == -expected


def test_reachable_pass_and_terminal_with_empty_squares():
    """独立の合法棋譜で到達した片側パスと空きあり終局を区別する。"""
    passing = CASES['pass']
    rows = tuple(passing['rows'])
    color = passing['color']
    assert not referee.legal_moves(rows, color)
    assert referee.legal_moves(rows, 'W' if color == 'B' else 'B')
    assert not ai_engine._extract_features(rows, color, reversi.legal_moves_for).terminal
    terminal = tuple(CASES['terminal_with_empty']['rows'])
    assert any('E' in row for row in terminal)
    assert ai_engine._extract_features(terminal, 'B', reversi.legal_moves_for).terminal


@pytest.mark.parametrize('label', CASES)
def test_evaluation_is_antisymmetric_and_invariant_under_eight_symmetries(label):
    """同点の座標選択とは分け、評価の視点と盤面対称性を確認する。"""
    rows = tuple(CASES[label]['rows'])
    expected = ai_engine.evaluate_position(rows, 'B', reversi.legal_moves_for)
    assert ai_engine.evaluate_position(rows, 'W', reversi.legal_moves_for) == -expected
    for rotation in range(4):
        for reflected in (False, True):
            transformed = _transform(rows, rotation, reflected)
            assert (
                ai_engine.evaluate_position(transformed, 'B', reversi.legal_moves_for) == expected
            )


def test_integer_numerator_preserves_mathematical_ties():
    """40*(4/64)+20*(-87/696)=0を、許容誤差による同点化なしで保つ。"""
    zero = ai_engine._PositionFeatures(0, 0, 0, 0, False)
    cancelled = ai_engine._PositionFeatures(0, 4, -87, 0, False)
    assert ai_engine._score_features(zero) == ai_engine._score_features(cancelled) == 0


@pytest.mark.parametrize('label', CASES)
def test_one_ply_selector_matches_independent_candidate_scores_and_order(label):
    """全候補を独立に採点し、最高値と(y,x)同点順、候補順からの独立を検証する。"""
    case = CASES[label]
    rows, color = tuple(case['rows']), case['color']
    moves = tuple(referee.legal_moves(rows, color))
    selector = ai_engine.make_one_ply_selector(reversi.legal_moves_for, reversi.apply_move)
    expected = tuple(case['selected']) if case['selected'] is not None else None
    state = random.getstate()
    assert selector(rows, color, moves) == expected
    assert selector(rows, color, tuple(reversed(moves))) == expected
    assert random.getstate() == state
    if moves:
        assert selector(rows, color, (moves[0],)) == moves[0]


def test_one_ply_hand_calculated_white_reply_and_difference_from_positional():
    """白の3候補は225/232、0、-5/8。位置選択との差も別の合法局面で確認する。"""
    case = CASES['after_first']
    assert [item['features']['score_fraction'] for item in case['candidates']] == [
        '225/232',
        '0',
        '-5/8',
    ]
    selector = ai_engine.make_one_ply_selector(reversi.legal_moves_for, reversi.apply_move)
    rows = tuple(case['rows'])
    assert selector(rows, 'W', tuple(referee.legal_moves(rows, 'W'))) == (2, 2)
    case = CASES['one_ply_differs']
    rows, color = tuple(case['rows']), case['color']
    moves = tuple(referee.legal_moves(rows, color))
    assert selector(rows, color, moves) != ai_engine.select_positional_move(rows, color, moves)


def test_empty_selector_calls_neither_rule_callback():
    """空候補では仮着手・評価の問い合わせを行わない。"""
    legal, apply = Mock(side_effect=AssertionError), Mock(side_effect=AssertionError)
    selector = ai_engine.make_one_ply_selector(legal, apply)
    assert selector(referee.INITIAL_ROWS, 'B', ()) is None
    legal.assert_not_called()
    apply.assert_not_called()


def test_selector_evaluates_children_from_root_color(monkeypatch):
    """子局面の手番へ評価視点を切り替えたり、得点を符号反転したりしない。"""
    case = CASES['after_first']
    rows = tuple(case['rows'])
    seen = []

    def score(child, color, callback):
        """同じ色で渡された子snapshotだけを観測する。"""
        seen.append((child, color, callback))
        return 1.0

    monkeypatch.setattr(ai_engine, 'evaluate_position', score)
    selector = ai_engine.make_one_ply_selector(reversi.legal_moves_for, reversi.apply_move)
    moves = tuple(referee.legal_moves(rows, 'W'))
    assert selector(rows, 'W', tuple(reversed(moves))) == min(moves, key=lambda m: (m[1], m[0]))
    assert len(seen) == len(moves)
    assert all(color == 'W' and callback is reversi.legal_moves_for for _, color, callback in seen)
    assert {child for child, _, _ in seen} == {
        referee.apply_move(rows, 'W', move) for move in moves
    }


@pytest.mark.parametrize('failing', ['legal', 'apply'])
def test_rule_callback_exception_propagates_without_side_effects(failing):
    """callback故障を成功や無断fallbackに変えない。"""
    error = LookupError('phase2 callback failure')
    legal = Mock(side_effect=error) if failing == 'legal' else reversi.legal_moves_for
    apply = Mock(side_effect=error) if failing == 'apply' else reversi.apply_move
    selector = ai_engine.make_one_ply_selector(legal, apply)
    rows = referee.INITIAL_ROWS
    state = random.getstate()
    with contextlib.redirect_stdout(io.StringIO()) as output:
        with pytest.raises(LookupError) as caught:
            selector(rows, 'B', tuple(referee.legal_moves(rows, 'B')))
    assert caught.value is error
    assert output.getvalue() == '' and random.getstate() == state


def test_default_board_keeps_real_cells_during_analysis_and_commits_once():
    """仮着手で実cellを変更せず、確定した白(2,2)だけを一度適用する。"""
    board = reversi.Board()
    rows = tuple(CASES['after_first']['rows'])
    board.cell = [list(row) for row in rows]
    real_rows = tuple(board.cell)
    original_factory = ai_engine.make_one_ply_selector
    observed = []

    def checked_apply(snapshot, color, move):
        """各仮着手の前後で実Boardを監視する。"""
        assert _rows(board) == rows
        assert all(a is b for a, b in zip(real_rows, board.cell, strict=True))
        result = reversi.apply_move(snapshot, color, move)
        assert _rows(board) == rows
        observed.append(move)
        return result

    board._selector = original_factory(reversi.legal_moves_for, checked_apply)
    state = random.getstate()
    with patch.object(board, '_play', wraps=board._play) as play:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            board.genmove('W')
    assert set(observed) == set(referee.legal_moves(rows, 'W'))
    assert len(observed) == len(set(observed))
    assert output.getvalue() == '2 2\n'
    play.assert_called_once_with('W', reversi.Pos(2, 2))
    assert _rows(board) == referee.apply_move(rows, 'W', (2, 2))
    assert random.getstate() == state


def test_default_factory_receives_only_stateless_module_callbacks():
    """実Boardへの束縛や実cellのclosure共有をfactory境界で防ぐ。"""
    original = ai_engine.make_one_ply_selector
    with patch.object(ai_engine, 'make_one_ply_selector', wraps=original) as factory:
        board = reversi.Board()
    factory.assert_called_once_with(reversi.legal_moves_for, reversi.apply_move)
    contents = [cell.cell_contents for cell in board._selector.__closure__]
    assert set(contents) == {reversi.legal_moves_for, reversi.apply_move}
    assert all(getattr(callback, '__self__', None) is None for callback in contents)


def test_nonterminal_bounds_over_independent_legal_trajectory():
    """固定の独立対局の両色視点で、正規化と有限値を終局まで確認する。"""
    rows, color, rng = referee.INITIAL_ROWS, 'B', random.Random(20261030)
    while True:
        moves = referee.legal_moves(rows, color)
        for side in ('B', 'W'):
            features = ai_engine._extract_features(rows, side, reversi.legal_moves_for)
            value = ai_engine.evaluate_position(rows, side, reversi.legal_moves_for)
            assert math.isfinite(value)
            if not features.terminal:
                assert abs(value) <= 100
                assert all(abs(v) <= 1 for v in features.normalized)
        other = 'W' if color == 'B' else 'B'
        if not moves:
            if not referee.legal_moves(rows, other):
                break
        else:
            rows = referee.apply_move(rows, color, rng.choice(moves))
        color = other
