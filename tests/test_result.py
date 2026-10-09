import queue
import subprocess
import sys
import threading
import unittest
import io
import random
from contextlib import redirect_stdout
from pathlib import Path

from reversi_player.reversi import Board
from reversi_player.web import Process, RequestHandler


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
        process = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        responses = queue.Queue()
        reader = threading.Thread(
            target=lambda: responses.put(process.stdout.readline()),
            daemon=True,
        )

        try:
            process.stdin.write('result\n')
            process.stdin.flush()
            reader.start()
            self.assertEqual(responses.get(timeout=1), 'NG\n')
        finally:
            if process.poll() is None:
                process.stdin.write('quit\n')
                process.stdin.flush()
                process.wait(timeout=2)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_web_process_gets_board_legal_moves_and_result(self):
        output = io.StringIO()

        with redirect_stdout(output):
            with Process() as process:
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
        saved_random_state = random.getstate()
        game = None

        try:
            for seed in range(32):
                random.seed(seed)
                board = Board()
                moves = []
                after_first_pass = None

                for _ in range(128):
                    color = ('B', 'W')[len(moves) % 2]
                    pos = board._genmove(color)
                    if pos is None:
                        moves.append([-1, -1])
                        if after_first_pass is None:
                            after_first_pass = moves[:]
                    else:
                        moves.append([pos.x, pos.y])

                    result = board.result()
                    if result != 'NG':
                        break

                if after_first_pass is not None and result in ('B', 'W', 'D'):
                    game = (board, moves, after_first_pass, result)
                    break
        finally:
            random.setstate(saved_random_state)

        self.assertIsNotNone(game, 'パスを含む終局対局を生成できなかった')
        board, moves, after_first_pass, result = game
        handler = object.__new__(RequestHandler)
        output = io.StringIO()

        def get_response(request):
            with Process() as process:
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
              for cell in row] for row in board.cell],
        )


if __name__ == '__main__':
    unittest.main()
