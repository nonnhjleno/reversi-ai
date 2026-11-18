"""Phase 6の不変なn-tupleモデル。盤面のルールやファイルI/Oには依存しない。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import fsum, tanh
from typing import TYPE_CHECKING, Iterator, Tuple

if TYPE_CHECKING:
    from reversi_player import ai_engine
elif __package__:
    from . import ai_engine
else:
    import ai_engine


Coordinates = Tuple[ai_engine.Move, ...]
# 448項の和がbinary64の有限範囲に収まるよう、重み単体に上限を設ける。
MAX_WEIGHT = float(2**1000)
_DIGITS = {'E': 0, 'B': 1, 'W': 2}
SYMMETRIES = (
    'identity',
    'rotate90',
    'rotate180',
    'rotate270',
    'flip_x',
    'flip_y',
    'diagonal',
    'antidiagonal',
)


def _images(x: int, y: int) -> tuple[ai_engine.Move, ...]:
    """1座標をSYMMETRIESの順に写す。"""
    return (
        (x, y),
        (7 - y, x),
        (7 - x, 7 - y),
        (y, 7 - x),
        (7 - x, y),
        (x, 7 - y),
        (y, x),
        (7 - y, 7 - x),
    )


def _orbit(coordinates: Coordinates) -> tuple[Coordinates, ...]:
    """各操作で座標列を写し、順序と重複を保つ。"""
    return tuple(zip(*(_images(x, y) for x, y in coordinates)))


def _representatives(length: int) -> tuple[Coordinates, ...]:
    """同じ直線のD4像・逆順をまとめ、座標列の最小値だけを残す。"""
    representatives = set()
    for x in range(8):
        for y in range(8):
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                if 0 <= x + dx * (length - 1) < 8 and 0 <= y + dy * (length - 1) < 8:
                    orbit = _orbit(tuple((x + i * dx, y + i * dy) for i in range(length)))
                    representatives.add(min(min(line, line[::-1]) for line in orbit))
    return tuple(sorted(representatives))


TUPLES = _representatives(2) + _representatives(3)
LUT_SIZES = tuple(3 ** len(coords) for coords in TUPLES)
_WEIGHTS_PER_TURN = sum(LUT_SIZES)
WEIGHT_COUNT = 2 * _WEIGHTS_PER_TURN
REFERENCE_COUNT = len(TUPLES) * len(SYMMETRIES)


def _reference_layout() -> tuple[tuple[int, Coordinates], ...]:
    """黒LUT内の先頭位置と順序付きの座標列を、各参照について前計算する。"""
    layout: list[tuple[int, Coordinates]] = []
    offset = 0
    for coordinates, size in zip(TUPLES, LUT_SIZES):
        layout.extend((offset, image) for image in _orbit(coordinates))
        offset += size
    return tuple(layout)


_LAYOUT = _reference_layout()


def _pattern_id(rows: ai_engine.BoardRows, coordinates: Coordinates) -> int:
    """検証済みの座標列を、先頭が最上位桁の3進数として読む。"""
    index = 0
    for x, y in coordinates:
        index = 3 * index + _DIGITS[rows[y][x]]
    return index


def pattern_id(rows: ai_engine.BoardRows, coordinates: Coordinates) -> int:
    """2または3個の相異なる座標を符号化する。不正な盤面や座標はValueError。"""
    ai_engine.validate_position(rows, 'B')
    if (
        type(coordinates) is not tuple
        or len(coordinates) not in (2, 3)
        or any(
            type(pos) is not tuple
            or len(pos) != 2
            or any(type(axis) is not int or not 0 <= axis < 8 for axis in pos)
            for pos in coordinates
        )
        or len(set(coordinates)) != len(coordinates)
    ):
        raise ValueError('invalid_tuple_coordinates')
    return _pattern_id(rows, coordinates)


def _references(rows: ai_engine.BoardRows, turn: str) -> Iterator[int]:
    """盤面と手番を検証し、同じ重みへの参照も出現ごとに返す。"""
    ai_engine.validate_position(rows, turn)
    turn_offset = 0 if turn == 'B' else _WEIGHTS_PER_TURN
    return (
        turn_offset + offset + _pattern_id(rows, coordinates) for offset, coordinates in _LAYOUT
    )


@dataclass(frozen=True)
class NtupleModel:
    """手番、tuple ID、パターンindex順に有限のbinary64重みを持つ不変モデル。"""

    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        """重み数・型・値域を検証し、通常のfloatに統一する。"""
        if type(self.weights) is not tuple or len(self.weights) != WEIGHT_COUNT:
            raise ValueError('invalid_weight_count')
        for weight in self.weights:
            if type(weight) not in (int, float) or not -MAX_WEIGHT <= weight <= MAX_WEIGHT:
                raise ValueError('invalid_weight')
        normalized = tuple(map(float, self.weights))
        # 値域の検査を通過したintはfloatへ変換可能で、NaN/Infもここまで到達しない。
        object.__setattr__(self, 'weights', normalized)

    @classmethod
    def zeros(cls) -> NtupleModel:
        """決定的な全ゼロの未学習モデルを作る。"""
        return cls((0.0,) * WEIGHT_COUNT)

    def features(self, rows: ai_engine.BoardRows, turn: str) -> tuple[tuple[int, int], ...]:
        """学習に使う(重みID, 出現回数)をID昇順で返す。"""
        return tuple(sorted(Counter(_references(rows, turn)).items()))

    def logit(self, rows: ai_engine.BoardRows, turn: str) -> float:
        """出現ごとに重みを加えた黒視点のtanh入力を返す。"""
        return fsum(self.weights[ref] for ref in _references(rows, turn))

    def value(self, rows: ai_engine.BoardRows, turn: str) -> float:
        """黒視点の推論値を返す。終局を判定する責務は呼び出し側に置く。"""
        return tanh(self.logit(rows, turn))
