import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'reversi_player' / 'reversi.py'
INITIAL_BOARD = [
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEWBEEE',
    'EEEBWEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
]


class StandardInputCommandTests(unittest.TestCase):
    def run_commands(self, commands):
        process = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input='\n'.join(commands) + '\n',
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, '')
        return process.stdout.splitlines()

    def test_name_ask_play_showboard_result_and_quit(self):
        output = self.run_commands([
            'name', 'showboard', 'ask B 2 3', 'ask B 3 3',
            'play B 2 3', 'play W 2 3', 'showboard', 'result', 'quit',
        ])
        self.assertEqual(output[0], 'sample')
        self.assertEqual(output[1:9], INITIAL_BOARD)
        self.assertEqual(output[9:13], ['OK', 'NG', 'OK', 'NG'])
        self.assertEqual(output[13:21], [
            'EEEEEEEE', 'EEEEEEEE', 'EEEEEEEE', 'EEBBBEEE',
            'EEEBWEEE', 'EEEEEEEE', 'EEEEEEEE', 'EEEEEEEE',
        ])
        self.assertEqual(output[21:], ['NG'])

    def test_genmove_returns_a_legal_position_and_updates_board(self):
        output = self.run_commands(['genmove B', 'showboard', 'quit'])
        self.assertEqual(len(output), 9)
        self.assertIn(output[0], {'2 3', '3 2', '4 5', '5 4'})
        position_x, position_y = map(int, output[0].split())
        board = output[1:]
        self.assertTrue(all(len(row) == 8 for row in board))
        self.assertEqual(board[position_y][position_x], 'B')
        self.assertEqual(''.join(board).count('B'), 4)
        self.assertEqual(''.join(board).count('W'), 1)

    def test_invalid_color_and_out_of_range_position(self):
        output = self.run_commands([
            'ask B -1 0', 'play W 8 8', 'genmove X', 'showboard', 'quit',
        ])
        self.assertEqual(output[:3], ['NG', 'NG', 'NG'])
        self.assertEqual(output[3:], INITIAL_BOARD)


if __name__ == '__main__':
    unittest.main()
