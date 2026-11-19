"""棋譜を独立refereeで監査し、対局単位の分割を固定する。"""

from argparse import ArgumentParser
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from hashlib import file_digest, sha256
from itertools import product
import json
from pathlib import Path
from random import Random
import sys
from time import perf_counter
from typing import Iterable, Iterator

from harness import referee

SPLIT_SEED = 20261118
SAMPLE_SEED = 65
SPLITS = ('train', 'validation', 'test')
_PASS = (-1, -1)
_D4 = tuple(
    bytes(
        (7 - x if flip_x else x) + (7 - y if flip_y else y) * 8
        for row in range(8)
        for column in range(8)
        for x, y in ((row, column) if transpose else (column, row),)
    )
    + bytes(range(64, 256))
    for transpose, flip_x, flip_y in product((False, True), repeat=3)
)


class GameError(ValueError):
    """監査で数える棋譜異常。"""


@dataclass(frozen=True, slots=True)
class State:
    """教師情報を持たない途中局面。placementにはパスを数えない。"""

    rows: referee.BoardRows
    turn: str
    ply: int
    placements: int

    @property
    def bucket(self) -> int:
        """序・中・終盤、各黒番・白番の順で0〜5に写す。"""
        phase = 0 if self.placements < 20 else 1 if self.placements < 42 else 2
        return 2 * phase + (self.turn == 'W')


def sha_file(path: Path) -> str:
    """実ファイルbytesのSHA-256を返す。"""
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def parse_game(value: object) -> tuple[bytes, int | None]:
    """正規化行を厳密に検査し、着手を0〜64のbytesへ写す。"""
    if type(value) is not list or len(value) != 2:
        raise GameError('record_shape')
    items, margin = value
    if type(items) is not list or len(items) > 128:
        raise GameError('move_list_shape')
    if margin is not None and (type(margin) is not int or not -64 <= margin <= 64):
        raise GameError('margin_shape')
    out = bytearray()
    for pair in items:
        if type(pair) is not list or len(pair) != 2 or any(type(axis) is not int for axis in pair):
            raise GameError('coordinate_shape')
        x, y = pair
        if (x, y) == _PASS:
            out.append(64)
        elif 0 <= x < 8 and 0 <= y < 8:
            out.append(8 * y + x)
        else:
            raise GameError('coordinate_range')
    return bytes(out), margin


def replay(moves: bytes, margin: int | None, *, collect: bool = False) -> tuple[int, list[State]]:
    """交互手番・強制パス・合法手・終局石差を独立ルールと照合する。"""
    rows: referee.BoardRows = referee.INITIAL_ROWS
    turn = 'B'
    placements = 0
    states = []
    for ply, encoded in enumerate(moves):
        other = 'W' if turn == 'B' else 'B'
        if collect:
            states.append(State(rows, turn, ply, placements))
        if encoded == 64:
            if referee.legal_moves(rows, turn):
                raise GameError('pass_with_legal_move')
            if not referee.legal_moves(rows, other):
                raise GameError('pass_after_end')
        elif encoded < 64:
            try:
                rows = referee.apply_move(rows, turn, (encoded % 8, encoded // 8))
            except ValueError as error:
                raise GameError('illegal_move') from error
            placements += 1
        else:
            raise GameError('coordinate_range')
        turn = other
    if referee.result(rows) == 'NG':
        raise GameError('incomplete')
    actual = sum(row.count('B') - row.count('W') for row in rows)
    if margin is not None and margin != actual:
        raise GameError('label_mismatch')
    return actual, states


def canonical(moves: bytes) -> bytes:
    """着手列をすべてのD4像で比較する。パスと着手順を保つ。"""
    return min(moves.translate(table) for table in _D4)


def label(margin: int) -> int:
    """黒から見た終局石差を、観測結果の+1/0/-1へ写す。"""
    return (margin > 0) - (margin < 0)


def sample_states(states: list[State], game_id: str) -> tuple[State, ...]:
    """候補を手番×進行段階に分け、各群から決定的に1局面を選ぶ。"""
    buckets: list[list[State]] = [[] for _ in range(6)]
    for state in states:
        buckets[state.bucket].append(state)
    rng = Random(f'{SAMPLE_SEED}:{game_id}')
    return tuple(rng.choice(items) for items in buckets if items)


@dataclass(frozen=True, slots=True)
class Game:
    """独立再生済みの代表。D4識別子を分割と記録の両方に使う。"""

    line: int
    key: bytes
    moves: bytes
    margin: int

    @property
    def game_id(self) -> str:
        """分割に依存しない棋譜のIDを返す。"""
        return sha256(self.key).hexdigest()


def split(games: Iterable[Game], seed: int = SPLIT_SEED) -> dict[str, list[Game]]:
    """結果別に決定的な分割を行い、先頭部分から各規模を抽出できるよう並べる。"""
    groups: dict[str, list[Game]] = {key: [] for key in SPLITS}
    ordered = sorted(games, key=lambda game: game.key)
    if len({game.key for game in ordered}) != len(ordered):
        raise ValueError('repeated_split_group')
    rng = Random(seed)
    for outcome in (-1, 0, 1):
        bucket = [game for game in ordered if label(game.margin) == outcome]
        rng.shuffle(bucket)
        cut_train = len(bucket) * 8 // 10
        cut_validation = len(bucket) * 9 // 10
        groups['train'].extend(bucket[:cut_train])
        groups['validation'].extend(bucket[cut_train:cut_validation])
        groups['test'].extend(bucket[cut_validation:])
    for group in groups.values():
        rng.shuffle(group)
    return groups


def read_split(path: Path) -> Iterator[Game]:
    """manifestで固定済みのJSONLから代表を読み、IDと原成績を検証する。"""
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            index, identifier, margin, encoded = json.loads(line)
            if type(index) is not int or index < 1 or type(margin) is not int or abs(margin) > 64:
                raise ValueError('bad_split_record')
            moves = bytes.fromhex(encoded)
            if len(moves) > 128 or any(move > 64 for move in moves):
                raise ValueError('bad_split_moves')
            game = Game(index, canonical(moves), moves, margin)
            if identifier != game.game_id:
                raise ValueError('bad_split_id')
            yield game


def audit(source: Path, output: Path) -> dict[str, object]:
    """全行を検証し、除外・分布・分割のhashを新規領域へ保存する。"""
    start = perf_counter()
    source_hash = sha_file(source)
    output.mkdir()
    categories: Counter[str] = Counter()
    margins: Counter[int] = Counter()
    lengths: Counter[int] = Counter()
    prefixes: Counter[bytes] = Counter()
    representatives: dict[bytes, Game] = {}
    incomplete: dict[int, bytes] = {}
    passes = count = 0
    with (
        source.open('rb') as stream,
        (output / 'excluded.jsonl').open('x', encoding='utf-8') as bad,
    ):
        for count, line in enumerate(stream, 1):
            moves = b''
            try:
                if len(line) > 10_000:
                    raise GameError('record_too_large')
                moves, recorded = parse_game(json.loads(line))
                actual, _ = replay(moves, recorded)
            except GameError as failure:
                reason = str(failure)
                if reason == 'incomplete':
                    incomplete[count] = canonical(moves)
                categories[reason] += 1
                bad.write(json.dumps([count, reason]) + '\n')
                continue
            except (ValueError, RecursionError):
                categories['invalid_json'] += 1
                bad.write(json.dumps([count, 'invalid_json']) + '\n')
                continue
            margins[actual] += 1
            lengths[len(moves)] += 1
            passes += 64 in moves
            key = canonical(moves)
            previous = representatives.get(key)
            if previous is not None:
                reason = 'exact_duplicate' if previous.moves == moves else 'd4_duplicate'
                categories[reason] += 1
                bad.write(json.dumps([count, reason, previous.line]) + '\n')
                continue
            representatives[key] = Game(count, key, moves, actual)
            prefixes[key[:8]] += 1
    if sha_file(source) != source_hash:
        raise ValueError('source_changed_during_audit')
    keys = sorted(representatives)
    with (output / 'incomplete_prefix_matches.jsonl').open('x', encoding='utf-8') as matches:
        matched = 0
        for position, prefix in incomplete.items():
            ix = bisect_left(keys, prefix)
            if ix < len(keys) and keys[ix].startswith(prefix):
                matched += 1
                matches.write(json.dumps([position, representatives[keys[ix]].line]) + '\n')
    groups = split(representatives.values())
    manifest = {}
    for name, games in groups.items():
        path = output / (name + '.jsonl')
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            for game in games:
                stream.write(
                    json.dumps(
                        [game.line, game.game_id, game.margin, game.moves.hex()],
                        separators=(',', ':'),
                    )
                    + '\n'
                )
        manifest[name] = {
            'sha256': sha_file(path),
            'games': len(games),
            'labels': dict(Counter(label(game.margin) for game in games)),
        }
    assert sum(map(len, groups.values())) == len(representatives)
    split_spec = {
        'format_version': 1,
        'seed': SPLIT_SEED,
        'source_sha256': source_hash,
        'splits': manifest,
    }
    split_manifest = output / 'split-manifest.json'
    with split_manifest.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(split_spec, stream, indent=2, sort_keys=True)
    report = {
        'format_version': 1,
        'source_sha256': source_hash,
        'source_bytes': source.stat().st_size,
        'split_manifest_sha256': sha_file(split_manifest),
        'audit_source_sha256': sha_file(Path(__file__)),
        'input_games': count,
        'replayed_complete': sum(margins.values()),
        'usable': len(keys),
        'pass_games': passes,
        'margins': dict(margins),
        'lengths': dict(lengths),
        'outcomes': dict(
            Counter(
                {
                    outcome: sum(n for value, n in margins.items() if label(value) == outcome)
                    for outcome in (-1, 0, 1)
                }
            )
        ),
        'excluded': dict(categories),
        'excluded_sha256': sha_file(output / 'excluded.jsonl'),
        'incomplete_prefix_matches': matched,
        'first_eight_orbits': len(prefixes),
        'most_common_prefixes': [n for _, n in prefixes.most_common(8)],
        'seed': SPLIT_SEED,
        'splits': manifest,
        'seconds': perf_counter() - start,
        'argv': sys.argv,
        'python': sys.version,
    }
    with (output / 'audit.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    return report


def main() -> int:
    """正規化済みJSONLを監査するCLI。"""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = audit(args.source, args.output)
    print(
        json.dumps({name: result[name] for name in ('input_games', 'usable', 'excluded', 'splits')})
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
