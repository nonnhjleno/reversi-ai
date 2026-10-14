"""本体をimportしない、対戦検証専用のルール判定。"""

from collections.abc import Sequence
from typing import Literal

Rows = Sequence[str]
Position = tuple[int, int]
BoardRows = tuple[str, ...]
Result = Literal['B', 'W', 'D', 'NG']

INITIAL_ROWS = (
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEWBEEE',
    'EEEBWEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
)
DIRECTIONS = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)


def flipped_cells(rows: Rows, color: str, position: Position) -> list[Position]:
    """8×8のrowsから着手で反転する座標を列挙する。入力は変更しない。

    rows[y][x]はB/W/E、positionは(x,y)。範囲外・不正色・非合法手なら空リスト。
    盤面の形状自体の検査は呼出側の責務であり、不正な行を補修しない。
    """
    position_x, position_y = position
    if color not in ('B', 'W') or not (0 <= position_x < 8 and 0 <= position_y < 8):
        return []
    if rows[position_y][position_x] != 'E':
        return []
    opponent = 'W' if color == 'B' else 'B'
    captured: list[Position] = []
    for delta_x, delta_y in DIRECTIONS:
        current_x, current_y = position_x + delta_x, position_y + delta_y
        ray = []
        while 0 <= current_x < 8 and 0 <= current_y < 8:
            cell = rows[current_y][current_x]
            if cell == opponent:
                ray.append((current_x, current_y))
            else:
                if cell == color:
                    captured.extend(ray)
                break
            current_x += delta_x
            current_y += delta_y
    return captured


def legal_moves(rows: Rows, color: str) -> list[Position]:
    """有効な8×8盤面で指定色の合法座標を行優先で返す。盤面は変更しない。"""
    return [
        (position_x, position_y)
        for position_y in range(8)
        for position_x in range(8)
        if flipped_cells(rows, color, (position_x, position_y))
    ]


def apply_move(rows: Rows, color: str, position: Position) -> BoardRows:
    """合法手を適用した新しい行tupleを返し、不正な着手はValueErrorにする。

    元のrowsは更新しない。パスはこの関数の着手ではなく呼出側で扱う。
    """
    captured = flipped_cells(rows, color, position)
    if not captured:
        raise ValueError('Illegal move: {} {}'.format(color, position))
    updated = [list(row) for row in rows]
    for position_x, position_y in [position] + captured:
        updated[position_y][position_x] = color
    return tuple(''.join(row) for row in updated)


def result(rows: Rows) -> Result:
    """両色の合法手と石数からNGまたはB/W/Dを返す。盤面は変更しない。"""
    if legal_moves(rows, 'B') or legal_moves(rows, 'W'):
        return 'NG'
    black_count = ''.join(rows).count('B')
    white_count = ''.join(rows).count('W')
    return 'B' if black_count > white_count else 'W' if white_count > black_count else 'D'
