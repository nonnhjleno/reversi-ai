"""レビュー済みテストの階層選択と、未知・欠損時の保守的な扱いを検証する。"""

import pytest

from tools import feedback

P2 = 'tests/test_ai_phase2.py'
P3 = 'tests/test_ai_phase3.py'
OWN = 'tests/test_profile_classification.py'


def _collection():
    """必須基底を備える合成collection。所属の期待値は各テストで独立に指定する。"""
    ids = [f'{path}::test_synthetic' for path in sorted(feedback.AI_TEST_FILES)]
    ids += [f'tests/test_harness.py::test_{name}' for name in feedback.QUICK_HARNESS]
    ids += [f'tests/test_quality.py::test_{name}' for name in feedback.QUICK_QUALITY]
    ids += [f'tests/test_harness.py::test_{name}' for name in feedback.STANDARD_EXTRA]
    for files in feedback.REVIEWED_AI_TESTS.values():
        for path, names in files.items():
            for name in names:
                base = f'{path}::{name}'
                if name == 'test_depth_zero_through_four_and_terminal_early_stop':
                    ids += [base + '[0-initial]', base + '[4-middle]']
                else:
                    ids.append(base)
    return ids


def test_reviewed_groups_are_disjoint_and_ordered():
    """Quick包含とFull専用群の分離を、実行順を保った選択集合で照合する。"""
    ids = _collection()
    quick = set(feedback.select_tests(ids, 'quick'))
    standard = set(feedback.select_tests(ids, 'standard-ai'))
    assert quick < standard < set(ids)
    groups = [
        {f'{path}::{name}' for path, names in files.items() for name in names}
        for files in feedback.REVIEWED_AI_TESTS.values()
    ]
    assert sum(map(len, groups)) == len(set.union(*groups))
    assert not any('test_depth_zero_through_four' in name for name in quick | standard)


@pytest.mark.parametrize(
    'base,in_quick,in_standard',
    [
        (P2 + '::test_initial_features_and_hand_calculated_first_move', True, True),
        (P2 + '::test_snapshot_callbacks_match_independent_rules_without_mutation', False, True),
        (P3 + '::test_initial_tree_has_known_counts', True, True),
        (P3 + '::test_all_fixed_positions_match_independent_negamax', False, True),
        (P3 + '::test_depth_zero_through_four_and_terminal_early_stop', False, False),
        (P2 + '::test_future_unreviewed', False, False),
        (OWN + '::test_future_unreviewed', False, False),
    ],
)
def test_quick_standard_and_full_membership(base, in_quick, in_standard):
    """目的が異なる各群と未知名について、固定した所属の期待値を使う。"""
    ids = _collection()
    if not any(node.split('[', 1)[0] == base for node in ids):
        ids.append(base)
    for profile, expected in [('quick', in_quick), ('standard-ai', in_standard)]:
        selected = feedback.select_tests(ids, profile)
        assert any(node.split('[', 1)[0] == base for node in selected) is expected


@pytest.mark.parametrize(
    'missing',
    [
        P2 + '::test_initial_features_and_hand_calculated_first_move',
        P3 + '::test_all_fixed_positions_match_independent_negamax',
        P3 + '::test_depth_zero_through_four_and_terminal_early_stop',
        OWN + '::test_unreviewed_case_stays_full_only',
    ],
)
def test_missing_reviewed_base_fails_closed(missing):
    """非選択群を含め登録済み名の欠損を拒否し、改名・削除の見落としを防ぐ。"""
    ids = [node for node in _collection() if node.split('[', 1)[0] != missing]
    for profile in ('quick', 'standard-ai'):
        with pytest.raises(ValueError):
            feedback.select_tests(ids, profile)


def test_unreviewed_case_stays_full_only():
    """既知ファイルの未登録関数を暗黙のファイル全件選択へ戻さない。"""
    future = P3 + '::test_future_expensive_search[depth99]'
    for profile in ('quick', 'standard-ai'):
        assert future not in feedback.select_tests(_collection() + [future], profile)


def test_collection_order_and_parameter_families_are_preserved():
    """同じ登録基底のパラメータを落とさず、collection順を並べ替えない。"""
    base = P3 + '::test_search_rejects_invalid_depth'
    ids = _collection() + [base + '[future-a]', base + '[future-b]']
    for profile in ('quick', 'standard-ai'):
        selected = feedback.select_tests(ids, profile)
        assert selected[-2:] == ids[-2:]
        assert feedback.select_tests(list(reversed(ids)), profile) == list(reversed(selected))


@pytest.mark.parametrize('path', [P2, P3, OWN])
def test_reviewed_files_still_require_full_on_modification(path):
    """名前別の選択登録を、ファイル差分の安全認定へ読み替えない。"""
    assert feedback.full_reasons([('M', path)])
    assert feedback.recommended_profile([('M', path)]) == 'full'


@pytest.mark.parametrize(
    'path,expected',
    [('reversi_player/ai_engine.py', 'quick'), ('reversi_player/reversi.py', 'standard-ai')],
)
def test_existing_ai_file_routing_is_preserved(path, expected):
    """前回の品質ゲート整理で確定したAI境界のroutingを保持する。"""
    assert not feedback.full_reasons([('M', path)])
    assert feedback.recommended_profile([('M', path)]) == expected
