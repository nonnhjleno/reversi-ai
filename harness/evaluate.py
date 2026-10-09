import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness import referee
from harness.protocol import HarnessError, Player


def _file_info(path):
    resolved = Path(path).resolve()
    return {'path': str(resolved), 'sha256': hashlib.sha256(resolved.read_bytes()).hexdigest()}


def _call(label, function, *arguments):
    try:
        return function(*arguments)
    except HarnessError as error:
        error.player = label
        raise


def _failure(label, kind, message, command=None):
    error = HarnessError(kind, message, command)
    error.player = label
    raise error


def _check_state(players, rows):
    expected_result = referee.result(rows)
    for label, player in players.items():
        actual = tuple(_call(label, player.command, 'showboard'))
        if actual != rows:
            _failure(label, 'board_mismatch', 'Board differs from independent referee', 'showboard')
        actual_result = _call(label, player.command, 'result')[0]
        if actual_result != expected_result:
            _failure(label, 'protocol_error', 'Wrong result: {}'.format(actual_result), 'result')


def play_game(script_a, script_b, index, seed, startup_timeout=5.0,
              command_timeout=1.0, genmove_timeout=10.0):
    colors = {'B': 'A', 'W': 'B'} if index % 2 == 0 else {'B': 'B', 'W': 'A'}
    pair_seed = seed + (index // 2) * 2
    seeds = {'A': pair_seed, 'B': pair_seed + 1}
    players = {}
    rows = referee.INITIAL_ROWS
    game = {
        'index': index, 'colors': colors, 'seeds': seeds,
        'moves': [], 'result': None, 'winner': None, 'error': None,
    }
    try:
        for label, script in [('A', script_a), ('B', script_b)]:
            players[label] = _call(
                label, lambda script=script, label=label: Player(
                    script, seed=seeds[label], startup_timeout=startup_timeout,
                    command_timeout=command_timeout, genmove_timeout=genmove_timeout,
                ),
            )
        _check_state(players, rows)
        color = 'B'
        for _ in range(128):
            if referee.result(rows) != 'NG':
                break
            label = colors[color]
            other = 'B' if label == 'A' else 'A'
            possible = referee.legal_moves(rows, color)
            response = _call(label, players[label].command, 'genmove ' + color)[0]
            seconds = players[label].commands[-1]['seconds']
            if response == 'NG':
                if possible:
                    _failure(label, 'illegal_move', 'NG despite legal moves', 'genmove ' + color)
                position = None
            else:
                position = tuple(map(int, response.split()))
                if position not in possible:
                    _failure(label, 'illegal_move', 'Illegal coordinate: ' + response, 'genmove ' + color)
                rows = referee.apply_move(rows, color, position)
                acknowledgement = _call(other, players[other].command, 'play {} {}'.format(color, response))[0]
                if acknowledgement != 'OK':
                    _failure(other, 'protocol_error', 'Rejected a legal opponent move', 'play')
            game['moves'].append({
                'color': color, 'player': label, 'position': position,
                'seconds': seconds, 'board': rows,
            })
            _check_state(players, rows)
            color = 'W' if color == 'B' else 'B'
        final_result = referee.result(rows)
        if final_result == 'NG':
            _failure(None, 'protocol_error', 'Game exceeded 128 turns')
        for final_color, label in colors.items():
            if _call(label, players[label].command, 'genmove ' + final_color) != ['NG']:
                _failure(label, 'illegal_move', 'Move after terminal position', 'genmove ' + final_color)
        _check_state(players, rows)
        for label, player in players.items():
            _call(label, player.finish)
        game['result'] = final_result
        game['winner'] = None if final_result == 'D' else colors[final_result]
    except HarnessError as error:
        game['error'] = {
            'player': error.player, 'kind': error.kind,
            'command': error.command, 'message': str(error),
            'diagnostics': error.diagnostics,
        }
    finally:
        for player in players.values():
            player.abort()
    game['final_board'] = rows
    game['players'] = {
        label: {
            'name': player.name,
            'startup_seconds': player.startup_seconds,
            'exit_code': player.process.returncode,
            'stderr_tail': list(player.stderr_tail),
            'commands': player.commands,
        }
        for label, player in players.items()
    }
    return game


def summarize(games):
    completed = [game for game in games if game['error'] is None]
    failure_counts = {}
    for game in games:
        if game['error']:
            kind = game['error']['kind']
            failure_counts[kind] = failure_counts.get(kind, 0) + 1
    summary = {
        'attempted': len(games), 'completed': len(completed),
        'failed': len(games) - len(completed), 'failures': failure_counts,
    }
    for label in ('A', 'B'):
        wins = sum(game['winner'] == label for game in completed)
        draws = sum(game['result'] == 'D' for game in completed)
        times = [
            command['seconds']
            for game in games for command in game['players'].get(label, {}).get('commands', [])
            if command['command'].startswith('genmove ') and 'error' not in command
        ]
        summary[label] = {
            'wins': wins, 'losses': len(completed) - wins - draws, 'draws': draws,
            'win_rate': wins / len(completed) if completed else None,
            'point_rate': (wins + draws / 2) / len(completed) if completed else None,
            'genmove_samples': len(times),
            'mean_genmove_seconds': sum(times) / len(times) if times else None,
            'max_genmove_seconds': max(times) if times else None,
            'black_games': sum(game['colors']['B'] == label for game in games),
            'white_games': sum(game['colors']['W'] == label for game in games),
        }
    return summary


def evaluate(script_a, script_b, games=2, seed=20261009, startup_timeout=5.0,
             command_timeout=1.0, genmove_timeout=10.0, assets_a=(), assets_b=()):
    if games < 2 or games % 2:
        raise ValueError('games must be a positive even number (at least 2)')
    if min(startup_timeout, command_timeout, genmove_timeout) <= 0:
        raise ValueError('timeouts must be positive')
    directory = Path(__file__).parent
    report = {
        'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'runtime': {
            'python': sys.version, 'executable': sys.executable,
            'platform': platform.platform(),
        },
        'conditions': {
            'games': games, 'seed': seed, 'startup_timeout': startup_timeout,
            'command_timeout': command_timeout, 'genmove_timeout': genmove_timeout,
            'seed_scope': 'Python random and PYTHONHASHSEED; pairwise per player',
        },
        'targets': {
            'A': {**_file_info(script_a), 'assets': [_file_info(path) for path in assets_a]},
            'B': {**_file_info(script_b), 'assets': [_file_info(path) for path in assets_b]},
        },
        'harness': [_file_info(path) for path in sorted(directory.glob('*.py'))],
    }
    report['games'] = [
        play_game(script_a, script_b, index, seed, startup_timeout, command_timeout, genmove_timeout)
        for index in range(games)
    ]
    report['summary'] = summarize(report['games'])
    return report


def compare_reports(before, after):
    mismatches = []
    if before['summary']['failed'] or after['summary']['failed']:
        mismatches.append('incomplete_evaluation')
    for key in ('schema_version', 'conditions', 'runtime'):
        if before[key] != after[key]:
            mismatches.append(key)
    for key in ('sha256', 'assets'):
        previous = before['targets']['B'][key]
        current = after['targets']['B'][key]
        if key == 'assets':
            previous = [asset['sha256'] for asset in previous]
            current = [asset['sha256'] for asset in current]
        if previous != current:
            mismatches.append('opponent.' + key)
    if [item['sha256'] for item in before['harness']] != [item['sha256'] for item in after['harness']]:
        mismatches.append('harness')
    differences = {}
    for key in ('win_rate', 'point_rate', 'mean_genmove_seconds', 'max_genmove_seconds'):
        previous = before['summary']['A'][key]
        current = after['summary']['A'][key]
        differences[key] = None if previous is None or current is None else current - previous
    return {
        'comparable': not mismatches, 'condition_mismatches': mismatches,
        'before': before['summary'], 'after': after['summary'],
        'candidate_delta_after_minus_before': differences,
    }


def main():
    parser = argparse.ArgumentParser(description='Independent-process Reversi evaluation and JSON comparison')
    parser.add_argument('--a', type=Path)
    parser.add_argument('--b', type=Path)
    parser.add_argument('--games', type=int, default=2)
    parser.add_argument('--seed', type=int, default=20261009)
    parser.add_argument('--startup-timeout', type=float, default=5.0)
    parser.add_argument('--a-asset', type=Path, action='append', default=[])
    parser.add_argument('--b-asset', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--compare', nargs=2, type=Path, metavar=('BEFORE', 'AFTER'))
    arguments = parser.parse_args()
    try:
        if arguments.compare:
            before, after = [json.loads(path.read_text(encoding='utf-8')) for path in arguments.compare]
            report = compare_reports(before, after)
            success = report['comparable']
        else:
            if not arguments.a or not arguments.b or not arguments.output:
                parser.error('evaluation requires --a, --b and --output')
            report = evaluate(
                arguments.a, arguments.b, arguments.games, arguments.seed,
                arguments.startup_timeout, assets_a=arguments.a_asset, assets_b=arguments.b_asset,
            )
            success = report['summary']['failed'] == 0
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            with arguments.output.open('x', encoding='utf-8') as writer:
                json.dump(report, writer, indent=2, ensure_ascii=False)
                writer.write('\n')
        print(json.dumps(report if arguments.compare else report['summary'], indent=2, ensure_ascii=False))
        return 0 if success else 1
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, '{}\n'.format(error))


if __name__ == '__main__':
    raise SystemExit(main())
