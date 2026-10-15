"""AI標準入出力の形式・外部期限・回収を担い、合法手判定と内部探索時間から分離する。"""

from __future__ import annotations

import os
import json
import math
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from os import PathLike
    from types import TracebackType
    from typing import IO, Literal, NotRequired, TypeGuard, TypedDict

    _Status = Literal['verified', 'unknown', 'failed']
    _PipeOperation = Literal['readline', 'write', 'flush', 'close']

    class _ClockDescriptor(TypedDict):
        """時計の通常形状。旧v1が検査しない周波数/patchはobjectのまま保持する。"""

        measurement_contract: str
        clock: str
        implementation: str
        monotonic: bool
        adjustable: bool
        resolution: float
        python_implementation: str
        python_version: list[int]
        executable: str
        platform: str
        qpc_frequency_hz: NotRequired[object]
        python_patch_version: NotRequired[object]

    class _TemporalReason(TypedDict):
        """原値を保持する時間検査理由。型は検証状態や証拠真正性の証明ではない。"""

        status: _Status
        code: str | None
        path: str
        before: NotRequired[object]
        after: NotRequired[object]

    class _TemporalResult(TypedDict):
        """時間検査の状態と理由。同じ理由列を返し、metadataを正常値へ補修しない。"""

        status: _Status
        reasons: Sequence[Mapping[str, object]]

    class _Identity(TypedDict, total=False):
        """現在のsession/generationと実測thread。原lifecycleの値は未検証である。"""

        process_session_id: object
        process_generation: object
        measurement_thread_id: int

    class _CommandRecord(_Identity, total=False):
        """生成中・失敗時のhost要求形状。取得側phaseの追記も形式/意味の検証済み証明ではない。"""

        command: str
        startup_probe: bool
        command_index: int
        command_id: str
        exchange_start_ns: int
        send_start_ns: int
        response_read_end_ns: int
        validation_end_ns: int
        exit_observed_ns: int
        spawn_before_ns: int
        limit_ns: int
        clock: _ClockDescriptor
        clock_domain_id: object
        error: str
        failure_observed_ns: int
        external_response_ns: int
        wire_envelope_ns: int
        seconds: float
        response: list[str]
        phase: object

    class _HostEvidence(TypedDict, total=False):
        """正常/部分診断の親証拠形状。自由拡張lifecycleやstderrを検証済みの型へ置き換えない。"""

        commands: list[_CommandRecord]
        name: str | None
        exit_code: int | None
        spawn_before_ns: int
        startup_end_ns: int | None
        startup_seconds: float | None
        clock: _ClockDescriptor | None
        clock_domain_id: object
        lifecycle: dict[str, object] | None
        stderr_tail: list[str]

    class _Conditions(TypedDict, total=False):
        """旧資料の欠損も含む期限形状。必要証拠・形式・正数条件は既存検査で確認する。"""

        startup_timeout: float
        command_timeout: float
        genmove_timeout: float

    class _LaunchOptions(TypedDict, total=False):
        """OS別Popenオプション。kwargsの型付けだけで、起動設定は増やさない。"""

        creationflags: int
        start_new_session: bool

LEGACY_MEASUREMENT_CONTRACT = 'local_cpython313_perf_ns_enclosed_v1'
MEASUREMENT_CONTRACT = 'local_cpython313_perf_ns_lifecycle_v2'


def clock_descriptor() -> _ClockDescriptor:
    """実際のperf_counter時計とruntimeを記録する。resolutionは精度保証ではない。"""
    information = time.get_clock_info('perf_counter')
    frequency: int | None = None
    if sys.platform == 'win32':
        import ctypes
        value = ctypes.c_longlong()
        if ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(value)):
            frequency = value.value
    return {'measurement_contract': MEASUREMENT_CONTRACT, 'clock': 'perf_counter_ns',
            'qpc_frequency_hz': frequency, 'python_patch_version': sys.version_info.micro,
            'implementation': information.implementation, 'monotonic': information.monotonic,
            'adjustable': information.adjustable, 'resolution': information.resolution,
            'python_implementation': sys.implementation.name,
            'python_version': list(sys.version_info[:2]), 'executable': sys.executable,
            'platform': sys.platform}


def command_id(context: Mapping[str, object], index: int) -> str:
    """run/pair/leg/roleと全要求indexを含む識別子を返す。genmove連番とは区別する。"""
    keys: tuple[str, ...] = ('run_id', 'pair_id', 'leg', 'player')
    if 'process_session_id' in context:
        keys = ('run_id', 'purpose', 'pair_id', 'leg', 'player', 'process_session_id', 'process_generation')
    return json.dumps([context.get(key) for key in keys] + [index],
                      separators=(',', ':'))


def clock_status(descriptor: object) -> _Status:
    """対応時計かを区別する。未対応/欠損はunknown、既知属性の矛盾はfailed。"""
    if not isinstance(descriptor, dict):
        return 'unknown'
    required = ('measurement_contract', 'clock', 'python_implementation', 'python_version',
                'platform', 'implementation', 'monotonic', 'adjustable', 'resolution', 'executable')
    if any(key not in descriptor or descriptor[key] is None for key in required):
        return 'unknown'
    version = descriptor['python_version']
    if not isinstance(version, list) or len(version) != 2 or any(type(value) is not int for value in version):
        return 'failed'
    if (descriptor.get('measurement_contract') not in (MEASUREMENT_CONTRACT, LEGACY_MEASUREMENT_CONTRACT)
            or descriptor.get('clock') != 'perf_counter_ns'
            or descriptor.get('python_implementation') != 'cpython'
            or descriptor.get('python_version') != [3, 13]
            or descriptor.get('platform') != 'win32'
            or descriptor.get('implementation') != 'QueryPerformanceCounter()'):
        return 'unknown'
    if (descriptor.get('monotonic') is not True or descriptor.get('adjustable') is not False
            or type(descriptor.get('resolution')) not in (int, float)
            or not math.isfinite(descriptor['resolution']) or descriptor['resolution'] <= 0
            or not isinstance(descriptor.get('executable'), str) or not descriptor['executable']):
        return 'failed'
    if descriptor['measurement_contract'] == MEASUREMENT_CONTRACT:
        frequency = descriptor.get('qpc_frequency_hz')
        if frequency is None or descriptor.get('python_patch_version') != 5:
            return 'unknown'
        if type(frequency) is not int or frequency <= 0:
            return 'failed'
    return 'verified'


def _registered_clock(value: object) -> TypeGuard[_ClockDescriptor]:
    """既存の時計登録判定だけを型ガードへ接続する。未検証metadataは絞らない。"""
    return clock_status(value) == 'verified'


def _optional_integer(value: object) -> TypeGuard[int | None]:
    """既存端点条件と同じく、Noneまたは厳密なintだけを認める。boolは除外する。"""
    return value is None or type(value) is int


def _integer_points(values: list[object]) -> TypeGuard[list[int]]:
    """局所端点listの既存の全要素検査を表す。値や順番を変えない。"""
    return not any(type(value) is not int for value in values)


def _positive_timeout(value: object) -> TypeGuard[int | float]:
    """従来と同じ有限・正・厳密int/floatの期限を認める。変換や既定値補充はしない。"""
    return (type(value) is int or type(value) is float) and math.isfinite(value) and value > 0


def _deadline_matches(limit: object, timeout: object, startup: bool) -> bool:
    """既存の期限alias条件を型の絞り込みとともに判定する。初回だけ残り時間を許す。"""
    if _positive_timeout(timeout):
        return type(limit) is int and limit >= 0 and (
            limit <= int(timeout * 1e9) if startup else limit == int(timeout * 1e9))
    return False


def _required_pipe(pipe: IO[str] | None, operation: _PipeOperation) -> IO[str]:
    """Noneなら既存属性アクセスと同じAttributeErrorを送り、同じpipeを返す。採時しない。"""
    if pipe is None:
        raise AttributeError("'NoneType' object has no attribute '{}'".format(operation),
                             name=operation, obj=pipe)
    return pipe


def temporal_result(reasons: Sequence[Mapping[str, object]]) -> _TemporalResult:
    """既知矛盾を優先し、欠損・比較不能の理由を失わずに時間検査を集約する。"""
    return {'status': 'failed' if any(item['status'] == 'failed' for item in reasons)
            else 'unknown' if reasons else 'verified', 'reasons': reasons}


def temporal_order(before: object, after: object, clock: object, path: str,
                   cross_process: bool = False) -> list[_TemporalReason]:
    """同threadは厳密、親子はQPCの曖昧幅を考慮する。端点は変更しない。"""
    status: _Status
    code: str | None
    status, code = 'verified', None
    if not _optional_integer(before) or not _optional_integer(after):
        status, code = 'failed', 'temporal_endpoint_invalid'
    elif before is None or after is None:
        status, code = 'unknown', 'temporal_endpoint_missing'
    elif not _registered_clock(clock):
        status, code = clock_status(clock), 'temporal_clock_unverified'
    elif cross_process:
        frequency = clock.get('qpc_frequency_hz')
        if type(frequency) is not int or frequency <= 0:
            status, code = 'unknown', 'temporal_frequency_missing'
        else:
            uncertainty = (1_000_000_000 + frequency - 1) // frequency + 1
            difference = after - before
            if difference < -uncertainty:
                status, code = 'failed', 'temporal_order_invalid'
            elif difference <= uncertainty:
                status, code = 'unknown', 'clock_order_ambiguous'
    elif before > after:
        status, code = 'failed', 'temporal_order_invalid'
    return [] if status == 'verified' else [
        {'status': status, 'code': code, 'path': path, 'before': before, 'after': after}]


def temporal_match(actual: object, expected: object, path: str) -> list[_TemporalReason]:
    """欠損と既知のidentity/alias矛盾を分ける。boolを整数と同一視しない。"""
    if actual is None or expected is None:
        return [{'status': 'unknown', 'code': 'temporal_identity_missing', 'path': path}]
    if type(actual) is not type(expected) or actual != expected:
        return [{'status': 'failed', 'code': 'temporal_identity_mismatch', 'path': path}]
    return []


def validate_process_timeline(player: _HostEvidence, commands: list[_CommandRecord],
                              expected_context: Mapping[str, object], conditions: _Conditions,
                              require_lifecycle: bool = True, require_normal: bool = True,
                              require_command_identity: bool = True) -> _TemporalResult:
    """保存hostの生存区間と全要求を検査する。旧4も確認可能な逆転は拒否する。

    失敗取得には正常quitを要求せずunknownを残す。未知時計/計測threadの順序を推測しない。
    """
    reasons: list[_TemporalReason] = []
    lifecycle = player.get('lifecycle')
    if lifecycle is None:
        lifecycle = {}
    clock = player.get('clock')
    state = clock_status(clock)
    if state != 'verified':
        reasons.append({'status': state, 'code': 'temporal_host_clock_unverified', 'path': 'clock'})
    if not isinstance(lifecycle, dict) or not isinstance(commands, list):
        return temporal_result([{'status': 'failed', 'code': 'temporal_container_invalid', 'path': 'lifecycle'}])
    thread = lifecycle.get('measurement_thread_id')
    parent_clock = clock if not require_lifecycle or type(thread) is int and thread > 0 else None
    if not require_normal:
        reasons.append({'status': 'unknown', 'code': 'temporal_normal_completion_unavailable', 'path': 'lifecycle'})
    if not require_command_identity:
        reasons.append({'status': 'unknown', 'code': 'legacy_command_identity_unavailable', 'path': 'commands'})
    context = dict(expected_context)
    if require_lifecycle:
        for key, value in expected_context.items():
            reasons += temporal_match(lifecycle.get(key), value, 'lifecycle.' + key)
        for key in ('process_session_id', 'process_generation'):
            context[key] = lifecycle.get(key)
        if not isinstance(context['process_session_id'], str) or not context['process_session_id']:
            reasons.append({'status': 'unknown' if context['process_session_id'] is None else 'failed',
                            'code': 'temporal_session_invalid', 'path': 'lifecycle.process_session_id'})
        reasons += temporal_match(context['process_generation'], 0, 'lifecycle.process_generation')
        for key in ('spawn_before_ns', 'startup_end_ns', 'exit_code'):
            reasons += temporal_match(lifecycle.get(key), player.get(key), 'lifecycle.' + key)
        reasons += temporal_match(lifecycle.get('host_clock'), clock, 'lifecycle.host_clock')
        reasons += temporal_match(lifecycle.get('clock_domain_id'), player.get('clock_domain_id'), 'lifecycle.domain')
        if require_normal:
            reasons += temporal_match(lifecycle.get('termination_kind'), 'normal_quit', 'lifecycle.termination_kind')
        for key in ('pid', 'measurement_thread_id'):
            value = lifecycle.get(key)
            if type(value) is not int or value <= 0:
                reasons.append({'status': 'unknown' if value is None else 'failed',
                                'code': 'temporal_process_field_invalid', 'path': 'lifecycle.' + key})
        reasons += temporal_order(player.get('spawn_before_ns'), lifecycle.get('popen_return_ns'), parent_clock, 'T-SP.return')
    elif not lifecycle:
        reasons.append({'status': 'unknown', 'code': 'legacy_lifecycle_unavailable', 'path': 'lifecycle'})
    lower = lifecycle.get('popen_return_ns') if require_lifecycle else player.get('spawn_before_ns')
    previous_end = None
    previous_clock = parent_clock
    for index, record in enumerate(commands):
        path = 'commands[{}]'.format(index)
        if not isinstance(record, dict):
            reasons.append({'status': 'failed', 'code': 'temporal_command_invalid', 'path': path})
            continue
        end_key = 'exit_observed_ns' if record.get('command') == 'quit' else 'validation_end_ns'
        owned = (not require_lifecycle or type(thread) is int and thread > 0
                 and type(record.get('measurement_thread_id')) is int and record['measurement_thread_id'] == thread)
        local_clock = parent_clock if owned and record.get('clock') == clock else None
        reasons += temporal_order(lower, record.get('exchange_start_ns'), local_clock, path + '.T-SP')
        if index:
            reasons += temporal_order(previous_end, record.get('exchange_start_ns'),
                                      local_clock if previous_clock is not None else None, path + '.T-SERIAL')
        previous_end = record.get(end_key)
        previous_clock = local_clock
        keys = ('exchange_start_ns', 'send_start_ns', end_key) if end_key == 'exit_observed_ns' else (
            'exchange_start_ns', 'send_start_ns', 'response_read_end_ns', end_key)
        for left, right in zip(keys, keys[1:]):
            reasons += temporal_order(record.get(left), record.get(right), local_clock, path + '.' + left + ':' + right)
        if require_normal:
            reasons += temporal_order(record.get(end_key), lifecycle.get('exit_observed_ns'),
                                      local_clock, path + '.T-EXIT.bound')
        timeout_key = 'startup_timeout' if index == 0 else (
            'genmove_timeout' if record.get('command', '').startswith('genmove ') else 'command_timeout')
        timeout = conditions.get(timeout_key)
        if local_clock is not None and state == 'verified' and 'error' not in record and all(record.get(key) is not None for key in keys):
            try:
                validate_command_record(record, timeout)
            except (ValueError, TypeError) as error:
                reasons.append({'status': 'failed', 'code': 'temporal_' + str(error), 'path': path})
        limit = record.get('limit_ns')
        valid_limit = _deadline_matches(limit, timeout, index == 0)
        if not valid_limit:
            reasons.append({'status': 'unknown' if limit is None else 'failed',
                            'code': 'temporal_deadline_invalid', 'path': path})
        if require_command_identity:
            reasons += temporal_match(record.get('command_index'), index, path + '.index')
            expected_id = command_id(context, index) if all(value is not None for value in context.values()) else None
            reasons += temporal_match(record.get('command_id'), expected_id, path + '.command_id')
        reasons += temporal_match(record.get('clock'), clock, path + '.clock')
        reasons += temporal_match(record.get('clock_domain_id'), player.get('clock_domain_id'), path + '.domain')
        if require_lifecycle:
            for key in ('process_session_id', 'process_generation', 'measurement_thread_id'):
                reasons += temporal_match(record.get(key), lifecycle.get(key), path + '.' + key)
        if record.get('command') == 'quit' and index != len(commands) - 1:
            reasons.append({'status': 'failed', 'code': 'temporal_command_after_quit', 'path': path})
    if commands:
        reasons += temporal_match(commands[0].get('command'), 'name', 'T-SP.name')
        if require_normal:
            reasons += temporal_match(commands[-1].get('command'), 'quit', 'T-EXIT.quit')
        reasons += temporal_match(commands[0].get('spawn_before_ns'), player.get('spawn_before_ns'), 'T-SP.alias')
        reasons += temporal_match(commands[0].get('validation_end_ns'), player.get('startup_end_ns'), 'T-SP.startup_end')
        reasons += temporal_order(player.get('spawn_before_ns'), player.get('startup_end_ns'), parent_clock, 'T-SP.startup')
        start, end = player.get('spawn_before_ns'), player.get('startup_end_ns')
        if parent_clock is not None and state == 'verified' and type(start) is int and type(end) is int:
            reasons += temporal_match(player.get('startup_seconds'), (end - start) / 1e9, 'T-SP.seconds')
            if end - start > int(conditions['startup_timeout'] * 1e9):
                reasons.append({'status': 'failed', 'code': 'temporal_startup_timeout', 'path': 'startup'})
        if require_lifecycle and require_normal:
            reasons += temporal_match(commands[-1].get('exit_observed_ns'), lifecycle.get('exit_observed_ns'), 'T-EXIT.alias')
            reasons += temporal_order(lifecycle.get('exit_observed_ns'), lifecycle.get('cleanup_end_ns'), parent_clock, 'T-EXIT.cleanup')
    else:
        reasons.append({'status': 'unknown', 'code': 'temporal_commands_missing', 'path': 'commands'})
    return temporal_result(reasons)


def validate_command_record(record: _CommandRecord, timeout: object) -> int:
    """成功host要求の実測端点・派生秒・期限を検査する純粋関数。

    欠損/順序/単位違反はValueError。失敗要求の値を成功envelopeに流用しない。
    """
    if 'error' in record:
        raise ValueError('host_command_failed')
    keys: tuple[str, ...]
    if record.get('command') == 'quit':
        keys = ('exchange_start_ns', 'send_start_ns', 'exit_observed_ns')
    else:
        keys = ('exchange_start_ns', 'send_start_ns', 'response_read_end_ns', 'validation_end_ns')
    points: list[object] = [record.get(key) for key in keys]
    if not _integer_points(points) or points != sorted(points):
        raise ValueError('host_interval_invalid')
    elapsed = points[-1] - points[0]
    if (type(record.get('external_response_ns')) is not int or record['external_response_ns'] != elapsed
            or type(record.get('seconds')) not in (int, float) or record['seconds'] != elapsed / 1e9):
        raise ValueError('host_elapsed_alias_mismatch')
    if not _positive_timeout(timeout):
        raise ValueError('host_deadline_invalid')
    if elapsed > int(timeout * 1e9):
        raise ValueError('host_deadline_exceeded')
    if record.get('command') != 'quit':
        wire = points[-2] - points[1]
        if type(record.get('wire_envelope_ns')) is not int or record['wire_envelope_ns'] != wire:
            raise ValueError('host_wire_alias_mismatch')
    if type(record.get('command_index')) is not int or record['command_index'] < 0:
        raise ValueError('host_command_index_invalid')
    return elapsed


class HarnessError(RuntimeError):
    """通信/対局検証の失敗種別・要求・候補役割・診断情報を運ぶ例外。"""
    def __init__(self, kind: str, message: str, command: str | None = None) -> None:
        """失敗種別とメッセージを保存し、候補役割と診断情報を後付け可能にする。"""
        super().__init__(message)
        self.kind = kind
        self.command = command
        self.player: str | None = None
        self.diagnostics: _HostEvidence | None = None


def validate_response(command: str, lines: list[str]) -> None:
    """改行を除いた応答行listのコマンド別形式を検査し、不正ならHarnessErrorを送出する。

    showboardは8行のB/W/E、座標はx y、quitは0行。合法性や盤面遷移は検証せず、
    ファイル読込・プロセス起動・入力更新も行わない。
    """
    operation = command.split()[0]
    valid = isinstance(lines, list) and all(isinstance(line, str) for line in lines)
    if not valid:
        raise HarnessError('protocol_error', 'Response must be a list of strings', command)
    if operation == 'quit':
        valid = lines == []
    elif operation == 'showboard':
        valid = len(lines) == 8 and all(re.fullmatch(r'[BWE]{8}', row) for row in lines)
    elif len(lines) != 1:
        valid = False
    elif operation == 'name':
        valid = bool(lines[0].strip())
    elif operation in ('ask', 'play'):
        valid = lines[0] in ('OK', 'NG')
    elif operation == 'genmove':
        valid = lines[0] == 'NG' or re.fullmatch(r'[0-7] [0-7]', lines[0]) is not None
    elif operation == 'result':
        valid = lines[0] in ('B', 'W', 'D', 'NG')
    else:
        valid = False
    if not valid:
        raise HarnessError('protocol_error', 'Invalid response: {!r}'.format(lines), command)


class Player:
    """独立AIプロセスとの標準入出力と期限・停止を管理する。

    成功応答/error/RTTをcommandsへ蓄積し、stderrは末尾100行を保持する。
    盤面の合法性はreferee側の責務。起動は別期限、通常要求とgenmoveは別期限を使う。
    stdinは1要求ずつの同期利用を前提とし、読取threadはstdout/stderrを回収する。
    """
    def __init__(self, script: str | PathLike[str], seed: int | None = None,
                 startup_timeout: float = 5.0, command_timeout: float = 1.0,
                 genmove_timeout: float = 10.0,
                 evaluation_config: str | PathLike[str] | None = None,
                 evaluation_trace: str | PathLike[str] | None = None,
                 evaluation_context: Mapping[str, object] | None = None) -> None:
        """子プロセスと読取threadを起動し、最初のname応答までを起動時間として記録する。

        seedはPython randomとPYTHONHASHSEEDに設定するが、他の乱数源は保証しない。
        予算設定/trace/contextは環境変数で渡し、起動失敗時は停止して例外を伝える。
        """
        self.script = Path(script).resolve()
        self.command_timeout = command_timeout
        self.genmove_timeout = genmove_timeout
        self.commands: list[_CommandRecord] = []
        self.stderr_tail: deque[str] = deque(maxlen=100)
        self._output: queue.Queue[str | None] = queue.Queue(maxsize=512)
        self._overflow = threading.Event()
        self._closed = False
        self.clock = clock_descriptor()
        self.evaluation_context = dict(evaluation_context or {})
        self.clock_domain_id = self.evaluation_context.get('clock_domain_id') or uuid.uuid4().hex
        self.evaluation_context['clock_domain_id'] = self.clock_domain_id
        self.evaluation_context['host_clock'] = self.clock
        self.evaluation_context.update(process_session_id=uuid.uuid4().hex, process_generation=0)
        self.lifecycle = {**self.evaluation_context, 'measurement_thread_id': threading.get_ident()}
        environment = os.environ.copy()
        environment.pop('PYTHONUNBUFFERED', None)
        environment.pop('REVERSI_EVAL_CONFIG', None)
        environment.pop('REVERSI_EVAL_TRACE', None)
        environment.pop('REVERSI_EVAL_TRACE_CONTEXT', None)
        environment['PYTHONIOENCODING'] = 'utf-8'
        if evaluation_config is not None:
            environment['REVERSI_EVAL_CONFIG'] = str(Path(evaluation_config).resolve())
        if evaluation_trace is not None:
            environment['REVERSI_EVAL_TRACE'] = str(Path(evaluation_trace).resolve())
        if evaluation_context is not None:
            environment['REVERSI_EVAL_TRACE_CONTEXT'] = json.dumps(
                self.evaluation_context, sort_keys=True, separators=(',', ':'),
            )
        command = [sys.executable, str(self.script)]
        if seed is not None:
            environment['PYTHONHASHSEED'] = str(seed % (2 ** 32))
            command = [sys.executable, str(Path(__file__).with_name('seeded_player.py')),
                       str(self.script), str(seed)]
        options: _LaunchOptions
        if sys.platform == 'win32':
            options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            options = {'start_new_session': True}
        self.spawn_before_ns = time.perf_counter_ns()
        self.lifecycle['spawn_before_ns'] = self.spawn_before_ns
        started = self.spawn_before_ns / 1e9
        try:
            self.process = subprocess.Popen(
                command, cwd=str(self.script.parent), env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', bufsize=1, **options,
            )
            self.lifecycle['popen_return_ns'] = time.perf_counter_ns()
            self.lifecycle['pid'] = self.process.pid
        except OSError as error:
            self.lifecycle.update(spawn_failure_ns=time.perf_counter_ns(), termination_kind='spawn_failed')
            failure = HarnessError('abnormal_exit', str(error))
            failure.diagnostics = {'commands': self.commands, 'lifecycle': dict(self.lifecycle),
                                   'spawn_before_ns': self.spawn_before_ns, 'clock': self.clock,
                                   'clock_domain_id': self.clock_domain_id}
            raise failure from error
        self._readers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()
        try:
            remaining = startup_timeout - (time.perf_counter() - started)
            self.name = self._exchange('name', max(remaining, 0.0), startup=True)[0]
            self.commands[0]['spawn_before_ns'] = self.spawn_before_ns
            self.startup_end_ns = self.commands[0]['validation_end_ns']
            self.lifecycle['startup_end_ns'] = self.startup_end_ns
            if self.startup_end_ns - self.spawn_before_ns > int(startup_timeout * 1e9):
                self.commands[0]['error'] = 'timeout'
                raise HarnessError('timeout', 'Startup deadline exceeded', 'name')
            self.startup_seconds = (self.startup_end_ns - self.spawn_before_ns) / 1e9
        except BaseException as error:
            self.abort()
            if isinstance(error, HarnessError):
                error.diagnostics = {
                    'lifecycle': dict(self.lifecycle),
                    'exit_code': self.process.returncode,
                    'stderr_tail': list(self.stderr_tail), 'commands': self.commands,
                }
            raise

    def _read_stdout(self) -> None:
        """stdoutを制限付きqueueへ取り込み、EOFと過剰出力を親の通信処理へ通知する。"""
        try:
            while True:
                line = _required_pipe(self.process.stdout, 'readline').readline(4097)
                try:
                    self._output.put_nowait(line if line else None)
                except queue.Full:
                    self._overflow.set()
                    return
                if not line:
                    return
        except (OSError, ValueError):
            return

    def _read_stderr(self) -> None:
        """stderrを読み捨てつつ末尾100行を保持する。全診断ログを保存する処理ではない。"""
        try:
            while True:
                line = _required_pipe(self.process.stderr, 'readline').readline(4097)
                if not line:
                    return
                self.stderr_tail.append(line.rstrip('\r\n'))
        except (OSError, ValueError):
            return

    def _exchange(self, command: str, timeout: float, startup: bool = False) -> list[str]:
        """送信前から応答受信・形式確認までを整数nsで実測し、派生秒とcommandsへ記録する。

        timeoutはこの処理全体の期限で、内部探索時間ではない。失敗時は子プロセスを停止し
        HarnessErrorを再送出する。quitはfinishで別に処理する。
        """
        started_ns = time.perf_counter_ns()
        started = started_ns / 1e9
        index = len(self.commands)
        record: _CommandRecord = {'command': command, 'startup_probe': startup, 'command_index': index,
                  'command_id': command_id(self.evaluation_context, index),
                  'exchange_start_ns': started_ns, 'clock': self.clock,
                  'clock_domain_id': self.clock_domain_id}
        record.update(self._command_identity())
        record['limit_ns'] = int(timeout * 1e9)
        self.commands.append(record)
        try:
            if self._overflow.is_set() or not self._output.empty():
                raise HarnessError('protocol_error', 'Unsolicited stdout before command', command)
            if self.process.poll() is not None:
                self._observe_exit('unexpected_exit')
                raise HarnessError('abnormal_exit', 'Player exited before command', command)
            try:
                record['send_start_ns'] = time.perf_counter_ns()
                _required_pipe(self.process.stdin, 'write').write(command + '\n')
                _required_pipe(self.process.stdin, 'flush').flush()
            except (BrokenPipeError, OSError) as error:
                raise HarnessError('abnormal_exit', str(error), command) from error
            lines = []
            for _ in range(8 if command == 'showboard' else 1):
                remaining = timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    raise HarnessError('timeout', 'Response deadline exceeded', command)
                try:
                    line = self._output.get(timeout=remaining)
                except queue.Empty as error:
                    raise HarnessError('timeout', 'Response deadline exceeded', command) from error
                if line is None:
                    raise HarnessError('abnormal_exit', 'EOF before full response', command)
                if not line.endswith('\n'):
                    raise HarnessError('protocol_error', 'Missing newline or excessive line length', command)
                lines.append(line.rstrip('\r\n'))
            record['response_read_end_ns'] = time.perf_counter_ns()
            if self._overflow.is_set():
                raise HarnessError('protocol_error', 'Excessive stdout', command)
            self._validate(command, lines)
            record['validation_end_ns'] = time.perf_counter_ns()
            # 到着が期限内でも、形式検査まで終わった時点で期限超過なら失敗。
            if record['validation_end_ns'] - started_ns > int(timeout * 1e9):
                raise HarnessError('timeout', 'Response deadline exceeded', command)
            record['response'] = lines
            record['external_response_ns'] = record['validation_end_ns'] - started_ns
            record['wire_envelope_ns'] = record['response_read_end_ns'] - record['send_start_ns']
            record['seconds'] = record['external_response_ns'] / 1e9
            return lines
        except HarnessError as error:
            record['error'] = error.kind
            record['failure_observed_ns'] = time.perf_counter_ns()
            record['seconds'] = (record['failure_observed_ns'] - started_ns) / 1e9
            self.abort()
            raise
        finally:
            record.setdefault('seconds', (time.perf_counter_ns() - started_ns) / 1e9)

    def _validate(self, command: str, lines: list[str]) -> None:
        """共通の純粋形式検査へ委譲する。読取や盤面更新は行わない。"""
        validate_response(command, lines)

    def command(self, command: str) -> list[str]:
        """改行を含まない1要求を送り、genmoveまたは通常要求の期限で応答を返す。

        通信履歴を更新する。不正な要求文字列はValueError、通信違反はHarnessErrorとする。
        """
        if '\n' in command or '\r' in command or not command.strip():
            raise ValueError('Exactly one nonempty command is required')
        timeout = self.genmove_timeout if command.split()[0] == 'genmove' else self.command_timeout
        return self._exchange(command, timeout)

    def finish(self) -> None:
        """quitを送り、正常終了・余分なstdoutなしを確認して通信履歴を追加する。

        quit秒はexchange開始から終了観測までで、thread/pipe回収の総時間とは異なる。
        検証失敗時もfinallyで停止する。既に閉じていれば何もしない。
        """
        if self._closed:
            return
        started_ns = time.perf_counter_ns()
        started = started_ns / 1e9
        index = len(self.commands)
        record: _CommandRecord = {'command': 'quit', 'startup_probe': False, 'command_index': index,
                  'command_id': command_id(self.evaluation_context, index),
                  'exchange_start_ns': started_ns, 'clock': self.clock,
                  'clock_domain_id': self.clock_domain_id}
        record.update(self._command_identity())
        record['limit_ns'] = int(self.command_timeout * 1e9)
        self.commands.append(record)
        try:
            if self.process.poll() is not None:
                self._observe_exit('unexpected_exit')
                raise HarnessError('abnormal_exit', 'Player exited before quit', 'quit')
            record['send_start_ns'] = time.perf_counter_ns()
            _required_pipe(self.process.stdin, 'write').write('quit\n')
            _required_pipe(self.process.stdin, 'flush').flush()
            remaining = self.command_timeout - (time.perf_counter() - started)
            self.process.wait(timeout=max(remaining, 0.0))
            self._observe_exit('normal_quit' if self.process.returncode == 0 else 'unexpected_exit')
            record['exit_observed_ns'] = cast(int, self.lifecycle['exit_observed_ns'])
            record['external_response_ns'] = record['exit_observed_ns'] - started_ns
            record['seconds'] = record['external_response_ns'] / 1e9
            if record['seconds'] > self.command_timeout:
                raise HarnessError('timeout', 'quit deadline exceeded', 'quit')
            # ここからのreader/pipe回収時間は、記録済みquit秒に含めない。
            for reader in self._readers:
                reader.join(timeout=0.5)
            if self.process.returncode != 0:
                raise HarnessError('abnormal_exit', 'Nonzero exit: {}'.format(self.process.returncode), 'quit')
            extra = []
            while not self._output.empty():
                line = self._output.get_nowait()
                if line is not None:
                    extra.append(line)
            if extra or self._overflow.is_set():
                raise HarnessError('protocol_error', 'Extra stdout: {!r}'.format(extra), 'quit')
            record['response'] = []
        except subprocess.TimeoutExpired as error:
            record['error'] = 'timeout'
            raise HarnessError('timeout', 'quit deadline exceeded', 'quit') from error
        except (BrokenPipeError, OSError) as error:
            record['error'] = 'abnormal_exit'
            raise HarnessError('abnormal_exit', str(error), 'quit') from error
        except HarnessError as error:
            record['error'] = error.kind
            raise
        finally:
            record.setdefault('seconds', (time.perf_counter_ns() - started_ns) / 1e9)
            self.abort()

    def _command_identity(self) -> _CommandRecord:
        """今回要求の実際のthreadとsessionを記録する。thread変更を黙って吸収しない。"""
        return {'process_session_id': self.lifecycle['process_session_id'],
                'process_generation': self.lifecycle['process_generation'],
                'measurement_thread_id': threading.get_ident()}

    def _observe_exit(self, kind: str) -> None:
        """終了を確認した最初の観測を保持し、後の回収で上限を広げない。"""
        if 'exit_observed_ns' not in self.lifecycle:
            self.lifecycle.update(exit_observed_ns=time.perf_counter_ns(),
                                  exit_code=self.process.returncode, termination_kind=kind)

    def evidence(self) -> _HostEvidence:
        """game/probe共通の親証拠を返す。時刻を再測定せず取得した値を複写する。"""
        return {'commands': self.commands, 'name': getattr(self, 'name', None),
                'exit_code': self.process.returncode, 'spawn_before_ns': self.spawn_before_ns,
                'startup_end_ns': getattr(self, 'startup_end_ns', None),
                'startup_seconds': getattr(self, 'startup_seconds', None), 'clock': self.clock,
                'clock_domain_id': self.clock_domain_id, 'lifecycle': dict(self.lifecycle)}

    def abort(self) -> None:
        """未終了processをOS別の停止手段で回収し、thread/pipeを閉じて_closedを設定する。

        Windowsではtaskkillのツリー停止を試み、残存時は直接killする。回収の全失敗や
        無期限の完了を保証する処理ではなく、閉じた後の再呼出は何もしない。
        """
        if self._closed:
            return
        if self.process.poll() is None:
            self.lifecycle.setdefault('abort_requested_ns', time.perf_counter_ns())
            if sys.platform == 'win32':
                try:
                    subprocess.run(
                        ['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                        capture_output=True, timeout=3,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=3)
            self._observe_exit('killed')
        else:
            self._observe_exit('unexpected_exit')
        for reader in self._readers:
            reader.join(timeout=1)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            _required_pipe(pipe, 'close').close()
        self._closed = True
        self.lifecycle['cleanup_end_ns'] = time.perf_counter_ns()

    def __enter__(self) -> Player:
        """起動済みの自身を返す。ここでは新たなプロセスを起動しない。"""
        return self

    def __exit__(self, exception_type: type[BaseException] | None,
                 exception: BaseException | None, traceback: TracebackType | None) -> None:
        """通常終了ならquitを確認し、例外終了なら強制停止する。元の例外は抑制しない。"""
        if exception_type is None:
            self.finish()
        else:
            self.abort()
