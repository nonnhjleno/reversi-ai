import queue
import threading
import unittest
import io
import random
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import referee
from harness.protocol import Player
from reversi_player.reversi import Board, Pos
from reversi_player.web import Process, RequestHandler
from tools.evidence_plugin import record_expectation, record_observation


class BoundedWebProcess(Process):
    def __init__(self):
        super().__init__()
        self._ready = False

    def command(self, command, lines=1):
        responses = queue.Queue()

        def receive():
            try:
                responses.put((True, super(BoundedWebProcess, self).command(command, lines)))
            except Exception as error:
                responses.put((False, error))

        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        timeout = 5 if not self._ready else 10 if command.startswith('genmove ') else 1
        try:
            succeeded, response = responses.get(timeout=timeout)
        except queue.Empty as error:
            self.proc.kill()
            self.proc.wait(timeout=2)
            reader.join(timeout=1)
            raise AssertionError('GUI command timed out: ' + command) from error
        self._ready = True
        if not succeeded:
            raise response
        return response

    def __exit__(self, exception_type, exception, traceback):
        try:
            if self.proc.poll() is None:
                super().__exit__(exception_type, exception, traceback)
                self.proc.wait(timeout=2)
        finally:
            for pipe in (self.proc.stdin, self.proc.stdout):
                pipe.close()


class ResultCommandTests(unittest.TestCase):
    def _board_from_rows(self, rows):
        board = Board()
        board.cell = [list(row) for row in rows]
        return board

    def test_initial_position_is_in_progress(self):
        self.assertEqual(Board().result(), 'NG')

    def test_one_color_may_pass_while_the_game_continues(self):
        black_can_move = self._board_from_rows(
            ['EWBBBBBB'] + ['BBBBBBBB'] * 7)
        white_can_move = self._board_from_rows(
            ['EBWWWWWW'] + ['WWWWWWWW'] * 7)

        self.assertTrue(black_can_move._has_legal_move('B'))
        self.assertFalse(black_can_move._has_legal_move('W'))
        self.assertEqual(black_can_move.result(), 'NG')

        self.assertFalse(white_can_move._has_legal_move('B'))
        self.assertTrue(white_can_move._has_legal_move('W'))
        self.assertEqual(white_can_move.result(), 'NG')

    def test_empty_square_does_not_prevent_terminal_result(self):
        board = self._board_from_rows(
            ['EBBBBBBB'] + ['BBBBBBBB'] * 7)

        self.assertEqual(board.result(), 'B')

    def test_terminal_winner_and_draw(self):
        black_wins = self._board_from_rows(['BBBBBBBB'] * 8)
        white_wins = self._board_from_rows(['WWWWWWWW'] * 8)
        draw = self._board_from_rows(
            ['BBBBBBBB'] * 4 + ['WWWWWWWW'] * 4)

        self.assertEqual(black_wins.result(), 'B')
        self.assertEqual(white_wins.result(), 'W')
        self.assertEqual(draw.result(), 'D')

    def test_result_does_not_change_the_board(self):
        board = self._board_from_rows(
            ['EWBBBBBB'] + ['BBBBBBBB'] * 7)
        before = [row[:] for row in board.cell]

        self.assertEqual(board.result(), 'NG')
        self.assertEqual(board.cell, before)

    def test_cli_flushes_result_before_receiving_next_command(self):
        script = Path(__file__).resolve().parents[1] / 'reversi_player' / 'reversi.py'
        with Player(script) as process:
            self.assertEqual(process.command('result'), ['NG'])
            self.assertLessEqual(process.commands[-1]['seconds'], 1)

    def test_web_process_gets_board_legal_moves_and_result(self):
        output = io.StringIO()

        with redirect_stdout(output):
            with BoundedWebProcess() as process:
                rows = process.command('showboard', lines=8).split('\n')
                legal_moves = [
                    process.command('ask B {} {}'.format(x, y))
                    for y in range(8)
                    for x in range(8)
                ]
                result = process.command('result')
                process.proc.stdout.close()
            process.proc.stdin.close()

        self.assertEqual(len(rows), 8)
        self.assertTrue(all(len(row) == 8 for row in rows))
        self.assertEqual(legal_moves.count('OK'), 4)
        self.assertTrue(all(value in ('OK', 'NG') for value in legal_moves))
        self.assertEqual(result, 'NG')

    def test_web_response_handles_pass_and_terminal_result(self):
        game = None
        for seed in range(32):
            generator = random.Random(seed)
            rows = referee.INITIAL_ROWS
            moves = []
            after_first_pass = None
            for _ in range(128):
                color = ('B', 'W')[len(moves) % 2]
                possible = referee.legal_moves(rows, color)
                if not possible:
                    moves.append([-1, -1])
                    if after_first_pass is None:
                        after_first_pass = moves[:]
                else:
                    position = generator.choice(possible)
                    rows = referee.apply_move(rows, color, position)
                    moves.append(list(position))
                result = referee.result(rows)
                if result != 'NG':
                    break
            if after_first_pass is not None and result in ('B', 'W', 'D'):
                game = (rows, moves, after_first_pass, result)
                break

        self.assertIsNotNone(game, 'パスを含む終局対局を生成できなかった')
        rows, moves, after_first_pass, result = game
        handler = object.__new__(RequestHandler)
        output = io.StringIO()

        def get_response(request):
            with BoundedWebProcess() as process:
                handler._make_board(process, request)
                response = handler._make_response(process, request)
                process.proc.stdout.close()
            process.proc.stdin.close()
            return response

        with redirect_stdout(output):
            pass_response = get_response({'positions': after_first_pass})
            end_response = get_response({'positions': moves})

        self.assertFalse(pass_response['end'])
        self.assertEqual(pass_response['score'], 0)
        self.assertTrue(end_response['end'])
        self.assertEqual(
            end_response['score'],
            1 if result == 'B' else -1 if result == 'W' else 0,
        )
        self.assertEqual(
            end_response['board'],
            [[1 if cell == 'B' else -1 if cell == 'W' else 0
              for cell in row] for row in rows],
        )


@pytest.mark.parametrize('color', ['B', 'W'])
@pytest.mark.parametrize('delta_x,delta_y', [
    (-1, -1), (0, -1), (1, -1), (-1, 0),
    (1, 0), (-1, 1), (0, 1), (1, 1),
])
def test_each_direction_flips_exactly_the_enclosed_stone(color, delta_x, delta_y):
    opponent = 'W' if color == 'B' else 'B'
    rows = [list('EEEEEEEE') for _ in range(8)]
    rows[3 + delta_y][3 + delta_x] = opponent
    rows[3 + 2 * delta_y][3 + 2 * delta_x] = color
    expected = [row[:] for row in rows]
    expected[3][3] = color
    expected[3 + delta_y][3 + delta_x] = color
    before = tuple(''.join(row) for row in rows)
    board = Board()
    board.cell = rows
    output = io.StringIO()
    with redirect_stdout(output):
        board.play(color, Pos(3, 3))
    assert output.getvalue() == 'OK\n'
    assert board.cell == expected
    assert referee.apply_move(before, color, (3, 3)) == tuple(''.join(row) for row in expected)


def test_all_eight_directions_flip_together():
    before = (
        'EEEEEEEE', 'EBEBEBEE', 'EEWWWEEE', 'EBWEWBEE',
        'EEWWWEEE', 'EBEBEBEE', 'EEEEEEEE', 'EEEEEEEE',
    )
    expected = (
        'EEEEEEEE', 'EBEBEBEE', 'EEBBBEEE', 'EBBBBBEE',
        'EEBBBEEE', 'EBEBEBEE', 'EEEEEEEE', 'EEEEEEEE',
    )
    record_expectation('eight-directions', inputs={'board': before, 'color': 'B', 'position': [3, 3]},
                       expected={'board': expected, 'stdout': 'OK\nOK\n'}, expectation_kind='fixed',
                       model='docs/harness_architecture.md:referee/外部script',
                       contract='docs/プログラム開発方法.md:リバーシのルール', implementation='reversi_player.reversi.Board.play')
    board = Board()
    board.cell = [list(row) for row in before]
    output = io.StringIO()
    with redirect_stdout(output):
        board.ask('B', Pos(3, 3))
        assert tuple(''.join(row) for row in board.cell) == before
        board.play('B', Pos(3, 3))
    record_observation('eight-directions', {'board': tuple(''.join(row) for row in board.cell), 'stdout': output.getvalue()})
    assert output.getvalue() == 'OK\nOK\n'
    assert tuple(''.join(row) for row in board.cell) == expected
    assert referee.apply_move(before, 'B', (3, 3)) == expected


@pytest.mark.parametrize('row', ['EEEEEEEE', 'EWEEEEEE', 'EBEEEEEE', 'EWEEBEEE'])
def test_no_capture_without_an_unbroken_opponent_ray(row):
    board = Board()
    board.cell = [list(row)] + [list('EEEEEEEE') for _ in range(7)]
    before = [cells[:] for cells in board.cell]
    output = io.StringIO()
    with redirect_stdout(output):
        board.ask('B', Pos(0, 0))
        board.play('B', Pos(0, 0))
    assert output.getvalue() == 'NG\nNG\n'
    assert board.cell == before
    assert referee.flipped_cells(tuple(''.join(cells) for cells in before), 'B', (0, 0)) == []


@pytest.mark.parametrize('rows,expected', [
    (referee.INITIAL_ROWS, 'NG'),
    (('EWBBBBBB',) + ('BBBBBBBB',) * 7, 'NG'),
    (('EBWWWWWW',) + ('WWWWWWWW',) * 7, 'NG'),
    (('EBBBBBBB',) + ('BBBBBBBB',) * 7, 'B'),
    (('BBBBBBBB',) * 8, 'B'),
    (('WWWWWWWW',) * 8, 'W'),
    (('BBBBBBBB',) * 4 + ('WWWWWWWW',) * 4, 'D'),
])
def test_referee_terminal_results_match_fixed_positions(rows, expected):
    assert referee.result(rows) == expected


@pytest.mark.parametrize('color,rows', [
    ('W', ('EWBBBBBB',) + ('BBBBBBBB',) * 7),
    ('B', ('EBWWWWWW',) + ('WWWWWWWW',) * 7),
    ('B', ('WWWWWWWW',) * 8),
    ('W', ('BBBBBBBB',) * 8),
])
def test_genmove_without_legal_moves_preserves_fixed_board(color, rows):
    board = Board()
    board.cell = [list(row) for row in rows]
    output = io.StringIO()
    with redirect_stdout(output):
        board.genmove(color)
    assert output.getvalue() == 'NG\n'
    assert tuple(''.join(row) for row in board.cell) == rows


@pytest.mark.parametrize('other,expected', [
    (Pos(1, 2), True), (Pos(0, 2), False), (Pos(1, 3), False),
    (SimpleNamespace(x=1, y=2), True), (SimpleNamespace(x=1, y=3), False),
    (SimpleNamespace(x=0), False),
])
def test_pos_typing_preserves_equality_and_duck_short_circuit(other, expected):
    """Pos以外でも座標を比較し、x不一致では欠損yを読まない既存挙動を固定する。"""
    result = Pos(1, 2).__eq__(other)
    assert result is expected


@pytest.mark.parametrize('other', [None, object(), SimpleNamespace(x=1)])
def test_pos_typing_preserves_missing_coordinate_errors(other):
    """型付けを理由にNotImplemented/Falseを返して元のAttributeErrorを隠さない。"""
    with pytest.raises(AttributeError):
        Pos(1, 2).__eq__(other)


class _LegacyComparisonValue:
    """比較が非bool値を返す既存の動的入力を再現し、アクセス順を記録する。"""

    def __init__(self, label, value, calls):
        """ラベル・戻り値・共有記録先を保持する。"""
        self.label = label
        self.value = value
        self.calls = calls

    def __eq__(self, other):
        """比較対象を変換せず、指定の値そのものを返す。"""
        self.calls.append(self.label)
        return self.value


@pytest.mark.parametrize('expected', ['legacy-result', [], 0, False, 2])
def test_pos_typing_preserves_non_boolean_comparison_values(expected):
    """andは最後に評価した値を返すので、bool()での正規化は互換性を壊す。"""
    calls = []
    other = SimpleNamespace(
        x=_LegacyComparisonValue('x', expected, calls),
        y=_LegacyComparisonValue('y', expected, calls),
    )
    assert Pos(1, 2).__eq__(other) is expected
    assert calls == (['x', 'y'] if expected else ['x'])
    calls.clear()
    assert (Pos(1, 2) != other) is (not bool(expected))
    assert calls == (['x', 'y'] if expected else ['x'])


@pytest.mark.parametrize('coordinates,valid', [
    ((0, 0), True), ((7, 7), True), ((-1, 0), False),
    ((8, 0), False), ((0, 8), False), ((0, -1), False),
])
def test_pos_typing_preserves_coordinate_bounds(coordinates, valid):
    position = Pos(*coordinates)
    assert position.is_valid() is valid
    assert str(position) == '{} {}'.format(*coordinates)


def test_pos_typing_preserves_addition_without_mutating_inputs():
    first = Pos(3, 4)
    second = Pos(-1, 2)
    assert str(first.add(second)) == str(first + second) == '2 6'
    assert (first.x, first.y, second.x, second.y) == (3, 4, -1, 2)


if __name__ == '__main__':
    unittest.main()
