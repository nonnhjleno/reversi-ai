"""n-tupleの数学とルール境界を、候補から独立した計算で照合する。"""

from collections import Counter
from itertools import combinations, product
from math import isfinite, tanh
import random

import pytest

from harness import referee
from reversi_player import ntuple, phase6_player


def _boards(rows):
    """転置と行・列の逆順だけで8つの正方形像を作る。"""
    for lines in (rows, tuple(zip(*rows))):
        for flip_y, flip_x in product((False, True), repeat=2):
            yield tuple(
                tuple(row[::-1] if flip_x else row) for row in (lines[::-1] if flip_y else lines)
            )


def _independent_lines():
    """変換した座標ラベル盤から端点対の軌道を調べ、代表を独立に求める。"""
    cells = tuple(tuple((x, y) for x in range(8)) for y in range(8))
    boards = tuple(_boards(cells))
    reps = set()
    lines = set()
    for (x, y), (u, v) in combinations(sorted(cell for row in cells for cell in row), 2):
        dx, dy = u - x, v - y
        distance = max(abs(dx), abs(dy))
        if distance not in (1, 2) or not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
            continue
        line = ((x, y), (u, v))
        if distance == 2:
            line = (line[0], ((x + u) // 2, (y + v) // 2), line[1])
        lines.add(line)
        variants = [tuple(board[b][a] for a, b in line) for board in boards]
        reps.add(min(variants + [item[::-1] for item in variants]))
    return tuple(sorted(reps, key=lambda line: (len(line), line))), lines


_LINES, _SEGMENTS = _independent_lines()
_TERNARY = {
    n: {digits: index for index, digits in enumerate(product('EBW', repeat=n))} for n in (2, 3)
}


def _oracle_features(rows, turn):
    """盤面の8つの像から、列挙済みのパターン表を引く。"""
    features = Counter()
    offset = {'B': 0, 'W': 936}[turn]
    for coords in _LINES:
        table = _TERNARY[len(coords)]
        for image in _boards(rows):
            features[offset + table[tuple(image[y][x] for x, y in coords)]] += 1
        offset += len(table)
    return tuple(sorted(features.items()))


def _oracle_value(rows, turn, weights):
    """参照回数の整数倍から黒視点のlogitと価値を求める。"""
    logit = sum(weights[index] * count for index, count in _oracle_features(rows, turn))
    return logit, tanh(logit)


def _swap(turn):
    """リバーシの相手色を返す。"""
    return {'B': 'W', 'W': 'B'}[turn]


def _reachable():
    """固定seedの合法棋譜を盤面、手番、棋譜履歴として列挙する。"""
    rng = random.Random(20261118)
    for _ in range(8):
        rows = referee.INITIAL_ROWS
        turn = 'B'
        history = []
        while True:
            yield rows, turn, tuple(history)
            if referee.result(rows) != 'NG':
                break
            moves = referee.legal_moves(rows, turn)
            move = rng.choice(moves) if moves else None
            history.append(move)
            if move is not None:
                rows = referee.apply_move(rows, turn, move)
            turn = _swap(turn)


def _weight_model():
    """総和を独立の通常加算で厳密に数えられる2のべき乗分母の重み。"""
    return ntuple.NtupleModel(tuple((i * 43 % 107 - 53) / 512 for i in range(1872)))


def test_tuple_inventory_and_all_geometric_lines():
    assert tuple(map(len, ntuple.TUPLES)).count(2) == 32
    assert tuple(map(len, ntuple.TUPLES)).count(3) == 24
    assert ntuple.TUPLES == _LINES
    assert ntuple.REFERENCE_COUNT == 448
    assert ntuple.WEIGHT_COUNT == 1872
    assert sum(ntuple.LUT_SIZES) == 936
    images = {
        min(line, line[::-1])
        for representative in ntuple.TUPLES
        for line in ntuple._orbit(representative)
    }
    assert images == _SEGMENTS
    assert len(_SEGMENTS) == 210 + 168


def test_all_d4_coordinates_order_inverses_and_closure():
    cells = tuple((x, y) for y in range(8) for x in range(8))
    grid = tuple(cells[i : i + 8] for i in range(0, 64, 8))
    rotated = tuple(tuple(col[::-1]) for col in zip(*grid))
    rotations = [grid]
    for _ in range(3):
        rotations.append(rotated)
        rotated = tuple(tuple(col[::-1]) for col in zip(*rotated))
    expected = rotations + [
        tuple(row[::-1] for row in grid),
        grid[::-1],
        tuple(zip(*grid)),
        tuple(row[::-1] for row in tuple(zip(*grid))[::-1]),
    ]
    actual = tuple(tuple(ntuple._images(x, y)[i] for x, y in cells) for i in range(8))
    # 独立の行列操作は出力位置→元座標なので、逆変換を対応させる。
    assert actual == tuple(
        tuple(cell for row in expected[i] for cell in row) for i in (0, 3, 2, 1, 4, 5, 6, 7)
    )
    assert len(set(actual)) == 8
    assert all(set(perm) == set(cells) for perm in actual)

    def compose(first, second):
        """座標写像を順に適用する。"""
        return tuple(second[x + 8 * y] for x, y in first)

    for index, perm in enumerate(actual):
        assert any(
            compose(perm, other) == cells and compose(other, perm) == cells for other in actual
        )
        assert all(compose(perm, other) in actual for other in actual)
        if index in range(4, 8):
            assert compose(perm, perm) == cells
    half_turn = compose(actual[1], actual[1])
    assert half_turn == actual[2]
    assert compose(half_turn, half_turn) == cells


@pytest.mark.parametrize(
    'length,expected,digits',
    [(n, i, digits) for n, table in _TERNARY.items() for digits, i in table.items()],
)
def test_pattern_all_codes_and_order(length, expected, digits):
    rows = ('EEEEEEEE', ''.join(digits).ljust(8, 'E'), *('EEEEEEEE',) * 6)
    coords = tuple((i, 1) for i in range(length))
    index = ntuple.pattern_id(rows, coords)
    assert index == expected
    decoded = []
    for _ in range(length):
        index, remainder = divmod(index, 3)
        decoded.append('EBW'[remainder])
    assert tuple(decoded[::-1]) == digits
    assert ntuple.pattern_id(rows, coords[::-1]) == _TERNARY[length][digits[::-1]]


@pytest.mark.parametrize(
    'coordinates',
    [
        (),
        ((0, 0),),
        ((0, 0), (0, 1), (0, 2), (0, 3)),
        ((0, 0), (0, 0)),
        ((8, 0), (0, 1)),
        ((0, -1), (0, 1)),
        ((True, 0), (0, 1)),
        ((0,), (0, 1)),
        [(0, 0), (0, 1)],
        (0, (0, 1)),
    ],
)
def test_rejects_invalid_pattern_coordinates(coordinates):
    with pytest.raises(ValueError, match='invalid_tuple_coordinates'):
        ntuple.pattern_id(referee.INITIAL_ROWS, coordinates)


@pytest.mark.parametrize(
    'rows,turn,error',
    [
        (('E' * 8,) * 7, 'B', 'invalid_board_rows'),
        (('E' * 9,) * 8, 'B', 'invalid_board_rows'),
        (('x' * 8,) * 8, 'B', 'invalid_board_rows'),
        (['E' * 8] * 8, 'B', 'invalid_board_rows'),
        ((tuple('E' * 8),) * 8, 'B', 'invalid_board_rows'),
        (referee.INITIAL_ROWS, 'E', 'invalid_color'),
        (referee.INITIAL_ROWS, True, 'invalid_color'),
    ],
)
def test_rejects_invalid_state(rows, turn, error):
    model = ntuple.NtupleModel.zeros()
    for evaluate in (model.features, model.logit, model.value):
        with pytest.raises(ValueError, match=error):
            evaluate(rows, turn)


@pytest.mark.parametrize(
    'weight',
    [float('nan'), float('inf'), float('-inf'), float(2**1001), 2**2000, True, '0.0', None],
)
def test_rejects_invalid_weights(weight):
    with pytest.raises(ValueError, match='invalid_weight'):
        ntuple.NtupleModel((weight,) + (0.0,) * 1871)


@pytest.mark.parametrize('weights', [(), (0.0,) * 1871, (0.0,) * 1873, [0.0] * 1872])
def test_rejects_invalid_weight_count_and_mutable_shape(weights):
    with pytest.raises(ValueError, match='invalid_weight_count'):
        ntuple.NtupleModel(weights)


def test_hand_fixture_multiplicity_side_turn_and_signed_labels():
    rows = referee.INITIAL_ROWS
    # 隅の縦2マスはD4全像でEE。隅の斜め2マスもEEだが別IDを参照する。
    assert ntuple.TUPLES[:2] == (((0, 0), (0, 1)), ((0, 0), (1, 1)))
    weights = [0.0] * 1872
    weights[0] = 0.25
    model = ntuple.NtupleModel(tuple(weights))
    assert model.logit(rows, 'B') == 2
    weights[9] = -0.125
    weights[936] = -0.5
    model_with_turn = ntuple.NtupleModel(tuple(weights))
    weights[0] = 99
    assert model.weights[0] == model_with_turn.weights[0] == 0.25
    assert model.features(rows, 'B') == _oracle_features(rows, 'B')
    assert dict(model.features(rows, 'B'))[0] == dict(model.features(rows, 'B'))[9] == 8
    assert model_with_turn.logit(rows, 'B') == 1
    assert model_with_turn.logit(rows, 'W') == -4
    assert model_with_turn.value(rows, 'B') == tanh(1)
    assert model_with_turn.value(rows, 'W') == tanh(-4)
    assert model.value(rows, 'W') == 0
    exchanged = tuple(row.translate(str.maketrans('BW', 'WB')) for row in rows)
    assert model_with_turn.value(exchanged, 'W') == tanh(-4)
    assert model_with_turn.value(exchanged, 'W') != -model_with_turn.value(rows, 'B')
    for result, label in [('B', 1), ('W', -1), ('D', 0)]:
        terminal = (
            ('B' * 8,) * 8
            if result == 'B'
            else ('W' * 8,) * 8
            if result == 'W'
            else ('B' * 8, 'W' * 8) * 4
        )
        assert referee.result(terminal) == result
        for turn in ('B', 'W'):
            assert (phase6_player._child_score(model_with_turn, terminal, turn) > 0) - (
                phase6_player._child_score(model_with_turn, terminal, turn) < 0
            ) == label


def test_tanh_monotone_symmetry_saturation_and_cancellation():
    rows = ('E' * 8,) * 8
    samples = [-ntuple.MAX_WEIGHT, -0.25, -(2.0**-70), 0.0, 2.0**-70, 0.25, ntuple.MAX_WEIGHT]
    output = []
    for sample in samples:
        model = ntuple.NtupleModel((sample,) + (0.0,) * 1871)
        assert model.logit(rows, 'B') == 8 * sample
        output.append(model.value(rows, 'B'))
    assert output == [-1.0, tanh(-2), -(2.0**-67), 0, 2.0**-67, tanh(2), 1.0]
    assert sorted(set(output)) == output
    assert output == [-value for value in reversed(output)]
    assert all(isfinite(value) and -1 <= value <= 1 for value in output)
    weights = [0.0] * 1872
    for index, value in [(0, ntuple.MAX_WEIGHT), (9, 0.125), (18, -ntuple.MAX_WEIGHT)]:
        weights[index] = value
    assert ntuple.NtupleModel(tuple(weights)).logit(rows, 'B') == 1
    maximum = ntuple.NtupleModel((ntuple.MAX_WEIGHT,) * 1872)
    assert maximum.logit(rows, 'W') == 448 * ntuple.MAX_WEIGHT
    assert maximum.value(rows, 'W') == 1.0


def test_generated_legal_histories_and_phase_coverage():
    model = _weight_model()
    zero = ntuple.NtupleModel.zeros()
    previous = random.getstate()
    coverage = set()
    count = 0
    for rows, turn, history in _reachable():
        count += 1
        moves = referee.legal_moves(rows, turn)
        other = referee.legal_moves(rows, _swap(turn))
        empty = sum(row.count('E') for row in rows)
        if len(moves) == 1:
            coverage.add('single')
        if len(moves) >= 10:
            coverage.add('many')
        if not moves and other:
            coverage.add('pass')
            assert model.features(rows, turn) != model.features(rows, _swap(turn))
        if not moves and not other:
            coverage.add('ended')
        if any(rows[y][x] != 'E' for x, y in [(0, 0), (0, 7), (7, 0), (7, 7)]):
            coverage.add('corner')
        if any(
            color * 3 in edge
            for color in 'BW'
            for edge in (
                rows[0],
                rows[7],
                ''.join(row[0] for row in rows),
                ''.join(row[7] for row in rows),
            )
        ):
            coverage.add('edge')
        if abs(sum(row.count('B') - row.count('W') for row in rows)) >= 20:
            coverage.add('large_difference')
        coverage.add(empty)
        if not history:
            assert rows == referee.INITIAL_ROWS
        expected = _oracle_features(rows, turn)
        features = model.features(rows, turn)
        assert features == expected == model.features(rows, turn)
        assert sum(n for _, n in features) == 448
        assert model.logit(rows, turn) == _oracle_value(rows, turn, model.weights)[0]
        value = model.value(rows, turn)
        assert value == _oracle_value(rows, turn, model.weights)[1]
        assert zero.logit(rows, turn) == zero.value(rows, turn) == 0
        transforms = tuple(_boards(rows))
        if len(set(transforms)) == 8:
            coverage.add('asymmetric')
        if len(set(transforms)) <= 2:
            coverage.add('symmetric')
        for image in transforms:
            transformed = tuple(map(''.join, image))
            assert Counter(''.join(rows)) == Counter(''.join(transformed))
            assert model.features(transformed, turn) == expected
            assert model.value(transformed, turn) == value
        # 少なくとも一つの代表で、同じ手番の8参照すべてが異なる局面を通る。
        wide = {index for index, count in features if count == 1}
        offsets = 0 if turn == 'B' else 936
        for coordinates in _LINES:
            size = len(_TERNARY[len(coordinates)])
            if sum(index in wide for index in range(offsets, offsets + size)) == 8:
                coverage.add('no_duplicate_for_tuple')
                break
            offsets += size
    assert count >= 480
    assert set(range(0, 61)) <= coverage
    assert {
        'single',
        'many',
        'pass',
        'ended',
        'corner',
        'edge',
        'large_difference',
        'asymmetric',
        'symmetric',
        'no_duplicate_for_tuple',
    } <= coverage
    assert random.getstate() == previous
    assert model == _weight_model()


def test_d4_commutes_with_reversi_legal_moves_and_play():
    cases = [
        (rows, turn) for rows, turn, history in _reachable() if len(history) in (0, 13, 40, 59)
    ]
    for rows, turn in cases:
        moves = referee.legal_moves(rows, turn)
        for operation in range(8):
            image = [[''] * 8 for _ in range(8)]
            for y, row in enumerate(rows):
                for x, cell in enumerate(row):
                    u, v = ntuple._images(x, y)[operation]
                    image[v][u] = cell
            transformed = tuple(map(''.join, image))
            expected = {ntuple._images(*move)[operation] for move in moves}
            assert set(referee.legal_moves(transformed, turn)) == expected
            if moves:
                move = moves[0]
                original_child = referee.apply_move(rows, turn, move)
                rotated_child = referee.apply_move(
                    transformed, turn, ntuple._images(*move)[operation]
                )
                for y, row in enumerate(original_child):
                    for x, cell in enumerate(row):
                        u, v = ntuple._images(x, y)[operation]
                        assert rotated_child[v][u] == cell


def test_one_ply_oracle_and_forced_pass_terminal_paths():
    model = _weight_model()
    select = phase6_player.make_selector(model)
    seen = set()
    for rows, turn, history in _reachable():
        if len(history) < 53 and len(history) not in (0, 1, 20, 43):
            continue
        moves = referee.legal_moves(rows, turn)
        before = tuple(list(rows))
        values = {}
        for move in moves:
            child = referee.apply_move(rows, turn, move)
            child_turn = _swap(turn)
            if referee.result(child) == 'NG':
                if not referee.legal_moves(child, child_turn):
                    seen.add('pass_after_move')
                    assert referee.legal_moves(child, turn)
                value = _oracle_value(child, child_turn, model.weights)[1]
            else:
                seen.add('terminal_after_move')
                difference = ''.join(child).count('B') - ''.join(child).count('W')
                if difference > 0:
                    value = 2 + difference / 64
                elif difference < 0:
                    value = -2 + difference / 64
                else:
                    value = 0
            values[move] = value if turn == 'W' else -value
        expected = min(moves, key=lambda m: (values[m], m[1], m[0]), default=None)
        assert select(rows, turn, tuple(reversed(moves))) == expected
        assert rows == before
    assert seen == {'pass_after_move', 'terminal_after_move'}


@pytest.mark.parametrize(
    'rows, expected',
    [
        (('B' * 8,) * 8, 3),
        (('W' * 8,) * 8, -3),
        (('B' * 7 + 'E',) * 8, 2.875),
        (('W' * 7 + 'E',) * 8, -2.875),
        (('B' * 8, 'W' * 8) * 4, 0),
    ],
)
def test_terminal_precedence_and_stone_margin(rows, expected):
    weight = -ntuple.MAX_WEIGHT if expected > 0 else ntuple.MAX_WEIGHT
    inverse = ntuple.NtupleModel((weight,) * 1872)
    assert inverse.value(rows, 'B') == inverse.value(rows, 'W') == (-1 if expected > 0 else 1)
    assert not referee.legal_moves(rows, 'B') and not referee.legal_moves(rows, 'W')
    for turn in ('B', 'W'):
        assert phase6_player._child_score(inverse, rows, turn) == expected


def test_gradient_finite_difference_uses_repeated_feature_count():
    rows = referee.INITIAL_ROWS
    weights = [0.0] * 1872
    weights[0] = 1 / 32
    value = ntuple.NtupleModel(tuple(weights)).value(rows, 'B')
    delta = 2**-20
    weights[0] += delta
    above = ntuple.NtupleModel(tuple(weights)).value(rows, 'B')
    weights[0] -= 2 * delta
    below = ntuple.NtupleModel(tuple(weights)).value(rows, 'B')
    assert (above - below) / (2 * delta) == pytest.approx(8 * (1 - value**2), rel=1e-9)
