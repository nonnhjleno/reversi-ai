import subprocess
import sys
import unittest
import random
from pathlib import Path

import pytest

from harness.protocol import Player
from harness import referee
from tools.evidence_plugin import record_expectation, record_observation

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
        self.assertTrue(output[0].strip())
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


@pytest.fixture
def player():
    with Player(SCRIPT, seed=7) as process:
        yield process


def test_live_initial_ask_for_both_colors_preserves_board(player):
    expected = {
        'B': {(2, 3), (3, 2), (4, 5), (5, 4)},
        'W': {(2, 4), (3, 5), (4, 2), (5, 3)},
    }
    assert player.command('showboard') == INITIAL_BOARD
    for color in ('B', 'W'):
        for position_y in range(8):
            for position_x in range(8):
                response = player.command('ask {} {} {}'.format(color, position_x, position_y))
                assert response == ['OK' if (position_x, position_y) in expected[color] else 'NG']
    assert player.command('showboard') == INITIAL_BOARD


@pytest.mark.parametrize('command', [
    'play B 0 0', 'play B 3 3', 'play W 2 3',
    'play B -1 3', 'play W 8 4',
])
def test_live_rejected_move_preserves_board(player, command):
    record_expectation('rejected-move', inputs={'command': command, 'initial_board': INITIAL_BOARD},
                       expected={'response': ['NG'], 'board': INITIAL_BOARD}, expectation_kind='fixed',
                       model='docs/harness_architecture.md:親Player/外部script',
                       contract='docs/プログラム開発方法.md:play/初期盤面', implementation='harness.protocol.Player.command')
    response = player.command(command)
    record_observation('rejected-move', {'response': response})
    assert response == ['NG']
    rows = player.command('showboard')
    record_observation('rejected-move', {'board': rows})
    assert rows == INITIAL_BOARD


def test_live_all_commands_obey_response_deadlines(player):
    assert player.command('name')[0].strip()
    assert player.command('ask B 2 3') == ['OK']
    assert player.command('play B 2 3') == ['OK']
    assert player.command('result') == ['NG']
    assert player.command('genmove W')[0] in ('2 2', '4 2', '2 4')
    assert len(player.command('showboard')) == 8
    player.finish()
    assert player.process.returncode == 0
    assert player.command_timeout == 1.0
    assert player.genmove_timeout == 10.0
    for command in player.commands:
        if not command['startup_probe']:
            limit = 10.0 if command['command'].startswith('genmove ') else 1.0
            assert command['seconds'] <= limit


@pytest.mark.parametrize('seed,expected_result,expected_rows', [
    (0, 'W', (
        'WWWWWWWW', 'WWBWWWWW', 'WBWBWBWW', 'WWBWWBWW',
        'WWWWWBWW', 'WWBWWWWW', 'WBWBBWWW', 'BWWWWWWW',
    )),
    (3, 'B', (
        'BBBBBBBB', 'WBBBBWBB', 'BWBBWBWB', 'BBWWBBWB',
        'BBWWBWWB', 'BWBWBBWB', 'BWWBBWWB', 'BWBBBBBB',
    )),
    (14, 'D', (
        'BBBBBBBW', 'BWWWWWWW', 'BWWWBWWW', 'BBBBBWBB',
        'WWWWWBBB', 'WWWWBBWB', 'WWWBWWBB', 'WWBBBBBB',
    )),
])
def test_live_terminal_results_and_passes_with_fixed_final_boards(player, seed, expected_result, expected_rows):
    generator = random.Random(seed)
    rows = referee.INITIAL_ROWS
    for index in range(128):
        color = ('B', 'W')[index % 2]
        possible = referee.legal_moves(rows, color)
        if not possible:
            assert player.command('genmove ' + color) == ['NG']
            assert tuple(player.command('showboard')) == rows
        else:
            position = generator.choice(possible)
            assert player.command('play {} {} {}'.format(color, *position)) == ['OK']
            rows = referee.apply_move(rows, color, position)
        outcome = referee.result(rows)
        assert player.command('result') == [outcome]
        if outcome != 'NG':
            break
    assert rows == expected_rows
    assert tuple(player.command('showboard')) == expected_rows
    assert player.command('result') == [expected_result]
    assert player.command('genmove B') == ['NG']
    assert player.command('genmove W') == ['NG']
    assert tuple(player.command('showboard')) == expected_rows


if __name__ == '__main__':
    unittest.main()
