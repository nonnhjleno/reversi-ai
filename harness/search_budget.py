"""要求設定と自己申告traceの意味・上限を検証する。内部計測の真正性までは証明しない。"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, cast
from harness.protocol import (clock_status, validate_command_record, temporal_order,
                              temporal_match, temporal_result)

if TYPE_CHECKING:
    from collections.abc import Buffer, Iterable, Mapping, Sequence
    from os import PathLike
    from typing import Literal, NotRequired, Required, TypeGuard, TypedDict
    from harness.protocol import _CommandRecord, _Status, _TemporalReason, _TemporalResult

    _WorkLimitKey = Literal['limit_nodes', 'limit_depth']

    class _Exemption(TypedDict):
        """正規化された除外宣言。適用許可や元コードの真正性は別途検証する。"""

        reason: str
        target_sha256: str

    class _Budget(TypedDict, total=False):
        """通常の正規化上限。kindとlimitの対応・値域は実行時検査を必要とする。"""

        kind: Required[str]
        limit_ns: int
        limit_nodes: int
        limit_depth: int
        exemption: _Exemption

    class _BudgetConfig(TypedDict):
        """v2設定の通常形状。searchの未知metadataは原辞書のobjectとして保持する。"""

        format_version: int
        budget: _Budget
        search: dict[str, object]

    class _BudgetCommand(_CommandRecord, total=False):
        """親要求の取得側phaseも継承する予算検査用形状。保存内容の検証済み証明ではない。"""


    class _IntervalResult(TypedDict):
        """元の時間検査の状態と理由。rawへの値補充や検証証明書を含まない。"""

        status: _Status
        reason: str | None

    class _TraceValidation(TypedDict):
        """予算検査の結果と途中測定値。失敗前に得た未検証値もobjectのまま残す。"""

        status: Literal['verified', 'unknown', 'failed', 'exempt']
        reason: str | None
        events: int
        measurements: list[dict[str, object]]
        artifact: NotRequired[object]

KINDS = {'time', 'nodes', 'depth', 'none', 'unknown'}
TRACE_VERSION = 4


def _positive_number(value: object) -> TypeGuard[int | float]:
    """従来の厳密int/float・有限・正の上限だけを認め、boolや変換を許さない。"""
    return (type(value) is int or type(value) is float) and math.isfinite(value) and value > 0


def _nonnegative_integer(value: object) -> TypeGuard[int]:
    """元のtype検査と負数拒否を型へ接続する。boolは整数としない。"""
    return type(value) is int and value >= 0


def _integer_points(points: list[object]) -> TypeGuard[list[int]]:
    """局所端点listの全要素検査を表し、原listや端点を変更しない。"""
    return not any(type(point) is not int for point in points)


def _optional_points(points: tuple[object, ...]) -> TypeGuard[tuple[int | None, ...]]:
    """既存load検査と同じく、提供済みの非整数を欠損より先に拒否する。"""
    return not any(point is not None and type(point) is not int for point in points)


def _present_points(points: tuple[int | None, ...]) -> TypeGuard[tuple[int, ...]]:
    """型検査後の局所tupleに欠損がないことだけを確認し、端点を補完しない。"""
    return not any(point is None for point in points)


def _object_events(events: list[object]) -> TypeGuard[list[dict[str, object]]]:
    """JSON event列の既存の辞書検査を表す。各fieldの意味や型は保証しない。"""
    return not any(not isinstance(event, dict) for event in events)


def validate_child_lifecycle(events: list[object] | None, commands: Sequence[_CommandRecord],
                             lifecycle: object, expected_identity: Mapping[str, object]) -> _TemporalResult:
    """原rawと親の生存区間・全要求を照合する。予算除外でも子の通信証拠は免除しない。"""
    reasons: list[_TemporalReason] = []
    if not events or not isinstance(events[0], dict) or events[0].get('format_version') != TRACE_VERSION:
        return temporal_result([{'status': 'unknown', 'code': 'temporal_child_lifecycle_missing', 'path': 'trace'}])
    header: dict[str, object] = events[0]
    reasons += temporal_match(header.get('type'), 'config', 'trace.header.type')
    if not isinstance(lifecycle, dict):
        lifecycle = {}
    clock = header.get('clock_descriptor')
    host_clock = lifecycle.get('host_clock')
    comparable = clock_status(clock) == clock_status(host_clock) == 'verified'
    for key, value in expected_identity.items():
        reasons += temporal_match(header.get(key), value, 'trace.header.' + key)
    for key in ('process_session_id', 'process_generation', 'pid', 'clock_domain_id'):
        reasons += temporal_match(header.get(key), lifecycle.get(key), 'trace.header.' + key)
    reasons += temporal_match(header.get('host_clock'), host_clock, 'trace.header.host_clock')
    reasons += temporal_match(clock, host_clock, 'trace.header.clock_descriptor')
    for descriptor in (clock, host_clock):
        state = clock_status(descriptor)
        if state != 'verified':
            reasons.append({'status': state, 'code': 'temporal_child_clock_unverified', 'path': 'trace.header.clock'})
    comparable = comparable and clock == host_clock and header.get('clock_domain_id') == lifecycle.get('clock_domain_id')
    comparable = comparable and bool(header.get('clock_domain_id')) and header.get('host_clock') == host_clock
    thread = header.get('measurement_thread_id')
    if type(thread) is not int or thread <= 0:
        reasons.append({'status': 'unknown' if thread is None else 'failed',
                        'code': 'temporal_child_thread_invalid', 'path': 'trace.header.thread'})
    local_clock = clock if clock_status(clock) == 'verified' and type(thread) is int and thread > 0 else None
    cross_clock = clock if comparable and not reasons else None
    entry, ready = header.get('child_entry_ns'), header.get('protocol_ready_ns')
    reasons += temporal_order(lifecycle.get('spawn_before_ns'), entry, cross_clock, 'T-INIT.spawn:entry', True)
    reasons += temporal_order(entry, ready, local_clock, 'T-INIT.entry:ready')
    if header.get('model_load_status') == 'startup':
        status, code = (_model_load_contract(header, entry, ready) if local_clock is not None else
                        ('unknown', 'model_load_clock_unverified'))
        if status != 'verified':
            reasons.append({'status': status, 'code': code, 'path': 'T-LOAD.startup'})
    allowed = {'config', 'command', 'genmove', 'process_exit_ready'}
    if not _object_events(events):
        return temporal_result(reasons + [{'status': 'failed', 'code': 'temporal_event_invalid', 'path': 'trace'}])
    if any(event.get('type') not in allowed for event in events):
        return temporal_result(reasons + [{'status': 'unknown', 'code': 'temporal_event_unsupported', 'path': 'trace'}])
    if len(events) != len(commands) + 2:
        reasons.append({'status': 'unknown' if len(events) < len(commands) + 2 else 'failed',
                        'code': 'temporal_event_count_mismatch', 'path': 'trace'})
    if events[-1].get('type') != 'process_exit_ready':
        reasons.append({'status': 'unknown', 'code': 'temporal_exit_ready_missing', 'path': 'trace.end'})
        child_end = None
    else:
        child_end = events[-1].get('exit_ready_ns')
        if commands:
            reasons += temporal_match(events[-1].get('command_id'), commands[-1].get('command_id'), 'T-EXIT.command_id')
        for key in ('process_session_id', 'process_generation'):
            reasons += temporal_match(events[-1].get(key), lifecycle.get(key), 'T-EXIT.' + key)
        reasons += temporal_match(events[-1].get('measurement_thread_id'), thread, 'T-EXIT.thread')
    previous = ready
    for index, record in enumerate(commands):
        path = 'trace.commands[{}]'.format(index)
        if index + 1 >= len(events) or events[index + 1].get('type') not in ('command', 'genmove'):
            reasons.append({'status': 'unknown', 'code': 'temporal_child_command_missing', 'path': path})
            continue
        event = events[index + 1]
        binding = []
        for key in ('command_id', 'command_index', 'command', 'process_session_id', 'process_generation'):
            binding += temporal_match(event.get(key), record.get(key), path + '.' + key)
        reasons += binding
        reasons += temporal_match(event.get('measurement_thread_id'), thread, path + '.thread')
        received = event.get('command_received_ns')
        owned_clock = local_clock if type(event.get('measurement_thread_id')) is int and event['measurement_thread_id'] == thread else None
        event_cross_clock = cross_clock if owned_clock is not None and not binding else None
        reasons += temporal_order(previous, received, owned_clock, path + '.T-SERIAL')
        reasons += temporal_order(record.get('send_start_ns'), received, event_cross_clock, path + '.T-CMD.send', True)
        if record.get('command') == 'quit':
            reasons += temporal_order(received, child_end, owned_clock, path + '.T-EXIT.ready')
            previous = received
        else:
            response = event.get('response_ready_ns')
            reasons += temporal_order(received, response, owned_clock, path + '.T-CMD.response')
            reasons += temporal_order(response, record.get('response_read_end_ns'), event_cross_clock, path + '.T-CMD.read', True)
            previous = response
        if event.get('type') == 'genmove':
            search_start, search_end = event.get('search_start_ns'), event.get('search_end_ns')
            for before, after, invariant in ((entry, search_start, 'T-INIT.search'),
                                             (received, search_start, 'T-SEARCH.received'),
                                             (search_start, search_end, 'T-SEARCH.order'),
                                             (search_end, event.get('response_ready_ns'), 'T-SEARCH.response'),
                                             (search_end, child_end, 'T-EXIT.search')):
                reasons += temporal_order(before, after, owned_clock, path + '.' + invariant)
            timing = (validate_search_interval(header, event, record) if event_cross_clock is not None else
                      {'status': 'unknown', 'reason': 'search_interval_clock_unproven'})
            if timing['status'] != 'verified':
                reasons.append({'status': timing['status'], 'code': timing['reason'], 'path': path + '.T-SEARCH'})
            status, code = (_model_load_contract(event, received, event.get('response_ready_ns'), (search_start, search_end))
                            if owned_clock is not None or event.get('model_load_status') == 'not_required' else
                            ('unknown', 'model_load_clock_unverified'))
            if status != 'verified':
                reasons.append({'status': status, 'code': code, 'path': path + '.T-LOAD'})
            if event.get('model_load_status') == 'command':
                reasons += temporal_order(event.get('model_load_end_ns'), search_start, owned_clock, path + '.T-LOAD.use')
        reasons += temporal_order(previous, child_end, owned_clock, path + '.T-EXIT.child_bound')
    reasons += temporal_order(child_end, lifecycle.get('exit_observed_ns'), cross_clock, 'T-EXIT.observed', True)
    return temporal_result(reasons)


def validate_search_interval(header: Mapping[str, object], event: Mapping[str, object],
                              record: _CommandRecord) -> _IntervalResult:
    """同一clock domainの実測child探索がhost要求区間へ含まれるか検査する。

    端点を秒や再実行から補わない。既知矛盾はfailed、時計証拠不足/未対応はunknown。
    """
    def result(status: _Status, reason: str | None = None) -> _IntervalResult:
        """時間軸の状態と理由を返し、元のtraceやhost記録は変更しない。"""
        return {'status': status, 'reason': reason}

    if header.get('format_version') not in (3, TRACE_VERSION):
        return result('unknown', 'legacy_search_interval_unavailable')
    host_clock = record.get('clock')
    child_clock = header.get('clock_descriptor')
    if not isinstance(host_clock, dict) or not isinstance(child_clock, dict):
        return result('unknown', 'search_interval_clock_missing')
    domains = [header.get('clock_domain_id'), record.get('clock_domain_id')]
    if any(domain is not None and type(domain) is not str for domain in domains):
        return result('failed', 'search_interval_domain_invalid')
    if any(not domain for domain in domains):
        return result('unknown', 'search_interval_domain_missing')
    if header['clock_domain_id'] != record['clock_domain_id'] or header.get('host_clock') != host_clock:
        return result('failed', 'search_interval_domain_mismatch')
    status: _Status | _TemporalResult
    for clock in (host_clock, child_clock):
        status = clock_status(clock)
        if status == 'unknown':
            return result('unknown', 'search_interval_clock_unsupported')
        if status == 'failed':
            return result('failed', 'search_interval_clock_invalid')
    if host_clock != child_clock:
        return result('failed', 'search_interval_clock_mismatch')
    if event.get('command_index') is not None and type(event['command_index']) is not int:
        return result('failed', 'search_interval_command_mismatch')
    if any(event.get(key) is not None and record.get(key) is not None
           and (type(event[key]) is not type(record[key]) or event[key] != record[key])
           for key in ('command_index', 'command_id')):
        return result('failed', 'search_interval_command_mismatch')
    if any(event.get(key) is None or record.get(key) is None for key in ('command_index', 'command_id')):
        return result('unknown', 'search_interval_command_missing')
    keys = ('command_received_ns', 'search_start_ns', 'search_end_ns', 'response_ready_ns')
    points = [event.get(key) for key in keys]
    if any(point is None for point in points):
        return result('unknown', 'search_interval_endpoint_missing')
    if not _integer_points(points) or points != sorted(points):
        return result('failed', 'search_interval_order_invalid')
    elapsed = event.get('search_elapsed_ns')
    if type(elapsed) is not int or elapsed != points[2] - points[1]:
        return result('failed', 'search_interval_elapsed_mismatch')
    host_points = [record.get(key) for key in ('exchange_start_ns', 'send_start_ns',
                                               'response_read_end_ns', 'validation_end_ns')]
    if not _integer_points(host_points):
        return result('unknown', 'search_interval_host_missing')
    combined = host_points[:2] + points + host_points[2:]
    if header.get('format_version') == TRACE_VERSION:
        edges = temporal_order(host_points[1], points[0], host_clock, 'search_interval.send', True)
        edges += temporal_order(points[-1], host_points[-2], host_clock, 'search_interval.read', True)
        status = temporal_result(edges)
        if status['status'] != 'verified':
            return result(status['status'], 'search_interval_' + cast(str, status['reasons'][0]['code']))
    elif combined != sorted(combined):
        return result('failed', 'search_interval_not_enclosed')
    try:
        validate_command_record(record, record.get('limit_ns', 10_000_000_000) / 1e9)
    except ValueError as error:
        return result('failed', 'search_interval_' + str(error))
    if elapsed > record['wire_envelope_ns']:
        return result('failed', 'search_interval_duration_invalid')
    return result('verified')


def _model_load_contract(info: Mapping[str, object], lower: object, upper: object,
                         search_interval: tuple[object, object] | None = None) -> tuple[_Status, str | None]:
    """model loadの実測区間を検査する。modelなしは0秒ではなく明示not_required。"""
    status = info.get('model_load_status')
    start, end, elapsed = (info.get(key) for key in
                           ('model_load_start_ns', 'model_load_end_ns', 'model_load_elapsed_ns'))
    if status == 'not_required':
        if start is not None or end is not None or type(elapsed) is not int or elapsed != 0:
            return 'failed', 'model_load_not_required_mismatch'
        return 'verified', None
    if status not in ('startup', 'command'):
        return 'unknown', 'model_load_contract_unsupported'
    points = (start, end, elapsed, lower, upper)
    if not _optional_points(points) or type(elapsed) is int and elapsed < 0:
        return 'failed', 'model_load_interval_invalid'
    if not _present_points(points):
        return 'unknown', 'model_load_endpoint_missing'
    start, end, elapsed, lower, upper = points
    if not lower <= start <= end <= upper or elapsed != end - start:
        return 'failed', 'model_load_interval_not_enclosed'
    # この既存演算の入力制約だけを記述する。castは不正原値を変換せず、元のTypeErrorを残す。
    if search_interval is not None and not (end <= cast(int, search_interval[0]) or start >= cast(int, search_interval[1])):
        return 'failed', 'model_load_overlaps_search'
    if info.get('model_id') is None:
        return 'failed', 'model_load_without_model'
    return 'verified', None


def canonical_json(value: object) -> str:
    """要求設定の同一性照合用canonical JSONを返す。NaN等は許可しない。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256(value: Buffer) -> str:
    """bytesのSHA-256文字列を返す。計測や実行者の真正性は証明しない。"""
    return hashlib.sha256(value).hexdigest()


def board_sha256(rows: Iterable[str]) -> str:
    """要求前盤面の行を改行で連結したASCII bytesをhashする。JSON/case IDとは別の規約。"""
    return sha256('\n'.join(rows).encode('ascii'))


def normalize_config(value: object) -> _BudgetConfig:
    """予算設定v1/v2を検査し、新しいv2辞書へ正規化する。

    v1時間msは整数nsへ厳密変換し、nodes/depthは整数制約を確認する。
    不正入力はValueErrorとし、search辞書は共有参照のまま保持する。
    設定が有効でも実際の予算遵守・計測真正性を保証するわけではない。
    """
    if (not isinstance(value, dict) or type(value.get('format_version')) is not int
            or value.get('format_version') not in (1, 2)):
        raise ValueError('unsupported budget config format_version')
    budget = value.get('budget')
    if not isinstance(budget, dict):
        raise ValueError('budget config must contain budget object')
    kind = budget.get('kind')
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('unsupported budget kind')
    normalized_budget: _Budget = {'kind': kind}
    limit_key = {'time': 'limit_ms', 'nodes': 'limit_nodes', 'depth': 'limit_depth'}.get(kind)
    if limit_key:
        if kind == 'time' and value.get('format_version') == 2 and 'limit_ns' not in budget:
            raise ValueError('format_version 2 time budget requires limit_ns')
        if kind == 'time' and value.get('format_version') == 2 and 'limit_ns' in budget:
            if 'limit_ms' in budget:
                raise ValueError('format_version 2 time budget must use limit_ns only')
            limit = budget['limit_ns']
            if type(limit) is not int or limit <= 0:
                raise ValueError('limit_ns must be a positive integer')
            normalized_budget['limit_ns'] = limit
            limit_key = 'limit_ns'
        else:
            limit = budget.get(limit_key)
            if not _positive_number(limit):
                raise ValueError('{} must be a positive finite number'.format(limit_key))
            if kind in ('nodes', 'depth'):
                if int(limit) != limit:
                    raise ValueError('{} must be an integer'.format(limit_key))
                normalized_budget[cast('_WorkLimitKey', limit_key)] = int(limit)
            else:
                try:
                    # 浮動小数点の丸めや一律猶予で予算上限を緩めない。
                    nanoseconds = Decimal(str(limit)) * Decimal(1_000_000)
                except InvalidOperation as error:
                    raise ValueError('limit_ms is invalid') from error
                if nanoseconds != nanoseconds.to_integral_value():
                    raise ValueError('limit_ms cannot be represented in integer nanoseconds')
                normalized_budget['limit_ns'] = int(nanoseconds)
    elif kind == 'none':
        exemption = budget.get('exemption')
        if exemption is not None:
            if not isinstance(exemption, dict):
                raise ValueError('none exemption requires a reason')
            reason = exemption.get('reason')
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('none exemption requires a reason')
            target_hash = exemption.get('target_sha256')
            if (not isinstance(target_hash, str) or len(target_hash) != 64
                    or any(character not in '0123456789abcdefABCDEF' for character in target_hash)):
                raise ValueError('none exemption requires target_sha256')
            normalized_budget['exemption'] = {
                'reason': str(exemption['reason']),
                'target_sha256': target_hash.lower(),
            }
    allowed_budget_fields = {'kind', 'exemption'}
    if kind == 'time':
        allowed_budget_fields.add('limit_ns' if value['format_version'] == 2 else 'limit_ms')
    elif limit_key:
        allowed_budget_fields.add(limit_key)
    for key, item in budget.items():
        if key not in allowed_budget_fields:
            raise ValueError('unexpected budget field: {}'.format(key))
    normalized: _BudgetConfig = {
        'format_version': 2,
        'budget': normalized_budget,
        'search': value.get('search', {}),
    }
    if not isinstance(normalized['search'], dict):
        raise ValueError('search must be an object')
    return normalized


def load_config(path: str | PathLike[str]) -> _BudgetConfig:
    """UTF-8設定JSONを読み、正規化v2辞書を返す。I/O・JSON・値エラーは伝播する。"""
    return normalize_config(json.loads(Path(path).read_text(encoding='utf-8')))


def exemption_status(config: _BudgetConfig, target_sha256: str) -> tuple[Literal['exempt', 'unknown', 'failed'], str]:
    """正規化none設定の宣言と元本体hashから(exempt/unknown/failed, 理由)を返す。

    B限定policyの許可は呼出側が別途確認する。実コードに探索がないことは証明しない。
    """
    budget = config['budget']
    if budget['kind'] != 'none':
        return 'unknown', 'budget_telemetry_required'
    exemption = budget.get('exemption')
    if not exemption:
        return 'unknown', 'none_without_verified_exemption'
    if exemption['target_sha256'] != target_sha256.lower():
        return 'failed', 'exemption_target_hash_mismatch'
    return 'exempt', 'declared_no_internal_search_budget'


def write_config(config: object, path: str | PathLike[str]) -> _BudgetConfig:
    """正規化v2設定を指定ファイルへ書込み、使用した辞書を返す。既存ファイルは上書きする。"""
    normalized = normalize_config(config)
    Path(path).write_text(json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return normalized


def _validate_trace_events(events: list[object] | None, config: object, player_commands: Sequence[_CommandRecord],
                           target_sha256: str, expected_identity: Mapping[str, object] | None = None,
                           allow_exemption: bool = False, purpose: str = 'game') -> _TraceValidation:
    """parse済みtraceと要求/commandsを照合し、予算状態と観測測定値を返す内部実装。

    欠損・未知版/計測定義はunknown、矛盾/上限違反はfailed。許可済みnoneはexempt。
    commandsの盤面/phaseが正しいことはlive/refereeまたはcontractsの再生検証が前提。
    自己申告の内部counter、全棋譜合法性や全ケース決定性まではここで保証しない。
    """
    try:
        normalized_config = normalize_config(config)
        exemption, reason = exemption_status(normalized_config, target_sha256)
        if exemption == 'failed':
            return {'status': 'failed', 'reason': reason, 'events': 0, 'measurements': []}
        if exemption == 'exempt':
            if allow_exemption and events is None:
                return {'status': 'exempt', 'reason': reason, 'events': 0, 'measurements': []}
            if not allow_exemption and purpose != 'probe':
                return {'status': 'unknown', 'reason': 'exemption_not_authorized', 'events': 0, 'measurements': []}
        if normalized_config['budget']['kind'] == 'unknown' or (normalized_config['budget']['kind'] == 'none' and exemption != 'exempt'):
            return {'status': 'unknown', 'reason': reason, 'events': 0, 'measurements': []}
        if events is None:
            return {'status': 'unknown', 'reason': 'trace_missing', 'events': 0, 'measurements': []}
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError) as error:
        return {'status': 'failed', 'reason': 'trace_invalid:{}'.format(error), 'events': 0, 'measurements': []}
    if not events or not isinstance(events[0], dict):
        return {'status': 'failed', 'reason': 'trace_header_invalid', 'events': len(events), 'measurements': []}
    if type(events[0].get('format_version')) is not int or events[0].get('format_version') not in (2, 3, TRACE_VERSION):
        return {'status': 'unknown', 'reason': 'trace_version_unsupported', 'events': len(events), 'measurements': []}
    header: dict[str, object] = events[0]
    required_header = ('run_id', 'pair_id', 'leg', 'player', 'requested_config_sha256',
                       'target_sha256', 'effective_config', 'clock', 'measurement_scope',
                       'model_id', 'model_load_elapsed_ns', 'fallback')
    if header.get('type') != 'config' or any(key not in header for key in required_header):
        return {'status': 'failed', 'reason': 'trace_config_event_missing', 'events': len(events), 'measurements': []}
    if (not isinstance(header.get('run_id'), str) or not isinstance(header.get('pair_id'), str)
            or type(header.get('leg')) is not int or header.get('player') not in ('A', 'B')
            or not isinstance(header.get('clock'), str) or not header['clock']
            or not isinstance(header.get('measurement_scope'), str) or not header['measurement_scope']
            or (header.get('model_id') is not None and not isinstance(header.get('model_id'), str))
            or not _nonnegative_integer(header.get('model_load_elapsed_ns'))
            or type(header.get('fallback')) is not bool):
        return {'status': 'failed', 'reason': 'trace_header_invalid', 'events': len(events), 'measurements': []}
    if header.get('fallback'):
        return {'status': 'failed', 'reason': 'initial_model_fallback', 'events': len(events), 'measurements': []}
    # 非空の計測名だけでは、同じ予算を測ったと判断できない。
    if header['clock'] != 'perf_counter_ns' or header['measurement_scope'] != 'search_only':
        return {'status': 'unknown', 'reason': 'measurement_contract_unsupported', 'events': len(events), 'measurements': []}
    expected_model = normalized_config['search'].get('model_id')
    if header['model_id'] != expected_model:
        return {'status': 'failed', 'reason': 'requested_model_mismatch', 'events': len(events), 'measurements': []}
    effective = header.get('effective_config')
    if effective != normalized_config:
        return {'status': 'failed', 'reason': 'effective_config_mismatch', 'events': len(events), 'measurements': []}
    config_hash = hashlib.sha256(canonical_json(normalized_config).encode('utf-8')).hexdigest()
    if header.get('requested_config_sha256') != config_hash or header.get('target_sha256') != target_sha256:
        return {'status': 'failed', 'reason': 'trace_identity_mismatch', 'events': len(events), 'measurements': []}
    if expected_identity:
        if any(header.get(key) is not None and value is not None
               and (type(header[key]) is not type(value) or header[key] != value)
               for key, value in expected_identity.items()):
            return {'status': 'failed', 'reason': 'trace_run_identity_mismatch', 'events': len(events), 'measurements': []}
        if any(header.get(key) is None or value is None for key, value in expected_identity.items()):
            return {'status': 'unknown', 'reason': 'trace_identity_missing', 'events': len(events), 'measurements': []}
    expected = [
        command for command in player_commands
        if command.get('command', '').startswith('genmove ')
    ]
    exempt_moves = exemption == 'exempt' and allow_exemption and purpose != 'probe'
    if exempt_moves:
        expected = []
    model_timing_unknown = None
    if header['format_version'] in (3, TRACE_VERSION):
        first = player_commands[0] if player_commands else {}
        if header.get('model_load_status') == 'not_required':
            status, model_reason = _model_load_contract(header, None, None)
        elif header['format_version'] == TRACE_VERSION:
            status, model_reason = (_model_load_contract(header, header.get('child_entry_ns'), header.get('protocol_ready_ns'))
                                    if clock_status(header.get('clock_descriptor')) == 'verified' else
                                    ('unknown', 'model_load_clock_unverified'))
        else:
            comparable = (clock_status(header.get('clock_descriptor')) == clock_status(first.get('clock')) == 'verified'
                          and header.get('clock_descriptor') == first.get('clock')
                          and header.get('clock_domain_id') == first.get('clock_domain_id')
                          and bool(header.get('clock_domain_id')))
            status, model_reason = (_model_load_contract(header, first.get('spawn_before_ns'), first.get('validation_end_ns'))
                                    if comparable else ('unknown', 'model_load_clock_unverified'))
        if header.get('model_id') is not None and header.get('model_load_status') == 'not_required':
            status, model_reason = 'unknown', 'model_initial_load_unverified'
        if status == 'failed':
            return {'status': 'failed', 'reason': model_reason, 'events': len(events), 'measurements': []}
        if status == 'unknown':
            model_timing_unknown = model_reason
    move_events: list[object] = ([event for event in events[1:] if isinstance(event, dict) and event.get('type') == 'genmove']
                   if header['format_version'] == TRACE_VERSION else events[1:])
    if not _object_events(move_events):
        return {'status': 'failed', 'reason': 'trace_event_not_object', 'events': len(events), 'measurements': []}
    if len(move_events) != len(expected):
        return {'status': 'failed', 'reason': 'trace_genmove_count_mismatch', 'events': len(events), 'measurements': []}
    measurements: list[dict[str, object]] = []
    timing_unknown = model_timing_unknown
    kind = normalized_config['budget']['kind']
    latest_board = None
    sequence = 0
    for command in player_commands:
        if command.get('command') == 'showboard' and 'response' in command:
            latest_board = command['response']
        if not command.get('command', '').startswith('genmove '):
            continue
        if exempt_moves:
            continue
        event = move_events[sequence]
        response: list[str] | list[None] | str | None = command.get('response', [None])
        response = response[0] if response else None
        color = command['command'].split()[1]
        if (event.get('type') != 'genmove' or type(event.get('sequence')) is not int
                or event.get('sequence') != sequence
                or event.get('phase') != command.get('phase')
                or event.get('phase') not in ('normal_move', 'pass_probe', 'terminal_probe')
                or 'model_id' not in event or type(event.get('fallback')) is not bool
                or not _nonnegative_integer(event.get('model_load_elapsed_ns'))
                or event.get('command') != command.get('command')
                or event.get('color') != color or event.get('response') != response
                or latest_board is None or event.get('board_sha256') != board_sha256(latest_board)):
            return {'status': 'failed', 'reason': 'trace_response_mismatch_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements}
        if event.get('fallback') or event.get('model_id') != header.get('model_id'):
            return {'status': 'failed', 'reason': 'model_or_fallback_mismatch_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements}
        # 内部探索nsの検査。外部RTT期限はprotocol/contractsで別に確認する。
        elapsed = event.get('search_elapsed_ns')
        if type(elapsed) is not int or elapsed < 0:
            return {'status': 'failed', 'reason': 'invalid_search_elapsed_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements}
        item: dict[str, object] = {
            'sequence': sequence,
            'phase': event.get('phase'),
            'model_id': event.get('model_id'),
            'model_load_elapsed_ns': event.get('model_load_elapsed_ns'),
            'fallback': event.get('fallback'),
            'command': command['command'],
            'search_elapsed_ns': elapsed,
            'nodes': event.get('nodes'),
            'max_depth_started': event.get('max_depth_started'),
            'completed_depth': event.get('completed_depth'),
            'stop_reason': event.get('stop_reason'),
        }
        if kind == 'time' and elapsed > normalized_config['budget']['limit_ns']:
            return {'status': 'failed', 'reason': 'time_budget_exceeded_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements + [item]}
        if kind == 'nodes':
            nodes = event.get('nodes')
            if type(nodes) is not int or nodes < 0:
                return {'status': 'failed', 'reason': 'invalid_nodes_at_{}'.format(sequence),
                        'events': len(events), 'measurements': measurements + [item]}
            if nodes > normalized_config['budget']['limit_nodes']:
                return {'status': 'failed', 'reason': 'node_budget_exceeded_at_{}'.format(sequence),
                        'events': len(events), 'measurements': measurements + [item]}
        if kind == 'depth':
            depth = event.get('max_depth_started')
            if type(depth) is not int or depth < 0:
                return {'status': 'failed', 'reason': 'invalid_depth_at_{}'.format(sequence),
                        'events': len(events), 'measurements': measurements + [item]}
            if depth > normalized_config['budget']['limit_depth']:
                return {'status': 'failed', 'reason': 'depth_budget_exceeded_at_{}'.format(sequence),
                        'events': len(events), 'measurements': measurements + [item]}
        completed_depth = event.get('completed_depth')
        if completed_depth is not None and (type(completed_depth) is not int or completed_depth < 0):
            return {'status': 'failed', 'reason': 'invalid_completed_depth_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements + [item]}
        if not isinstance(event.get('stop_reason'), str) or not event['stop_reason']:
            return {'status': 'failed', 'reason': 'missing_stop_reason_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements + [item]}
        depth_limit = normalized_config['budget'].get('limit_depth')
        started_depth = event.get('max_depth_started')
        if started_depth is not None and (type(started_depth) is not int or started_depth < 0):
            return {'status': 'failed', 'reason': 'invalid_max_depth_started_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements + [item]}
        if depth_limit is not None:
            if type(started_depth) is not int or type(completed_depth) is not int:
                return {'status': 'failed', 'reason': 'depth_fields_required_at_{}'.format(sequence),
                        'events': len(events), 'measurements': measurements + [item]}
            if completed_depth > started_depth or started_depth > depth_limit:
                return {'status': 'failed', 'reason': 'depth_budget_exceeded_at_{}'.format(sequence),
                        'events': len(events), 'measurements': measurements + [item]}
        if (type(started_depth) is int and type(completed_depth) is int
                and completed_depth > started_depth):
            return {'status': 'failed', 'reason': 'depth_progression_invalid_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements + [item]}
        nodes = event.get('nodes')
        if nodes is not None and (type(nodes) is not int or nodes < 0):
            return {'status': 'failed', 'reason': 'invalid_nodes_at_{}'.format(sequence),
                    'events': len(events), 'measurements': measurements + [item]}
        if kind == 'unknown':
            return {'status': 'unknown', 'reason': 'budget_kind_unknown', 'events': len(events),
                    'measurements': measurements + [item]}
        timing = validate_search_interval(header, event, command)
        if timing['status'] == 'failed':
            return {'status': 'failed', 'reason': timing['reason'], 'events': len(events),
                    'measurements': measurements}
        if timing['status'] == 'unknown':
            timing_unknown = timing['reason']
        else:
            status, model_reason = _model_load_contract(
                event, event['command_received_ns'], event['response_ready_ns'],
                (event['search_start_ns'], event['search_end_ns']),
            )
            if status == 'failed':
                return {'status': 'failed', 'reason': model_reason, 'events': len(events), 'measurements': measurements}
            if status == 'unknown':
                timing_unknown = model_reason
            if header['format_version'] == TRACE_VERSION and event.get('model_load_status') == 'command':
                if cast(int, event['model_load_end_ns']) > cast(int, event['search_start_ns']):
                    return {'status': 'failed', 'reason': 'model_load_after_use',
                            'events': len(events), 'measurements': measurements}
            item.update({key: event[key] for key in ('command_index', 'command_id',
                        'command_received_ns', 'search_start_ns', 'search_end_ns', 'response_ready_ns')})
        measurements.append(item)
        sequence += 1
    if kind == 'unknown':
        return {'status': 'unknown', 'reason': 'budget_kind_unknown', 'events': len(events),
                'measurements': measurements}
    if header.get('format_version') == 2:
        timing_unknown = 'legacy_search_interval_unavailable'
    if timing_unknown:
        return {'status': 'unknown', 'reason': timing_unknown, 'events': len(events), 'measurements': measurements}
    return {'status': 'exempt' if exemption == 'exempt' and purpose != 'probe' else 'verified', 'reason': None,
            'events': len(events), 'measurements': measurements}


def validate_trace_events(events: list[object] | None, config: object, player_commands: Sequence[_CommandRecord],
                          target_sha256: str, expected_identity: Mapping[str, object] | None = None,
                          allow_exemption: bool = False, purpose: str = 'game') -> _TraceValidation:
    """live/reload共通のtrace意味検証を行い、例外もfailedの理由辞書へ変換する。

    入力を変更せず、ファイルやprocessを操作しない。Noneはtrace欠損を表す。
    """
    try:
        if events is not None:
            canonical_json(events)
        return _validate_trace_events(
            events, config, player_commands, target_sha256, expected_identity, allow_exemption, purpose,
        )
    except Exception as error:
        return {
            'status': 'failed', 'reason': 'trace_validation_error:{}'.format(error),
            'events': 0, 'measurements': [],
        }


def validate_trace(path: str | PathLike[str], config: object, player_commands: Sequence[_CommandRecord],
                   target_sha256: str, expected_identity: Mapping[str, object] | None = None,
                   allow_exemption: bool = False) -> _TraceValidation:
    """設定とUTF-8 JSONLファイルを読み、共通event検証へ接続する取得時wrapper。

    none/unknownでは予算traceを読まず、除外許可またはunknownを返す。
    保存済み正式報告の再利用時は、contracts側でartifact hashと棋譜も照合する。
    """
    try:
        normalize_config(config)
        trace_path = Path(path)
        events = None if not trace_path.is_file() else [
            json.loads(line) for line in trace_path.read_bytes().decode('utf-8').splitlines() if line.strip()
        ]
        return validate_trace_events(
            events, config, player_commands, target_sha256, expected_identity, allow_exemption,
        )
    except (OSError, ValueError, TypeError) as error:
        return {'status': 'failed', 'reason': 'trace_invalid:{}'.format(error),
                'events': 0, 'measurements': []}


def compatible_budgets(config_a: object, config_b: object, policy: str) -> tuple[bool, str | None]:
    """正規化要求がpolicyに合うかを(可否, 理由)で返す。

    equal-timeは同じns上限、equal-workはkind/上限とnodesのcounting定義を照合する。
    実効trace・時計/scope・baseline本体hashの検証は別処理で必要。未知policyはValueError。
    """
    budget_a = normalize_config(config_a)['budget']
    budget_b = normalize_config(config_b)['budget']
    if policy == 'descriptive':
        return True, None
    if policy == 'equal-time':
        valid = budget_a.get('kind') == budget_b.get('kind') == 'time'
        valid = valid and budget_a.get('limit_ns') == budget_b.get('limit_ns')
        return valid, None if valid else 'equal_time_requires_same_time_limit'
    if policy == 'equal-work':
        kind = budget_a.get('kind')
        valid = kind in ('nodes', 'depth') and kind == budget_b.get('kind')
        valid = valid and budget_a == budget_b
        if kind == 'nodes':
            definition_a = normalize_config(config_a)['search'].get('counting_definition_id')
            definition_b = normalize_config(config_b)['search'].get('counting_definition_id')
            valid = valid and isinstance(definition_a, str) and bool(definition_a)
            valid = valid and definition_a == definition_b
        return valid, None if valid else 'equal_work_requires_same_kind_limit_and_definition'
    if policy == 'baseline-asymmetric':
        valid = budget_b.get('kind') == 'none' and bool(budget_b.get('exemption'))
        valid = valid and budget_a.get('kind') in ('time', 'nodes', 'depth')
        if budget_a.get('kind') == 'nodes':
            definition = normalize_config(config_a)['search'].get('counting_definition_id')
            valid = valid and isinstance(definition, str) and bool(definition)
        return valid, None if valid else 'baseline_asymmetric_requires_candidate_budget_and_verified_none_baseline'
    raise ValueError('unknown budget policy')
