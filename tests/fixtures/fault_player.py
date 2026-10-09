import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from reversi_player.reversi import Board, Pos


def main():
    mode = os.environ.get('REVERSI_HARNESS_TEST_FAULT', 'illegal_move')
    board = Board()
    for line in sys.stdin:
        command = line.split()
        if command == ['name']:
            if mode == 'startup_timeout':
                time.sleep(20)
            print('fault-fixture', flush=True)
            if mode == 'extra_stdout':
                print('DEBUG', flush=True)
        elif command == ['quit']:
            if mode == 'quit_timeout':
                time.sleep(20)
            return
        elif command == ['showboard']:
            if mode == 'board_mismatch':
                print('\n'.join(['EEEEEEEE'] * 8), flush=True)
            else:
                board.showboard()
        elif command == ['result']:
            print(board.result(), flush=True)
        elif command[0] == 'play':
            board.play(command[1], Pos(int(command[2]), int(command[3])))
        elif command[0] == 'genmove':
            if mode == 'timeout':
                time.sleep(20)
            elif mode == 'abnormal_exit':
                print('intentional crash', file=sys.stderr, flush=True)
                raise SystemExit(3)
            elif mode == 'protocol_error':
                print('DEBUG', flush=True)
            elif mode == 'slow_genmove':
                time.sleep(0.15)
                print('2 3', flush=True)
            else:
                print('0 0', flush=True)


if __name__ == '__main__':
    main()
