"""行形式を経由せず、既存n-tupleの参照列と黒視点の値を求める。"""

from __future__ import annotations

from math import fsum, tanh
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from reversi_player import ntuple
    from reversi_player.bitboard import BitPosition
elif __package__:
    from . import ntuple
    from .bitboard import BitPosition
else:
    import ntuple
    from bitboard import BitPosition

_CELL_MASKS = tuple(1 << i for i in range(64))
# 既存表は長さ2を先に並べる。D4像の順と重複を保ったまま座標だけを置き換える。
_REFERENCES_2 = tuple(
    (offset, 8 * y0 + x0, 8 * y1 + x1)
    for offset, (x0, y0), (x1, y1) in (
        (offset, coords[0], coords[1]) for offset, coords in ntuple._LAYOUT if len(coords) == 2
    )
)
_REFERENCES_3 = tuple(
    (offset, 8 * y0 + x0, 8 * y1 + x1, 8 * y2 + x2)
    for offset, (x0, y0), (x1, y1), (x2, y2) in (
        (offset, coords[0], coords[1], coords[2])
        for offset, coords in ntuple._LAYOUT
        if len(coords) == 3
    )
)


def _digits(position: BitPosition) -> tuple[int, ...]:
    """不変の黒・白マスクから、ビット番号順の64個の3進数字を得る。"""
    black, white = position.black, position.white
    return tuple(1 if black & bit else 2 if white & bit else 0 for bit in _CELL_MASKS)


def _indexes(digits: tuple[int, ...], side: int) -> Iterator[int]:
    """各出現を元の順で符号化し、手番別LUTの全体位置を返す。"""
    for offset, a, b in _REFERENCES_2:
        yield side + offset + 3 * digits[a] + digits[b]
    for offset, a, b, c in _REFERENCES_3:
        yield side + offset + 9 * digits[a] + 3 * digits[b] + digits[c]


def references(position: BitPosition, turn: str) -> Iterator[int]:
    """Bitboardと手番を検証し、重複を含む既存順の参照列を返す。"""
    if not isinstance(position, BitPosition):
        raise ValueError('invalid_bitboard_position')
    if type(turn) is not str or turn not in ('B', 'W'):
        raise ValueError('invalid_turn')
    side = 0 if turn == 'B' else ntuple._WEIGHTS_PER_TURN
    return _indexes(_digits(position), side)


def logit(position: BitPosition, turn: str, *, model: ntuple.NtupleModel) -> float:
    """同じ順に参照した重みから黒視点のtanh入力を計算する。"""
    weights = model.weights
    return fsum(weights[ref] for ref in references(position, turn))


def value(position: BitPosition, turn: str, *, model: ntuple.NtupleModel) -> float:
    """既存モデルの値を黒視点で返す。終局判定は呼出側が行う。"""
    return tanh(logit(position, turn, model=model))
