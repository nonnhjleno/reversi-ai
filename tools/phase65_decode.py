"""授業データの限定された直列化形式を、オブジェクト復元なしでJSONLへ変換する。"""

from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import pickletools
import sys
from typing import BinaryIO, Iterable, Iterator, cast

Move = tuple[int, int]
RawGame = tuple[list[Move], int]
_ALLOWED = frozenset(
    'PROTO EMPTY_LIST MARK BININT1 BININT2 BININT TUPLE2 BINPUT LONG_BINPUT '
    'BINGET LONG_BINGET APPEND APPENDS STOP'.split()
)
_MAX_SIZE = 64 * 1024 * 1024
_MAX_MOVES = 128
_MAX_GAMES = 200_000
_MAX_MEMO = 6_500_000


def _operations(stream: BinaryIO) -> Iterator[tuple[str, object]]:
    """許可したopcodeだけを列挙し、STOPの後に余分なbytesがないことを確認する。"""
    for op, value, offset in pickletools.genops(stream):
        if op.name not in _ALLOWED:
            raise ValueError(f'unsupported_opcode:{op.name}@{offset}')
        yield op.name, value
    if stream.read(1):
        raise ValueError('bytes_after_stop')


def _inventory(stream: BinaryIO) -> tuple[Counter[str], set[int]]:
    """参照されるmemo番号だけを集め、配列の上限を事前に固定する。"""
    counts: Counter[str] = Counter()
    needed = set()
    for op, value in _operations(stream):
        counts[op] += 1
        if op in ('BINGET', 'LONG_BINGET'):
            if type(value) is not int or not 0 <= value < _MAX_MEMO:
                raise ValueError('invalid_reference_number')
            needed.add(value)
    if counts['PROTO'] != 1 or counts['STOP'] != 1:
        raise ValueError('invalid_protocol_boundary')
    return counts, needed


def _move(value: object) -> Move:
    """memoで共有できる値を二整数の座標tupleに限定する。"""
    if (
        type(value) is not tuple
        or len(value) != 2
        or type(value[0]) is not int
        or type(value[1]) is not int
    ):
        raise ValueError('invalid_move_shape')
    return value[0], value[1]


def _game(value: object) -> RawGame:
    """着手列と石差を取り出す。座標や結果の意味の検査は独立再生で行う。"""
    if type(value) is not tuple or len(value) != 2:
        raise ValueError('invalid_game_shape')
    moves, difference = value
    if type(moves) is not list or type(difference) is not int or len(moves) > _MAX_MOVES:
        raise ValueError('invalid_game_fields')
    return list(map(_move, moves)), difference


def decode(stream: BinaryIO, references: set[int]) -> Iterator[RawGame]:
    """限定したスタック文法を読み、最外周の各ゲームを順番に返す。"""
    operations = _operations(stream)
    if next(operations, None) != ('PROTO', 3) or next(operations, None) != ('EMPTY_LIST', None):
        raise ValueError('expected_protocol_3_list')
    root = object()
    stack = [root]
    marks: list[int] = []
    memo: dict[int, Move] = {}
    memo_size = games = 0
    for op, value in operations:
        if op in ('BININT1', 'BININT2', 'BININT'):
            stack.append(value)
        elif op == 'EMPTY_LIST':
            if len(marks) > 1 or (not marks and len(stack) != 1):
                raise ValueError('invalid_move_list_location')
            stack.append([])
        elif op == 'MARK':
            if len(marks) >= 2:
                raise ValueError('nested_list_limit')
            marks.append(len(stack))
        elif op == 'TUPLE2':
            floor = marks[-1] if marks else 1
            if len(stack) - floor < 2:
                raise ValueError('missing_tuple_items')
            right = stack.pop()
            left = stack.pop()
            if type(right) is not int or type(left) not in (int, list):
                raise ValueError('invalid_tuple_items')
            stack.append((left, right))
        elif op in ('BINPUT', 'LONG_BINPUT'):
            if value != memo_size or memo_size >= _MAX_MEMO:
                raise ValueError('invalid_memo_index')
            if memo_size in references:
                memo[memo_size] = _move(stack[-1])
            memo_size += 1
        elif op in ('BINGET', 'LONG_BINGET'):
            number = cast(int, value)  # genopsの整数opcodeで、inventoryでも値域を確認する。
            if number not in memo:
                raise ValueError('invalid_memo_reference')
            stack.append(memo[number])
        elif op in ('APPEND', 'APPENDS'):
            if op == 'APPENDS':
                if not marks:
                    raise ValueError('missing_append_mark')
                index = marks.pop()
            else:
                index = len(stack) - 1
            if index < 1:
                raise ValueError('missing_append_list')
            target = stack[index - 1]
            items = stack[index:]
            del stack[index:]
            if target is root and not marks:
                for item in items:
                    games += 1
                    if games > _MAX_GAMES:
                        raise ValueError('game_count_limit')
                    yield _game(item)
            elif isinstance(target, list) and len(marks) <= 1:
                if len(target) + len(items) > _MAX_MOVES:
                    raise ValueError('move_count_limit')
                target.extend(map(_move, items))
            else:
                raise ValueError('invalid_append_target')
        elif op == 'STOP':
            if len(stack) != 1 or marks or not games:
                raise ValueError('invalid_stop')
        else:
            raise ValueError('unexpected_opcode:' + op)
        if len(stack) > 1200:
            raise ValueError('stack_limit')


def main(argv: Iterable[str] | None = None) -> int:
    """原本hashを照合し、stderrにopcode内訳、stdoutにJSONLを出力する。"""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('expected_sha256')
    args = parser.parse_args(argv)
    if args.input.stat().st_size > _MAX_SIZE:
        raise ValueError('input_size_limit')
    with args.input.open('rb') as stream:
        checksum = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
        actual = checksum.hexdigest()
        if actual != args.expected_sha256:
            raise ValueError('input_hash_mismatch')
        stream.seek(0)
        counts, references = _inventory(stream)
        stream.seek(0)
        games = 0
        for game in decode(stream, references):
            print(json.dumps(game, separators=(',', ':')))
            games += 1
    print(
        json.dumps(
            {
                'source_sha256': actual,
                'opcodes': counts,
                'memo_references': len(references),
                'games': games,
                'version': sys.version,
            }
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
