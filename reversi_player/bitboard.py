"""探索内用の不変盤面と、8方向のビット演算による合法手・反転。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reversi_player import ai_engine
elif __package__:
    from . import ai_engine
else:
    import ai_engine

_FULL = (1 << 64) - 1
_NO_LEFT = 0xFEFEFEFEFEFEFEFE
_NO_RIGHT = 0x7F7F7F7F7F7F7F7F
_FILE_BITS = tuple(1 << x for x in range(8))
# xが右向き、yが下向き。端列の除外をシフトより先に適用する。
_SHIFTS = (
    (1, _NO_RIGHT),
    (-1, _NO_LEFT),
    (8, _FULL),
    (-8, _FULL),
    (9, _NO_RIGHT),
    (7, _NO_LEFT),
    (-7, _NO_RIGHT),
    (-9, _NO_LEFT),
)


def _shift(bits: int, amount: int, mask: int) -> int:
    """盤面端を越える石を落とし、1方向へずらす。"""
    if amount > 0:
        return ((bits & mask) << amount) & _FULL
    return (bits & mask) >> -amount


def _move_bit(move: ai_engine.Move) -> int:
    """妥当な(x,y)をビットへ変換し、範囲外やboolを拒否する。"""
    if type(move) is not tuple or len(move) != 2:
        raise ValueError('invalid_move')
    x, y = move
    if type(x) is not int or type(y) is not int or not (0 <= x < 8 and 0 <= y < 8):
        raise ValueError('invalid_move')
    return 1 << (8 * y + x)


@dataclass(frozen=True)
class BitPosition:
    """黒・白の64bit集合を持つ盤面。2つの集合は重ならない。"""

    black: int
    white: int

    def __post_init__(self) -> None:
        """不正な整数、盤外ビット、2色の重なりを拒否する。"""
        if (
            type(self.black) is not int
            or type(self.white) is not int
            or not 0 <= self.black <= _FULL
            or not 0 <= self.white <= _FULL
            or self.black & self.white
        ):
            raise ValueError('invalid_bit_position')

    @classmethod
    def from_rows(cls, rows: ai_engine.BoardRows) -> BitPosition:
        """検証済み形式の行を黒・白の集合へ変換する。"""
        ai_engine.validate_position(rows, 'B')
        black = white = 0
        for y, row in enumerate(rows):
            for x, cell in enumerate(row):
                if cell == 'B':
                    black |= 1 << (8 * y + x)
                elif cell == 'W':
                    white |= 1 << (8 * y + x)
        return cls(black, white)

    def to_rows(self) -> ai_engine.BoardRows:
        """既存評価関数が読む、行順の不変盤面を生成する。"""
        rows = []
        for offset in range(0, 64, 8):
            black, white = self.black >> offset, self.white >> offset
            rows.append(
                ''.join('B' if black & bit else 'W' if white & bit else 'E' for bit in _FILE_BITS)
            )
        return tuple(rows)

    def _sides(self, color: str) -> tuple[int, int]:
        """指定した色の石と相手石をこの順で返す。"""
        if color == 'B':
            return self.black, self.white
        if color == 'W':
            return self.white, self.black
        raise ValueError('invalid_color')

    def legal_mask(self, color: str) -> int:
        """自色と空きで挟む相手石を各方向に見つけ、合法手の集合を返す。"""
        own, opponent = self._sides(color)
        empty = _FULL ^ (own | opponent)
        result = 0
        for amount, mask in _SHIFTS:
            run = _shift(own, amount, mask) & opponent
            # 着手先と自色の間に並ぶ相手石は多くても6個。
            for _ in range(5):
                run |= _shift(run, amount, mask) & opponent
            result |= _shift(run, amount, mask) & empty
        return result

    def legal_moves(self, color: str) -> ai_engine.LegalMoves:
        """合法手を下位ビットから走査し、行・列順に返す。"""
        mask = self.legal_mask(color)
        moves = []
        while mask:
            bit = mask & -mask
            index = bit.bit_length() - 1
            moves.append((index % 8, index // 8))
            mask ^= bit
        return tuple(moves)

    def _flips(self, own: int, opponent: int, start: int) -> int:
        """着手ビットから、自色で閉じた相手石の集合を返す。"""
        if start & (own | opponent):
            return 0
        result = 0
        for amount, mask in _SHIFTS:
            line = 0
            probe = _shift(start, amount, mask)
            while probe & opponent:
                line |= probe
                probe = _shift(probe, amount, mask)
            if probe & own:
                result |= line
        return result

    def flips(self, color: str, move: ai_engine.Move) -> int:
        """試験・診断用に反転集合を返す。着手できない位置の集合は空。"""
        own, opponent = self._sides(color)
        return self._flips(own, opponent, _move_bit(move))

    def play(self, color: str, move: ai_engine.Move) -> BitPosition:
        """合法な着手を適用して新しい盤面を作る。元盤面は保持する。"""
        own, opponent = self._sides(color)
        start = _move_bit(move)
        flipped = self._flips(own, opponent, start)
        if not flipped:
            raise ValueError('illegal_move')
        own |= start | flipped
        opponent ^= flipped
        return BitPosition(own, opponent) if color == 'B' else BitPosition(opponent, own)

    def disc_difference(self, color: str) -> int:
        """指定色の石数から相手色の石数を引く。CPython 3.7でも利用できる。"""
        own, opponent = self._sides(color)
        return bin(own).count('1') - bin(opponent).count('1')
