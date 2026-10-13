"""履歴込み固定序盤母集合の生成・検証とseed付き非復元抽出。
合法手歩行による生成分布と、生成済み有限母集合内の抽出分布を区別する。
"""

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from harness import referee


FORMAT_VERSION = 1
RULES_VERSION = 'reversi-8x8-v1'
IDENTITY_VERSION = 1
GENERATOR_VERSION = 1
MAX_ATTEMPTS = 1_000_000


def canonical_json(value):
    """序盤/選択ID用のcanonical JSONを返す。NaN等は許可しない。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    """bytesはそのまま、それ以外はcanonical JSONのUTF-8をSHA-256化する。"""
    payload = value if isinstance(value, bytes) else canonical_json(value).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _transforms(rows):
    """8行の盤面を変更せず、反射と回転からD4の8変換を順に生成する。"""
    board = tuple(rows)
    for reflected in (False, True):
        current = tuple(row[::-1] for row in board) if reflected else board
        for _ in range(4):
            yield current
            current = tuple(''.join(current[7 - x][y] for x in range(8)) for y in range(8))


def orbit_id(rows, to_move):
    """D4変換の最小盤面表現と手番から対称重複排除用IDを返す。履歴は含めない。"""
    return digest(min(''.join(board) + to_move for board in _transforms(rows)))


def case_id(moves, rows, to_move):
    """初期盤面・全履歴・到達盤面・手番を結ぶケースIDを返す。AI版や保存先は含めない。"""
    return digest({
        'identity_version': IDENTITY_VERSION,
        'rules_version': RULES_VERSION,
        'initial_board': referee.INITIAL_ROWS,
        'moves': moves,
        'board': rows,
        'to_move': to_move,
    })


def _entry(moves, rows, to_move):
    """case ID・orbit ID・履歴・盤面・手番をまとめた序盤辞書を作る。

    盤面は行文字列のlistへ変換するが、movesは複製しない。
    """
    return {
        'case_id': case_id(moves, rows, to_move),
        'orbit_id': orbit_id(rows, to_move),
        'moves': moves,
        'board': list(rows),
        'to_move': to_move,
    }


def _play_random_opening(generator, placements):
    """渡された乱数生成器で合法手を選び、指定着手数の非終局序盤を作る。

    rows[y][x]はB/W/E、着手座標は(x, y)、passはNone。乱数生成器の状態を進め、
    途中または指定着手数で終局すればNoneを返す。全序盤の一様生成ではない。
    """
    rows = referee.INITIAL_ROWS
    color = 'B'
    moves = []
    placed = 0
    while placed < placements:
        legal = referee.legal_moves(rows, color)
        if not legal:
            other = 'W' if color == 'B' else 'B'
            if not referee.legal_moves(rows, other):
                return None
            moves.append({'color': color, 'position': None})
            color = other
            legal = referee.legal_moves(rows, color)
        position = generator.choice(legal)
        rows = referee.apply_move(rows, color, position)
        moves.append({'color': color, 'position': list(position)})
        placed += 1
        color = 'W' if color == 'B' else 'B'
    if referee.result(rows) != 'NG':
        return None
    return _entry(moves, rows, color)


def _population_content(population):
    """自己hashフィールドを除く母集合内容を新しい浅い辞書で返す。"""
    return {key: value for key, value in population.items() if key != 'population_hash'}


def validate_entry(entry, placements):
    """初期盤面から序盤履歴を再生し、着手数・盤面・手番・case/orbit IDを照合する。

    入力を変更せず期待entryを返し、不整合はValueError等で拒否する。
    """
    rows = referee.INITIAL_ROWS
    color = 'B'
    moves = entry.get('moves')
    if not isinstance(moves, list):
        raise ValueError('opening moves must be a list')
    placed = 0
    for move in moves:
        if not isinstance(move, dict) or set(move) != {'color', 'position'}:
            raise ValueError('opening move has invalid fields')
        if move['color'] != color:
            raise ValueError('opening move has wrong color')
        legal = referee.legal_moves(rows, color)
        position = move['position']
        if position is None:
            other = 'W' if color == 'B' else 'B'
            if legal or not referee.legal_moves(rows, other):
                raise ValueError('invalid pass in opening history')
        else:
            if (not isinstance(position, list) or len(position) != 2
                    or any(type(value) is not int for value in position)):
                raise ValueError('opening position must contain two integers')
            if tuple(position) not in legal:
                raise ValueError('illegal move in opening history')
            rows = referee.apply_move(rows, color, tuple(position))
            placed += 1
        color = 'W' if color == 'B' else 'B'
    if placed != placements or referee.result(rows) != 'NG':
        raise ValueError('opening has wrong placement count or is terminal')
    expected = _entry(moves, rows, color)
    if entry.get('board') != list(rows) or entry.get('to_move') != color:
        raise ValueError('opening board or side to move does not match history')
    if entry.get('case_id') != expected['case_id']:
        raise ValueError('opening case_id does not match history')
    if entry.get('orbit_id') != expected['orbit_id']:
        raise ValueError('opening orbit_id does not match board')
    return expected


def validate_population(population):
    """母集合の版・合法履歴・対称/ケース重複・正の整数N・内容hashを確認する。

    入力は変更せず同じ辞書を返す。報告側aliasや標本の資格はcontractsが検証する。
    """
    if (not isinstance(population, dict) or type(population.get('format_version')) is not int
            or population.get('format_version') != FORMAT_VERSION):
        raise ValueError('unsupported opening format_version')
    if population.get('rules_version') != RULES_VERSION:
        raise ValueError('unsupported opening rules_version')
    if type(population.get('identity_version')) is not int or population.get('identity_version') != IDENTITY_VERSION:
        raise ValueError('unsupported opening identity_version')
    if population.get('purpose') not in ('development', 'held_out'):
        raise ValueError('purpose must be development or held_out')
    placements = population.get('placements')
    entries = population.get('entries')
    if type(placements) is not int or placements < 1 or not isinstance(entries, list):
        raise ValueError('invalid placements or entries')
    seen_cases = set()
    seen_orbits = set()
    for entry in entries:
        checked = validate_entry(entry, placements)
        if checked['case_id'] in seen_cases or checked['orbit_id'] in seen_orbits:
            raise ValueError('duplicate case or symmetric opening')
        seen_cases.add(checked['case_id'])
        seen_orbits.add(checked['orbit_id'])
    if type(population.get('population_size')) is not int or population['population_size'] < 1:
        raise ValueError('population_size must be a positive integer')
    if population.get('population_size') != len(entries):
        raise ValueError('population_size does not match entries')
    if population.get('population_hash') != digest(_population_content(population)):
        raise ValueError('population_hash does not match contents')
    return population


def load_population(path):
    """UTF-8 JSONを読み、内容を検証した母集合を返す。I/O・JSON・検証例外は伝播する。"""
    return validate_population(json.loads(Path(path).read_text(encoding='utf-8')))


def generate_population(count, placements, seed, purpose='development', exclude_paths=(),
                        max_attempts=MAX_ATTEMPTS):
    """seed付き合法手歩行から対称重複を除いた固定母集合を生成する。

    除外JSONを読み、生成器版/Python/ソースhashを記録するが、出力ファイルは書かない。
    独立Randomを使い、試行上限までに指定件数を得られなければValueErrorとする。
    全可能序盤への一様抽出やPython版をまたぐ再現性の保証ではない。
    """
    if type(count) is not int or count < 1:
        raise ValueError('count must be a positive integer')
    if type(placements) is not int or placements < 1:
        raise ValueError('placements must be a positive integer')
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    if purpose not in ('development', 'held_out'):
        raise ValueError('purpose must be development or held_out')
    if type(max_attempts) is not int or count > max_attempts or max_attempts < 1:
        raise ValueError('requested count exceeds generation attempt limit')
    excluded = set()
    excluded_hashes = []
    for path in exclude_paths:
        source = load_population(path)
        excluded.update(entry['orbit_id'] for entry in source['entries'])
        excluded_hashes.append(source['population_hash'])
    generator = random.Random(seed)
    entries = []
    # 対称排除は集合構成の規約で、全可能序盤への一様性を意味しない。
    seen_orbits = set(excluded)
    for _ in range(max_attempts):
        if len(entries) == count:
            break
        entry = _play_random_opening(generator, placements)
        if entry is not None and entry['orbit_id'] not in seen_orbits:
            entries.append(entry)
            seen_orbits.add(entry['orbit_id'])
    if len(entries) != count:
        raise ValueError('could not generate {} unique openings within {} attempts'.format(count, max_attempts))
    module_path = Path(__file__).resolve()
    population = {
        'format_version': FORMAT_VERSION,
        'rules_version': RULES_VERSION,
        'identity_version': IDENTITY_VERSION,
        'purpose': purpose,
        'placements': placements,
        'generator': {
            'algorithm_version': GENERATOR_VERSION,
            'python_version': sys.version,
            'seed': seed,
            'max_attempts': max_attempts,
            'code_sha256': hashlib.sha256(module_path.read_bytes()).hexdigest(),
            'sampling': 'uniform_legal_move_walk_with_symmetric_deduplication',
            'excluded_population_hashes': excluded_hashes,
        },
        'population_size': len(entries),
        'entries': entries,
    }
    population['population_hash'] = digest(population)
    return validate_population(population)


def select_entries(population, count, seed):
    """検証した固定母集合から独立seedで非復元抽出し、選択entryと全manifestを返す。

    元entryへの参照を返すため複製ではない。countは1..Nで、Python Random.sampleに
    依存する。生成時の合法手歩行分布と、この母集合内抽出の分布を区別する。
    """
    validate_population(population)
    entries = population['entries']
    if type(count) is not int or not 1 <= count <= len(entries):
        raise ValueError('selection count must be between 1 and population size')
    if type(seed) is not int:
        raise ValueError('selection seed must be an integer')
    selected = random.Random(seed).sample(entries, count)
    manifest = {
        'format_version': 1,
        'population_hash': population['population_hash'],
        'population_size': len(entries),
        'selection_algorithm': 'python_random_sample_without_replacement_v1',
        'selection_seed': seed,
        'selected_case_ids': [entry['case_id'] for entry in selected],
        'sample_size': count,
    }
    manifest['selection_hash'] = digest(manifest)
    return selected, manifest


def main():
    """序盤生成/検証CLIを実行し、新規母集合JSONまたは検証概要を出力する。

    既存生成先は上書きせず、成功0、捕捉したI/O・値・JSON不正は終了2とする。
    """
    parser = argparse.ArgumentParser(description='Generate and validate fixed Reversi opening populations')
    subparsers = parser.add_subparsers(dest='command', required=True)
    generate = subparsers.add_parser('generate')
    generate.add_argument('--count', type=int, required=True)
    generate.add_argument('--placements', type=int, default=8)
    generate.add_argument('--seed', type=int, required=True)
    generate.add_argument('--purpose', choices=('development', 'held_out'), default='development')
    generate.add_argument('--exclude-openings', type=Path, action='append', default=[])
    generate.add_argument('--output', type=Path, required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'generate':
            population = generate_population(
                args.count, args.placements, args.seed, args.purpose, args.exclude_openings,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8', newline='\n') as output:
                json.dump(population, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write('\n')
            print(json.dumps({
                'population_hash': population['population_hash'],
                'population_size': population['population_size'],
                'output': str(args.output.resolve()),
            }, ensure_ascii=False))
            return 0
        population = load_population(args.path)
        print(json.dumps({
            'population_hash': population['population_hash'],
            'population_size': population['population_size'],
            'purpose': population['purpose'],
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(2, '{}\n'.format(error))


if __name__ == '__main__':
    raise SystemExit(main())
