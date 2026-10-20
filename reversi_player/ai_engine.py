"""リバーシAIの着手選択を行う。盤面規則や入出力には依存しない。"""

from __future__ import annotations

import random
from collections.abc import Callable

type BoardRows = tuple[str, ...]
type Move = tuple[int, int]
type LegalMoves = tuple[Move, ...]
type MoveSelector = Callable[[BoardRows, str, LegalMoves], Move | None]

POSITION_WEIGHTS = (
    (120, -20, 20, 5, 5, 20, -20, 120),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (120, -20, 20, 5, 5, 20, -20, 120),
)


def select_move(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
    """順序付き合法手の複製をshuffleし、先頭の座標を返す。

    rowsとcolorは将来の選択方式と共通の入力契約として受け取る。
    入力は変更せず、候補が空なら乱数を消費せずNoneを返す。
    """
    candidates = list(legal_moves)
    if not candidates:
        return None

    random.shuffle(candidates)
    return candidates[0]


def select_positional_move(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
    """着手先の固定位置重みを比較し、同点は(y, x)昇順で返す。

    ``legal_moves``はBoardが列挙した完全な合法手集合とする。盤面・候補・乱数状態は変更しない。
    空候補ではNoneを返し、重みは学習済みではない暫定的な人手設定を使う。
    """
    if not legal_moves:
        return None

    return min(
        legal_moves,
        key=lambda move: (-POSITION_WEIGHTS[move[1]][move[0]], move[1], move[0]),
    )
