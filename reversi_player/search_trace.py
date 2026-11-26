"""実AIのdepth/time設定と、取得時に生成するtrace4。harnessへ依存しない。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import TYPE_CHECKING, TextIO, cast

if TYPE_CHECKING:
    from reversi_player import ai_engine, ntuple_io, timed_search
elif __package__:
    from . import ai_engine, timed_search
else:
    import ai_engine
    import timed_search


DEFAULT_TRACE_DEPTH = timed_search.DEFAULT_TIMED_DEPTH
MODEL_ENV = 'REVERSI_SEARCH_MODEL'
_MODEL_FIELDS = {'model_path', 'model_id', 'model_sha256'}


@dataclass(frozen=True)
class SearchModel:
    """探索で共有する凍結モデルと起動時の読込区間。"""

    artifact: ntuple_io.ModelArtifact
    start_ns: int
    end_ns: int


def load_search_model(path: str) -> SearchModel:
    """絶対パスのPhase 6モデルを検証する。通常起動ではimportしない。"""
    started = time.perf_counter_ns()
    model_path = Path(path)
    if not model_path.is_absolute():
        raise ValueError('search_model_requires_absolute_path')
    if TYPE_CHECKING:
        from reversi_player import ntuple_io
    elif __package__:
        from . import ntuple_io
    else:
        import ntuple_io
    artifact = ntuple_io.load_model(model_path)
    return SearchModel(artifact, started, time.perf_counter_ns())


def _object(value: object) -> dict[str, object]:
    """JSON objectのキーを検査して型を確定する。"""
    if not isinstance(value, dict) or not all(type(key) is str for key in value):
        raise ValueError('expected_json_object')
    return cast('dict[str, object]', value)


def _canonical(value: object) -> str:
    """契約のcanonical JSONを独立生成する。NaNは拒否する。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False
    )


def clock_descriptor() -> dict[str, object]:
    """実行中のCPU時計・CPython・QPCの属性を取得する。"""
    information = time.get_clock_info('perf_counter')
    frequency: int | None = None
    if sys.platform == 'win32':
        import ctypes

        value = ctypes.c_longlong()
        if ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(value)):
            frequency = value.value
    return {
        'measurement_contract': 'local_cpython313_perf_ns_lifecycle_v2',
        'clock': 'perf_counter_ns',
        'qpc_frequency_hz': frequency,
        'python_patch_version': sys.version_info.micro,
        'implementation': information.implementation,
        'monotonic': information.monotonic,
        'adjustable': information.adjustable,
        'resolution': information.resolution,
        'python_implementation': sys.implementation.name,
        'python_version': list(sys.version_info[:2]),
        'executable': sys.executable,
        'platform': sys.platform,
    }


def load(
    child_entry_ns: int,
    legal_moves_for: ai_engine.LegalMovesFor,
    apply_move: ai_engine.ApplyMove,
) -> SearchTrace | None:
    """設定なし/unknown空searchは非計測、対応外や不完全な設定は例外にする。"""
    names = ('REVERSI_EVAL_CONFIG', 'REVERSI_EVAL_TRACE', 'REVERSI_EVAL_TRACE_CONTEXT')
    values = [os.environ.get(name) for name in names]
    if not any(values):
        return None
    if not all(values):
        raise ValueError('incomplete_evaluation_environment')
    config_path, trace_path, raw_context = values
    assert config_path is not None and trace_path is not None and raw_context is not None
    config = _object(json.loads(Path(config_path).read_text(encoding='utf-8')))
    if (
        set(config) != {'format_version', 'budget', 'search'}
        or type(config['format_version']) is not int
        or config['format_version'] != 2
    ):
        raise ValueError('unsupported_config_format')
    budget, search = _object(config['budget']), _object(config['search'])
    if budget == {'kind': 'unknown'} and not search:
        return None
    kind = budget.get('kind')
    depth = budget.get('limit_depth')
    limit_ns = budget.get('limit_ns')
    if kind == 'depth':
        if set(budget) != {'kind', 'limit_depth'} or type(depth) is not int or not 1 <= depth <= 4:
            raise ValueError('unsupported_search_budget')
        time_limit = None
    elif kind == 'time':
        if (
            set(budget) != {'kind', 'limit_ns'}
            or type(limit_ns) is not int
            or not timed_search.MIN_TIME_BUDGET_NS <= limit_ns <= timed_search.MAX_TIME_BUDGET_NS
        ):
            raise ValueError('unsupported_search_budget')
        time_limit = limit_ns
        depth = 0
    else:
        raise ValueError('unsupported_search_budget')
    allowed = {'algorithm', 'ordered', 'deterministic', 'counting_definition_id', 'tt_capacity'}
    if kind == 'time':
        allowed.update(('max_depth', 'move_ordering'))
    if set(search) - (allowed | _MODEL_FIELDS):
        raise ValueError('unsupported_search_setting')
    algorithm = search.get('algorithm', 'iterative' if kind == 'time' else 'alphabeta')
    ordered = search.get('ordered', True)
    maximum = search.get('max_depth', DEFAULT_TRACE_DEPTH)
    tt_capacity = ai_engine.validate_tt_capacity(search.get('tt_capacity', 0))
    if algorithm == 'minimax' and tt_capacity:
        raise ValueError('tt_requires_alpha_beta')
    if (
        type(algorithm) is not str
        or type(ordered) is not bool
        or (
            kind == 'depth'
            and (
                algorithm not in ('alphabeta', 'minimax')
                or (algorithm == 'minimax' and 'ordered' in search)
            )
        )
        or (kind == 'time' and algorithm != 'iterative')
        or type(maximum) is not int
        or not 1 <= maximum <= timed_search.MAX_TIMED_DEPTH
    ):
        raise ValueError('unsupported_search_algorithm')
    if (
        search.get('deterministic', kind == 'depth') is not (kind == 'depth')
        or search.get('counting_definition_id', ai_engine.SEARCH_COUNTING_DEFINITION)
        != ai_engine.SEARCH_COUNTING_DEFINITION
    ):
        raise ValueError('unsupported_search_contract')
    move_ordering = timed_search.validate_move_ordering(
        search.get('move_ordering', 'static'), ordered, tt_capacity
    )
    context = _object(json.loads(raw_context))
    for key in (
        'run_id',
        'purpose',
        'pair_id',
        'player',
        'requested_config_sha256',
        'target_sha256',
        'process_session_id',
        'clock_domain_id',
    ):
        if type(context.get(key)) is not str or not context[key]:
            raise ValueError('invalid_trace_identity:' + key)
    for key in ('leg', 'process_generation'):
        if type(context.get(key)) is not int or cast(int, context[key]) < 0:
            raise ValueError('invalid_trace_identity:' + key)
    digest = hashlib.sha256(_canonical(config).encode('utf-8')).hexdigest()
    if context['requested_config_sha256'] != digest or context['player'] not in ('A', 'B'):
        raise ValueError('trace_config_identity_mismatch')
    if context.get('host_clock') != clock_descriptor():
        raise ValueError('trace_clock_mismatch')
    model = None
    if _MODEL_FIELDS & search.keys():
        path, model_id, model_digest = (
            search.get(key) for key in ('model_path', 'model_id', 'model_sha256')
        )
        if not all(isinstance(value, str) and value for value in (path, model_id, model_digest)):
            raise ValueError('incomplete_search_model_identity')
        assert isinstance(path, str)
        model = load_search_model(path)
        if (model.artifact.model_id, model.artifact.file_sha256) != (model_id, model_digest):
            raise ValueError('search_model_identity_mismatch')
    return SearchTrace(
        config,
        context,
        Path(trace_path),
        child_entry_ns,
        depth,
        str(algorithm),
        ordered,
        legal_moves_for,
        apply_move,
        time_limit_ns=time_limit,
        max_depth=maximum,
        model=model,
        tt_capacity=tt_capacity,
        move_ordering=move_ordering,
    )


class SearchTrace:
    """単一プロセス・単一スレッドの実探索と原イベントを所有する。"""

    def __init__(
        self,
        config: dict[str, object],
        context: dict[str, object],
        path: Path,
        child_entry_ns: int,
        depth: int,
        algorithm: str,
        ordered: bool,
        legal_moves_for: ai_engine.LegalMovesFor,
        apply_move: ai_engine.ApplyMove,
        *,
        time_limit_ns: int | None = None,
        max_depth: int = DEFAULT_TRACE_DEPTH,
        model: SearchModel | None = None,
        tt_capacity: int = 0,
        move_ordering: str = 'static',
    ) -> None:
        """検証後の設定を保持し、原traceを排他作成する。"""
        self._config, self._context = config, context
        self._child_entry_ns, self._depth = child_entry_ns, depth
        self._algorithm, self._ordered = algorithm, ordered
        self._legal, self._apply = legal_moves_for, apply_move
        self._model = model
        self._tt_capacity = tt_capacity
        self._tt_stats: ai_engine.TTDiagnostics | None = None
        self._measure_iterations = 'max_depth' in _object(config['search'])
        self._iterations: list[timed_search.Iteration] | None = None
        self._leaf_evaluator = model.artifact.model.value if model is not None else None
        self._timed = (
            timed_search.TimedSelector(
                legal_moves_for,
                apply_move,
                replace(timed_search.load_policy(), max_depth=max_depth),
                limit_ns=time_limit_ns,
                ordered=ordered,
                leaf_evaluator=self._leaf_evaluator,
                tt_capacity=tt_capacity,
                move_ordering=move_ordering,
            )
            if time_limit_ns is not None
            else None
        )
        self._stream: TextIO = path.open('x', encoding='utf-8', newline='\n')
        self._thread = threading.get_ident()
        self._sequence = 0
        self._search: tuple[int, int, ai_engine.SearchResult | timed_search.TimedResult] | None = (
            None
        )

    def _emit(self, event: dict[str, object]) -> None:
        """探索外で追記・flushする。失敗は呼出し元へ伝える。"""
        self._stream.write(_canonical(event) + '\n')
        self._stream.flush()

    def ready(self) -> None:
        """Board構築後の受付準備と、起動時のモデル読込の有無を記録する。"""
        model = self._model
        self._emit(
            {
                **self._context,
                'type': 'config',
                'format_version': 4,
                'effective_config': self._config,
                'clock': 'perf_counter_ns',
                'measurement_scope': 'search_only',
                'clock_descriptor': clock_descriptor(),
                'model_id': model.artifact.model_id if model else None,
                'model_load_elapsed_ns': model.end_ns - model.start_ns if model else 0,
                'fallback': False,
                'model_load_status': 'startup' if model else 'not_required',
                'model_load_start_ns': model.start_ns if model else None,
                'model_load_end_ns': model.end_ns if model else None,
                'child_entry_ns': self._child_entry_ns,
                'protocol_ready_ns': time.perf_counter_ns(),
                'pid': os.getpid(),
                'measurement_thread_id': self._thread,
            }
        )

    def begin(self, received_ns: int) -> None:
        """time探索だけにコマンド受付時刻を渡す。固定depthには影響しない。"""
        if self._timed is not None:
            self._timed.begin(received_ns)

    def select(
        self, rows: ai_engine.BoardRows, color: str, moves: ai_engine.LegalMoves
    ) -> ai_engine.Move | None:
        """純粋探索を実測する。実Boardの確定は呼出し元が一度だけ行う。"""
        started = time.perf_counter_ns()
        self._tt_stats = (
            ai_engine.TTDiagnostics(capacity=self._tt_capacity) if self._tt_capacity else None
        )
        if self._timed is not None:
            self._iterations = [] if self._measure_iterations else None
            self._timed.select(
                rows, color, moves, tt_stats=self._tt_stats, iterations=self._iterations
            )
            result = self._timed.result
            assert result is not None
            ended = time.perf_counter_ns()
            self._search = started, ended, result
            return result.move
        result_fixed = (
            ai_engine.search_minimax(
                rows,
                color,
                self._depth,
                self._legal,
                self._apply,
                leaf_evaluator=self._leaf_evaluator,
            )
            if self._algorithm == 'minimax'
            else ai_engine.search_alpha_beta(
                rows,
                color,
                self._depth,
                self._legal,
                self._apply,
                ordered=self._ordered,
                leaf_evaluator=self._leaf_evaluator,
                tt_capacity=self._tt_capacity,
                tt_stats=self._tt_stats,
            )
        )
        ended = time.perf_counter_ns()
        self._search = started, ended, result_fixed
        return result_fixed.move

    def record(
        self, index: int, command: str, received: int, rows: ai_engine.BoardRows, response: str
    ) -> None:
        """実行済みコマンドと探索の実測を応答出力前に保存する。"""
        keys = (
            'run_id',
            'purpose',
            'pair_id',
            'leg',
            'player',
            'process_session_id',
            'process_generation',
        )
        identifier = json.dumps(
            [self._context[key] for key in keys] + [index], separators=(',', ':')
        )
        event: dict[str, object] = {
            'type': 'command',
            'command': command,
            'command_index': index,
            'command_id': identifier,
            'command_received_ns': received,
            'process_session_id': self._context['process_session_id'],
            'process_generation': self._context['process_generation'],
            'measurement_thread_id': self._thread,
        }
        words = command.split()
        if len(words) == 2 and words[0] == 'genmove':
            color = words[1]
            if self._search is None:
                started = time.perf_counter_ns()
                ended = time.perf_counter_ns()
                other = 'W' if color == 'B' else 'B'
                phase = 'pass_probe' if self._legal(rows, other) else 'terminal_probe'
                nodes = completed = 0
                reason = 'no_legal_move'
                begun = 0
                extra: dict[str, object] = {'value': None, 'leaves': 0, 'passes': 0, 'cutoffs': 0}
            else:
                started, ended, result = self._search
                nodes = result.nodes
                phase = 'normal_move'
                if isinstance(result, timed_search.TimedResult):
                    completed, begun = result.completed_depth, result.max_depth_started
                    reason = result.stop_reason
                    extra = {
                        'value': result.value,
                        'leaves': result.leaves,
                        'passes': result.passes,
                        'cutoffs': result.cutoffs,
                        'stop_deadline_ns': result.stop_deadline_ns,
                        'stop_requested_ns': result.stop_requested_ns,
                    }
                else:
                    completed = begun = result.depth
                    reason = 'fixed_depth_completed'
                    extra = {
                        'value': result.value,
                        'leaves': result.leaves,
                        'passes': result.passes,
                    }
            event.update(
                {
                    'type': 'genmove',
                    'sequence': self._sequence,
                    'phase': phase,
                    'color': color,
                    'board_sha256': hashlib.sha256('\n'.join(rows).encode('ascii')).hexdigest(),
                    'response': response.rstrip('\n'),
                    'model_id': self._model.artifact.model_id if self._model else None,
                    'model_load_elapsed_ns': 0,
                    'fallback': False,
                    'search_start_ns': started,
                    'search_end_ns': ended,
                    'search_elapsed_ns': ended - started,
                    'nodes': nodes,
                    'max_depth_started': begun,
                    'completed_depth': completed,
                    'stop_reason': reason,
                    'model_load_status': 'not_required',
                    'model_load_start_ns': None,
                    'model_load_end_ns': None,
                }
            )
            event.update(extra)
            if self._iterations is not None and self._search is not None:
                event['iterations'] = [asdict(step) for step in self._iterations]
            if self._tt_stats is not None:
                event['tt_diagnostics'] = asdict(self._tt_stats)
                self._tt_stats = None
            self._sequence += 1
            self._search = None
        if words != ['quit']:
            event['response_ready_ns'] = time.perf_counter_ns()
        self._emit(event)
        if words == ['quit']:
            self._emit(
                {
                    'type': 'process_exit_ready',
                    'command_id': identifier,
                    'process_session_id': self._context['process_session_id'],
                    'process_generation': self._context['process_generation'],
                    'measurement_thread_id': self._thread,
                    'exit_ready_ns': time.perf_counter_ns(),
                }
            )
            self._stream.close()
