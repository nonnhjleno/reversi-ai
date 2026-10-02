class Board(object):
    def __init__(self):
	# 初期化する
        self.cell = [['E' for _ in range(8)] for _ in range(8)]

    def play(self, color, x, y):
        self._play(color, x, y)

    def showboard(self):
        pass

    def _play(self, color, x, y):
        if not self._ask(color, x, y):
            return

        # 石を置く
        self._update(x, y)

    def _ask(self, color, x, y):
        pass

    def _update(self, x, y):
        pass

    def __str__(self):
        return '\n'.join(''.join(r) for r in self.cell)


def main():
    board = Board()

    while True:
        # U1: コマンド入力
        cmd = input().split()
        print('cmd', cmd)

        if cmd[0] == 'quit':
            return
        elif cmd[0] == 'play':
            board.play(cmd[1], int(cmd[2]), int(cmd[3]))
        elif cmd[0] == 'showboard':
            board.showboard()


if __name__ == '__main__':
    main()
