"""独立AIの先後対戦取得・局単位checkpoint・比較CLI。勝敗と品質・統計資格を分離する。"""

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harness import analysis, contracts, openings, referee, search_budget, statistics
from harness.protocol import HarnessError, Player, MEASUREMENT_CONTRACT, clock_descriptor


def _file_info(path):
    """ファイルを読み、絶対パスとbytesのSHA-256を返す。読込失敗は呼出側へ伝える。"""
    resolved = Path(path).resolve()
    return {'path': str(resolved), 'sha256': hashlib.sha256(resolved.read_bytes()).hexdigest()}


def _call(label, function, *arguments):
    """player呼出を実行し、HarnessErrorに候補役割を付けて再送出する。"""
    try:
        return function(*arguments)
    except HarnessError as error:
        error.player = label
        raise


def _failure(label, kind, message, command=None):
    """候補役割・失敗種別・要求を持つHarnessErrorを発生させる。"""
    error = HarnessError(kind, message, command)
    error.player = label
    raise error


def _check_state(players, rows):
    """双方へshowboard/resultを送り、独立referee盤面と照合する。

    AIの盤面は更新しないが通信とcommands記録が増える。不一致はHarnessErrorとする。
    """
    expected_result = referee.result(rows)
    for label, player in players.items():
        actual = tuple(_call(label, player.command, 'showboard'))
        if actual != rows:
            _failure(label, 'board_mismatch', 'Board differs from independent referee', 'showboard')
        actual_result = _call(label, player.command, 'result')[0]
        if actual_result != expected_result:
            _failure(label, 'protocol_error', 'Wrong result: {}'.format(actual_result), 'result')


def _seed_for(seed, case_id, role, repeat_index=0):
    """共通契約のcase・役割・反復別seed導出へ委譲する。"""
    return contracts.seed_for(seed, case_id, role, repeat_index)


def _config_info(config, source_path=None):
    """設定辞書またはJSONをv2へ正規化し、設定・hash・読込元パスを返す。

    省略設定はunknown予算とし、ファイル指定時のみ原設定を読む。
    """
    if isinstance(config, (str, Path)):
        source_path = Path(config).resolve()
        config = search_budget.load_config(source_path)
    elif config is None:
        config = search_budget.normalize_config({'format_version': 1, 'budget': {'kind': 'unknown'}, 'search': {}})
    else:
        config = search_budget.normalize_config(config)
    return {
        'config': config,
        'sha256': hashlib.sha256(search_budget.canonical_json(config).encode('utf-8')).hexdigest(),
        'path': str(source_path.resolve()) if source_path else None,
    }


def _runtime_info():
    """比較用にPython・OS・CPUと関連環境変数を記録する。実行環境自体は変更しない。"""
    thread_variables = (
        'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
        'NUMEXPR_NUM_THREADS', 'CUDA_VISIBLE_DEVICES',
    )
    return {
        'python': sys.version,
        'executable': sys.executable,
        'platform': platform.platform(),
        'implementation': platform.python_implementation(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'cpu_count': os.cpu_count(),
        'evaluation_concurrency': 1,
        'thread_environment': {key: os.environ.get(key) for key in thread_variables},
        'clock': clock_descriptor(),
    }


def _harness_info():
    """取得fingerprintに含める固定モジュール群のパスとソースhashを収集する。

    seed adapterを含めるが、対象AIの全importを網羅するhashではない。
    """
    directory = Path(__file__).parent
    return [_file_info(directory / name) for name in (
        'evaluate.py', 'protocol.py', 'referee.py', 'openings.py',
        'search_budget.py', 'contracts.py', 'seeded_player.py',
    )]


def _hash_payload(value):
    """共通canonical化規約で評価条件等のJSON hashを返す。"""
    return hashlib.sha256(openings.canonical_json(value).encode('utf-8')).hexdigest()


EvidenceWriteError = contracts.ArtifactWriteError


def _write_progress(evidence_run, report, games, planned_games, state,
                    in_progress=None, interruptions=()):
    """取得中の報告snapshotと局単位progressをrun-state.jsonへ保存する。

    一時ファイルをflush/fsyncして置換し、I/O失敗はEvidenceWriteErrorとする。
    started_legsはplay_game呼出単位であり実Popen件数ではなく、局中通信の逐次永続化もしない。
    """
    progress_path = evidence_run / 'run-state.json'
    temporary_path = evidence_run / 'run-state.json.tmp'
    try:
        snapshot = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, OverflowError) as error:
        raise EvidenceWriteError('checkpoint_serialize', progress_path, report.get('run_id'), error) from error
    snapshot.update({
        'report_state': state,
        'games': games,
        'summary': summarize(games),
        'progress': {
            'planned_games': planned_games,
            'started_legs': len(games) + len(interruptions) + int(in_progress is not None),
            'completed_games': sum(
                game.get('game_status') == 'complete'
                and game.get('protocol_status') == 'verified' for game in games
            ),
            'not_started_legs': planned_games - len(games) - len(interruptions)
            - int(in_progress is not None),
            'in_progress': in_progress,
            'interrupted_legs': list(interruptions),
        },
    })
    try:
        with temporary_path.open('w', encoding='utf-8', newline='\n') as writer:
            json.dump(snapshot, writer, ensure_ascii=False, indent=2, allow_nan=False)
            writer.write('\n')
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary_path, progress_path)
    except (OSError, ValueError, TypeError, OverflowError) as error:
        raise EvidenceWriteError('checkpoint', progress_path, report.get('run_id'), error) from error


def _experiment_id(conditions, runtime, target_infos, evaluation_fingerprint):
    """取得条件・runtime・本体/明示資産hash・取得版から実験IDを返す。

    解析confidenceや保存先とは分離するが、条件内の観測済み決定性flagも含む。
    """
    return _hash_payload({
        'conditions': conditions,
        'runtime': runtime,
        'targets': {label: {
            'sha256': info['sha256'],
            'assets': [asset['sha256'] for asset in info['assets']],
        } for label, info in target_infos.items()},
        'evaluation_fingerprint': evaluation_fingerprint,
    })


def _verify_determinism(script, label, case, seed, config_path, config_hash,
                        target_hash, run_id, evidence_dir, startup_timeout,
                        command_timeout, genmove_timeout, clock_domain_id=None):
    """同一case・seed・設定の単発genmoveを別プロセスで2回試し、出力とtraceを保存する。

    先頭ケースだけの確認で全ケースの決定性証明ではない。trace保存が必要で、
    起動/通信/読込の失敗はfailedの証拠辞書として返す。
    """
    outputs = []
    trace_hashes = []
    trace_artifacts = []
    attempts = []
    for attempt in range(2):
        trace_dir = evidence_dir / 'determinism' / label
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / '{}.jsonl'.format(attempt)
        context = {
            'purpose': 'probe', 'case_id': case['case_id'], 'role_seed': seed,
            'clock_domain_id': clock_domain_id,
            'run_id': run_id, 'pair_id': 'determinism-{}'.format(label),
            'leg': attempt, 'player': label,
            'requested_config_sha256': config_hash,
            'target_sha256': target_hash,
        }
        try:
            with Player(
                script, seed=seed, startup_timeout=startup_timeout,
                command_timeout=command_timeout, genmove_timeout=genmove_timeout,
                evaluation_config=config_path, evaluation_trace=trace_path,
                evaluation_context=context,
            ) as player:
                player.command('showboard')
                for move in case['moves']:
                    if move['position'] is not None:
                        response = player.command('play {} {} {}'.format(
                            move['color'], *move['position'],
                        ))
                        if response != ['OK']:
                            raise ValueError('replay opening move rejected')
                    player.command('showboard')
                player.command('showboard')
                outputs.append(player.command('genmove ' + case['to_move'])[0])
                legal = referee.legal_moves(case['board'], case['to_move'])
                player.commands[-1]['phase'] = 'normal_move' if legal else 'pass_probe'
                player.command('showboard')
            attempts.append(player.evidence())
            trace_hash = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            trace_hashes.append(trace_hash)
            trace_artifacts.append({
                'path': trace_path.relative_to(evidence_dir).as_posix(),
                'sha256': trace_hash,
            })
        except Exception as error:
            if isinstance(error, HarnessError) and error.diagnostics:
                attempts.append(error.diagnostics)
            return {
                'status': 'failed', 'reason': str(error), 'target_sha256': target_hash,
                'config_sha256': config_hash, 'repetitions': attempt + 1,
                'outputs': outputs, 'trace_sha256': trace_hashes,
                'trace_artifacts': trace_artifacts,
                'attempts': attempts,
            }
    return {
        'status': 'verified' if outputs[0] == outputs[1] else 'failed',
        'reason': None if outputs[0] == outputs[1] else 'repeated_output_mismatch',
        'target_sha256': target_hash, 'config_sha256': config_hash,
        'case_id': case['case_id'], 'seed': seed, 'repetitions': 2,
        'outputs': outputs, 'trace_sha256': trace_hashes,
        'trace_artifacts': trace_artifacts,
        'attempts': attempts,
    }


def play_game(script_a, script_b, index, seed, startup_timeout=5.0,
              command_timeout=1.0, genmove_timeout=10.0, case=None, pair_id=None,
              leg=0, seeds=None, repeat_index=0, config_paths=None, trace_paths=None, configs=None,
              target_hashes=None, run_id=None, budget_policy='descriptive', clock_domain_id=None):
    """A/Bを独立プロセスで起動し、序盤再生から終局確認までの1legを取得する。

    genmoveは担当AI自身を更新するため、相手だけへplayを送り双方盤面を毎手照合する。
    通常評価ではevaluateからcase/role seed・設定/traceパス・元hashを明示して呼ぶ。
    対局・通信・予算を別状態で返し、失敗を勝敗へ変換しない。プロセスとログ/traceに
    副作用があり、終了時に起動済みplayerを回収する。全終了経路の証跡完全性は保証しない。
    """
    colors = {'B': 'A', 'W': 'B'} if index % 2 == 0 else {'B': 'B', 'W': 'A'}
    case = case or {
        'case_id': openings.case_id([], referee.INITIAL_ROWS, 'B'),
        'moves': [], 'board': list(referee.INITIAL_ROWS), 'to_move': 'B',
    }
    seeds = seeds or {'A': seed + (index // 2) * 2, 'B': seed + (index // 2) * 2 + 1}
    config_paths = config_paths or {}
    trace_paths = trace_paths or {}
    configs = configs or {}
    target_hashes = target_hashes or {}
    players = {}
    rows = referee.INITIAL_ROWS
    game = {
        'index': index, 'pair_id': pair_id, 'case_id': case['case_id'], 'leg': leg,
        'repeat_index': repeat_index,
        'colors': colors, 'seeds': seeds, 'opening_moves': case['moves'],
        'opening_start_board': case['board'], 'opening_to_move': case['to_move'],
        'moves': [], 'result': None, 'winner': None, 'score_a': None, 'error': None,
        'game_status': 'incomplete', 'protocol_status': 'not_checked',
        'budget_status': 'unknown',
        'players': {label: {'launch_status': 'not_started', 'commands': [], 'exit_code': None,
                            'config': configs.get(label),
                            'budget_trace': {'status': 'unknown', 'reason': 'player_not_started',
                                             'events': 0, 'measurements': []}}
                    for label in ('A', 'B')},
    }
    try:
        for label, script in [('A', script_a), ('B', script_b)]:
            players[label] = _call(
                label, lambda script=script, label=label: Player(
                    script, seed=seeds[label], startup_timeout=startup_timeout,
                    command_timeout=command_timeout, genmove_timeout=genmove_timeout,
                    evaluation_config=config_paths.get(label),
                    evaluation_trace=trace_paths.get(label),
                    evaluation_context={
                        'purpose': 'game', 'case_id': case['case_id'], 'role_seed': seeds[label],
                        'clock_domain_id': clock_domain_id,
                        'run_id': run_id, 'pair_id': pair_id, 'leg': leg, 'player': label,
                        'requested_config_sha256': hashlib.sha256(
                            search_budget.canonical_json(configs[label]).encode('utf-8')
                        ).hexdigest(),
                        'target_sha256': target_hashes[label],
                    },
                ),
            )
        _check_state(players, rows)
        for opening_move in case['moves']:
            color = opening_move['color']
            position = opening_move['position']
            if position is None:
                if referee.legal_moves(rows, color):
                    _failure(None, 'protocol_error', 'Opening pass is no longer legal')
                other = 'W' if color == 'B' else 'B'
                if not referee.legal_moves(rows, other):
                    _failure(None, 'protocol_error', 'Opening ends in a terminal position')
            else:
                rows = referee.apply_move(rows, color, tuple(position))
                for label, player in players.items():
                    response = _call(label, player.command, 'play {} {} {}'.format(color, *position))[0]
                    if response != 'OK':
                        _failure(label, 'protocol_error', 'Rejected a forced opening move', 'play')
            _check_state(players, rows)
        if tuple(rows) != tuple(case['board']):
            _failure(None, 'board_mismatch', 'Replayed opening differs from its recorded board')
        color = case['to_move']
        for _ in range(128):
            if referee.result(rows) != 'NG':
                break
            label = colors[color]
            other = 'B' if label == 'A' else 'A'
            possible = referee.legal_moves(rows, color)
            response = _call(label, players[label].command, 'genmove ' + color)[0]
            seconds = players[label].commands[-1]['seconds']
            phase = 'normal_move' if possible else (
                'terminal_probe' if referee.result(rows) != 'NG' else 'pass_probe'
            )
            players[label].commands[-1]['phase'] = phase
            if response == 'NG':
                if possible:
                    _failure(label, 'illegal_move', 'NG despite legal moves', 'genmove ' + color)
                position = None
            else:
                position = tuple(map(int, response.split()))
                if position not in possible:
                    _failure(label, 'illegal_move', 'Illegal coordinate: ' + response, 'genmove ' + color)
                rows = referee.apply_move(rows, color, position)
                # genmoveは担当AI自身も更新するため、担当側へ同じplayを二重適用しない。
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
        game['result'] = final_result
        game['winner'] = None if final_result == 'D' else colors[final_result]
        game['score_a'] = 0.5 if final_result == 'D' else float(game['winner'] == 'A')
        # 合法終局と、後続terminal probe・quitの通信成功は別状態で記録する。
        game['game_status'] = 'complete'
        for final_color, label in colors.items():
            terminal_response = _call(label, players[label].command, 'genmove ' + final_color)
            players[label].commands[-1]['phase'] = 'terminal_probe'
            if terminal_response != ['NG']:
                _failure(label, 'illegal_move', 'Move after terminal position', 'genmove ' + final_color)
        _check_state(players, rows)
        for label, player in players.items():
            _call(label, player.finish)
        game['protocol_status'] = 'verified'
    except HarnessError as error:
        if error.player in game['players'] and error.player not in players:
            game['players'][error.player]['launch_status'] = 'failed'
            if error.diagnostics:
                game['players'][error.player].update(error.diagnostics)
        game['protocol_status'] = 'failed'
        if error.kind in ('illegal_move', 'board_mismatch'):
            game['game_status'] = 'invalid'
        game['error'] = {
            'player': error.player, 'kind': error.kind,
            'command': error.command, 'message': str(error),
            'diagnostics': error.diagnostics,
        }
    except Exception as error:
        game['protocol_status'] = 'failed'
        game['error'] = {
            'player': None, 'kind': 'infrastructure_error', 'command': None,
            'message': str(error), 'diagnostics': None,
        }
    finally:
        for player in players.values():
            player.abort()
    game['final_board'] = rows
    game['players'].update({
        label: {
            **player.evidence(),
            'stderr_tail': list(player.stderr_tail),
            'config': configs.get(label),
            'launch_status': 'started',
        }
        for label, player in players.items()
    })
    budget_results = {label: item.get('budget_trace', {
        'status': 'unknown', 'reason': 'player_not_started', 'events': 0, 'measurements': [],
    }) for label, item in game['players'].items()}
    for label, player in players.items():
        try:
            budget_results[label] = search_budget.validate_trace(
                trace_paths[label], configs[label], player.commands, target_hashes[label],
                {'run_id': run_id, 'pair_id': pair_id, 'leg': leg, 'player': label},
                allow_exemption=(label == 'B' and budget_policy == 'baseline-asymmetric'
                                 and configs[label]['budget']['kind'] == 'none'),
            )
        except Exception as error:
            budget_results[label] = {
                'status': 'failed', 'reason': 'trace_validation_error:{}'.format(error),
                'events': 0, 'measurements': [],
            }
        game['players'][label]['budget_trace'] = budget_results[label]
    game['budget'] = budget_results
    game['budget_status'] = contracts.aggregate_role_budget(
        {label: item['status'] for label, item in budget_results.items()},
        {'budget_policy': budget_policy},
    )
    return game


def summarize(games):
    """共通reducerで取得中の参考集計を返す。正式集計はfresh原証跡から別途生成する。"""
    return contracts.reduce_observations(games)


def _evaluate(script_a, script_b, games=None, seed=20261009, startup_timeout=5.0,
             command_timeout=1.0, genmove_timeout=10.0, assets_a=(), assets_b=(),
             openings_file=None, selection_seed=None, config_a=None, config_b=None,
             budget_policy='descriptive', evaluation_profile='descriptive',
             require_budget_verification=False, confidence=0.95, evidence_dir=None, operation=None):
    """先後ペアの対戦を取得・保存・再検証し、評価JSON用の辞書を返す。

    母集合JSON、設定、本体/明示資産を読み、run専用領域へ原母集合・trace・checkpointを保存。
    configは一時ファイル、最終JSONの書込みはCLI側が行う。正式評価は予定局数・抽出seed・
    固定work/決定性宣言を要求し、CIは共通検証入口の結果から算出する。
    入力条件不正は起動前に拒否し、中断時は取得済みlegをcheckpointへ残して再送出する。
    参考勝敗、品質、区間、数値基準を分離し、最終採用判断はundeterminedのまま。
    """
    games_explicit = games is not None
    if games is not None and (type(games) is not int or games < 2 or games % 2):
        raise ValueError('games must be a positive even number (at least 2)')
    if any(type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0
           for limit in (startup_timeout, command_timeout, genmove_timeout)):
        raise ValueError('timeouts must be positive')
    profiles = {'smoke', 'descriptive', 'confirmatory', 'deployment'}
    policies = {'descriptive', 'equal-time', 'equal-work', 'baseline-asymmetric'}
    if evaluation_profile not in profiles:
        raise ValueError('unknown evaluation profile')
    if evaluation_profile == 'confirmatory' and not games_explicit:
        raise ValueError('confirmatory profile requires explicit games')
    if budget_policy not in policies:
        raise ValueError('unknown budget policy')
    if not 0 < confidence < 1:
        raise ValueError('confidence must be between zero and one')
    config_infos = {
        'A': _config_info(config_a),
        'B': _config_info(config_b),
    }
    configs = {label: info['config'] for label, info in config_infos.items()}
    compatible, incompatibility = search_budget.compatible_budgets(
        configs['A'], configs['B'], budget_policy,
    )
    if not compatible:
        raise ValueError(incompatibility)
    if evaluation_profile == 'deployment' and budget_policy != 'equal-time':
        raise ValueError('deployment profile requires equal-time budget policy')
    explicit_selection_seed = selection_seed is not None
    population = openings.load_population(openings_file) if openings_file is not None else None
    game_count = contracts.resolve_game_count(
        games, evaluation_profile, population['population_size'] if population else 0,
    )
    games_explicit = game_count['games_explicit']
    games = game_count['resolved_games']
    if population is None:
        selected_entries = None
        selection_manifest = None
        population_size = 0
    else:
        selection_seed = seed if selection_seed is None else selection_seed
        selected_entries, selection_manifest = openings.select_entries(
            population, games // 2, selection_seed,
        )
        population_size = population['population_size']
    if evaluation_profile == 'confirmatory':
        if population is None or not explicit_selection_seed or not games_explicit:
            raise ValueError('confirmatory profile requires explicit games, a fixed opening population and selection seed')
        if budget_policy not in ('equal-work', 'baseline-asymmetric'):
            raise ValueError('confirmatory profile requires equal-work or baseline-asymmetric policy')
        if budget_policy == 'baseline-asymmetric' and configs['B']['budget']['kind'] != 'none':
            raise ValueError('baseline-asymmetric confirmatory evaluation requires a none-budget baseline')
        deterministic = {
            label: info['config']['search'].get('deterministic') is True
            for label, info in config_infos.items()
        }
        if not all(deterministic.values()):
            raise ValueError('confirmatory profile requires deterministic=true in both configurations')
        if configs['A']['budget']['kind'] not in ('nodes', 'depth'):
            raise ValueError('confirmatory profile requires a deterministic candidate work limit')
    if evaluation_profile == 'deployment' and any(
            config['budget']['kind'] != 'time' for config in configs.values()):
        raise ValueError('deployment profile requires time budgets for both players')
    target_infos = {
        'A': {**_file_info(script_a), 'assets': [_file_info(path) for path in assets_a]},
        'B': {**_file_info(script_b), 'assets': [_file_info(path) for path in assets_b]},
    }
    if budget_policy == 'baseline-asymmetric':
        exemption, _ = search_budget.exemption_status(configs['B'], target_infos['B']['sha256'])
        if exemption != 'exempt':
            raise ValueError('baseline exemption target hash must match the baseline before launch')
        if configs['A']['budget']['kind'] == 'nodes' and not configs['A']['search'].get('counting_definition_id'):
            raise ValueError('baseline-asymmetric node budget requires counting_definition_id')
    harness_info = _harness_info()
    runtime = _runtime_info()
    conditions = {
        'games': games,
        'planned_games': games,
        'seed': seed,
        'startup_timeout': startup_timeout,
        'command_timeout': command_timeout,
        'genmove_timeout': genmove_timeout,
        'seed_scope': 'sha256(case_id, role, master_seed); Python random and PYTHONHASHSEED',
        'evaluation_profile': evaluation_profile,
        'budget_policy': budget_policy,
        'determinism_verified': False,
        'budget_configs': {label: info['config'] for label, info in config_infos.items()},
        'budget_config_hashes': {label: info['sha256'] for label, info in config_infos.items()},
        'opening_population_hash': population['population_hash'] if population else None,
        'opening_population_size': population_size,
        'selection_manifest': selection_manifest,
        'timeout_policy': 'startup=separate; command=1s; genmove=10s; measured with perf_counter',
        'measurement_contract': MEASUREMENT_CONTRACT,
    }
    # ソースbytesのhashなので、コメントだけの編集でも取得fingerprintは変わる。
    evaluation_fingerprint = _hash_payload([item['sha256'] for item in harness_info])
    statistics_path = Path(statistics.__file__).resolve()
    analysis_fingerprint = _hash_payload({
        'analysis_sha256': hashlib.sha256(Path(analysis.__file__).read_bytes()).hexdigest(),
        'statistics_sha256': hashlib.sha256(statistics_path.read_bytes()).hexdigest(),
        'analysis_spec': {'method': 'hoeffding_serfling_finite_population', 'confidence': confidence},
    })
    experiment_id = _experiment_id(conditions, runtime, target_infos, evaluation_fingerprint)
    report = {
        'schema_version': 5,
        'report_kind': 'evaluation',
        'run_id': uuid.uuid4().hex,
        'clock_domain_id': uuid.uuid4().hex,
        'experiment_id': experiment_id,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'runtime': runtime,
        'conditions': conditions,
        'verification_requirements': {
            'require_budget_verification': bool(require_budget_verification),
        },
        'request': {
            **game_count,
        },
        'targets': target_infos,
        'harness': harness_info,
        'evaluation_fingerprint': evaluation_fingerprint,
        'analysis_fingerprint': analysis_fingerprint,
        'population': {
            'hash': population['population_hash'] if population else None,
            'size': population_size,
            'purpose': population['purpose'] if population else 'initial_position_only',
            'placements': population['placements'] if population else 0,
            'generator': population['generator'] if population else None,
            'selected_case_ids': selection_manifest['selected_case_ids'] if selection_manifest else [],
            'initial_board': list(referee.INITIAL_ROWS),
        },
    }
    evidence_root = (Path(evidence_dir) if evidence_dir else Path('evaluation_results') / 'runs').resolve()
    run_id = report['run_id']
    operation.update(started=True, phase='prepare', operation_id=run_id, path=str(evidence_root))
    evidence_run = evidence_root / run_id
    evidence_run.mkdir(parents=True, exist_ok=False)
    report['evidence'] = {'root': str(evidence_run), 'format_version': 1}
    if population is not None:
        population_path = evidence_run / 'opening-population.json'
        with population_path.open('x', encoding='utf-8', newline='\n') as writer:
            json.dump(population, writer, ensure_ascii=False, indent=2, allow_nan=False)
            writer.write('\n')
        report['evidence']['population'] = {
            'path': population_path.name,
            'sha256': hashlib.sha256(population_path.read_bytes()).hexdigest(),
        }
    entries = selected_entries if selected_entries is not None else [None] * (games // 2)
    all_games = []
    interruptions = []
    _write_progress(evidence_run, report, all_games, games, 'not_started', interruptions=interruptions)
    with tempfile.TemporaryDirectory(prefix='reversi-evaluation-config-') as temporary_directory:
        temporary_directory = Path(temporary_directory)
        config_paths = {}
        for label in ('A', 'B'):
            config_paths[label] = temporary_directory / '{}-config.json'.format(label.lower())
            operation['path'] = str(config_paths[label])
            search_budget.write_config(configs[label], config_paths[label])
        operation.update(phase='collection', path=str(evidence_run))
        determinism_results = {}
        if evaluation_profile == 'confirmatory':
            probe_case = entries[0]
            for label, script in (('A', script_a), ('B', script_b)):
                probe_seed = _seed_for(seed, probe_case['case_id'], label)
                determinism_results[label] = _verify_determinism(
                    script, label, probe_case, probe_seed, config_paths[label],
                    config_infos[label]['sha256'], target_infos[label]['sha256'],
                    run_id, evidence_run, startup_timeout, command_timeout, genmove_timeout,
                    clock_domain_id=report['clock_domain_id'],
                )
            conditions['determinism_verified'] = all(
                result.get('status') == 'verified' for result in determinism_results.values()
            )
            report['experiment_id'] = _experiment_id(
                conditions, runtime, target_infos, evaluation_fingerprint,
            )
            report['conditions'] = conditions
        report['determinism'] = determinism_results
        for pair_index, entry in enumerate(entries):
            case = entry or {
                'case_id': openings.case_id([], referee.INITIAL_ROWS, 'B'),
                'moves': [], 'board': list(referee.INITIAL_ROWS), 'to_move': 'B',
            }
            seeds = {
                label: _seed_for(seed, case['case_id'], label, 0 if entry else pair_index)
                for label in ('A', 'B')
            }
            pair_id = _hash_payload({
                'pair_version': 1,
                'case_id': case['case_id'],
                'seeds': seeds,
                'role_swap_version': 1,
            })
            for leg in (0, 1):
                leg_dir = evidence_run / pair_id / 'leg-{}'.format(leg)
                leg_dir.mkdir(parents=True, exist_ok=False)
                trace_paths = {
                    label: leg_dir / '{}.jsonl'.format(label)
                    for label in ('A', 'B')
                }
                index = pair_index * 2 + leg
                active_leg = {'index': index, 'pair_id': pair_id, 'leg': leg}
                _write_progress(
                    evidence_run, report, all_games, games, 'running',
                    in_progress=active_leg, interruptions=interruptions,
                )
                try:
                    game_record = play_game(
                        script_a, script_b, index, seed, startup_timeout, command_timeout,
                        genmove_timeout, case=case, pair_id=pair_id, leg=leg, seeds=seeds,
                        repeat_index=pair_index if entry is None else 0,
                        config_paths=config_paths, trace_paths=trace_paths, configs=configs,
                        target_hashes={label: target_infos[label]['sha256'] for label in ('A', 'B')},
                        run_id=run_id, budget_policy=budget_policy, clock_domain_id=report['clock_domain_id'],
                    )
                except BaseException as error:
                    interruptions.append({**active_leg, 'error': '{}: {}'.format(type(error).__name__, error)})
                    try:
                        _write_progress(
                            evidence_run, report, all_games, games, 'partial',
                            interruptions=interruptions,
                        )
                    except EvidenceWriteError as secondary:
                        error.add_note('secondary_checkpoint_failure: ' + str(secondary))
                        print('secondary_checkpoint_failure: ' + str(secondary), file=sys.stderr)
                    raise
                all_games.append(game_record)
                for label in ('A', 'B'):
                    trace = trace_paths[label]
                    game_record = all_games[-1]
                    player_record = game_record['players'].get(label, {})
                    trace_record = player_record.get('budget_trace', {})
                    try:
                        relative_trace = trace.relative_to(evidence_run).as_posix()
                        trace_record['artifact'] = {
                            'path': relative_trace,
                            'sha256': hashlib.sha256(trace.read_bytes()).hexdigest() if trace.exists() else None,
                            'exists': trace.exists(), 'format_version': search_budget.TRACE_VERSION,
                        }
                    except OSError as error:
                        trace_record['artifact'] = {'path': trace.name, 'exists': False, 'error': str(error)}
                _write_progress(
                    evidence_run, report, all_games, games, 'partial', interruptions=interruptions,
                )
    report['games'] = all_games
    operation['phase'] = 'analysis'
    # 取得記録上の予備資格。正式統計は原証跡を含めて後から再検証する。
    _, pair_failures = statistics.pair_scores(all_games)
    for game in all_games:
        game['pair_status'] = 'qualified' if game['pair_id'] not in pair_failures else 'unqualified'
        game['pair_reason'] = pair_failures.get(game['pair_id'])
    protocol_ok = all(game['protocol_status'] == 'verified' for game in all_games)
    budget_states = [game['budget_status'] for game in all_games]
    budget_status = contracts.aggregate_game_budget(budget_states, games)
    report['status'] = {
        'execution': 'complete' if all(game['game_status'] == 'complete' for game in all_games) else 'failed',
        'protocol': 'verified' if protocol_ok else 'failed',
        'budget': budget_status,
    }
    report['summary'] = contracts._acquisition_summary(report)
    report['analysis_spec'] = {
        'method': 'hoeffding_serfling_finite_population', 'confidence': confidence,
        'estimand': 'mean_A_pair_score_on_fixed_opening_population',
        'primary_claim': 'candidate_mean_score_above_half',
    }
    report['analysis_id'] = _hash_payload({
        'experiment_id': report['experiment_id'], 'analysis_fingerprint': analysis_fingerprint,
        'analysis_spec': report['analysis_spec'],
    })
    report['statistics'] = analysis.estimate(report, confidence)
    report['status']['statistics'] = report['statistics']['status']
    interval = report['statistics'].get('interval')
    if evaluation_profile != 'confirmatory':
        report['status']['performance_criterion'] = 'not_requested'
    elif interval is None:
        report['status']['performance_criterion'] = 'undetermined'
    else:
        report['status']['performance_criterion'] = (
            'met' if report['statistics']['point_rate'] >= 0.55 and interval['lower'] > 0.5
            else 'not_met'
        )
    # CIや数値基準が得られても、運用証拠を含む最終採用を自動決定しない。
    report['status']['decision'] = 'undetermined'
    _write_progress(
        evidence_run, report, all_games, games, 'complete', interruptions=interruptions,
    )
    return report


def evaluate(script_a, script_b, games=None, seed=20261009, startup_timeout=5.0,
             command_timeout=1.0, genmove_timeout=10.0, assets_a=(), assets_b=(),
             openings_file=None, selection_seed=None, config_a=None, config_b=None,
             budget_policy='descriptive', evaluation_profile='descriptive',
             require_budget_verification=False, confidence=0.95, evidence_dir=None):
    """取得API。preflight不正は元例外、開始後のI/O/保存障害は運用例外で区別する。"""
    operation = {'started': False, 'phase': 'preflight', 'operation_id': None, 'path': evidence_dir}
    try:
        return _evaluate(
            script_a, script_b, games, seed, startup_timeout, command_timeout, genmove_timeout,
            assets_a, assets_b, openings_file, selection_seed, config_a, config_b, budget_policy,
            evaluation_profile, require_budget_verification, confidence, evidence_dir, operation,
        )
    except contracts.ArtifactWriteError:
        raise
    except (OSError, ValueError, TypeError, OverflowError) as error:
        if operation['started']:
            raise contracts.ArtifactWriteError(operation['phase'], getattr(error, 'filename', None) or operation['path'],
                                               operation['operation_id'], error) from error
        raise




def _comparison_conditions(report):
    """共通契約へ委譲し、候補実装差を除いた取得比較条件を返す。"""
    return contracts.comparison_conditions(report)


def compare_reports(before, after, confidence=0.95, require_budget_verification=False, evidence_roots=None):
    """共通解析入口へ委譲する比較facade。保存flagによる独自の資格判定をしない。"""
    return analysis.compare(before, after, confidence, require_budget_verification, evidence_roots)


def main():
    """対戦取得または保存報告比較を実行し、新規JSONと標準出力へ結果を出すCLI。

    成功条件を満たせば0、要求不成立や開始後の保存失敗なら1、入力不正なら2。
    数値基準未達と実行失敗は同一扱いにしない。既存出力は上書きしない。
    """
    parser = argparse.ArgumentParser(description='Independent-process Reversi evaluation and JSON comparison')
    parser.add_argument('--a', type=Path)
    parser.add_argument('--b', type=Path)
    parser.add_argument('--games', type=int)
    parser.add_argument('--seed', type=int, default=20261009)
    parser.add_argument('--startup-timeout', type=float, default=5.0)
    parser.add_argument('--command-timeout', type=float, default=1.0)
    parser.add_argument('--genmove-timeout', type=float, default=10.0)
    parser.add_argument('--a-asset', type=Path, action='append', default=[])
    parser.add_argument('--b-asset', type=Path, action='append', default=[])
    parser.add_argument('--a-config', type=Path)
    parser.add_argument('--b-config', type=Path)
    parser.add_argument('--openings', type=Path)
    parser.add_argument('--selection-seed', type=int)
    parser.add_argument('--budget-policy', choices=('descriptive', 'equal-time', 'equal-work', 'baseline-asymmetric'), default='descriptive')
    parser.add_argument('--evaluation-profile', choices=('smoke', 'descriptive', 'confirmatory', 'deployment'), default='descriptive')
    parser.add_argument('--require-budget-verification', action='store_true')
    parser.add_argument('--confidence', type=float, default=0.95)
    parser.add_argument('--ci-method', choices=('finite-population-bound',), default='finite-population-bound')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--compare', nargs=2, type=Path, metavar=('BEFORE', 'AFTER'))
    arguments = parser.parse_args()
    operation_started = False
    try:
        if arguments.output and arguments.output.exists():
            parser.error('output already exists: {}'.format(arguments.output))
        if arguments.compare:
            before, after = [json.loads(path.read_text(encoding='utf-8')) for path in arguments.compare]
            for source in (before, after):
                decoded = contracts.decode_input(source)
                if decoded['input_status'] != 'valid':
                    parser.error(','.join(decoded['issues']))
            if not 0 < arguments.confidence < 1:
                parser.error('confidence_invalid')
            operation_started = True
            report = compare_reports(
                before, after, arguments.confidence, arguments.require_budget_verification,
            )
            success = report['comparable']
        else:
            if not arguments.a or not arguments.b or not arguments.output:
                parser.error('evaluation requires --a, --b and --output')
            if arguments.output.exists():
                parser.error('output already exists: {}'.format(arguments.output))
            evidence_dir = arguments.output.parent / 'runs'
            report = evaluate(
                arguments.a, arguments.b, arguments.games, arguments.seed,
                arguments.startup_timeout, arguments.command_timeout, arguments.genmove_timeout,
                assets_a=arguments.a_asset, assets_b=arguments.b_asset,
                openings_file=arguments.openings, selection_seed=arguments.selection_seed,
                config_a=arguments.a_config, config_b=arguments.b_config,
                budget_policy=arguments.budget_policy,
                evaluation_profile=arguments.evaluation_profile,
                require_budget_verification=arguments.require_budget_verification,
                confidence=arguments.confidence,
                evidence_dir=evidence_dir,
            )
            operation_started = True
            success = analysis.assess_evaluation(
                report, arguments.confidence,
                arguments.require_budget_verification,
            )['request_success']
        if arguments.output:
            contracts.write_report_exclusive(arguments.output, report, phase='final',
                                             operation_id=report.get('run_id', report.get('analysis_id')))
        print(json.dumps(report if arguments.compare else report['summary'], indent=2, ensure_ascii=False))
        return 0 if success else 1
    except EvidenceWriteError as error:
        parser.exit(1, '{}\n'.format(error))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1 if operation_started else 2, '{}\n'.format(error))


if __name__ == '__main__':
    raise SystemExit(main())
