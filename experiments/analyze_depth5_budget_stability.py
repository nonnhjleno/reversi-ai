"""保存した独立診断と参考対局を集計する。"""

from collections import Counter, defaultdict

from depth5_budget_stability import OUT, lines, read, save, sha, time_stats


def depth_stats(events):
    """直接観測した反復だけを深度集計に含める。"""
    return {
        'observed': len(events),
        'started_5': sum(event['max_depth_started'] == 5 for event in events),
        'completed_5': sum(event['completed_depth'] == 5 for event in events),
        'adopted_5': sum(event['adopted_depth'] == 5 for event in events),
        'started_5_returned_4': sum(
            event['max_depth_started'] == 5 and event['adopted_depth'] == 4 for event in events),
        'depth_5_changed_move_from_depth_4': sum(
            event['adopted_depth'] == 5 and any(
                it['depth'] == 4 and it['status'] == 'completed' and it['move'] != event['returned_move']
                for it in event['iterations']) for event in events),
        'adopted_4': sum(event['adopted_depth'] == 4 for event in events),
        'fallback': sum(event['fallback_used'] for event in events),
        'stop_reasons': Counter(event['stop_reason'] for event in events),
        'internal_seconds': time_stats([event['search_internal_elapsed_ns'] / 1e9 for event in events]),
    }


def counts(items):
    """正常応答・失敗・時間を全試行から集計する。"""
    answered = [item['genmove']['seconds'] for item in items if 'genmove' in item]
    events = [event for item in items for event in item.get('events', []) if event['type'] == 'genmove']
    return {'trials': len(items), 'ok': sum(item['status'] == 'ok' for item in items),
            'failures': Counter(item['error_type'] for item in items if item['status'] == 'failed'),
            'depths': depth_stats(events), 'external_seconds': time_stats(answered),
            'legal_position_external_seconds': time_stats([
                item['genmove']['seconds'] for item in items
                if item.get('legal_count') and 'genmove' in item])}


def measurement():
    """独立した負荷条件、診断あり／なし、局面別の結果を保存する。"""
    paths = sorted((OUT / 'probes').glob('*.json'))
    by_condition = defaultdict(list)
    by_position = defaultdict(list)
    paired = defaultdict(dict)
    for path in paths:
        if path.name == 'old-normalized-config.json' or path.name.startswith('old-d'):
            continue
        item = read(path)
        condition, index, repeat, depth, mode = item['name'].split('-')
        by_condition[(condition, depth, mode)].append(item)
        by_position[(condition, depth, mode, item['position_id'])].append(item)
        paired[(condition, depth, index, repeat)][mode] = item
    pair_groups = defaultdict(list)
    for (condition, depth, index, repeat), items in paired.items():
        if items.keys() != {'plain', 'diag'} or any(r['status'] != 'ok' for r in items.values()):
            continue
        plain, diag = items['plain']['genmove'], items['diag']['genmove']
        pair_groups[(condition, depth)].append({
            'position_id': items['diag']['position_id'], 'repeat': repeat,
            'plain_move': plain['response'], 'diag_move': diag['response'],
            'diag_minus_plain_seconds': diag['seconds'] - plain['seconds'],
        })
    plan = read(OUT / 'plan.json')
    expected = len(plan['cases']) * plan['repeats'] * len(plan['conditions']) * 4
    expected_searches = sum(bool(case['legal_moves']) for case in plan['cases']) * plan['repeats']
    payload = {
        'plan_sha256': sha(OUT / 'plan.json'), 'expected_trials': expected,
        'expected_searches_per_depth_condition': expected_searches,
        'actual_trials': sum(map(len, by_condition.values())),
        'by_condition': {'/'.join(key): counts(items) for key, items in by_condition.items()},
        'by_position': {'/'.join(key): {
            **counts(items), 'move_counts': Counter(
                ' '.join(r['genmove']['response']) for r in items if r['status'] == 'ok'),
        } for key, items in by_position.items()},
        'paired_telemetry': {'/'.join(key): {
            'count': len(rows), 'move_matches': sum(r['plain_move'] == r['diag_move'] for r in rows),
            'time_delta_seconds': time_stats([r['diag_minus_plain_seconds'] for r in rows]),
            'rows': rows,
        } for key, rows in pair_groups.items()},
    }
    save(OUT / 'measurement-summary.json', payload)


def matches():
    """完走局と先後交換ペアの勝ち点、対局中の実深度と時間を集計する。"""
    details = []
    by_role = defaultdict(list)
    events = defaultdict(list)
    pairs = defaultdict(list)
    failures = Counter()
    for path in sorted((OUT / 'games').glob('game-??.json')):
        raw = read(path)
        game = raw['game']
        status = game['protocol_status']
        detail = {'index': game['index'], 'case_id': game['case_id'], 'colors': game['colors'],
                  'result': game['result'], 'score_a0': game['score_a'],
                  'protocol_status': status, 'error': game['error'], 'event_errors': raw['event_errors']}
        details.append(detail)
        successful = status == 'verified' and not raw['event_errors']
        if successful:
            pairs[game['case_id']].append(game)
        else:
            failures[game['error']['kind'] if game['error'] else 'evidence'] += 1
        for role, player in game['players'].items():
            by_role[role].extend(c['seconds'] for c in player['commands']
                                 if c['command'].startswith('genmove ') and 'error' not in c)
        if successful:
            source = lines(path.with_suffix('.jsonl'))
            for role, launch in zip(('A', 'B'), raw['launches'], strict=True):
                events[role].extend(e for e in source if e.get('process_id') == launch['pid'])
    completed = [g for group in pairs.values() for g in group]
    paired = [group for group in pairs.values() if len(group) == 2]
    a0 = sum(g['score_a'] for g in completed)
    n = len(completed)

    def compare_pair(group):
        """両棋譜の先後交換後の初回分岐と得点差を記録する。"""
        left, right = [[(m['color'], m['position']) for m in g['moves']] for g in group]
        diff = next((idx for idx, (a, b) in enumerate(zip(left, right)) if a != b), None)
        if diff is None and len(left) != len(right):
            diff = min(len(left), len(right))
        return {'case_id': group[0]['case_id'],
                'a1_minus_a0_points': 2 - 2 * sum(g['score_a'] for g in group),
                'same_moves': diff is None,
                'first_different_turn_after_opening': None if diff is None else diff + 1,
                'moves_at_difference': None if diff is None else [left[diff:diff+1], right[diff:diff+1]]}

    payload = {
        'games': details, 'n_completed_and_checked': n, 'failures': failures,
        'a0': {'points': a0, 'point_rate': a0 / n if n else None},
        'a1': {'points': n - a0, 'point_rate': 1 - a0 / n if n else None},
        'a1_win_draw_loss': [sum(g['score_a'] == s for g in completed) for s in (0, .5, 1)],
        'color_swapped_pairs': [compare_pair(group) for group in paired],
        'external_seconds': {role: time_stats(v) for role, v in by_role.items()},
        'depths': {role: depth_stats(v) for role, v in events.items()},
    }
    save(OUT / 'games-summary.json', payload)


def fixed_opponent_matches():
    """同じ固定相手・手番・seedのA0/A1を対応させて集計する。"""
    directory = OUT / 'fixed-opponent-games'
    plan = read(OUT / 'fixed-opponent-plan.json')
    by_depth = defaultdict(list)
    by_leg = defaultdict(dict)
    for path in sorted(directory.glob('game-??.json')):
        raw = read(path)
        game = raw['game']
        depth = raw['depths'][0]
        assert game['case_id'] == plan['case']['case_id']
        good = game['protocol_status'] == 'verified' and not raw['event_errors']
        candidate_events = [e for e in lines(path.with_suffix('.jsonl'))
                            if raw['launches'] and e.get('process_id') == raw['launches'][0]['pid']]
        row = {
            'file': path.name, 'leg': game['leg'], 'seeds': game['seeds'],
            'colors': game['colors'], 'completed_and_checked': good, 'result': game['result'],
            'score': game['score_a'] if good else None, 'error': game['error'],
            'event_errors': raw['event_errors'],
            'moves': [(m['color'], m['position']) for m in game['moves']],
            'depths': depth_stats(candidate_events),
            'external_seconds': {role: time_stats([
                c['seconds'] for c in player['commands']
                if c['command'].startswith('genmove ') and 'error' not in c
            ]) for role, player in game['players'].items()},
        }
        by_depth[depth].append(row)
        by_leg[game['leg']][depth] = row
    paired = []
    for leg, pair in sorted(by_leg.items()):
        if pair.keys() != {4, 5}:
            continue
        a0, a1 = pair[4], pair[5]
        assert a0['seeds'] == a1['seeds'] and a0['colors'] == a1['colors']
        verified = a0['completed_and_checked'] and a1['completed_and_checked']
        paired.append({
            'leg': leg, 'same_moves': a0['moves'] == a1['moves'] if verified else None,
            'a1_minus_a0_points': a1['score'] - a0['score'] if verified else None,
        })
    save(OUT / 'fixed-opponent-summary.json', {
        'plan_sha256': sha(OUT / 'fixed-opponent-plan.json'),
        'games_by_candidate_depth': by_depth, 'paired_comparisons': paired,
    })


if __name__ == '__main__':
    import sys

    {'measurement': measurement, 'games': matches,
     'fixed-opponent-games': fixed_opponent_matches}[sys.argv[1]]()
