"""有限母集合の低水準区間計算。N・抽出・固定得点・原traceは呼出側が先に検証する。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing import Any, NotRequired, TypedDict

    class _GameRecord(TypedDict, total=False):
        """算術が参照する入力形状。任意キーと未検証得点を許し、正式資格は表さない。"""

        pair_id: str | None
        case_id: str | None
        leg: int | None
        game_status: str | None
        protocol_status: str | None
        budget_status: str | None
        colors: dict[str, str]
        seeds: dict[str, int | None]
        score_a: object

    class _PairScore(TypedDict):
        """既存の値域検査を通った先後平均と元の得点値。原trace検証とは別である。"""

        case_id: str | None
        score: float
        game_scores: list[object]

    class _Interval(TypedDict):
        """算術区間の既存辞書形状。全数の場合はrhoキーを持たない。"""

        mean: float
        lower: float
        upper: float
        half_width: float
        method: str
        confidence: float
        rho: NotRequired[float]
        calculation_status: str
        result_scope: str
        formal_eligibility: str

    class _StatisticsResult(TypedDict, total=False):
        """単独/対応計算の既存結果。失敗経路によるキー欠損やNoneを保持する。"""

        status: str
        calculation_status: str
        result_scope: str
        formal_eligibility: str
        reason: str | None
        pair_count: int
        invalid_pairs: dict[str, str]
        point_rate: float | None
        interval: _Interval | None
        pairs: dict[str, _PairScore]
        mean_delta: float


def confidence_interval(values: Sequence[float], population_size: int, confidence: float = 0.95,
                        value_range: tuple[float, float] = (0.0, 1.0)) -> _Interval:
    """有界な固定得点の有限母集合平均に対し、全数区間または有限母集合boundを計算する。

    valuesは一様な非復元抽出の観測、population_sizeは検証済みNを想定する。
    n=Nなら幅0だが、その網羅性/trace/決定性はここで検証しない。正式入口はanalysis。
    入力は更新せず、値域・N・confidenceの算術条件不正はValueErrorとする。
    """
    if not values:
        raise ValueError('at least one observation is required')
    if type(population_size) is not int or population_size < len(values):
        raise ValueError('population size must be at least the sample size')
    if not 0 < confidence < 1:
        raise ValueError('confidence must be between zero and one')
    lower, upper = value_range
    if lower >= upper or any(not lower <= value <= upper for value in values):
        raise ValueError('observations must lie within a nonempty value range')
    mean = sum(values) / len(values)
    # 幅0は固定得点の全数に限る。その網羅性/資格を保証するのは呼出側。
    if len(values) == population_size:
        return {'mean': mean, 'lower': mean, 'upper': mean, 'half_width': 0.0,
                'method': 'finite_population_census', 'confidence': confidence,
                'calculation_status': 'computed', 'result_scope': 'arithmetic_only',
                'formal_eligibility': 'not_assessed'}
    count = len(values)
    fraction = count / population_size
    # 標本比率に応じた有限母集合補正を使い、同点標本だけで幅0にはしない。
    rho = 1 - (count - 1) / population_size if fraction <= 0.5 else (1 - fraction) * (1 + 1 / count)
    half_width = (upper - lower) * math.sqrt(rho * math.log(2 / (1 - confidence)) / (2 * count))
    return {
        'mean': mean,
        'lower': max(lower, mean - half_width),
        'upper': min(upper, mean + half_width),
        'half_width': half_width,
        'method': 'hoeffding_serfling_finite_population',
        'confidence': confidence,
        'rho': rho,
        'calculation_status': 'computed', 'result_scope': 'arithmetic_only',
        'formal_eligibility': 'not_assessed',
    }


def pair_scores(games: Iterable[_GameRecord]) -> tuple[dict[str, _PairScore], dict[str, str]]:
    """記録上の条件を満たす先後2legを得点化し、ペア別得点と不適格理由を返す。

    A勝1・引分0.5・負0の2局平均。保存statusを参照する低水準関数であり、
    raw意味や母集合を検証した正式資格とは別。入力を変更しない。
    """
    grouped: dict[str, list[_GameRecord]] = {}
    reasons: dict[str, str] = {}
    for game in games:
        pair_id = game.get('pair_id')
        if not pair_id:
            continue
        grouped.setdefault(pair_id, []).append(game)
    scores: dict[str, _PairScore] = {}
    for pair_id, pair_games in grouped.items():
        if len(pair_games) != 2:
            reasons[pair_id] = 'incomplete_pair'
            continue
        if {game.get('leg') for game in pair_games} != {0, 1}:
            reasons[pair_id] = 'invalid_pair_legs'
            continue
        if any(game.get('game_status') != 'complete' for game in pair_games):
            reasons[pair_id] = 'incomplete_or_invalid_game'
            continue
        if any(game.get('protocol_status') != 'verified' for game in pair_games):
            reasons[pair_id] = 'protocol_not_verified'
            continue
        if any(game.get('budget_status') not in ('verified', 'exempt') for game in pair_games):
            reasons[pair_id] = 'budget_not_verified'
            continue
        # 未検証得点だけの動的境界。後続の既存値域検査は省略/変更しない。
        values: list[Any] = []
        for game in pair_games:
            values.append(game.get('score_a'))
        if {game.get('colors', {}).get('B') for game in pair_games} != {'A', 'B'}:
            reasons[pair_id] = 'roles_not_swapped'
            continue
        if len({game.get('case_id') for game in pair_games}) != 1:
            reasons[pair_id] = 'case_id_mismatch'
            continue
        if len({tuple(sorted(game.get('seeds', {}).items())) for game in pair_games}) != 1:
            reasons[pair_id] = 'player_seed_mismatch'
            continue
        if any(value not in (0.0, 0.5, 1.0) for value in values):
            reasons[pair_id] = 'invalid_game_score'
            continue
        scores[pair_id] = {
            'case_id': pair_games[0]['case_id'],
            'score': sum(values) / 2,
            'game_scores': values,
        }
    return scores, reasons


def estimate_pair_scores(games: Sequence[_GameRecord], population_size: int,
                         confidence: float = 0.95, eligible: bool = True,
                         ineligibility_reason: str | None = None,
                         expected_pair_count: int | None = None,
                         expected_case_ids: Iterable[str | None] | None = None) -> _StatisticsResult:
    """ペア完全性と指定coverageを確認してAの平均得点区間を返す低水準関数。

    eligibleは算術計算の許可だけで正式資格ではない。Nの根拠は検証せず、成功も
    computed/arithmetic_only/not_assessedとする。失敗ペアを除外して予定nを縮めない。
    資格/coverage不足や区間計算のValueErrorはunavailableにする。原gamesは変更しない。
    """
    scores, invalid = pair_scores(games)
    if not eligible:
        return {
            'status': 'unavailable', 'reason': ineligibility_reason or 'evaluation_not_eligible',
            'pair_count': len(scores), 'invalid_pairs': invalid, 'point_rate': None, 'interval': None,
        }
    observed_cases = {item['case_id'] for item in scores.values()}
    expected_cases_match = expected_case_ids is None or observed_cases == set(expected_case_ids)
    pair_count_matches = expected_pair_count is None or len(scores) == expected_pair_count
    if invalid or len(scores) != len(games) // 2 or not pair_count_matches or not expected_cases_match:
        return {
            'status': 'unavailable',
            'reason': 'expected_cases_mismatch' if not expected_cases_match else 'incomplete_pairs',
            'pair_count': len(scores),
            'invalid_pairs': invalid, 'point_rate': None, 'interval': None,
        }
    values = [item['score'] for item in scores.values()]
    try:
        interval = confidence_interval(values, population_size, confidence)
    except ValueError as error:
        return {
            'status': 'unavailable', 'reason': str(error), 'pair_count': len(values),
            'invalid_pairs': invalid, 'point_rate': None, 'interval': None,
        }
    return {
        'status': 'computed', 'calculation_status': 'computed', 'result_scope': 'arithmetic_only',
        'formal_eligibility': 'not_assessed', 'reason': None, 'pair_count': len(values),
        'invalid_pairs': invalid, 'point_rate': interval['mean'], 'interval': interval,
        'pairs': scores,
    }


def paired_difference(before_games: Iterable[_GameRecord], after_games: Iterable[_GameRecord],
                      population_size: int, confidence: float = 0.95,
                      expected_pair_count: int | None = None,
                      expected_case_ids: Iterable[str | None] | None = None) -> _StatisticsResult:
    """同じpair IDのafter-before得点差を[-1,1]の有限母集合区間で推定する。

    固定母集合・取得条件同一性・trace検証は呼出側の責務。両側の予定ペアが
    揃わなければunavailableを返し、区間の不正算術条件はValueErrorを伝播する。
    """
    before, before_invalid = pair_scores(before_games)
    after, after_invalid = pair_scores(after_games)
    expected_cases_match = expected_case_ids is None or (
        {item['case_id'] for item in before.values()} == set(expected_case_ids)
        and {item['case_id'] for item in after.values()} == set(expected_case_ids)
    )
    pair_count_matches = expected_pair_count is None or (
        len(before) == expected_pair_count and len(after) == expected_pair_count
    )
    if (before_invalid or after_invalid or before.keys() != after.keys()
            or not expected_cases_match or not pair_count_matches):
        reason = 'expected_cases_mismatch' if not expected_cases_match else 'unmatched_or_invalid_pairs'
        return {'status': 'unavailable', 'reason': reason, 'interval': None}
    differences = []
    for pair_id in before:
        if before[pair_id]['case_id'] != after[pair_id]['case_id']:
            return {'status': 'unavailable', 'reason': 'case_id_mismatch', 'interval': None}
        differences.append(after[pair_id]['score'] - before[pair_id]['score'])
    if not differences:
        return {'status': 'unavailable', 'reason': 'no_complete_pairs', 'interval': None}
    interval = confidence_interval(differences, population_size, confidence, (-1.0, 1.0))
    return {
        'status': 'computed', 'calculation_status': 'computed', 'result_scope': 'arithmetic_only',
        'formal_eligibility': 'not_assessed', 'reason': None, 'pair_count': len(differences),
        'mean_delta': interval['mean'], 'interval': interval,
    }
