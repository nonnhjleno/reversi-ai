"""リバーシAIの着手選択を行う。盤面規則や入出力には依存しない。"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

type BoardRows = tuple[str, ...]
type Move = tuple[int, int]
type LegalMoves = tuple[Move, ...]
type MoveSelector = Callable[[BoardRows, str, LegalMoves], Move | None]
type LegalMovesFor = Callable[[BoardRows, str], LegalMoves]
type ApplyMove = Callable[[BoardRows, str, Move], BoardRows]

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


_CORNERS = ((0, 0), (7, 0), (0, 7), (7, 7))


def validate_position(rows: BoardRows, color: str) -> None:
    """通常tupleの8×8 B/W/E snapshotと通常strのB/Wを検査する。

    callbackと評価で同じ値域を使用し、違反はValueErrorにする。
    盤面の到達可能性や過去の手番はこの静的snapshot境界では検査しない。
    """
    if (
        type(rows) is not tuple
        or len(rows) != 8
        or any(
            type(row) is not str or len(row) != 8 or any(cell not in 'BWE' for cell in row)
            for row in rows
        )
    ):
        raise ValueError('invalid_board_rows')
    if type(color) is not str or color not in ('B', 'W'):
        raise ValueError('invalid_color')


@dataclass(frozen=True)
class _PositionFeatures:
    """固定視点の整数特徴と終局判定。外部の勝ち点率や探索予算ではない。"""

    corner_difference: int
    mobility_difference: int
    position_difference: int
    disc_difference: int
    terminal: bool

    @property
    def normalized(self) -> tuple[float, float, float]:
        """角・mobility・位置を固定分母で正規化して返す。"""
        return (
            self.corner_difference / 4,
            self.mobility_difference / 64,
            self.position_difference / 696,
        )

    @property
    def contributions(self) -> tuple[float, float, float]:
        """非終局式の3項の寄与を診断用に返す。終局得点とは区別する。"""
        return (
            10.0 * self.corner_difference,
            40 * self.mobility_difference / 64,
            20 * self.position_difference / 696,
        )


def _effective_position_weights(rows: BoardRows) -> tuple[tuple[int, ...], ...]:
    """角の二重加点と取得後の隣接負点を除いた独立の表を作る。"""
    weights = [list(row) for row in POSITION_WEIGHTS]
    for x, y in _CORNERS:
        weights[y][x] = 0
        if rows[y][x] != 'E':
            adjacent_x, adjacent_y = (1 if x == 0 else 6), (1 if y == 0 else 6)
            for nx, ny in ((adjacent_x, y), (x, adjacent_y), (adjacent_x, adjacent_y)):
                weights[ny][nx] = 0
    return tuple(tuple(row) for row in weights)


def _extract_features(
    rows: BoardRows, viewpoint: str, legal_moves_for: LegalMovesFor
) -> _PositionFeatures:
    """両色の完全合法手数と盤面を観測し、固定視点の整数特徴を返す。

    callbackはstatelessで盤面を変更せず完全な合法手tupleを返す契約とする。
    callbackの故障は呼出側へ伝播し、不完全な結果を補完しない。
    """
    validate_position(rows, viewpoint)
    opponent = 'W' if viewpoint == 'B' else 'B'
    own_moves = legal_moves_for(rows, viewpoint)
    opponent_moves = legal_moves_for(rows, opponent)
    weights = _effective_position_weights(rows)
    corner_difference = sum(
        (rows[y][x] == viewpoint) - (rows[y][x] == opponent) for x, y in _CORNERS
    )
    position_difference = sum(
        weights[y][x] * ((cell == viewpoint) - (cell == opponent))
        for y, row in enumerate(rows)
        for x, cell in enumerate(row)
    )
    return _PositionFeatures(
        corner_difference,
        len(own_moves) - len(opponent_moves),
        position_difference,
        sum(row.count(viewpoint) - row.count(opponent) for row in rows),
        not own_moves and not opponent_moves,
    )


def _score_features(features: _PositionFeatures) -> float:
    """確定勝敗を優先し、非終局は整数分子を一度だけfloatへ変換する。"""
    if features.terminal:
        difference = features.disc_difference
        if difference > 0:
            return float(1000 + difference)
        if difference < 0:
            return float(-1000 + difference)
        return 0.0

    # 40*c/4 + 40*m/64 + 20*p/696 と同値。同じ有理数の丸め方を統一する。
    numerator = (
        6960 * features.corner_difference
        + 435 * features.mobility_difference
        + 20 * features.position_difference
    )
    return numerator / 696


def evaluate_position(rows: BoardRows, viewpoint: str, legal_moves_for: LegalMovesFor) -> float:
    """指定色の固定視点で盤面価値を返す。盤面・乱数・標準出力は変更しない。

    両色に合法手がなければ終局。一方だけのパスは非終局として採点する。
    非終局の絶対値は100以下で、勝敗確定値はその範囲を超える。
    この得点は勝率ではなく、人手で固定した候補比較用の値である。
    """
    return _score_features(_extract_features(rows, viewpoint, legal_moves_for))


def make_one_ply_selector(legal_moves_for: LegalMovesFor, apply_move: ApplyMove) -> MoveSelector:
    """statelessなルールcallback2個を保持する決定的な3引数selectorを作る。"""

    def _select_one_ply(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
        """各候補を仮着手してroot色で採点し、同点は(y,x)順で選ぶ。

        rootの完全な合法手集合はBoardが保証する。空候補ではcallbackを呼ばない。
        仮着手は新snapshotを返す契約で、実Boardの確定着手はこの関数では行わない。
        """
        if not legal_moves:
            return None
        validate_position(rows, color)
        return min(
            legal_moves,
            key=lambda move: (
                -evaluate_position(apply_move(rows, color, move), color, legal_moves_for),
                move[1],
                move[0],
            ),
        )

    return _select_one_ply
