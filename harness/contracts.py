"""評価報告の共通契約と原証跡の再検証入口。
盤面はrows[y][x]のB/W/E、着手は(x, y)、passはNone、A/Bは候補役割。
"""

from __future__ import annotations

import hashlib
import copy
import json
import math
import sys
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from os import PathLike
    from typing import Literal, TypeGuard, TypedDict
    from harness.openings import _OpeningEntry
    from harness.protocol import _CommandRecord
    from harness.protocol import _HostEvidence, _Status, _TemporalReason, _TemporalResult
    from harness.referee import Position
    from harness.search_budget import _BudgetCommand, _TraceValidation

    _Role = Literal['A', 'B']
    _Record = dict[str, object]
    _Cache = dict[Path, bytes | OSError]
    _Requirement = tuple[str, list[str] | None, str | None, float | None]
    _BudgetState = Literal['verified', 'unknown', 'failed', 'exempt']
    _ComponentState = Literal['verified', 'unknown', 'failed', 'not_required', 'complete']

    class _RawBudgetConfig(TypedDict, total=False):
        """通常の予算設定容器。上限やsearch metadataの原値はobjectであり別途検査する。"""

        format_version: int
        budget: _Record
        search: _Record

    class _Artifact(TypedDict, total=False):
        """保存参照の通常形状。hashや存在宣言は原bytesの検証に代わらない。"""

        path: str
        sha256: str
        exists: bool
        format_version: int

    class _SavedTrace(TypedDict, total=False):
        """保存cacheの通常形状。状態・測定値を認可せず毎回原traceと照合する。"""

        status: str
        reason: str | None
        measurements: list[_Record]
        artifact: _Artifact

    class _DiagnosticValidation(TypedDict, total=False):
        """取得保留/失敗や読込不能時の部分結果。欠損events/測定値を補造しない。"""

        status: _BudgetState
        reason: str | None
        measurements: list[_Record]

    _Validation = _TraceValidation | _DiagnosticValidation

    class _Player(_HostEvidence, total=False):
        """通常の親記録と保存cache。型は時刻/通信/予算の検証済み証明ではない。"""

        config: object
        budget_trace: _SavedTrace
        launch_status: str
        stderr_tail: list[str]

    class _Move(TypedDict, total=False):
        """保存棋譜の通常形状。座標の長さ/値域・手番/合法性は再生時に検査する。"""

        color: str
        player: str
        position: Sequence[int] | None
        board: Sequence[str]
        seconds: float

    class _Game(TypedDict, total=False):
        """通常の一局記録。異常入力の検査を省略するための型ではない。"""

        case_id: str
        pair_id: str
        leg: int
        seeds: dict[str, int]
        colors: dict[str, str]
        players: dict[str, _Player]
        opening_moves: list[_Move]
        opening_start_board: Sequence[str]
        opening_to_move: str
        moves: list[_Move]
        final_board: Sequence[str]
        game_status: str
        protocol_status: str
        budget_status: str
        budget: dict[str, _SavedTrace]
        winner: str | None
        result: str | None
        score_a: float
        error: _Record | None

    class _Conditions(TypedDict, total=False):
        """通常の取得条件。未知fieldの値域や旧形式の不足を型だけで認定しない。"""

        evaluation_profile: object
        budget_policy: str
        games: int
        planned_games: int
        seed: object
        determinism_verified: bool
        budget_configs: dict[str, _RawBudgetConfig]
        budget_config_hashes: dict[str, object]
        selection_manifest: _Manifest | None
        opening_population_hash: str | None
        opening_population_size: object
        startup_timeout: float
        command_timeout: float
        genmove_timeout: float
        measurement_contract: object
        candidate_search_contract: _Record

    class _Manifest(TypedDict, total=False):
        """抽出manifestの通常形状。母集合/網羅性や整数fieldの検証は別途行う。"""

        selected_case_ids: list[str]
        selection_seed: object
        format_version: object
        population_size: object
        sample_size: object

    class _Probe(TypedDict, total=False):
        """決定性probeの通常記録。各原試行とrawのfresh照合が別途必要。"""

        status: str
        repetitions: int
        outputs: list[str]
        trace_artifacts: list[_Artifact]
        attempts: list[_Player]
        target_sha256: str
        config_sha256: str
        case_id: str
        seed: int

    class _Runtime(TypedDict, total=False):
        """通常の実行環境。時計/環境の対応可能性を宣言だけで保証しない。"""

        python: str
        clock: object

    class _Target(TypedDict, total=False):
        """対象本体/資産の通常参照。固定Bやassetの同一性は別条件で検査する。"""

        sha256: str
        assets: list[_Artifact]

    class _Evidence(TypedDict, total=False):
        """原証跡の通常保存先。移動先指定でも元資料の内容hashは変えない。"""

        root: str
        population: _Artifact

    class _RoleMetrics(TypedDict, total=False):
        """生成する参考集計。整数ns・RTT秒と両分母を区別する。"""

        wins: int
        losses: int
        draws: int
        win_rate: float | None
        point_rate: float | None
        genmove_samples: int
        mean_genmove_seconds: float | None
        max_genmove_seconds: float | None
        search_samples: int
        search_metric_status: str
        search_sum_ns: int | float
        search_max_ns: int | float | None
        normal_search_sum_ns: int | float
        normal_search_max_ns: int | float | None
        mean_search_seconds: float | None
        max_search_seconds: float | None
        normal_search_samples: int
        mean_normal_search_seconds: float | None
        max_normal_search_seconds: float | None
        black_games: int
        white_games: int

    class _Summary(TypedDict, total=False):
        """構築中も含む参考集計。正式資格を発行する構造ではない。"""

        attempted: int
        completed: int
        failed: int
        failures: dict[str, int]
        A: _RoleMetrics
        B: _RoleMetrics

    class _SourceReport(TypedDict, total=False):
        """通常入力の記録形状だけを記述する。原値を変換せず不正入力も実行時に検査する。"""

        schema_version: int
        report_kind: str
        run_id: str
        clock_domain_id: object
        conditions: _Conditions
        runtime: _Runtime
        targets: dict[str, _Target]
        games: list[_Game]
        status: _Record
        summary: _Summary
        determinism: dict[str, _Probe]
        evidence: _Evidence
        population: _Record
        harness: list[_Artifact]
        experiment_id: str
        evaluation_fingerprint: object
        verification_requirements: _Record
        analysis_spec: _Record

    class _Decoded(TypedDict):
        """入力検査の状態と原出典hash。conditionsの未知値を有効な数値に絞らない。"""

        input_status: Literal['invalid', 'unsupported', 'valid']
        requested_profile: object
        conditions: _Record
        source_schema: object
        source_report_sha256: str | None
        issues: list[str]

    class _Requirements(TypedDict):
        """既知profile/policyから導出した検証集合。rawの任意除外は参照しない。"""

        roles: tuple[Literal['A'], Literal['B']]
        budget_required: bool
        formal_requested: bool
        baseline_exemption_allowed: bool

    class _Resolution(TypedDict):
        """局数指定と予定数。省略と明示指定、由来を区別する。"""

        games_explicit: bool
        requested_games: int | None
        resolved_games: int
        resolution: str

    class _Axis(TypedDict, total=False):
        """生成途中の検証軸。not_requiredとverifiedを同一視しない。"""

        status: _ComponentState
        verified_N: int | None
        case_ids: list[str]
        population_hash: str
        planned_n: int | None
        selected_case_ids: list[str]
        per_leg_results: list[_Record]
        observed_pair_legs: list[list[object]]

    class _LocatedReason(_TemporalReason, total=False):
        """基礎時間理由へ診断detailと原slot情報を保持する。黙ってキーを削除しない。"""

        detail: str
        run_id: object
        pair_id: object
        case_id: object
        leg: object
        player: object
        process_session_id: object
        command_id: object

    class _Timing(_Axis, total=False):
        """4時間軸の通常結果と理由。各軸の失敗/不明を全体へ伝播する。"""

        reasons: Sequence[Mapping[str, object]]
        host_lifecycle: _TemporalResult
        child_lifecycle: _TemporalResult
        command_intervals: _TemporalResult
        causal_order: _TemporalResult
        lifecycle: _TemporalResult

    class _Derived(TypedDict, total=False):
        """派生値。failed/unknown時のgame内容を有効な型へ過剰保証しない。"""

        games: object
        qualified_pair_scores: dict[str, object]
        coverage_kind: str
        validated_pair_count: int

    class _Inspection(TypedDict, total=False):
        """fresh検証の生成途中と返却形状。snapshotや未知metadataはobjectとして扱う。"""

        validation_contract_version: int
        validator_fingerprint: str
        status: _Status
        reasons: list[Mapping[str, object]]
        integrity_errors: list[str]
        population: _Axis
        selection: _Axis
        coverage: _Axis
        gameplay: _Axis
        budget: _Axis
        input_status: str
        source_consistency: _Axis
        timing: _Timing
        execution: _Axis
        protocol: _Axis
        derived: _Derived
        decoded: _Decoded
        source_report_sha256: str | None
        snapshot: Mapping[str, object]
        canonical_summary: _Summary
        derived_measurements: list[_Record]
        metrics: _Axis

    class _GameIdentity(TypedDict):
        """原gameの位置情報だけを持つ。reasonのcode/location/statusと混同しない。"""

        case_id: str | None
        pair_id: str | None
        leg: int | None

    _MeasurementSeries = dict[tuple[str | None, int | None], dict[str, _Validation]]


def _number(value: object) -> TypeGuard[int | float]:
    """既存の厳密int/float membershipを表す。bool・変換は許可しない。"""
    return type(value) is int or type(value) is float


def _nonnegative_number(value: object) -> TypeGuard[int | float]:
    """従来どおり型・有限性・非負を順に確認し、未知値を補完しない。"""
    return _number(value) and math.isfinite(value) and value >= 0


def _positive_number(value: object) -> TypeGuard[int | float]:
    """既存の有限正数期限を表し、boolや非有限値を有効な期限へ変換しない。"""
    return _number(value) and math.isfinite(value) and value > 0


def _get_container[Value](value: dict[str, Value] | None) -> dict[str, Value]:
    """元のget属性参照と同じNone例外を送り、同じ容器を返す。採時や補修はしない。"""
    if value is None:
        raise AttributeError("'NoneType' object has no attribute 'get'", name='get', obj=value)
    return value


class ArtifactWriteError(RuntimeError):
    """開始後の証跡/結果保存失敗。入力不正と区別し、phase・path・原原因を保持する。"""
    def __init__(self, phase: str, path: str | PathLike[str], operation_id: str | None,
                 cause: BaseException) -> None:
        """診断情報を保持する。既存ファイルを削除したり再試行で上書きしたりしない。"""
        self.phase = phase
        self.path = str(path)
        self.operation_id = operation_id
        self.cause = cause
        super().__init__('artifact_save_failed phase={} path={} operation={} cause={}: {}'.format(
            phase, path, operation_id, type(cause).__name__, cause))


def write_report_exclusive(path: str | PathLike[str], report: object, phase: str = 'final',
                            operation_id: str | None = None) -> dict[str, str | None]:
    """新しいJSONを排他的に保存してflush/fsyncする。開始後の失敗は運用例外とする。

    serializeを先に行い、既存出力やrace勝者は上書きしない。途中書込の原子性までは保証しない。
    """
    path = Path(path)
    try:
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as writer:
            writer.write(encoded)
            writer.flush()
            os.fsync(writer.fileno())
    except (OSError, ValueError, TypeError, OverflowError) as error:
        raise ArtifactWriteError(phase, path, operation_id, error) from error
    return {'publication': 'saved', 'path': str(path), 'operation_id': operation_id}


def _same_board(first: object, second: object) -> bool:
    """盤面表現のlist/tuple差を吸収して比較し、反復不能な値ならFalseを返す。"""
    try:
        return tuple(cast('Iterable[object]', first)) == tuple(cast('Iterable[object]', second))
    except TypeError:
        return False


def _replay_game(game: _Game, opening: _OpeningEntry | None) -> bool:
    """保存棋譜を独立refereeで再生し、手番・合法手/pass・記録盤面の整合を判定する。

    rows[y][x]はB/W/E、着手は(x, y)、passはNone。opening指定時は開始履歴も
    照合する。入力を変更せず、通信・raw・得点の完全検証は他の検査と組み合わせる。
    """
    from harness import referee

    rows = tuple(referee.INITIAL_ROWS)
    color = 'B'
    for move in game.get('opening_moves', []):
        if not isinstance(move, dict) or move.get('color') != color:
            return False
        position = move.get('position')
        legal = referee.legal_moves(rows, color)
        if position is None:
            if legal or not referee.legal_moves(rows, 'W' if color == 'B' else 'B'):
                return False
        else:
            position = tuple(position)
            if position not in legal:
                return False
            rows = referee.apply_move(rows, color, position)
        color = 'W' if color == 'B' else 'B'
    if not _same_board(rows, game.get('opening_start_board')) or color != game.get('opening_to_move'):
        return False
    if opening is not None and (
            game.get('opening_moves') != opening.get('moves')
            or not _same_board(game.get('opening_start_board'), opening.get('board'))
            or game.get('opening_to_move') != opening.get('to_move')):
        return False
    colors = game.get('colors', {})
    for move in game.get('moves', []):
        if not isinstance(move, dict) or move.get('color') != color:
            return False
        if move.get('player') != colors.get(color):
            return False
        legal = referee.legal_moves(rows, color)
        position = move.get('position')
        if position is None:
            if legal or referee.result(rows) != 'NG':
                return False
        else:
            position = tuple(position)
            if position not in legal:
                return False
            rows = referee.apply_move(rows, color, position)
        if not _same_board(rows, move.get('board')):
            return False
        color = 'W' if color == 'B' else 'B'
    if game.get('game_status') == 'complete' and not _same_board(rows, game.get('final_board')):
        return False
    return True


def canonical_json(value: object) -> str:
    """ID生成用のキー順固定・余分な空白なしのJSON文字列を返す。NaN等は許可しない。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: object) -> str:
    """canonical JSONをUTF-8化したSHA-256を返す。内容のhashであり真正性の証明ではない。"""
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def seed_for(seed: object, case_id: object, role: str, repeat_index: int = 0) -> int:
    """master seed・履歴込みcase ID・候補役割A/B・反復番号から役割seedを導出する。

    先後交換では色ではなく役割に同じseedを割り当てる。乱数源の実際の利用は保証しない。
    """
    return int(digest({
        'seed_version': 1,
        'master_seed': seed,
        'case_id': case_id,
        'role': role,
        'repeat_index': repeat_index,
    })[:16], 16)


def pair_id_for(case_id: object, seeds: Mapping[str, object]) -> str:
    """case IDとA/Bのseedを結ぶ先後交換ペアIDを返す。run・leg・AI版・保存先を含めない。"""
    return digest({
        'pair_version': 1,
        'case_id': case_id,
        'seeds': seeds,
        'role_swap_version': 1,
    })


def decode_input(report: object) -> _Decoded:
    """原JSONの型・版・要求profileを検査し、利用可能な条件だけを返す。

    不正なrawをconsumer用辞書として扱わない。非有限値やJSON外の値も拒否する。
    旧schemaの予算設定は欠落を許すが、存在する設定は新形式と同じ規則で検査する。
    """
    result: _Decoded = {'input_status': 'invalid', 'requested_profile': None, 'conditions': {},
              'source_schema': None, 'source_report_sha256': None, 'issues': []}

    def json_value(value: object) -> bool:
        """文字列キーとJSONの有限値だけからなる再帰構造か確認する。"""
        if value is None or type(value) in (str, bool, int):
            return True
        if type(value) is float:
            return math.isfinite(value)
        if isinstance(value, (list, tuple)):
            return all(json_value(item) for item in value)
        if isinstance(value, dict):
            return all(type(key) is str and json_value(item) for key, item in value.items())
        return False

    if not isinstance(report, dict) or not json_value(report):
        result['issues'] = ['report_not_json_object']
        return result
    if 'validation_contract_version' in report and 'decoded' in report:
        result['issues'] = ['inspection_is_not_source_report']
        return result
    result['source_report_sha256'] = digest(report)
    version = report.get('schema_version', 1)
    result['source_schema'] = version
    if type(version) is not int:
        result['issues'] = ['schema_type_invalid']
        return result
    if version not in (1, 2, 3, 4, 5):
        result.update({'input_status': 'unsupported', 'issues': ['unsupported_schema_version']})
        return result
    if report.get('report_kind', 'evaluation') != 'evaluation':
        result['issues'] = ['report_kind_mismatch']
        return result
    if 'summary' in report and not isinstance(report['summary'], dict):
        result['issues'] = ['summary_not_object']
        return result
    for key in ('targets', 'runtime', 'population', 'evidence', 'request', 'status', 'analysis_spec'):
        if key in report and not isinstance(report[key], dict):
            result['issues'] = [key + '_not_object']
            return result
    for key in ('A', 'B'):
        if key in report.get('summary', {}) and not isinstance(report['summary'][key], dict):
            result['issues'] = ['summary_role_not_object']
            return result
        target = report.get('targets', {}).get(key, {})
        if not isinstance(target, dict) or not isinstance(target.get('assets', []), list) or any(
                not isinstance(asset, dict) for asset in target.get('assets', [])):
            result['issues'] = ['target_role_invalid']
            return result
    if not isinstance(report.get('harness', []), list) or any(
            not isinstance(item, dict) for item in report.get('harness', [])):
        result['issues'] = ['harness_manifest_invalid']
        return result
    verification = report.get('verification_requirements', {})
    if not isinstance(verification, dict) or type(verification.get('require_budget_verification', False)) is not bool:
        result['issues'] = ['verification_requirements_invalid']
        return result
    conditions = report.get('conditions', {})
    if not isinstance(conditions, dict):
        result['issues'] = ['conditions_not_object']
        return result
    if 'budget_config_hashes' in conditions and not isinstance(conditions['budget_config_hashes'], dict):
        result['issues'] = ['budget_config_hashes_invalid']
        return result
    profile = conditions.get('evaluation_profile')
    if version >= 3:
        if conditions.get('selection_manifest') is not None and not isinstance(conditions['selection_manifest'], dict):
            result['issues'] = ['selection_manifest_invalid']
            return result
        if profile not in ('smoke', 'descriptive', 'deployment', 'confirmatory'):
            result['issues'] = ['evaluation_profile_invalid']
            return result
    # 旧形式でも存在する不正設定を比較側へ渡さず、欠落とは区別する。
    if version >= 3 or 'budget_configs' in conditions:
        from harness.search_budget import normalize_config
        configs = conditions.get('budget_configs')
        if (not isinstance(configs, dict) or not set(configs).issubset({'A', 'B'})
                or (version >= 3 and set(configs) != {'A', 'B'})):
            result['issues'] = ['budget_configs_invalid']
            return result
        try:
            for config in configs.values():
                normalize_config(config)
        except (ValueError, TypeError, AttributeError):
            result['issues'] = ['budget_config_invalid']
            return result
    if version >= 3:
        if (not isinstance(report.get('games'), list)
                or any(not isinstance(game, dict) for game in report['games'])):
            result['issues'] = ['games_not_object_array']
            return result
        specification = report.get('analysis_spec', {})
        if not isinstance(specification, dict):
            result['issues'] = ['analysis_spec_not_object']
            return result
        confidence = specification.get('confidence', 0.95)
        if type(confidence) not in (int, float) or not 0 < confidence < 1:
            result['issues'] = ['confidence_invalid']
            return result
        if specification.get('method', 'hoeffding_serfling_finite_population') != 'hoeffding_serfling_finite_population':
            result.update({'input_status': 'unsupported', 'issues': ['unsupported_analysis_method']})
            return result
        if specification.get('estimand', 'mean_A_pair_score_on_fixed_opening_population') != 'mean_A_pair_score_on_fixed_opening_population':
            result['issues'] = ['analysis_estimand_invalid']
            return result
        if specification.get('primary_claim', 'candidate_mean_score_above_half') not in (
                'candidate_mean_score_above_half', 'not_requested'):
            result['issues'] = ['analysis_primary_claim_invalid']
            return result
    result.update({'input_status': 'valid', 'requested_profile': profile, 'conditions': copy.deepcopy(conditions)})
    return result


def verification_requirements(conditions: Mapping[str, object], require_budget: bool = False) -> _Requirements:
    """既知profile/policyから必要roleと検証軸を導く。rawの任意除外宣言は読まない。"""
    strict = conditions.get('evaluation_profile') in ('confirmatory', 'deployment')
    return {'roles': ('A', 'B'), 'budget_required': bool(strict or require_budget),
            'formal_requested': conditions.get('evaluation_profile') == 'confirmatory',
            'baseline_exemption_allowed': conditions.get('budget_policy') == 'baseline-asymmetric'}


def aggregate_role_budget(states: Mapping[str, object], conditions: Mapping[str, object]) -> _Status:
    """必要A/Bを集約する。欠損はunknown、Bだけの許可exemptは全体verifiedとする。"""
    values = []
    for label in verification_requirements(conditions)['roles']:
        state = states.get(label, 'unknown')
        if state == 'exempt' and not (label == 'B' and conditions.get('budget_policy') == 'baseline-asymmetric'):
            state = 'failed'
        values.append(state if state in ('verified', 'exempt', 'failed') else 'unknown')
    return 'failed' if 'failed' in values else 'unknown' if 'unknown' in values else 'verified'


def aggregate_game_budget(states: Sequence[object], planned_games: object) -> _Status:
    """全予定gameの予算状態を集約する。空集合や不足をexempt/verifiedへ昇格しない。"""
    if 'failed' in states or 'exempt' in states:
        return 'failed'
    if not states or len(states) != planned_games or any(state != 'verified' for state in states):
        return 'unknown'
    return 'verified'


def resolve_game_count(requested: int | None, profile: str, population_size: int) -> _Resolution:
    """局数指定とprofile・母集合件数から予定局数と指定由来を返す。

    母集合未使用は0を想定する。正式評価の省略、奇数、母集合超過等はValueErrorとする。
    """
    explicit = requested is not None
    if explicit:
        if type(requested) is not int or requested < 2 or requested % 2:
            raise ValueError('games must be a positive even number (at least 2)')
        resolved = requested
        reason = 'explicit'
    elif profile == 'confirmatory':
        raise ValueError('confirmatory profile requires explicit games')
    else:
        resolved = population_size * 2 if population_size else 2
        reason = 'all_openings' if population_size else 'default_initial_pair'
    if population_size and resolved // 2 > population_size:
        raise ValueError('requested games exceed opening population size')
    return {
        'games_explicit': explicit, 'requested_games': requested,
        'resolved_games': resolved, 'resolution': reason,
    }


def _artifact_bytes(report: _SourceReport, artifact: _Artifact, cache: _Cache) -> bytes:
    """証跡を読み、解析一呼出内のcacheへbytesまたはOSErrorを保存する。

    同じ実パスを再読込せず、hash照合とparseに同じbytesを使う。元reportは変更しない。
    """
    path = (Path(report.get('evidence', {}).get('root', '')) / artifact['path']).resolve()
    if path not in cache:
        try:
            cache[path] = path.read_bytes()
        except OSError as error:
            cache[path] = error
    value = cache[path]
    if isinstance(value, OSError):
        raise value
    return value


def _validate_report(report: _SourceReport, evidence_bytes: _Cache | None = None,
                       validate_summary: bool = True) -> list[str]:
    """報告の版・ID・予定coverage・棋譜・保存集計・証跡hashの不整合コードを返す。

    v1/v2は互換用に空配列を返す。rawの意味検証はinspect_evaluationで補うため、
    空配列だけでは正式資格を保証しない。evidence_bytesは一呼出内の読込cache。
    """
    evidence_bytes = {} if evidence_bytes is None else evidence_bytes
    if not isinstance(report, dict):
        return ['report_not_object']
    version = report.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2, 3, 4, 5):
        return ['unsupported_schema_version']
    if version < 3:
        return []
    errors = []
    key: tuple[str | None, int] | str
    expected_value: object
    seeds: dict[str, int] | None
    artifact: _Artifact | None
    repeat_index: object
    case_id: str | None
    if report.get('report_kind') != 'evaluation':
        errors.append('report_kind_mismatch')
    conditions = report.get('conditions')
    games = report.get('games')
    if not isinstance(conditions, dict) or not isinstance(games, list):
        return errors + ['required_fields_invalid']
    targets = report.get('targets')
    if not isinstance(targets, dict) or not isinstance(report.get('runtime'), dict):
        errors.append('experiment_inputs_missing')
    else:
        expected_experiment_id = digest({
            'conditions': conditions,
            'runtime': report['runtime'],
            'targets': {label: {
                'sha256': info.get('sha256'),
                'assets': [asset.get('sha256') for asset in info.get('assets', [])],
            } for label, info in targets.items()},
            'evaluation_fingerprint': report.get('evaluation_fingerprint'),
        })
        if report.get('experiment_id') != expected_experiment_id:
            errors.append('experiment_id_mismatch')
    if conditions.get('evaluation_profile') == 'confirmatory':
        checks = report.get('determinism')
        if not conditions.get('determinism_verified') or not isinstance(checks, dict):
            errors.append('determinism_not_verified')
        else:
            for label in ('A', 'B'):
                check = checks.get(label, {})
                if (check.get('status') != 'verified' or check.get('repetitions') != 2
                        or not isinstance(check.get('outputs'), list)
                        or len(check['outputs']) != 2 or check['outputs'][0] != check['outputs'][1]
                        or check.get('target_sha256') != _get_container(targets).get(label, {}).get('sha256')
                        or check.get('config_sha256') != conditions.get('budget_config_hashes', {}).get(label)):
                    errors.append('determinism_evidence_invalid')
                    continue
                artifacts = check.get('trace_artifacts', [])
                if len(artifacts) != 2:
                    errors.append('determinism_trace_missing')
                    continue
                for artifact in artifacts:
                    try:
                        actual = hashlib.sha256(_artifact_bytes(report, artifact, evidence_bytes)).hexdigest()
                    except (OSError, KeyError, TypeError, ValueError):
                        errors.append('determinism_trace_missing')
                    else:
                        if actual != artifact.get('sha256'):
                            errors.append('determinism_trace_hash_mismatch')
    planned = conditions.get('planned_games')
    if type(planned) is not int or planned < 2 or planned % 2:
        errors.append('planned_games_invalid')
        return errors
    manifest = conditions.get('selection_manifest')
    selected_cases = None
    expected_openings = {}
    if manifest is not None:
        if not isinstance(manifest, dict):
            errors.append('selection_manifest_invalid')
        else:
            selected_cases = manifest.get('selected_case_ids')
            if (not isinstance(selected_cases, list)
                    or len(selected_cases) != planned // 2
                    or len(set(selected_cases)) != len(selected_cases)):
                errors.append('selection_manifest_invalid')
            else:
                evidence = report.get('evidence', {})
                population_artifact = evidence.get('population') if isinstance(evidence, dict) else None
                if not isinstance(population_artifact, dict):
                    errors.append('population_artifact_missing')
                else:
                    try:
                        population_bytes = _artifact_bytes(report, population_artifact, evidence_bytes)
                        if hashlib.sha256(population_bytes).hexdigest() != population_artifact.get('sha256'):
                            errors.append('population_artifact_hash_mismatch')
                        else:
                            from harness.openings import select_entries, validate_population
                            population = validate_population(json.loads(population_bytes.decode('utf-8')))
                            if population.get('population_hash') != conditions.get('opening_population_hash'):
                                errors.append('population_identity_mismatch')
                            runtime_version = report.get('runtime', {}).get('python', '').split()[0]
                            if runtime_version.split('.')[:2] == sys.version.split()[0].split('.')[:2]:
                                selected, reproduced_manifest = select_entries(
                                    population, planned // 2, cast(int, manifest.get('selection_seed')),
                                )
                                manifest_view: Mapping[str, object] = manifest
                                if reproduced_manifest != manifest_view:
                                    errors.append('sampling_reproduction_mismatch')
                                expected_openings = {entry['case_id']: entry for entry in selected}
                    except (OSError, KeyError):
                        errors.append('population_artifact_missing')
                    except (ValueError, TypeError, json.JSONDecodeError):
                        errors.append('population_artifact_invalid')
    elif conditions.get('evaluation_profile') == 'confirmatory':
        errors.append('selection_manifest_missing')
    expected_pairs: dict[str, tuple[object, dict[str, int]]] = {}
    expected: dict[str, tuple[object, dict[str, int]]] | None = expected_pairs
    if isinstance(selected_cases, list):
        for case_index, case_id in enumerate(selected_cases):
            seeds = {label: seed_for(conditions.get('seed'), case_id, label)
                     for label in ('A', 'B')}
            expected_pairs[pair_id_for(case_id, seeds)] = (case_id, seeds)
    else:
        from harness import openings, referee
        initial_case = openings.case_id([], referee.INITIAL_ROWS, 'B')
        if conditions.get('opening_population_hash') is None:
            for repeat_index in range(planned // 2):
                seeds = {label: seed_for(conditions.get('seed'), initial_case, label, repeat_index)
                         for label in ('A', 'B')}
                expected_pairs[pair_id_for(initial_case, seeds)] = (initial_case, seeds)
        elif planned == 2:
            expected = None
        else:
            errors.append('expected_cases_unavailable')
    seen = set()
    for game in games:
        if not isinstance(game, dict):
            errors.append('game_record_invalid')
            continue
        pair = game.get('pair_id')
        leg = game.get('leg')
        if type(leg) is not int or leg not in (0, 1):
            errors.append('game_leg_invalid')
            continue
        key = (pair, leg)
        if key in seen:
            errors.append('duplicate_pair_leg')
        seen.add(key)
        seeds = game.get('seeds')
        colors = game.get('colors')
        if not isinstance(seeds, dict) or set(seeds) != {'A', 'B'}:
            errors.append('game_seeds_invalid')
            continue
        case_id = game.get('case_id')
        expected_opening = expected_openings.get(cast(str, case_id))
        if expected_opening is not None:
            if (game.get('opening_moves') != expected_opening.get('moves')
                    or game.get('opening_start_board') != expected_opening.get('board')
                    or game.get('opening_to_move') != expected_opening.get('to_move')):
                errors.append('opening_history_mismatch')
        elif conditions.get('opening_population_hash') is None:
            if (game.get('opening_moves') != []
                    or game.get('opening_start_board') != report.get('population', {}).get('initial_board')
                    or game.get('opening_to_move') != 'B'):
                errors.append('opening_history_mismatch')
        if not _replay_game(game, expected_opening):
            errors.append('game_history_or_board_mismatch')
        repeat_index = game.get('repeat_index')
        if type(repeat_index) is not int or repeat_index < 0:
            errors.append('repeat_index_invalid')
        else:
            expected_seeds = {
                label: seed_for(conditions.get('seed'), case_id, label, repeat_index)
                for label in ('A', 'B')
            }
            if seeds != expected_seeds:
                errors.append('seed_derivation_mismatch')
        if pair != pair_id_for(case_id, seeds):
            errors.append('pair_id_mismatch')
        if expected is not None:
            if pair not in expected:
                errors.append('unexpected_pair')
                continue
            expected_case, expected_seeds = expected[pair]
            if case_id != expected_case or seeds != expected_seeds:
                errors.append('pair_identity_mismatch')
        if not isinstance(colors, dict) or colors != ({'B': 'A', 'W': 'B'} if leg == 0 else {'B': 'B', 'W': 'A'}):
            errors.append('role_assignment_mismatch')
        final_board = game.get('final_board')
        if not isinstance(final_board, (list, tuple)) or len(final_board) != 8:
            errors.append('final_board_invalid')
        else:
            from harness.referee import result as board_result
            outcome = board_result(tuple(final_board))
            if game.get('game_status') == 'complete' and outcome != game.get('result'):
                errors.append('terminal_result_mismatch')
            winner = None if outcome == 'D' else (colors or {}).get(outcome)
            expected_score = 0.5 if outcome == 'D' else float(winner == 'A')
            if game.get('game_status') == 'complete' and (
                    game.get('winner') != winner or game.get('score_a') != expected_score):
                errors.append('score_mismatch')
        for label, player in (game.get('players') or {}).items():
            trace = player.get('budget_trace', {}) if isinstance(player, dict) else {}
            artifact = trace.get('artifact') if isinstance(trace, dict) else None
            if trace.get('status') in ('verified', 'failed'):
                if not isinstance(artifact, dict) or not artifact.get('exists'):
                    errors.append('trace_artifact_missing')
                else:
                    try:
                        actual = hashlib.sha256(_artifact_bytes(report, artifact, evidence_bytes)).hexdigest()
                    except (OSError, KeyError, TypeError, ValueError):
                        errors.append('trace_artifact_missing')
                    else:
                        if actual != artifact.get('sha256'):
                            errors.append('trace_artifact_hash_mismatch')
    if len(games) != planned:
        errors.append('planned_game_coverage_mismatch')
    if expected is not None:
        expected_keys = {(pair, leg) for pair in expected for leg in (0, 1)}
        if seen != expected_keys:
            errors.append('planned_pair_coverage_mismatch')
    summary = report.get('summary')
    status = report.get('status')
    if isinstance(status, dict):
        expected_execution = 'complete' if all(
            game.get('game_status') == 'complete' for game in games if isinstance(game, dict)
        ) and len(games) == planned else 'failed'
        expected_protocol = 'verified' if all(
            game.get('protocol_status') == 'verified' for game in games if isinstance(game, dict)
        ) and len(games) == planned else 'failed'
        budget_states = [game.get('budget_status') for game in games if isinstance(game, dict)]
        expected_budget = aggregate_game_budget(budget_states, planned)
        if (status.get('execution') != expected_execution
                or status.get('protocol') != expected_protocol
                or status.get('budget') != expected_budget):
            errors.append('status_aggregate_mismatch')
    else:
        errors.append('status_missing')
    if not validate_summary:
        return sorted(set(errors))
    if not isinstance(summary, dict):
        errors.append('summary_missing')
    else:
        completed = [game for game in games if game.get('game_status') == 'complete'
                     and game.get('protocol_status') == 'verified']
        expected_failures: dict[str, int] = {}
        for game in games:
            error = game.get('error')
            if isinstance(error, dict) and isinstance(error.get('kind'), str):
                error_kind = cast(str, error['kind'])
                expected_failures[error_kind] = expected_failures.get(error_kind, 0) + 1
        if (summary.get('attempted') != len(games)
                or summary.get('completed') != len(completed)
                or summary.get('failed') != len(games) - len(completed)
                or summary.get('failures') != expected_failures):
            errors.append('summary_counts_mismatch')
        for label in ('A', 'B'):
            wins = sum(game.get('winner') == label for game in completed)
            draws = sum(game.get('result') == 'D' for game in completed)
            data = summary.get(label)
            win_rate = wins / len(completed) if completed else None
            point_rate = (wins + draws / 2) / len(completed) if completed else None
            response_times = []
            for game in games:
                player = (game.get('players') or {}).get(label, {})
                for command in player.get('commands', []) if isinstance(player, dict) else []:
                    if (isinstance(command, dict)
                            and isinstance(command.get('command'), str)
                            and command['command'].startswith('genmove ')
                            and 'error' not in command):
                        seconds = command.get('seconds')
                        if not _nonnegative_number(seconds):
                            errors.append('command_timing_invalid')
                        else:
                            response_times.append(seconds)
            black_games = sum(game.get('colors', {}).get('B') == label for game in games)
            white_games = sum(game.get('colors', {}).get('W') == label for game in games)
            if not isinstance(data, dict):
                errors.append('summary_scores_mismatch')
                continue
            expected_scores = {
                'wins': wins, 'losses': len(completed) - wins - draws, 'draws': draws,
                'win_rate': win_rate, 'point_rate': point_rate,
                'genmove_samples': len(response_times),
                'mean_genmove_seconds': sum(response_times) / len(response_times) if response_times else None,
                'max_genmove_seconds': max(response_times) if response_times else None,
                'black_games': black_games, 'white_games': white_games,
            }
            for key, expected_value in expected_scores.items():
                actual_value = data.get(key)
                if isinstance(expected_value, float) and _number(actual_value):
                    valid = math.isclose(actual_value, expected_value, rel_tol=1e-12, abs_tol=1e-12)
                elif type(expected_value) is int:
                    valid = type(actual_value) is int and actual_value == expected_value
                else:
                    valid = actual_value == expected_value
                if not valid:
                    errors.append('summary_scores_mismatch')
                    break
    return sorted(set(errors))


class _TranscriptEvidenceMissing(ValueError):
    """schema5の通信対応証拠が不足し、保存された要求との対応を確認できない状態。"""


def _schema5_identity(player: _Player, context: Mapping[str, object], case_id: object,
                        role_seed: object, clock_domain_id: object, purpose: str) -> _Record:
    """原報告から期待slotを定め、取得済みsessionとの対応を照合する。時計順序は扱わない。"""
    lifecycle = player.get('lifecycle')
    if lifecycle is None:
        raise _TranscriptEvidenceMissing('schema5_lifecycle_identity_missing')
    if not isinstance(lifecycle, dict):
        raise ValueError('schema5_lifecycle_identity_invalid')
    expected = {**context, 'purpose': purpose, 'case_id': case_id,
                'role_seed': role_seed, 'clock_domain_id': clock_domain_id}
    for key, value in expected.items():
        if lifecycle.get(key) is None or value is None:
            raise _TranscriptEvidenceMissing('schema5_identity_missing:' + key)
        if type(lifecycle[key]) is not type(value) or lifecycle[key] != value:
            raise ValueError('schema5_identity_mismatch:' + key)
    session, generation = lifecycle.get('process_session_id'), lifecycle.get('process_generation')
    if session is None or generation is None:
        raise _TranscriptEvidenceMissing('schema5_session_identity_missing')
    if not isinstance(session, str) or not session or type(generation) is not int or generation != 0:
        raise ValueError('schema5_session_identity_invalid')
    expected.update(process_session_id=session, process_generation=generation)
    return expected


def _validate_player_transcript(player: _Player, expected: Sequence[_Requirement], conditions: _Conditions,
                                  context: Mapping[str, object], strengthened: bool) -> list[_BudgetCommand]:
    """目的別に構成した期待通信列と実ログを照合する。game終局をprobeへ要求しない。"""
    from harness.protocol import command_id, validate_command_record, validate_response

    commands = player.get('commands')
    if not isinstance(commands, list) or len(commands) != len(expected):
        raise ValueError('command_coverage_mismatch')
    if type(player.get('exit_code')) is not int or player['exit_code'] != 0:
        raise ValueError('player_exit_invalid')
    contexts: list[_BudgetCommand] = []
    for sequence, (record, requirement) in enumerate(zip(commands, expected)):
        command, response, phase, seconds = requirement
        if not isinstance(record, dict) or record.get('command') != command or 'error' in record:
            raise ValueError('command_order_or_error:' + str(sequence))
        if type(record.get('startup_probe')) is not bool or record['startup_probe'] != (command == 'name'):
            raise ValueError('startup_phase_mismatch')
        validate_response(command, cast('list[str]', record.get('response')))
        if response is not None and record['response'] != response:
            raise ValueError('command_response_mismatch:' + str(sequence))
        if command == 'name' and player.get('name') != record['response'][0]:
            raise ValueError('player_name_mismatch')
        if phase is not None and record.get('phase') != phase:
            raise ValueError('command_phase_mismatch')
        timeout_key = 'startup_timeout' if command == 'name' else 'genmove_timeout' if phase else 'command_timeout'
        timeout = conditions.get(timeout_key)
        elapsed = record.get('seconds')
        if (not _nonnegative_number(elapsed)
                or not _positive_number(timeout)
                or elapsed > timeout or (seconds is not None and elapsed != seconds)):
            raise ValueError('command_timing_invalid')
        if strengthened:
            if 'process_session_id' in context:
                if any(record.get(key) is None for key in ('command_id', 'command_index', 'clock', 'clock_domain_id')):
                    raise _TranscriptEvidenceMissing('schema5_command_binding_missing')
                for key in ('process_session_id', 'process_generation'):
                    if record.get(key) is None:
                        raise _TranscriptEvidenceMissing('schema5_command_identity_missing:' + key)
                    if type(record[key]) is not type(context[key]) or record[key] != context[key]:
                        raise ValueError('schema5_command_identity_mismatch:' + key)
                points = ('exchange_start_ns', 'send_start_ns', 'exit_observed_ns') if command == 'quit' else (
                    'exchange_start_ns', 'send_start_ns', 'response_read_end_ns', 'validation_end_ns')
                if any(record.get(key) is None for key in points):
                    raise _TranscriptEvidenceMissing('schema5_command_interval_missing')
            validate_command_record(record, timeout)
            if type(record.get('limit_ns')) is not int or record['limit_ns'] < 0 or (
                    record['limit_ns'] > int(timeout * 1e9) if command == 'name'
                    else record['limit_ns'] != int(timeout * 1e9)):
                raise ValueError('host_deadline_alias_mismatch')
            if record['command_index'] != sequence or record.get('command_id') != command_id(context, sequence):
                raise ValueError('host_command_identity_mismatch')
            if type(player.get('clock_domain_id')) is not str or not player['clock_domain_id'] or record.get('clock_domain_id') != player['clock_domain_id']:
                raise ValueError('host_clock_domain_mismatch')
            if record.get('clock') != player.get('clock'):
                raise ValueError('host_clock_descriptor_mismatch')
        contexts.append({**record, **({'phase': phase} if phase else {})})
    if strengthened:
        start, end = player.get('spawn_before_ns'), player.get('startup_end_ns')
        if (type(start) is not int or type(end) is not int or start > commands[0]['exchange_start_ns']
                or commands[0].get('spawn_before_ns') != start or end != commands[0]['validation_end_ns']
                or player.get('startup_seconds') != (end - start) / 1e9
                or end - start > int(conditions['startup_timeout'] * 1e9)):
            raise ValueError('startup_interval_invalid')
    return contexts


def _observe_probe(player: _Player, case: _OpeningEntry, response: str, label: str, attempt: int,
                    conditions: _Conditions, run_id: str, strengthened: bool,
                    schema5: bool = False, clock_domain_id: object = None) -> list[_BudgetCommand]:
    """保存probeを独立盤面で再生し、単発genmove・更新盤面・quitの通信を検証する。"""
    from harness import referee

    rows: tuple[str, ...] = referee.INITIAL_ROWS
    expected: list[_Requirement] = [('name', None, None, None), ('showboard', list(rows), None, None)]
    for move in case['moves']:
        if move['position'] is not None:
            rows = referee.apply_move(rows, move['color'], cast('Position', tuple(cast('Iterable[int]', move['position']))))
            expected.append(('play {} {} {}'.format(move['color'], *cast('Iterable[int]', move['position'])), ['OK'], None, None))
        expected.append(('showboard', list(rows), None, None))
    if not _same_board(rows, case['board']):
        raise ValueError('probe_opening_board_invalid')
    expected.append(('showboard', list(rows), None, None))
    color = case['to_move']
    legal = referee.legal_moves(rows, color)
    if response == 'NG':
        if legal:
            raise ValueError('probe_illegal_pass')
    else:
        position = tuple(map(int, response.split()))
        if position not in legal:
            raise ValueError('probe_illegal_move')
        rows = referee.apply_move(rows, color, position)
    expected.extend([('genmove ' + color, [response], 'normal_move' if legal else 'pass_probe', None),
                     ('showboard', list(rows), None, None), ('quit', [], None, None)])
    context = {'run_id': run_id, 'pair_id': 'determinism-' + label, 'leg': attempt, 'player': label}
    if schema5:
        context = _schema5_identity(player, context, case['case_id'],
                                   seed_for(conditions['seed'], case['case_id'], label), clock_domain_id, 'probe')
    return _validate_player_transcript(player, expected, conditions, context, strengthened)


def _command_contexts(game: _Game, conditions: _Conditions, run_id: str | None = None,
                       strengthened: bool = False, schema5: bool = False, clock_domain_id: object = None,
                       causal_order: list[tuple[str, int]] | None = None) -> dict[str, list[_BudgetCommand]]:
    """現行v3の取得順序を棋譜から再構築し、両playerの保存通信を照合する。

    合法終局、phase、RTT期限、quitとexitも確認し、traceへ渡す新しいcommands辞書を返す。
    gameは更新せず、不完全または矛盾した記録は例外で拒否する。AIは起動しない。
    """
    from harness import referee

    expected: dict[str, list[_Requirement]] = {label: [] for label in ('A', 'B')}
    rows: tuple[str, ...] = referee.INITIAL_ROWS

    def append_command(label: str, requirement: _Requirement) -> None:
        """collectorの予定呼出順を保存し、実時刻で並べ替えて因果違反を隠さない。"""
        if causal_order is not None:
            causal_order.append((label, len(expected[label])))
        expected[label].append(requirement)

    for label in ('A', 'B'):
        append_command(label, ('name', None, None, None))

    def check_state() -> None:
        """再生中盤面のshowboard/resultを両playerの期待通信列へ追加する。"""
        for label in ('A', 'B'):
            append_command(label, ('showboard', list(rows), None, None))
            append_command(label, ('result', [referee.result(rows)], None, None))

    check_state()
    for move in game['opening_moves']:
        position = move['position']
        if position is not None:
            rows = referee.apply_move(rows, move['color'], cast('Position', tuple(position)))
            for label in ('A', 'B'):
                append_command(label, ('play {} {} {}'.format(move['color'], *position), ['OK'], None, None))
        check_state()
    for move in game['moves']:
        color, label, position = move['color'], move['player'], move['position']
        legal = referee.legal_moves(rows, color)
        if referee.result(rows) != 'NG':
            raise ValueError('move_after_terminal')
        phase = 'normal_move' if legal else 'pass_probe'
        if position is None:
            if legal:
                raise ValueError('illegal_pass')
            response = 'NG'
        else:
            if (not isinstance(position, (list, tuple)) or len(position) != 2
                    or any(type(value) is not int for value in position) or tuple(position) not in legal):
                raise ValueError('illegal_position')
            response = '{} {}'.format(*position)
        append_command(label, ('genmove ' + color, [response], phase, move['seconds']))
        if position is not None:
            rows = referee.apply_move(rows, color, tuple(position))
            other = 'B' if label == 'A' else 'A'
            append_command(other, ('play {} {}'.format(color, response), ['OK'], None, None))
        check_state()
    if referee.result(rows) == 'NG':
        raise ValueError('nonterminal_complete_game')
    outcome = referee.result(rows)
    winner = None if outcome == 'D' else game['colors'][outcome]
    score = 0.5 if outcome == 'D' else float(winner == 'A')
    if (not _same_board(rows, game.get('final_board')) or game.get('result') != outcome
            or game.get('winner') != winner or type(game.get('score_a')) not in (int, float)
            or game['score_a'] != score):
        raise ValueError('terminal_score_or_board_mismatch')
    for color, label in game['colors'].items():
        append_command(label, ('genmove ' + color, ['NG'], 'terminal_probe', None))
    check_state()
    for label in ('A', 'B'):
        append_command(label, ('quit', [], None, None))
    contexts = {}
    players = game.get('players')
    if not isinstance(players, dict) or set(players) != {'A', 'B'}:
        raise ValueError('required_players_missing')
    for label in ('A', 'B'):
        player = players[label]
        context: _Record = {'run_id': run_id, 'pair_id': game['pair_id'], 'leg': game['leg'], 'player': label}
        if schema5:
            context = _schema5_identity(player, context, game['case_id'], game['seeds'][label], clock_domain_id, 'game')
        contexts[label] = _validate_player_transcript(
            player, expected[label], conditions, context, strengthened,
        )
    return contexts


def _trace_timing_status(validation: _Validation) -> Literal['verified', 'unknown', 'failed', 'not_required']:
    """raw検証の時間証拠を投影する。別の予算/意味検査で早期停止したら未検証とする。"""
    if validation['status'] == 'exempt':
        return 'not_required'
    if validation['status'] == 'verified':
        return 'verified'
    time_failure = str(validation.get('reason', '')).startswith((
        'search_interval_', 'invalid_search_elapsed_', 'model_load_', 'initial_model_', 'time_budget_exceeded_',
    ))
    return 'failed' if validation['status'] == 'failed' and time_failure else 'unknown'


def _protocol_timing_status(error: object) -> Literal['failed', 'unknown']:
    """host時間/対応関係の既知違反はfailed、それ以外で未検査の区間はunknownとする。"""
    return 'failed' if str(error).startswith(('host_', 'startup_interval_', 'command_timing_')) else 'unknown'


def _inspect_timelines(snapshot: _SourceReport, cache: _Cache, command_states: Sequence[str]) -> _Timing:
    """全game/必須probeの原証拠を検査し、時間の各軸と理由を返す。

    予算検査の早期終了やB適用除外でも親子の生存期間検査を省略しない。
    同一inspectionのbytes cacheだけを共用し、原資料・保存状態は変更しない。
    """
    from harness.protocol import (clock_status, temporal_match, temporal_order, temporal_result,
                                  validate_process_timeline)
    from harness.search_budget import validate_child_lifecycle

    axes: dict[str, list[Mapping[str, object]]] = {name: [] for name in ('host_lifecycle', 'child_lifecycle', 'command_intervals', 'causal_order')}
    seen_sessions = set()
    schema5 = snapshot['schema_version'] == 5
    conditions = snapshot['conditions']

    def add(axis: str, observed: _TemporalResult, identity: Mapping[str, object],
            commands: Sequence[_CommandRecord] = ()) -> None:
        """理由へ原slotと要求IDを付け、unknownを消さずfailed優先で集約する。"""
        details = observed.get('reasons', [])
        if observed['status'] != 'verified' and not details:
            details = [{'status': observed['status'], 'code': 'temporal_evidence_unverified', 'path': axis}]
        for detail in details:
            item = {**identity, **detail}
            path = str(item.get('path', ''))
            if 'commands[' in path:
                index = path.split('commands[', 1)[1].split(']', 1)[0]
                if index.isdigit() and int(index) < len(commands) and isinstance(commands[int(index)], dict):
                    item['command_id'] = commands[int(index)].get('command_id')
            axes[axis].append(item)

    def inspect_player(player: _Player, artifact: _Artifact | None, context: Mapping[str, object],
                        normal_expected: bool) -> None:
        """1sessionのhost/rawを検査する。子証拠欠損はhostの既知矛盾を打ち消さない。"""
        if not isinstance(player, dict):
            player = {}
        commands = player.get('commands', [])
        if not isinstance(commands, list):
            commands = []
        lifecycle = player.get('lifecycle')
        identity = {**context, 'process_session_id': lifecycle.get('process_session_id')
                    if isinstance(lifecycle, dict) else None}
        host = validate_process_timeline(player, commands, context, conditions, require_lifecycle=schema5,
                                         require_normal=normal_expected,
                                         require_command_identity=snapshot['schema_version'] >= 4)
        add('host_lifecycle', host, identity, commands)
        clock_reasons = temporal_match(player.get('clock'), snapshot['runtime'].get('clock'), 'runtime.clock')
        add('host_lifecycle', temporal_result(clock_reasons), identity)
        session = identity['process_session_id']
        if schema5 and isinstance(session, str) and session:
            if session in seen_sessions:
                add('host_lifecycle', temporal_result([{'status': 'failed', 'code': 'temporal_session_reused',
                                                       'path': 'lifecycle.process_session_id'}]), identity)
            seen_sessions.add(session)
        try:
            if not isinstance(artifact, dict) or artifact.get('exists') is False:
                events = None
            else:
                payload = _artifact_bytes(snapshot, artifact, cache)
                if hashlib.sha256(payload).hexdigest() != artifact.get('sha256'):
                    raise ValueError('temporal_trace_hash_mismatch')
                events = [json.loads(line) for line in payload.decode('utf-8').splitlines() if line.strip()]
            expected = dict(context)
            if isinstance(lifecycle, dict) and schema5:
                expected.update(process_session_id=lifecycle.get('process_session_id'),
                                process_generation=lifecycle.get('process_generation'))
            child = validate_child_lifecycle(events, commands, lifecycle, expected)
        except (OSError, KeyError) as error:
            diagnostic: _LocatedReason = {'status': 'unknown', 'code': 'temporal_trace_unavailable',
                                          'path': 'trace', 'detail': str(error)}
            child = temporal_result([diagnostic])
        except (ValueError, TypeError, AttributeError) as error:
            diagnostic = {'status': 'failed', 'code': 'temporal_trace_invalid',
                          'path': 'trace', 'detail': str(error)}
            child = temporal_result([diagnostic])
        add('child_lifecycle', child, identity, commands)

    def context_for(pair: object, leg: object, label: str, purpose: str, case: object, seed: object) -> _Record:
        """期待contextは保存位置と既存識別規則から作り、rawの自己申告を採用しない。"""
        context = {'run_id': snapshot.get('run_id'), 'pair_id': pair, 'leg': leg, 'player': label}
        if schema5:
            context.update(purpose=purpose, case_id=case, role_seed=seed,
                           clock_domain_id=snapshot.get('clock_domain_id'))
        return context

    if conditions.get('evaluation_profile') == 'confirmatory':
        selected = (conditions.get('selection_manifest') or {}).get('selected_case_ids', [])
        case = selected[0] if selected else None
        for label in ('A', 'B'):
            check = snapshot.get('determinism', {}).get(label, {})
            for attempt in range(2):
                attempts, artifacts = check.get('attempts', []), check.get('trace_artifacts', [])
                player = attempts[attempt] if attempt < len(attempts) else {}
                artifact = artifacts[attempt] if attempt < len(artifacts) else None
                seed = seed_for(conditions['seed'], case, label) if case is not None else None
                inspect_player(player, artifact, context_for('determinism-' + label, attempt, label, 'probe', case, seed),
                               check.get('status') == 'verified')

    for game in snapshot['games']:
        players = game.get('players', {})
        for label in ('A', 'B'):
            player = players.get(label, {}) if isinstance(players, dict) else {}
            artifact = player.get('budget_trace', {}).get('artifact') if isinstance(player, dict) else None
            inspect_player(player, artifact, context_for(game.get('pair_id'), game.get('leg'), label,
                                                        'game', game.get('case_id'), game.get('seeds', {}).get(label)),
                           game.get('game_status') == 'complete' and game.get('protocol_status') == 'verified')
        identity = {'run_id': snapshot.get('run_id'), 'pair_id': game.get('pair_id'), 'leg': game.get('leg')}
        try:
            if not schema5:
                raise _TranscriptEvidenceMissing('legacy_causal_domain_unavailable')
            schedule: list[tuple[str, int]] = []
            contexts = _command_contexts(game, conditions, snapshot['run_id'], True, True,
                                         snapshot.get('clock_domain_id'), causal_order=schedule)
            clock = snapshot['runtime'].get('clock')
            reasons = []
            for label in ('A', 'B'):
                reasons += temporal_match(players[label].get('clock'), clock, 'T-CAUSE.' + label + '.clock')
                reasons += temporal_match(players[label].get('clock_domain_id'), snapshot.get('clock_domain_id'),
                                          'T-CAUSE.' + label + '.domain')
            threads = [_get_container(players[label].get('lifecycle', {})).get('measurement_thread_id') for label in ('A', 'B')]
            if any(type(thread) is not int or thread <= 0 for thread in threads):
                reasons.append({'status': 'unknown', 'code': 'temporal_causal_thread_unproven', 'path': 'T-CAUSE.thread'})
            reasons += temporal_match(threads[0], threads[1], 'T-CAUSE.thread')
            comparable = (clock_status(clock) == 'verified' and not reasons)
            causal_clock = clock if comparable else None
            reasons += temporal_order(contexts['A'][0].get('validation_end_ns'),
                                      players['B'].get('spawn_before_ns'), causal_clock, 'T-CAUSE.A-name:B-spawn')
            for (before_role, before_index), (after_role, after_index) in zip(schedule, schedule[1:]):
                before, after = contexts[before_role][before_index], contexts[after_role][after_index]
                end = before.get('exit_observed_ns') if before['command'] == 'quit' else before.get('validation_end_ns')
                reasons += temporal_order(end, after.get('exchange_start_ns'), causal_clock,
                                          'T-CAUSE.' + before['command_id'] + ':' + after['command_id'])
            add('causal_order', temporal_result(reasons), identity)
        except _TranscriptEvidenceMissing as error:
            diagnostic: _LocatedReason = {'status': 'unknown', 'code': 'temporal_causal_evidence_missing',
                                          'path': 'T-CAUSE', 'detail': str(error)}
            add('causal_order', temporal_result([diagnostic]), identity)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            diagnostic = {'status': 'unknown', 'code': 'temporal_causal_context_unverified',
                          'path': 'T-CAUSE', 'detail': str(error)}
            add('causal_order', temporal_result([diagnostic]), identity)

    required_states = [state for state in command_states if state != 'not_required']
    command_state: _Status = ('failed' if 'failed' in required_states else 'unknown'
                     if not required_states or 'unknown' in required_states else 'verified')
    add('command_intervals', temporal_result([]) if command_state == 'verified' else
        temporal_result([{'status': command_state, 'code': 'temporal_command_intervals_' + command_state,
                          'path': 'command_intervals'}]), {})
    if not snapshot['games']:
        for name in axes:
            axes[name].append({'status': 'unknown', 'code': 'temporal_required_games_missing', 'path': name})
    output: _Timing = {
        'host_lifecycle': temporal_result(axes['host_lifecycle']),
        'child_lifecycle': temporal_result(axes['child_lifecycle']),
        'command_intervals': temporal_result(axes['command_intervals']),
        'causal_order': temporal_result(axes['causal_order']),
    }
    combined = temporal_result([item for reasons in axes.values() for item in reasons])
    output.update({'status': combined['status'], 'reasons': combined['reasons']})
    output['lifecycle'] = temporal_result(axes['host_lifecycle'] + axes['child_lifecycle'])
    return output


def _inspect_evaluation(report: _SourceReport, evidence_root: str | PathLike[str] | None = None,
                          verify_saved_metrics: bool = True) -> _Inspection:
    """原報告のsnapshotと保存証跡から、現在の検証結果と派生値を生成する共通入口。

    reportは変更せず、母集合由来N・抽出・coverage・棋譜/通信・raw/probeを検証する。
    結果は各軸の状態、理由、検証器版・元報告hash、派生game/ペアを含む辞書。
    全体はfailedを優先し、証拠不足や未対応契約はunknown、要求確認済みならverified。
    この結果だけで全ケースの決定性やAI内部counterの真正性を証明したとは扱わない。
    evidence_rootは同一資料の読込先のみ置換し、原報告hashは置換前から計算する。
    """
    from harness import openings, referee, search_budget

    result: _Inspection = {
        'validation_contract_version': 3,
        'validator_fingerprint': digest({
            name: hashlib.sha256(Path(__file__).with_name(name + '.py').read_bytes()).hexdigest()
            for name in ('contracts', 'openings', 'referee', 'protocol', 'search_budget')
        }),
        'status': 'unknown', 'reasons': [], 'integrity_errors': [],
        'population': {'status': 'unknown', 'verified_N': None, 'case_ids': []},
        'selection': {'status': 'unknown', 'planned_n': None, 'selected_case_ids': []},
        'coverage': {'status': 'unknown'}, 'gameplay': {'status': 'unknown', 'per_leg_results': []},
        'budget': {'status': 'unknown', 'per_leg_results': []},
        'input_status': 'invalid', 'source_consistency': {'status': 'unknown'},
        'timing': {'status': 'unknown'},
        'execution': {'status': 'unknown'}, 'protocol': {'status': 'unknown'},
        'derived': {'games': [], 'qualified_pair_scores': {}, 'coverage_kind': 'unverifiable'},
    }

    def reason(code: str, location: str, status: str = 'failed', **identity: object) -> None:
        """検証理由と位置・case/pair/leg等を蓄積し、failedだけを整合性エラーへ加える。"""
        result['reasons'].append({'code': code, 'location': location, 'status': status, **identity})
        if status == 'failed':
            result['integrity_errors'].append(code)

    try:
        # 呼出元の可変reportと切り離し、照合した値だけを後続解析へ渡す。
        snapshot = copy.deepcopy(report)
        decoded = decode_input(snapshot)
        result['decoded'] = decoded
        result['input_status'] = decoded['input_status']
        result['source_report_sha256'] = decoded['source_report_sha256']
        if decoded['input_status'] != 'valid':
            for issue in decoded['issues']:
                reason(issue, 'input')
            result['status'] = 'failed'
            return result
        # 移動先指定が出典の内容hashを変えないよう、読込先置換前にhashする。
        if evidence_root is not None:
            snapshot.setdefault('evidence', {})['root'] = str(evidence_root)
        if snapshot.get('schema_version', 1) < 3:
            result['snapshot'] = snapshot
            result['status'] = 'unknown'
            reason('legacy_evidence_unavailable', 'schema_version', 'unknown')
            return result
        result['snapshot'] = snapshot
        cache: _Cache = {}
        artifact: _Artifact | None
        errors = _validate_report(snapshot, cache, validate_summary=verify_saved_metrics)
        for error in errors:
            unavailable = error in ('trace_artifact_missing', 'population_artifact_missing',
                                    'determinism_trace_missing')
            reason(error, 'report', 'unknown' if unavailable else 'failed')
        conditions = snapshot['conditions']
        if snapshot['schema_version'] >= 4:
            from harness.protocol import MEASUREMENT_CONTRACT
            if conditions.get('measurement_contract') != MEASUREMENT_CONTRACT:
                reason('measurement_contract_unavailable', 'conditions.measurement_contract', 'unknown')
        for game in snapshot['games']:
            players = game.get('players', {})
            if not isinstance(players, dict):
                reason('required_players_missing', 'game.players')
                continue
            states = {label: player.get('budget_trace', {}).get('status', 'unknown')
                      for label, player in players.items() if isinstance(player, dict)}
            if any(state not in ('verified', 'unknown', 'failed', 'exempt') for state in states.values()):
                reason('budget_state_invalid', 'game.players.budget_trace.status')
            for label, state in states.items():
                if state == 'exempt':
                    from harness.search_budget import normalize_config, exemption_status
                    try:
                        config = normalize_config(conditions['budget_configs'][label])
                        approved = exemption_status(config, snapshot['targets'][label]['sha256'])[0]
                    except (KeyError, ValueError, TypeError):
                        approved = 'failed'
                    if label != 'B' or conditions.get('budget_policy') != 'baseline-asymmetric' or approved != 'exempt':
                        reason('budget_exemption_not_authorized', 'game.players.budget_trace', player=label)
            expected_budget = aggregate_role_budget(states, conditions)
            if game.get('budget_status') != expected_budget:
                reason('budget_state_hierarchy_mismatch', 'game.budget_status')
            for label in ('A', 'B'):
                cached = game.get('budget', {}).get(label, {})
                if cached.get('status', 'unknown') != states.get(label, 'unknown'):
                    reason('budget_state_alias_mismatch', 'game.budget.' + label)
                if states.get(label) in ('failed', 'unknown'):
                    reason('acquisition_' + states[label] + '_requires_explicit_review',
                           'game.players.budget_trace', states[label], player=label)
        source_errors = [code for code in result['integrity_errors'] if not code.startswith('acquisition_')]
        result['source_consistency']['status'] = 'failed' if source_errors else 'verified'
        configs = conditions.get('budget_configs', {})
        if isinstance(configs, dict) and set(configs) == {'A', 'B'}:
            compatible, incompatibility = search_budget.compatible_budgets(
                configs['A'], configs['B'], cast(str, conditions.get('budget_policy')),
            )
            if not compatible:
                reason(cast(str, incompatibility), 'conditions.budget_configs')
        else:
            reason('required_budget_configs_missing', 'conditions.budget_configs')
        manifest = conditions.get('selection_manifest')
        if manifest is not None:
            try:
                if any(type(manifest.get(key)) is not int for key in (
                        'format_version', 'population_size', 'sample_size', 'selection_seed')):
                    raise ValueError('selection_manifest_integer_fields_invalid')
                artifact = snapshot['evidence']['population']
                payload = _artifact_bytes(snapshot, artifact, cache)
                if hashlib.sha256(payload).hexdigest() != artifact.get('sha256'):
                    raise ValueError('population_artifact_hash_mismatch')
                population = openings.validate_population(json.loads(payload.decode('utf-8')))
                # 未照合の報告Nではなく、原母集合のhashと履歴を照合した件数を正本にする。
                population_size = len(population['entries'])
                result['population'] = {
                    'status': 'verified', 'verified_N': population_size,
                    'population_hash': population['population_hash'],
                    'case_ids': [entry['case_id'] for entry in population['entries']],
                }
                reported: Mapping[str, object] = snapshot.get('population', {})
                aliases = [
                    (reported.get('size'), population_size),
                    (conditions.get('opening_population_size'), population_size),
                    (manifest.get('population_size'), population_size),
                    (reported.get('hash'), population['population_hash']),
                    (conditions.get('opening_population_hash'), population['population_hash']),
                    (reported.get('purpose'), population['purpose']),
                    (reported.get('placements'), population['placements']),
                    (reported.get('selected_case_ids'), manifest.get('selected_case_ids')),
                    (conditions.get('games'), conditions.get('planned_games')),
                ]
                for actual, expected_value in aliases:
                    if actual is None:
                        reason('population_alias_missing', 'population', 'unknown')
                        result['population']['status'] = 'unknown'
                    elif actual != expected_value or (type(expected_value) is int and type(actual) is not int):
                        reason('population_alias_mismatch', 'population')
                        result['population']['status'] = 'failed'
                # 未対応環境での抽出再現不能を、不正な標本だったと決めつけない。
                runtime_version = snapshot.get('runtime', {}).get('python', '').split()[0]
                if runtime_version.split('.')[:2] != sys.version.split()[0].split('.')[:2]:
                    reason('sampling_runtime_unsupported', 'runtime.python', 'unknown')
                else:
                    selected, reproduced = openings.select_entries(
                        population, conditions['planned_games'] // 2, cast(int, manifest.get('selection_seed')),
                    )
                    manifest_view: Mapping[str, object] = manifest
                    if reproduced != manifest_view:
                        reason('sampling_reproduction_mismatch', 'conditions.selection_manifest')
                    else:
                        result['selection'] = {
                            'status': 'verified', 'planned_n': len(selected),
                            'selected_case_ids': [entry['case_id'] for entry in selected],
                        }
            except (OSError, KeyError) as error:
                reason('population_evidence_unavailable', 'evidence.population', 'unknown', detail=str(error))
            except (ValueError, TypeError) as error:
                reason('population_evidence_invalid', 'evidence.population', detail=str(error))
                result['population']['status'] = 'failed'
        else:
            result['population']['status'] = 'not_required'
            result['selection']['status'] = 'not_required'
        games = snapshot['games']
        timing_states: list[str] = []
        events: list[object] | None
        validation: _Validation
        if conditions.get('evaluation_profile') == 'confirmatory' and result['selection']['status'] == 'verified':
            probe_case_id = result['selection']['selected_case_ids'][0]
            probe_case = next(entry for entry in population['entries'] if entry['case_id'] == probe_case_id)
            for label in ('A', 'B'):
                try:
                    check = snapshot['determinism'][label]
                    if check.get('case_id') != probe_case_id or check.get('seed') != seed_for(conditions['seed'], probe_case_id, label):
                        raise ValueError('determinism_case_or_seed_mismatch')
                    for attempt, artifact in enumerate(check['trace_artifacts']):
                        payload = _artifact_bytes(snapshot, artifact, cache)
                        if hashlib.sha256(payload).hexdigest() != artifact.get('sha256'):
                            raise ValueError('determinism_trace_hash_mismatch')
                        events = [json.loads(line) for line in payload.decode('utf-8').splitlines() if line.strip()]
                        response = check['outputs'][attempt]
                        legal = referee.legal_moves(probe_case['board'], probe_case['to_move'])
                        if response == 'NG':
                            if legal:
                                raise ValueError('determinism_probe_illegal_pass')
                        elif tuple(map(int, response.split())) not in legal:
                            raise ValueError('determinism_probe_illegal_move')
                        if snapshot['schema_version'] >= 4:
                            if check['attempts'][attempt].get('clock') != snapshot['runtime'].get('clock'):
                                raise ValueError('host_clock_descriptor_mismatch')
                            commands = _observe_probe(check['attempts'][attempt], probe_case, response,
                                                      label, attempt, conditions, snapshot['run_id'], True,
                                                      schema5=snapshot['schema_version'] == 5,
                                                      clock_domain_id=snapshot.get('clock_domain_id'))
                        else:
                            commands = [
                                {'command': 'showboard', 'response': probe_case['board']},
                                {'command': 'genmove ' + probe_case['to_move'], 'response': [response],
                                 'phase': 'normal_move' if legal else 'pass_probe'},
                            ]
                        validation = search_budget.validate_trace_events(
                            events, conditions['budget_configs'][label], commands, snapshot['targets'][label]['sha256'],
                            (_schema5_identity(check['attempts'][attempt],
                                {'run_id': snapshot['run_id'], 'pair_id': 'determinism-' + label,
                                 'leg': attempt, 'player': label}, probe_case['case_id'],
                                check['seed'], snapshot.get('clock_domain_id'), 'probe')
                             if snapshot['schema_version'] == 5 else
                             {'run_id': snapshot['run_id'], 'pair_id': 'determinism-' + label,
                              'leg': attempt, 'player': label}),
                            allow_exemption=False, purpose='probe',
                        )
                        timing_states.append(_trace_timing_status(validation))
                        if validation['status'] not in ('verified', 'exempt'):
                            reason('determinism_trace_' + cast(str, validation.get('reason', 'not_verified')),
                                   'determinism.' + label, validation['status'], player=label)
                except (OSError, KeyError) as error:
                    timing_states.append('unknown')
                    reason('determinism_evidence_unavailable', 'determinism.' + label, 'unknown', detail=str(error))
                except _TranscriptEvidenceMissing as error:
                    timing_states.append('unknown')
                    reason('determinism_evidence_unavailable', 'determinism.' + label, 'unknown', detail=str(error))
                except Exception as error:
                    timing_states.append(_protocol_timing_status(error))
                    reason('determinism_semantics_invalid', 'determinism.' + label, detail=str(error))
        coverage_errors = {'duplicate_pair_leg', 'planned_game_coverage_mismatch',
                           'planned_pair_coverage_mismatch', 'unexpected_pair', 'pair_identity_mismatch',
                           'pair_id_mismatch', 'seed_derivation_mismatch', 'game_leg_invalid'}
        result['coverage'] = {'status': 'failed' if coverage_errors.intersection(errors) else 'verified',
                              'observed_pair_legs': [[game.get('pair_id'), game.get('leg')] for game in games]}
        # 元の取得statusを上書きせず、再検証結果を派生gameへ反映する。
        validated_games = copy.deepcopy(games)
        budget_states = []
        measurement_series: _MeasurementSeries = {}
        protocol_states = []
        for game, derived in zip(games, validated_games):
            identity: _GameIdentity = {'case_id': game.get('case_id'), 'pair_id': game.get('pair_id'), 'leg': game.get('leg')}
            protocol_state: _Status
            try:
                if game.get('game_status') != 'complete' or game.get('protocol_status') != 'verified':
                    raise ValueError('game_not_completed_and_verified')
                if not _replay_game(game, None):
                    raise ValueError('game_history_or_board_mismatch')
                contexts = _command_contexts(game, conditions, snapshot['run_id'], snapshot['schema_version'] >= 4,
                                             schema5=snapshot['schema_version'] == 5,
                                             clock_domain_id=snapshot.get('clock_domain_id'))
                protocol_state = 'verified'
                from harness.protocol import clock_status
                for label in ('A', 'B'):
                    state = clock_status(game['players'][label].get('clock'))
                    if snapshot['schema_version'] >= 4 and game['players'][label].get('clock') != snapshot['runtime'].get('clock'):
                        state = 'failed'
                    timing_states.append(state)
                    if state != 'verified':
                        reason('host_clock_' + state, 'game.players.clock', state, player=label, **identity)
            except _TranscriptEvidenceMissing as error:
                contexts = {}
                protocol_state = 'unknown'
                timing_states.append('unknown')
                reason('saved_protocol_evidence_unavailable', 'game.players.commands', 'unknown',
                       detail=str(error), **identity)
            except Exception as error:
                contexts = {}
                protocol_state = 'failed'
                timing_states.append(_protocol_timing_status(error))
                reason('saved_protocol_invalid', 'game.players.commands', detail=str(error), **identity)
            protocol_states.append(protocol_state)
            derived['protocol_status'] = protocol_state
            result['gameplay']['per_leg_results'].append({**identity, 'status': protocol_state})
            leg_results: dict[str, _Validation] = {}
            for label in ('A', 'B'):
                try:
                    config = search_budget.normalize_config(conditions['budget_configs'][label])
                    target_hash = snapshot['targets'][label]['sha256']
                    config_hash = digest(config)
                    model_id = config['search'].get('model_id')
                    if model_id is not None:
                        model_hash = config['search'].get('model_sha256')
                        assets = snapshot['targets'][label].get('assets', [])
                        if not model_hash or model_hash not in [asset.get('sha256') for asset in assets]:
                            raise ValueError('model_asset_contract_unverified')
                    player = game.get('players', {}).get(label, {})
                    if (config_hash != conditions['budget_config_hashes'].get(label)
                            or search_budget.normalize_config(player.get('config')) != config):
                        raise ValueError('budget_config_alias_mismatch')
                    trace = player.get('budget_trace', {})
                    artifact = trace.get('artifact')
                    allowed = label == 'B' and conditions.get('budget_policy') == 'baseline-asymmetric'
                    exempt, _ = search_budget.exemption_status(config, target_hash)
                    # Bの対局予算除外はAtrace・両通信・決定性証拠の免除ではない。
                    if exempt == 'exempt' and allowed and snapshot['schema_version'] < 5:
                        events = None
                    elif not isinstance(artifact, dict) or not artifact.get('exists'):
                        events = None
                    else:
                        payload = _artifact_bytes(snapshot, artifact, cache)
                        if hashlib.sha256(payload).hexdigest() != artifact.get('sha256'):
                            raise ValueError('trace_artifact_hash_mismatch')
                        events = [json.loads(line) for line in payload.decode('utf-8').splitlines() if line.strip()]
                    validation = search_budget.validate_trace_events(
                        events, config, contexts.get(label, []), target_hash,
                        (_schema5_identity(player,
                            {'run_id': snapshot.get('run_id'), 'pair_id': game.get('pair_id'),
                             'leg': game.get('leg'), 'player': label}, game.get('case_id'),
                            game.get('seeds', {}).get(label), snapshot.get('clock_domain_id'), 'game')
                         if snapshot['schema_version'] == 5 else
                         {'run_id': snapshot.get('run_id'), 'pair_id': game.get('pair_id'),
                          'leg': game.get('leg'), 'player': label}), allow_exemption=allowed,
                    )
                    if protocol_state != 'verified':
                        if trace.get('status') == 'failed':
                            validation = {'status': 'failed', 'reason': 'acquisition_failed_requires_explicit_review',
                                          'measurements': []}
                        elif validation['status'] != 'failed' or validation.get('reason') == 'trace_genmove_count_mismatch':
                            # 未照合の空contextsから作った要求数を、raw違反の証明にしない。
                            validation = {'status': 'unknown', 'reason': 'protocol_context_unverified',
                                          'measurements': []}
                    # 新しいrawが通っても、取得時のfailedを自動で成功履歴へ修復しない。
                    elif trace.get('status') in ('failed', 'unknown') and validation['status'] in ('verified', 'exempt'):
                        validation = {'status': cast('_BudgetState', trace['status']), 'reason': 'acquisition_' + trace['status'] + '_requires_explicit_review'}
                except (OSError, KeyError, _TranscriptEvidenceMissing) as error:
                    validation = {'status': 'unknown', 'reason': 'trace_evidence_unavailable:' + str(error)}
                except Exception as error:
                    validation = {'status': 'failed', 'reason': 'trace_evidence_invalid:' + str(error)}
                leg_results[label] = validation
                timing_states.append(_trace_timing_status(validation))
                if verify_saved_metrics and validation['status'] in ('verified', 'exempt'):
                    expected_measurements = validation.get('measurements', [])
                    measurement_aliases = [game.get('budget', {}).get(label, {}).get('measurements'),
                               game.get('players', {}).get(label, {}).get('budget_trace', {}).get('measurements')]
                    if any(canonical_json(alias) != canonical_json(expected_measurements) for alias in measurement_aliases):
                        reason('metric_measurement_alias_mismatch', 'game.budget.measurements', player=label, **identity)
                        result.setdefault('metrics', {})['status'] = 'failed'
                if validation['status'] not in ('verified', 'exempt'):
                    reason(cast(str, validation.get('reason', 'trace_not_verified')), 'game.players.budget_trace',
                           validation['status'], player=label, **identity)
            budget_state = aggregate_role_budget({label: item['status'] for label, item in leg_results.items()}, conditions)
            budget_states.append(budget_state)
            derived['budget_status'] = budget_state
            result['budget']['per_leg_results'].append({**identity, 'status': budget_state, 'players': leg_results})
            measurement_series[(game.get('pair_id'), game.get('leg'))] = leg_results
        result['gameplay']['status'] = ('failed' if 'failed' in protocol_states else
                                      'unknown' if not protocol_states or 'unknown' in protocol_states else 'verified')
        result['protocol']['status'] = result['gameplay']['status']
        result['execution']['status'] = 'complete' if games and all(
            game.get('game_status') == 'complete' for game in games) else 'failed'
        result['timing'] = _inspect_timelines(snapshot, cache, timing_states)
        for item in result['timing']['reasons']:
            reason(cast(str, item['code']), cast(str, item.get('path', 'timing')), cast(str, item['status']),
                   **{key: value for key, value in item.items() if key not in ('code', 'path', 'status')})
        result['budget']['status'] = aggregate_game_budget(budget_states, conditions['planned_games'])
        canonical_summary = reduce_observations(games, measurement_series)
        result['canonical_summary'] = canonical_summary
        result['derived_measurements'] = [
            {'pair_id': pair, 'leg': leg, 'players': values}
            for (pair, leg), values in measurement_series.items()
        ]
        if verify_saved_metrics and snapshot['schema_version'] >= 4:
            for label in ('A', 'B'):
                if all(item.get(label, {}).get('status') in ('verified', 'exempt') for item in measurement_series.values()):
                    reported = snapshot.get('summary', {}).get(cast('_Role', label), {})
                    for key, value in canonical_summary[cast('_Role', label)].items():
                        if canonical_json(reported.get(key)) != canonical_json(value):
                            reason('metric_summary_mismatch', 'summary.' + label + '.' + key)
                            result.setdefault('metrics', {})['status'] = 'failed'
        metric_axes = (result['budget']['status'], result['timing']['status'], result['gameplay']['status'])
        metric_state: _Status = 'failed' if 'failed' in metric_axes else 'unknown' if 'unknown' in metric_axes else 'verified'
        result.setdefault('metrics', {})['status'] = result.get('metrics', {}).get('status', metric_state)
        pair_count = len({game.get('pair_id') for game in validated_games})
        result['derived'].update({'games': validated_games, 'validated_pair_count': pair_count})
        if result['population']['status'] == result['selection']['status'] == 'verified':
            complete = (result['coverage']['status'] == result['gameplay']['status'] == result['budget']['status'] == 'verified'
                        and pair_count == result['selection']['planned_n'])
            # n=Nの数だけでなく全caseと全要求検証が必要。timeの全完走は記述的扱い。
            full = set(result['selection']['selected_case_ids']) == set(result['population']['case_ids'])
            result['derived']['coverage_kind'] = ('full_coverage_descriptive' if full and complete else 'sample_verified')
            fixed_work = (conditions.get('evaluation_profile') == 'confirmatory'
                          and configs.get('A', {}).get('budget', {}).get('kind') in ('nodes', 'depth')
                          and conditions.get('budget_policy') in ('equal-work', 'baseline-asymmetric')
                          and conditions.get('determinism_verified') is True)
            if complete and fixed_work:
                result['derived']['coverage_kind'] = 'census_verified' if full else 'sample_verified'
            elif not complete:
                result['derived']['coverage_kind'] = 'incomplete'
        failed = result['integrity_errors']
        if any(code.startswith(('metric_', 'budget_state_', 'status_aggregate_')) for code in failed):
            result['source_consistency']['status'] = 'failed'
        if snapshot['schema_version'] < 4:
            reason('legacy_measured_intervals_unavailable', 'schema_version', 'unknown')
        unknown = any(item['status'] == 'unknown' for item in result['reasons'])
        result['status'] = 'failed' if failed else 'unknown' if unknown else 'verified'
    except Exception as error:
        reason('report_validation_error', 'report', detail=str(error))
        result['status'] = 'failed'
    if result['status'] != 'verified' and result['derived']['coverage_kind'] in ('census_verified', 'sample_verified'):
        result['derived']['coverage_kind'] = 'invalid' if result['status'] == 'failed' else 'unverifiable'
    result['integrity_errors'] = sorted(set(result['integrity_errors']))
    return result


def inspect_evaluation(report: _SourceReport, evidence_root: str | PathLike[str] | None = None) -> _Inspection:
    """原報告・raw・全保存cacheを毎回再検証する公開入口。検証省略の引数は設けない。"""
    return _inspect_evaluation(report, evidence_root, verify_saved_metrics=True)


def _acquisition_summary(report: _SourceReport) -> _Summary:
    """summary生成前の取得coreからfresh集計を返す。正式資格を発行する入口ではない。"""
    inspection = _inspect_evaluation(report, verify_saved_metrics=False)
    if 'canonical_summary' not in inspection:
        raise ValueError('acquisition_inspection_unavailable:' + ','.join(
                    cast(str, item['code']) for item in inspection['reasons']))
    return inspection['canonical_summary']


def reduce_observations(games: Sequence[_Game], measurements_by_leg: _MeasurementSeries | None = None) -> _Summary:
    """正常終局かつ通信verifiedの局を分母に参考勝敗・率を集計する。

    予算不合格でも参考勝敗は残す。成功genmoveの外部RTT秒とtrace由来探索nsを
    別に集計する。正式利用はfresh検証済みmeasurements_by_legを渡す。
    省略時は取得中checkpoint用参考cacheであり、認証済み集計ではない。入力は変更しない。
    """
    completed = [
        game for game in games
        if game.get('game_status') == 'complete' and game.get('protocol_status') == 'verified'
    ]
    failure_counts: dict[str, int] = {}
    for game in games:
        if game['error']:
            kind = cast(str, game['error']['kind'])
            failure_counts[kind] = failure_counts.get(kind, 0) + 1
    summary: _Summary = {
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
        search_measurements = [
            measurement
            for game in games
            for measurement in ((measurements_by_leg.get((game.get('pair_id'), game.get('leg')), {}).get(label, {}).get('measurements', [])
                                 if measurements_by_leg is not None else
                                 game.get('budget', {}).get(label, {}).get('measurements', [])))
        ]
        normal_search_times = [
            cast('int | float', item['search_elapsed_ns']) for item in search_measurements
            if item.get('phase') == 'normal_move'
        ]
        all_search_times = [cast('int | float', item['search_elapsed_ns']) for item in search_measurements]
        states = [(measurements_by_leg.get((game.get('pair_id'), game.get('leg')), {}).get(label, {}).get('status')
                   if measurements_by_leg is not None else game.get('budget', {}).get(label, {}).get('status'))
                  for game in games]
        metric_status = ('failed' if 'failed' in states else 'unknown' if not states or any(
            state not in ('verified', 'exempt') for state in states) else
            'not_required' if all(state == 'exempt' for state in states) else 'verified')
        summary[label] = {
            'wins': wins, 'losses': len(completed) - wins - draws, 'draws': draws,
            'win_rate': wins / len(completed) if completed else None,
            'point_rate': (wins + draws / 2) / len(completed) if completed else None,
            'genmove_samples': len(times),
            'mean_genmove_seconds': sum(times) / len(times) if times else None,
            'max_genmove_seconds': max(times) if times else None,
            'search_samples': len(all_search_times),
            'search_metric_status': metric_status if measurements_by_leg is not None else 'reference_only',
            'search_sum_ns': sum(all_search_times),
            'search_max_ns': max(all_search_times) if all_search_times else None,
            'normal_search_sum_ns': sum(normal_search_times),
            'normal_search_max_ns': max(normal_search_times) if normal_search_times else None,
            'mean_search_seconds': sum(all_search_times) / len(all_search_times) / 1_000_000_000
            if all_search_times else None,
            'max_search_seconds': max(all_search_times) / 1_000_000_000 if all_search_times else None,
            'normal_search_samples': len(normal_search_times),
            'mean_normal_search_seconds': sum(normal_search_times) / len(normal_search_times) / 1_000_000_000
            if normal_search_times else None,
            'max_normal_search_seconds': max(normal_search_times) / 1_000_000_000
            if normal_search_times else None,
            'black_games': sum(game['colors']['B'] == label for game in games),
            'white_games': sum(game['colors']['W'] == label for game in games),
        }
    return summary


def _inspection_errors(inspection: _Inspection) -> list[str]:
    """inspectionを互換用エラー配列へ変換する。正式/deploymentではunknownも拒否理由に含める。"""

    errors = list(inspection['integrity_errors'])
    if inspection.get('decoded', {}).get('requested_profile') in ('confirmatory', 'deployment'):
        errors.extend(cast(str, item['code']) for item in inspection['reasons'] if item['status'] == 'unknown')
    return sorted(set(errors))


def validate_report(report: _SourceReport) -> list[str]:
    """原報告を共通入口で再検証してエラー配列を返す。空配列と正式統計資格は同義でない。"""
    return _inspection_errors(inspect_evaluation(report))


def comparison_conditions(report: _SourceReport) -> Mapping[str, object]:
    """原報告を変更せず比較条件を作り、候補Aの探索実装差と必要な共通契約を分離する。

    A.searchは決定性/counting定義だけ保持し、A設定hashを除く。Bの条件は残す。
    """
    conditions = copy.deepcopy(report.get('conditions', {}))
    candidate = conditions.get('budget_configs', {}).get('A')
    if isinstance(candidate, dict):
        search = candidate.get('search', {})
        conditions['candidate_search_contract'] = {
            'deterministic': search.get('deterministic'),
            'counting_definition_id': search.get('counting_definition_id'),
        }
        candidate['search'] = {}
    conditions.get('budget_config_hashes', {}).pop('A', None)
    return conditions


def comparison_mismatches(before: _SourceReport, after: _SourceReport) -> list[str]:
    """正規化比較条件・版・runtime・取得fingerprint・固定B本体/資産の不一致項目を返す。

    個々の報告や証跡の再検証は行わないため、共通inspectionと組み合わせて使う。
    """
    mismatches = []
    if comparison_conditions(before) != comparison_conditions(after):
        mismatches.append('conditions')
    for key in ('schema_version', 'runtime', 'evaluation_fingerprint'):
        if before.get(key) != after.get(key):
            mismatches.append(key)
    if before.get('schema_version', 1) < 3 and after.get('schema_version', 1) < 3:
        if [item.get('sha256') for item in before.get('harness', [])] != [
                item.get('sha256') for item in after.get('harness', [])]:
            mismatches.append('harness')
    values: list[str | None] | list[list[str | None]]
    for key in ('sha256', 'assets'):
        if key == 'assets':
            assets = [report.get('targets', {}).get('B', {}).get('assets') for report in (before, after)]
            values = [[asset.get('sha256') for asset in value or []] for value in assets]
        else:
            values = [report.get('targets', {}).get('B', {}).get('sha256') for report in (before, after)]
        if values[0] != values[1]:
            mismatches.append('opponent.' + key)
    return mismatches
