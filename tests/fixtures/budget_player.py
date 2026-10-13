"""予算・ライフサイクルtraceの試験専用AI。実探索の強さや計数の証明には使わない。"""

import time

CHILD_ENTRY_NS = time.perf_counter_ns()

import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness import referee
from harness.search_budget import board_sha256, canonical_json
from harness.protocol import clock_descriptor, command_id


def main():
    """rows[y][x]のB/W/E盤面を更新し、stdout応答と別JSONLの実測証拠を生成する。"""
    config = json.loads(Path(os.environ['REVERSI_EVAL_CONFIG']).read_text(encoding='utf-8'))
    config['search'] = config.get('search', {})
    context = json.loads(os.environ['REVERSI_EVAL_TRACE_CONTEXT'])
    rows = referee.INITIAL_ROWS
    model_id = config['search'].get('model_id')
    load_start = load_end = None
    if model_id is not None:
        load_start = time.perf_counter_ns()
        Path(config['search']['model_path']).read_bytes()
        load_end = time.perf_counter_ns()
    trace = open(os.environ['REVERSI_EVAL_TRACE'], 'a', encoding='utf-8', newline='\n')

    def emit(event):
        """探索区間外で証拠を追記・flushする。通信期限にはこのコストも含まれる。"""
        trace.write(canonical_json(event) + '\n')
        trace.flush()

    ready = time.perf_counter_ns()
    emit({
        'type': 'config', 'format_version': 4, **context,
        'effective_config': config, 'clock': 'perf_counter_ns',
        'measurement_scope': 'search_only', 'model_id': model_id,
        'model_load_elapsed_ns': load_end - load_start if model_id is not None else 0, 'fallback': False,
        'clock_descriptor': clock_descriptor(), 'model_load_status': 'startup' if model_id is not None else 'not_required',
        'model_load_start_ns': load_start, 'model_load_end_ns': load_end,
        'child_entry_ns': CHILD_ENTRY_NS, 'protocol_ready_ns': ready,
        'pid': os.getpid(), 'measurement_thread_id': threading.get_ident(),
    })
    sequence = 0
    for command_index, line in enumerate(sys.stdin):
        received = time.perf_counter_ns()
        command = line.strip().split()
        event = {
            'type': 'command', 'command': line.strip(), 'command_received_ns': received,
            'command_index': command_index, 'command_id': command_id(context, command_index),
            'process_session_id': context['process_session_id'],
            'process_generation': context['process_generation'],
            'measurement_thread_id': threading.get_ident(),
        }
        if command == ['quit']:
            emit(event)
            emit({'type': 'process_exit_ready', 'command_id': event['command_id'],
                  'process_session_id': context['process_session_id'],
                  'process_generation': context['process_generation'],
                  'measurement_thread_id': threading.get_ident(),
                  'exit_ready_ns': time.perf_counter_ns()})
            trace.close()
            return
        if command == ['name']:
            response = 'budget-fixture'
        elif command == ['showboard']:
            response = '\n'.join(rows)
        elif command == ['result']:
            response = referee.result(rows)
        elif command[0] in ('ask', 'play'):
            color = command[1]
            position = (int(command[2]), int(command[3]))
            legal = position in referee.legal_moves(rows, color)
            if command[0] == 'play' and legal:
                rows = referee.apply_move(rows, color, position)
            response = 'OK' if legal else 'NG'
        elif command[0] == 'genmove':
            color = command[1]
            before = rows
            started = time.perf_counter_ns()
            legal = referee.legal_moves(rows, color)
            phase = 'normal_move' if legal else (
                'terminal_probe' if referee.result(rows) != 'NG' else 'pass_probe')
            if legal:
                position = legal[0]
                rows = referee.apply_move(rows, color, position)
                response = '{} {}'.format(*position)
            else:
                response = 'NG'
            ended = time.perf_counter_ns()
            budget = config['budget']
            if budget['kind'] != 'none' or context['purpose'] == 'probe':
                event.update({
                    'type': 'genmove', 'sequence': sequence, 'phase': phase, 'color': color,
                    'board_sha256': board_sha256(before), 'response': response,
                    'model_id': model_id, 'model_load_elapsed_ns': 0, 'fallback': False,
                    'search_elapsed_ns': ended - started,
                    'search_start_ns': started, 'search_end_ns': ended,
                    'model_load_status': 'not_required', 'model_load_start_ns': None,
                    'model_load_end_ns': None,
                    # 模擬counterであり、実AIの探索量を表すものではない。
                    'nodes': min(1, budget.get('limit_nodes', 1)),
                    'max_depth_started': min(1, budget.get('limit_depth', 1)),
                    'completed_depth': min(1, budget.get('limit_depth', 1)),
                    'stop_reason': 'fixture',
                })
            sequence += 1
        else:
            raise ValueError('unsupported command')
        event['response_ready_ns'] = time.perf_counter_ns()
        emit(event)
        print(response, flush=True)


if __name__ == '__main__':
    main()
