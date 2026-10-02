import random


class Pos(object):

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def is_valid(self):
        return 0 <= self.x < 8 and 0 <= self.y < 8

    def add(self, p):
        return Pos(self.x + p.x, self.y + p.y)

    def __add__(self, p):
        return Pos(self.x + p.x, self.y + p.y)

    def __eq__(self, p):
        return self.x == p.x and self.y == p.y

    def __str__(self):
        return '{} {}'.format(self.x, self.y)


AROUND = [
    Pos(-1, -1), Pos(-1, 0), Pos(-1, 1),
    Pos(0, -1), Pos(0, 1),
    Pos(1, -1), Pos(1, 0), Pos(1, 1)]


class Board(object):

    def __init__(self):
        # python 内包表現
        self.cell = [['E' for _ in range(8)] for _ in range(8)]
        self.cell[3][4] = 'B'
        self.cell[4][3] = 'B'
        self.cell[3][3] = 'W'
        self.cell[4][4] = 'W'

    def ask(self, color, pos):
        '''指定された座標にコマを置けるかどうかを確認し、その結果を表示する。
        Args:
          color (str): コマの色 "B" or "W"
          pos (Pos): コマを置く座標
        '''
        # 引数確認
        if color != 'B' and color != 'W':
            print('NG', flush=True)
            return
        elif not pos.is_valid():
            print('NG', flush=True)
            return

        # 場所の確認
        if self._ask(color, pos):
            print('OK', flush=True)
        else:
            print('NG', flush=False)

    def play(self, color, pos):
        '''指定された座標にコマを置き、その結果を表示する。
        Args:
          color (str): コマの色 "B" or "W"
          pos (Pos): コマを置く座標
        '''
        # 引数確認
        if color != 'B' and color != 'W':
            print('NG', flush=True)
            return
        elif not pos.is_valid():
            print('NG', flush=True)
            return

        # 駒を置く
        if self._play(color, pos):
            print('OK', flush=True)
        else:
            print('NG', flush=True)

    def genmove(self, color):
        '''指定された色のコマを置き、その結果を表示する。
        Args:
          color (str): コマの色 "B" or "W"
        '''
        # 引数確認
        if color != 'B' and color != 'W':
            print('NG', flush=True)
            return

        # 駒を置く
        pos = self._genmove(color)

        if pos is not None:
            print(pos)
        else:
            print('NG')

    def showboard(self):
        '''盤面をっ表示する。'''
        print(self, flush=True)

    def _get_reverse_pos(self, color, pos, direction):
        '''指定された座標にコマを置けるかを確認し、ひっくり返す必要のある終点座標を返す。
        Args:
          color (str): コマの色 "B" or "W"
          pos (Pos): コマを置く座標
          direction (Pos): 調べる方向
        Return:
          bool: コマを置けたなら終点座標を返す。おけない場合はNoneを返す。
        '''
        opposite = 'B' if color == 'W' else 'W'
        current = pos + direction
        # 下のコードと同じ
        # current = pos.add(direction)

        if not current.is_valid():
            return None
        elif self.cell[current.y][current.x] != opposite:
            return None

        while True:
            current = current + direction

            if not current.is_valid():
                return None
            elif self.cell[current.y][current.x] == 'E':
                return None
            elif self.cell[current.y][current.x] == color:
                return current

    def _play(self, color, pos):
        '''指定された座標にコマを置いて、必要な部分のコマを反転させる。
        Args:
          color (str): コマの色 "B" or "W"
          pos (Pos): コマを置く座標
        Return:
          bool: コマを置けたならTrueを返す
        '''
        # 置けることの確認
        if not self._ask(color, pos):
            return False

        # ひっくり返す
        self._update(color, pos)

        return True

    def _ask(self, color, pos):
        '''指定された座標にコマを置けるかどうかを確認する。
        Args:
          color (str): コマの色 "B" or "W"
          pos (Pos): コマを置く座標
        Return:
          bool: コマを置けるならTrueを返す
        '''
        if self.cell[pos.y][pos.x] != 'E':
            return False

        for direction in AROUND:
            p = self._get_reverse_pos(color, pos, direction)

            if p is not None:
                return True

        return False

    def _update(self, color, pos):
        '''指定された座標にコマを置いて、必要な部分のコマを反転させる。
        指定座標は置くことが可能な場所であること。
        Args:
          color (str): コマの色 "B" or "W"
          pos (Pos): コマを置く座標
        '''
        # 石を置く
        self.cell[pos.y][pos.x] = color

        # ひっくり返す
        for direction in AROUND:
            target = self._get_reverse_pos(color, pos, direction)

            if target is None:
                continue

            current = pos + direction

            while current != target:
                self.cell[current.y][current.x] = color
                current += direction

    def _genmove(self, color):
        '''指定された色のコマを置いて、必要な部分のコマを反転させる。
        Args:
          color (str): コマの色 "B" or "W"
        Return:
          Pos: コマを置いた座標（おけなかった場合はNone）を返す。
        '''
        candidates = []

        for y in range(8):
            for x in range(8):
                pos = Pos(x, y)

                if self._ask(color, pos):
                    candidates.append(pos)

        if len(candidates) == 0:
            return None

        random.shuffle(candidates)

        self._play(color, candidates[0])

        return candidates[0]

    def __str__(self):
        return '\n'.join(''.join(r) for r in self.cell)


def main():
    board = Board()

    while True:
        cmd = input().split()

        if cmd[0] == 'quit':
            return
        if cmd[0] == 'name':
            print('sample', flush=True)
        elif len(cmd) == 4 and cmd[0] == 'ask':
            board.ask(cmd[1], Pos(int(cmd[2]), int(cmd[3])))
        elif len(cmd) == 4 and cmd[0] == 'play':
            board.play(cmd[1], Pos(int(cmd[2]), int(cmd[3])))
        elif len(cmd) == 2 and cmd[0] == 'genmove':
            board.genmove(cmd[1])
        elif cmd[0] == 'showboard':
            board.showboard()


if __name__ == '__main__':
    main()

