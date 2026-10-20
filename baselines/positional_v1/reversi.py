from __future__ import annotations

import random as random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Literal, TypeGuard
    from reversi_player import ai_engine
elif __package__:
    from . import ai_engine
else:
    import ai_engine


def _is_move(value: object) -> TypeGuard[ai_engine.Move]:
    """selectorの戻り値が通常tupleと厳密なint座標かを判定する。"""
    if type(value) is not tuple or len(value) != 2:
        return False

    x, y = value
    return type(x) is int and type(y) is int


class Pos(object):
    """盤面上の座標を表す。x は列、y は行で、左上を (0, 0) とする。"""

    def __init__(self, x: int, y: int) -> None:
        """座標を初期化する。盤面内かどうかの判定は is_valid() で行う。"""
        self.x = x
        self.y = y

    def is_valid(self) -> bool:
        """座標が 8×8 の盤面内にあれば True を返す。"""
        return 0 <= self.x < 8 and 0 <= self.y < 8

    def add(self, p: Pos) -> Pos:
        """別の座標または方向 p を加えた新しい Pos を返す。自身は変更しない。"""
        return Pos(self.x + p.x, self.y + p.y)

    def __add__(self, p: Pos) -> Pos:
        """+ 演算子で座標を加算し、新しい Pos を返す。"""
        return Pos(self.x + p.x, self.y + p.y)

    def __eq__(self, p: Any) -> Any:
        """座標を直接比較する既存の動的境界。非bool結果・短絡・属性例外も保持する。"""
        return self.x == p.x and self.y == p.y

    def __str__(self) -> str:
        """GUIとの通信で使う「x y」形式の文字列を返す。"""
        return '{} {}'.format(self.x, self.y)


# 8方向の相対座標。盤面の走査や石を挟める方向の判定に共用する。
AROUND = [
    Pos(-1, -1), Pos(-1, 0), Pos(-1, 1),
    Pos(0, -1), Pos(0, 1),
    Pos(1, -1), Pos(1, 0), Pos(1, 1)]


class Board(object):
    """リバーシの盤面を保持し、着手判定・盤面更新・GUI応答を行う。

    cell は cell[y][x] の二次元リストで、'B' は黒、'W' は白、
    'E' は空きマスを表す。内部の着手判定は _ask() に集約し、
    result() は現在の盤面だけから対局状態と勝敗を返す。
    """

    def __init__(self, *, selector: ai_engine.MoveSelector | None = None) -> None:
        """初期盤面を作り、任意の着手選択器を保持する。

        selector省略時は位置評価選択を使う。盤面規則と実盤面更新は
        常にBoardが担当する。
        """
        self.cell: list[list[str]] = [['E' for _ in range(8)] for _ in range(8)]
        self.cell[3][4] = 'B'
        self.cell[4][3] = 'B'
        self.cell[3][3] = 'W'
        self.cell[4][4] = 'W'
        self._selector = ai_engine.select_positional_move if selector is None else selector

    def ask(self, color: str, pos: Pos) -> None:
        """着手可能かを検査し、GUI向けに OK または NG を出力する。

        color は 'B'（黒）または 'W'（白）、pos は候補座標。
        盤面は変更しない。座標・色が不正、または合法手でない場合は NG。
        """
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
            print('NG', flush=True)

    def play(self, color: str, pos: Pos) -> None:
        """指定した合法手を盤面に反映し、GUI向けに結果を出力する。

        color は 'B'（黒）または 'W'（白）、pos は着手座標。
        不正な入力または置けない場所なら盤面を変更せず NG を出力する。
        """
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

    def genmove(self, color: str) -> None:
        """指定色の合法手からランダムに1手を選び、着手結果を出力する。

        合法手がある場合は盤面を更新して座標を出力する。
        合法手がない場合は NG を出力し、盤面は変更しない。
        """
        # 引数確認
        if color != 'B' and color != 'W':
            print('NG', flush=True)
            return

        # 駒を置く
        pos = self._genmove(color)

        if pos is not None:
            print(pos, flush=True)
        else:
            print('NG', flush=True)

    def showboard(self) -> None:
        """現在の盤面を8行の文字列にして標準出力へ表示する。"""
        print(self, flush=True)

    def result(self) -> Literal['B', 'W', 'D', 'NG']:
        """現在の盤面から対局状態または勝敗を返す。

        どちらかに合法手があれば 'NG'、両色とも合法手がなければ
        石数に応じて 'B'、'W'、'D' を返す。盤面は変更しない。
        """
        if self._has_legal_move('B') or self._has_legal_move('W'):
            return 'NG'

        black_count = sum(row.count('B') for row in self.cell)
        white_count = sum(row.count('W') for row in self.cell)

        if black_count > white_count:
            return 'B'
        elif white_count > black_count:
            return 'W'
        else:
            return 'D'

    def _get_reverse_pos(self, color: str, pos: Pos, direction: Pos) -> Pos | None:
        """1方向を調べ、石を挟める場合は自色の終点座標を返す。

        pos から direction へ進み、最初に相手の石があり、その先に
        空きマスを挟まず自色の石が続く場合に、その自色の座標を返す。
        条件を満たさない場合は None。盤面や pos 自体は変更しない。
        呼び出し側で color と pos の妥当性を確認しておく。
        """
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

    def _play(self, color: str, pos: Pos) -> bool:
        """合法性を確認して着手し、成功したかを返す。

        合法手なら _update() で盤面を更新して True、置けなければ
        盤面を変えず False を返す。color と pos は有効値であること。
        """
        # 置けることの確認
        if not self._ask(color, pos):
            return False

        # ひっくり返す
        self._update(color, pos)

        return True

    def _ask(self, color: str, pos: Pos) -> bool:
        """空きマスで、少なくとも1方向に相手の石を挟めるか判定する。

        True は合法手、False は着手不可を表す。盤面は変更しない。
        color は 'B' または 'W'、pos は盤面内であることが前提。
        """
        if self.cell[pos.y][pos.x] != 'E':
            return False

        for direction in AROUND:
            p = self._get_reverse_pos(color, pos, direction)

            if p is not None:
                return True

        return False

    def _has_legal_move(self, color: str) -> bool:
        """指定色に合法手が1つでもあるかを返す。

        全マスを調べ、合法手が見つかった時点で True を返す。
        color は 'B' または 'W' であることが前提。盤面は変更しない。
        """
        for y in range(8):
            for x in range(8):
                if self._ask(color, Pos(x, y)):
                    return True

        return False

    def _update(self, color: str, pos: Pos) -> None:
        """合法手 pos に石を置き、挟んだ相手の石を自色に変える。

        pos は事前に _ask() で合法と確認済みであること。
        各方向の終点石は既に自色なので、その手前の石だけを反転する。
        """
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

    def _genmove(self, color: str) -> Pos | None:
        """合法手を選択器へ渡し、検証後に実盤面へ一度だけ適用する。

        合法手がない場合は選択器を呼ばずNoneを返す。不正な選択結果や
        適用失敗はRuntimeErrorとし、成功座標を応答しない。
        """
        candidates: list[Pos] = []

        for y in range(8):
            for x in range(8):
                pos = Pos(x, y)

                if self._ask(color, pos):
                    candidates.append(pos)

        if len(candidates) == 0:
            return None

        rows = tuple(''.join(row) for row in self.cell)
        legal_moves = tuple((pos.x, pos.y) for pos in candidates)
        selection: object = self._selector(rows, color, legal_moves)
        if (
            not _is_move(selection)
            or not 0 <= selection[0] < 8
            or not 0 <= selection[1] < 8
            or selection not in legal_moves
        ):
            raise RuntimeError('selector_result_invalid')

        pos = candidates[legal_moves.index(selection)]
        if self._play(color, pos) is not True:
            raise RuntimeError('selector_apply_failed')

        return pos

    def __str__(self) -> str:
        """盤面を1行8マス、改行区切りの文字列にして返す。"""
        return '\n'.join(''.join(r) for r in self.cell)


def main() -> None:
    """GUIとの標準入出力プロトコルを読み取り、盤面操作を実行する。

    1行ずつコマンドを受け取り、quit で終了する。name、ask、play、
    genmove、showboard、result に応答するため、標準出力には応答だけを出す。
    """
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
        elif cmd == ['result']:
            print(board.result(), flush=True)


if __name__ == '__main__':
    main()

