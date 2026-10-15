"""保存証跡の共通検証と正式資格の判定核。数式・品質・保存成功・採用を分離する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from harness import contracts, statistics
from harness.contracts import _get_field, _item_field

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence, Sized
    from os import PathLike
    from typing import Literal, TypeGuard, TypedDict
    from harness.contracts import _Inspection, _Requirements, _SourceReport

    _Root = str | PathLike[str] | None
    _Roots = tuple[_Root, _Root] | list[_Root] | None
    _Number = int | float
    _RequiredAxis = Literal['population', 'selection', 'coverage', 'gameplay', 'budget', 'timing', 'metrics', 'source_consistency']

    class _Assessment(TypedDict):
        """同一fresh検証から導く参考/正式/要求の別々の結果。保存資格証明ではない。"""

        input_status: str
        quality_status: str
        formal_eligibility: str
        formal_reason: str | None
        confidence: _Number | None
        reference_available: bool
        request_success: bool
        metrics_certified: bool
        requirements: _Requirements
        reasons: list[Mapping[str, object]]
        validation_contract_version: object
        validator_fingerprint: object
        source_report_sha256: object

    class _PairAssessment(TypedDict):
        """両単独資格と取得条件の照合結果。対応差の算術成功と採用は別である。"""

        formal_pair_eligibility: str
        formal_reason: str | None
        condition_mismatches: list[str]
        comparison_match_status: str
        request_success: bool
        evaluations: list[_Assessment]


def _number(value: object) -> TypeGuard[int | float]:
    """元の厳密int/float述語を表し、boolや変換可能な文字列を許可しない。"""
    return type(value) in (int, float)


def _numbers(values: Sequence[object]) -> TypeGuard[Sequence[int | float]]:
    """差分の既存all検査を表す。列を変換せず非数値時は従来のNone分岐へ渡す。"""
    return all(type(value) in (int, float) for value in values)


def _field_length(value: object) -> int:
    """元と同じlenだけを要求する。要素型を認可せず、元のTypeErrorを保つ。"""
    return len(cast('Sized', value))


def _update_result(result: Mapping[str, object], fields: Mapping[str, object]) -> None:
    """同じ算術辞書へ元の順で追記し、コピーや正常値への修復を行わない。"""
    cast('Callable[[Mapping[str, object]], object]', getattr(result, 'update'))(fields)


def _provenance(inspection: _Inspection) -> dict[str, object]:
    """不可の場合も現在の検証器版と元報告hashを残す。"""
    return {key: inspection.get(key) for key in (
        'validation_contract_version', 'validator_fingerprint', 'source_report_sha256',
    )}


def _confidence(inspection: _Inspection, confidence: object) -> _Number:
    """要求confidenceを検査する。boolや非有限値を確率として扱わない。"""
    value = confidence
    if value is None:
        value = _get_field(inspection.get('snapshot', {}).get('analysis_spec', {}), 'confidence', 0.95)
    if not _number(value) or not 0 < value < 1:
        raise ValueError('confidence_invalid')
    return value


def _formal_eligibility(inspection: _Inspection) -> tuple[bool, str | None]:
    """同一Inspectionに対する正式固定得点資格を一意に判定する。"""
    conditions = inspection.get('decoded', {}).get('conditions', {})
    if inspection.get('input_status') != 'valid':
        return False, 'input_' + inspection.get('input_status', 'invalid')
    if inspection['integrity_errors']:
        return False, 'report_integrity_failed:' + ','.join(inspection['integrity_errors'])
    if conditions.get('evaluation_profile') != 'confirmatory':
        return False, 'profile_not_confirmatory'
    if inspection.get('decoded', {}).get('source_schema') != 5:
        return False, 'legacy_measured_intervals_unavailable'
    if inspection['status'] != 'verified':
        return False, 'evaluation_verification_incomplete'
    axes: tuple[_RequiredAxis, ...] = ('population', 'selection', 'coverage', 'gameplay', 'budget', 'timing', 'metrics', 'source_consistency')
    if any(inspection[key]['status'] != 'verified' for key in axes):
        return False, 'required_evidence_not_verified'
    if any(_get_field(inspection['timing'].get(axis, {}), 'status') != 'verified' for axis in
           ('host_lifecycle', 'child_lifecycle', 'command_intervals', 'causal_order')):
        return False, 'required_temporal_evidence_not_verified'
    if not conditions.get('determinism_verified'):
        return False, 'determinism_not_verified'
    policy = conditions.get('budget_policy')
    if policy not in ('equal-work', 'baseline-asymmetric'):
        return False, 'budget_policy_not_confirmatory'
    configs = conditions.get('budget_configs', {})
    if _item_field(_item_field(_item_field(configs, 'A'), 'budget'), 'kind') not in ('nodes', 'depth') or any(
            _get_field(_item_field(config, 'search'), 'deterministic') is not True
            for config in cast('Callable[[], Iterable[object]]', getattr(configs, 'values'))()):
        return False, 'fixed_work_determinism_required'
    return True, None


def _assess_inspection(inspection: _Inspection, confidence: object = None, require_budget: bool = False) -> _Assessment:
    """品質、正式資格、参考可否、要求成功を分けて判定する共通核。

    同一呼出内のfresh Inspectionだけを使う。保存Assessmentを認証情報にしない。
    """
    conditions = inspection.get('decoded', {}).get('conditions', {})
    snapshot = inspection.get('snapshot', {})
    source_required = _get_field(snapshot.get('verification_requirements', {}), 'require_budget_verification', False)
    requirements = contracts.verification_requirements(
        conditions, require_budget or source_required or conditions.get('require_budget_verification', False),
    )
    eligible, reason = _formal_eligibility(inspection)
    input_status = inspection.get('input_status', 'invalid')
    try:
        confidence = _confidence(inspection, confidence)
    except (ValueError, TypeError):
        input_status, eligible, reason = 'invalid', False, 'confidence_invalid'
        confidence = None
    formal_status = ('eligible' if eligible else 'ineligible' if
                     input_status != 'valid' or requirements['formal_requested'] else 'not_requested')
    snapshot = inspection.get('snapshot', {})
    legacy = inspection.get('decoded', {}).get('source_schema', 0) in (1, 2)
    if legacy and input_status == 'valid':
        reference = not _get_field(snapshot.get('summary', {}), 'failed', True)
    else:
        reference = (input_status == 'valid' and not inspection['integrity_errors']
                     and inspection.get('gameplay', {}).get('status') == 'verified'
                     and inspection.get('coverage', {}).get('status') == 'verified')
    required_quality = inspection['status'] == 'verified' if requirements['budget_required'] else reference
    request_success = reference and required_quality and (eligible or not requirements['formal_requested'])
    return {'input_status': input_status, 'quality_status': inspection['status'],
            'formal_eligibility': formal_status, 'formal_reason': reason, 'confidence': confidence,
            'reference_available': reference, 'request_success': bool(request_success),
            'metrics_certified': inspection.get('metrics', {}).get('status') == 'verified',
            'requirements': requirements, 'reasons': inspection['reasons'],
            'validation_contract_version': inspection.get('validation_contract_version'),
            'validator_fingerprint': inspection.get('validator_fingerprint'),
            'source_report_sha256': inspection.get('source_report_sha256')}


def assess_evaluation(report: _SourceReport, confidence: object = None, require_budget: bool = False,
                      evidence_root: _Root = None) -> _Assessment:
    """原報告と証跡をfresh検証して資格を判定する。保存Inspectionは入力として拒否する。"""
    return _assess_inspection(contracts.inspect_evaluation(report, evidence_root), confidence, require_budget)


def _eligibility(inspection: _Inspection) -> tuple[bool, str | None]:
    """互換tuple入口。判定は共通Assessmentへ委譲する。"""
    assessment = _assess_inspection(inspection)
    return assessment['formal_eligibility'] == 'eligible', assessment['formal_reason']


def statistical_eligibility(report: _SourceReport, evidence_root: _Root = None) -> tuple[bool, str | None]:
    """原報告とrawを毎回再検証して正式資格と理由を返す。"""
    return _eligibility(contracts.inspect_evaluation(report, evidence_root))


def _estimate_inspection(inspection: _Inspection, confidence: object) -> Mapping[str, object]:
    """呼出内のfresh検証結果からのみ正式/参考統計を組み立てる。"""
    assessment = _assess_inspection(inspection, confidence)
    requested = assessment['requirements']['formal_requested']
    eligible = assessment['formal_eligibility'] == 'eligible'
    result: Mapping[str, object] = {'status': 'unavailable', 'reason': assessment['formal_reason'],
              'pair_count': 0, 'invalid_pairs': {}, 'point_rate': None, 'interval': None}
    if assessment['input_status'] == 'valid' and not requested:
        _update_result(result, {'status': 'descriptive_only', 'reason': 'formal_ci_not_requested'})
    elif eligible:
        conditions = inspection['decoded']['conditions']
        result = statistics.estimate_pair_scores(
            inspection['derived']['games'], cast(int, inspection['population']['verified_N']), cast(float, assessment['confidence']),
            expected_pair_count=cast(int, conditions['planned_games']) // 2,
            expected_case_ids=inspection['selection']['selected_case_ids'],
        )
        if result['status'] == 'computed':
            _update_result(result, {'status': 'available', 'result_scope': 'validated_formal',
                                   'formal_eligibility': 'eligible'})
    _update_result(result, {
        'verification': {'status': inspection['status'], 'input_status': assessment['input_status'],
                         'reasons': inspection['reasons'], **_provenance(inspection)},
        'assessment': assessment,
        'statistical_eligibility': assessment['formal_eligibility'],
        'coverage_kind': inspection['derived']['coverage_kind'],
        'analysis_spec': {'method': 'hoeffding_serfling_finite_population',
                          'confidence': assessment['confidence'],
                          'estimand': 'mean_A_pair_score_on_fixed_opening_population',
                          'primary_claim': 'candidate_mean_score_above_half' if requested else 'not_requested'},
    })
    return result


def estimate(report: _SourceReport, confidence: object = None, evidence_root: _Root = None) -> Mapping[str, object]:
    """保存原資料を再検証し、資格が満たされる場合だけ固定母集合区間を返す。"""
    return _estimate_inspection(contracts.inspect_evaluation(report, evidence_root), confidence)


def _inspect_pair(before: _SourceReport, after: _SourceReport, evidence_roots: _Roots) -> list[_Inspection]:
    """二つの読込先を厳密に確認し、両原報告を独立してfresh検証する。"""
    roots = (None, None) if evidence_roots is None else evidence_roots
    if not isinstance(roots, (tuple, list)) or len(roots) != 2:
        raise ValueError('two evidence roots are required')
    return [contracts.inspect_evaluation(report, root) for report, root in zip((before, after), roots)]


def _assess_pair_inspections(inspections: Sequence[_Inspection], confidence: object = 0.95,
                            require_budget: bool = False) -> _PairAssessment:
    """両側の単独資格と取得条件同一性から共通の対応差資格を判定する。"""
    assessments = [_assess_inspection(item, confidence, require_budget) for item in inspections]
    valid = all(item['input_status'] == 'valid' for item in assessments)
    snapshots = [item.get('snapshot', {}) for item in inspections]
    mismatches = contracts.comparison_mismatches(*snapshots) if valid else ['input_invalid']
    if any(_get_field(snapshot.get('summary', {}), 'failed') or (
            _get_field(snapshot.get('conditions', {}), 'planned_games') is not None
            and _field_length(snapshot.get('games', [])) != _item_field(snapshot['conditions'], 'planned_games'))
           for snapshot in snapshots):
        mismatches.append('incomplete_evaluation')
    if any(item['integrity_errors'] for item in inspections):
        mismatches.append('report_integrity_failed')
    if any(item['requirements']['budget_required'] and item['quality_status'] != 'verified'
           for item in assessments):
        mismatches.append('budget_verification_unavailable')
    eligible = not mismatches and all(item['formal_eligibility'] == 'eligible' for item in assessments)
    reason = next((item['formal_reason'] for item in assessments
                   if item['formal_eligibility'] != 'eligible'), None)
    if reason is None and mismatches:
        reason = 'comparison_conditions_mismatch:' + ','.join(mismatches)
    return {'formal_pair_eligibility': 'eligible' if eligible else 'ineligible',
            'formal_reason': reason, 'condition_mismatches': list(dict.fromkeys(mismatches)),
            'comparison_match_status': 'verified' if not mismatches else 'failed',
            'request_success': not mismatches and all(item['reference_available'] for item in assessments),
            'evaluations': assessments}


def assess_comparison(before: _SourceReport, after: _SourceReport, confidence: object = 0.95,
                       require_budget: bool = False, evidence_roots: _Roots = None) -> _PairAssessment:
    """両原報告を独立に再検証し、同じprivate核で対応比較の資格を判定する。"""
    return _assess_pair_inspections(_inspect_pair(before, after, evidence_roots), confidence, require_budget)


def _paired_inspections(inspections: Sequence[_Inspection], confidence: object) -> Mapping[str, object]:
    """共通PairFormalを満たすfresh観測だけを対応差の数式へ渡す。"""
    assessment = _assess_pair_inspections(inspections, confidence)
    result: Mapping[str, object] = {'status': 'unavailable', 'reason': assessment['formal_reason'], 'interval': None}
    if assessment['formal_pair_eligibility'] == 'eligible':
        selection = inspections[0]['selection']
        result = statistics.paired_difference(
            inspections[0]['derived']['games'], inspections[1]['derived']['games'],
            cast(int, inspections[0]['population']['verified_N']), cast(float, confidence),
            expected_pair_count=selection['planned_n'], expected_case_ids=selection['selected_case_ids'],
        )
        if result['status'] == 'computed':
            _update_result(result, {'status': 'available', 'result_scope': 'validated_formal',
                                   'formal_eligibility': 'eligible'})
    _update_result(result, {'assessment': assessment, 'verification': [_provenance(item) for item in inspections]})
    return result


def paired(before: _SourceReport, after: _SourceReport, confidence: object = 0.95,
           evidence_roots: _Roots = None) -> Mapping[str, object]:
    """両sourceを毎回検証し、同条件の正式対応差だけを返す。AIは起動しない。"""
    return _paired_inspections(_inspect_pair(before, after, evidence_roots), confidence)


def _analysis_lineage(sources: Sequence[Mapping[str, object]], spec: object,
                       inspections: Sequence[_Inspection]) -> dict[str, str]:
    """元取得版を変えず、現在の判定/比較版と解析条件を不可結果にも記録する。"""
    fingerprint = contracts.digest({
        'analysis_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'statistics_sha256': hashlib.sha256(Path(statistics.__file__).read_bytes()).hexdigest(),
        'analysis_spec': spec, 'validation': [{key: item.get(key) for key in
                                             ('validation_contract_version', 'validator_fingerprint')}
                                            for item in inspections],
    })
    return {'analysis_fingerprint': fingerprint,
            'analysis_id': contracts.digest({'sources': sources, 'analysis_fingerprint': fingerprint})}


def compare(before: _SourceReport, after: _SourceReport, confidence: object = 0.95,
             require_budget_verification: bool = False, evidence_roots: _Roots = None) -> dict[str, object]:
    """全比較公開入口の共通実装。保存状態だけによる別判定を行わない。"""
    inspections = _inspect_pair(before, after, evidence_roots)
    assessment = _assess_pair_inspections(inspections, confidence, require_budget_verification)
    snapshots = [item.get('snapshot', {}) for item in inspections]
    summaries = [item.get('canonical_summary', snapshot.get('summary', {}))
                 for item, snapshot in zip(inspections, snapshots)]
    differences: dict[str, _Number | None] = {}
    for key in ('win_rate', 'point_rate', 'mean_genmove_seconds', 'max_genmove_seconds'):
        values = [_get_field(_get_field(summary, 'A', {}), key) for summary in summaries]
        differences[key] = values[1] - values[0] if _numbers(values) else None
    pair_result = _paired_inspections(inspections, confidence)
    available = pair_result['status'] == 'available'
    budget_verified = all(item['status'] == 'verified' and item['budget']['status'] == 'verified'
                          and item.get('metrics', {}).get('status') == 'verified' for item in inspections)
    normalized_confidence = assessment['evaluations'][0]['confidence']
    spec = {'method': 'hoeffding_serfling_finite_population', 'confidence': normalized_confidence,
            'estimand': 'mean_after_minus_before_A_pair_score_on_fixed_opening_population',
            'primary_claim': 'paired_mean_delta_above_zero'}
    sources = [{'run_id': snapshot.get('run_id'), 'experiment_id': snapshot.get('experiment_id'),
                'report_sha256': item.get('source_report_sha256')}
               for snapshot, item in zip(snapshots, inspections)]
    interval = pair_result.get('interval')
    return {'schema_version': 5, 'report_kind': 'comparison', 'sources': sources,
            'analysis_spec': spec, **_analysis_lineage(sources, spec, inspections),
            'assessment': assessment, 'comparable': assessment['request_success'],
            'condition_mismatches': assessment['condition_mismatches'],
            'before': summaries[0], 'after': summaries[1],
            'candidate_delta_after_minus_before': differences,
            'pair_statistics_available': available, 'paired_difference': pair_result if available else None,
            'budget_verified': budget_verified,
            'budget_comparable': budget_verified and not assessment['condition_mismatches'],
            'budget_comparison_reason': None if budget_verified else 'budget_conditions_or_evidence_mismatch',
            'comparison_scope': 'formal_paired' if available else 'descriptive',
            'candidate_search_config_changed': (
                _get_field(_get_field(_get_field(snapshots[0].get('conditions', {}), 'budget_configs', {}), 'A', {}), 'search')
                != _get_field(_get_field(_get_field(snapshots[1].get('conditions', {}), 'budget_configs', {}), 'A', {}), 'search')),
            'improvement_criterion': 'undetermined' if interval is None else
                                     'met' if cast(float, _item_field(interval, 'lower')) > 0 else 'not_met',
            'decision': 'undetermined'}


def comparison_report(before: _SourceReport, after: _SourceReport, result: Mapping[str, object] | None = None,
                       confidence: object = 0.95, evidence_roots: _Roots = None) -> dict[str, object]:
    """公開builderもfresh比較する。caller結果の資格/予約field上書きは許可しない。"""
    fresh = compare(before, after, confidence, evidence_roots=evidence_roots)
    if result is not None:
        if not isinstance(result, dict) or any(key not in fresh or fresh[key] != value for key, value in result.items()):
            raise ValueError('caller_comparison_result_mismatch')
    return fresh


def reanalysis_report(report: _SourceReport, confidence: object = None, evidence_root: _Root = None) -> dict[str, object]:
    """元資料を保全して再解析報告を作る。出力schemaで旧sourceの能力を昇格しない。"""
    inspection = contracts.inspect_evaluation(report, evidence_root)
    result = _estimate_inspection(inspection, confidence)
    snapshot = inspection.get('snapshot', {})
    source = {'run_id': snapshot.get('run_id'), 'experiment_id': snapshot.get('experiment_id'),
              'report_sha256': inspection.get('source_report_sha256')}
    spec = result['analysis_spec']
    return {'schema_version': 5, 'report_kind': 'analysis', 'source': source, 'analysis_spec': spec,
            **_analysis_lineage([source], spec, [inspection]), 'statistics': result}


def main() -> int:
    """入力不正2、開始後の要求/保存失敗1、要求成功0を返す再解析CLI。"""
    parser = argparse.ArgumentParser(description='Revalidate saved evidence without launching an AI')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path)
    parser.add_argument('--confidence', type=float)
    arguments = parser.parse_args()
    try:
        if arguments.output.exists():
            parser.error('output already exists: {}'.format(arguments.output))
        source: _SourceReport = json.loads(arguments.report.read_text(encoding='utf-8'))
        decoded = contracts.decode_input(source)
        if decoded['input_status'] != 'valid':
            parser.error(','.join(decoded['issues']))
        if arguments.confidence is not None and (not 0 < arguments.confidence < 1):
            parser.error('confidence_invalid')
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, '{}\n'.format(error))
    try:
        result = reanalysis_report(source, arguments.confidence, arguments.evidence_root)
        contracts.write_report_exclusive(arguments.output, result, phase='reanalysis_final',
                                         operation_id=cast(str, result['analysis_id']))
        print(json.dumps({'analysis_id': result['analysis_id'],
                          'status': _item_field(result['statistics'], 'status'),
                          'verification': _item_field(_item_field(result['statistics'], 'verification'), 'status')}))
        return 0 if _item_field(_item_field(result['statistics'], 'assessment'), 'request_success') else 1
    except contracts.ArtifactWriteError as error:
        parser.exit(1, '{}\n'.format(error))


if __name__ == '__main__':
    raise SystemExit(main())
