"""Phase 5レビューで判明した、復帰後の実経過超過を独立時計で検証する。Full専用。"""

from unittest.mock import patch
import pytest
from tools import quality


@pytest.mark.parametrize(
    'limit,elapsed,code,expected',
    [
        (1.0, 0.5, 0, True),
        (1.0, 1.0, 0, True),
        (1.0, 1.01, 0, False),
        (1800.0, 33049.0, 0, False),
        (1.0, 0.5, 7, False),
    ],
)
def test_measured_elapsed_budget_is_checked_after_native_wait(
    tmp_path, limit, elapsed, code, expected
):
    """OS待機が正常終了しても、外部経過上限の違反を成功へ変えない。"""
    seen = []

    class Finished:
        """既に終了した子を模す。ホストを休止させず時計の飛びだけを与える。"""

        def __init__(self, *args, **kwargs):
            """Popenの生成を受け付ける。"""

        def __enter__(self):
            """同じ所有processを返す。"""
            return self

        def __exit__(self, *args):
            """追加の停止処理を行わない。"""

        def wait(self, timeout):
            """要求された既存待機上限と実終了値を保持する。"""
            seen.append(timeout)
            return code

    ticks = iter([100.0, 100.0 + elapsed, 100.0 + elapsed + 0.01])
    log = tmp_path / 'raw.txt'
    with (
        patch.object(quality.subprocess, 'Popen', Finished),
        patch.object(quality.time, 'perf_counter', side_effect=lambda: next(ticks)),
    ):
        result = quality.run_step(quality.Step('elapsed', ('owned-child',), limit), tmp_path, log)
    assert result['passed'] is expected
    assert result['exit_code'] == code and seen == [limit]
    assert result['seconds'] >= elapsed
    if elapsed > limit:
        assert 'timeout_elapsed' in result['error']
        assert 'timeout_elapsed' in log.read_text(encoding='utf-8')
    else:
        assert result['error'] is None


def test_comparison_reference_reuse_matches_fresh_independent_inputs():
    """共有する参照のkeyで盤面・手番・深度を落とさず、不変な同じ結果を返す。"""
    from dataclasses import FrozenInstanceError
    from harness import referee
    from reversi_player import ai_engine
    import test_ai_phase4 as phase4

    rows = referee.INITIAL_ROWS
    after = referee.apply_move(rows, 'B', (3, 2))
    keys = [(rows, 'B', 0), (rows, 'B', 1), (rows, 'W', 1), (after, 'W', 1)]
    phase4._comparison_reference.cache_clear()
    try:
        with (
            patch.object(phase4, '_oracle', wraps=phase4._oracle) as oracle,
            patch.object(ai_engine, 'search_minimax', wraps=ai_engine.search_minimax) as minimum,
        ):
            for key in keys:
                cached = phase4._comparison_reference(*key)
                assert phase4._comparison_reference(*key) is cached
                expected, result = cached
                assert isinstance(expected, tuple) and isinstance(expected[1], tuple)
                with pytest.raises(FrozenInstanceError):
                    result.value = 999
            assert oracle.call_count == minimum.call_count == len(keys)
        for key in keys:
            assert phase4._comparison_reference(*key) == phase4._comparison_reference.__wrapped__(
                *key
            )
    finally:
        phase4._comparison_reference.cache_clear()


def test_ordering_matrix_executes_each_candidate_with_shared_reference():
    """着手順序の2候補は別々に探索し、参照の再利用で候補比較を省略しない。"""
    from reversi_player import ai_engine
    import test_ai_phase4 as phase4

    phase4._comparison_reference.cache_clear()
    try:
        with (
            patch.object(
                ai_engine, 'search_alpha_beta', wraps=ai_engine.search_alpha_beta
            ) as candidate,
            patch.object(ai_engine, 'search_minimax', wraps=ai_engine.search_minimax) as minimum,
        ):
            for ordered in (False, True):
                phase4.test_alpha_beta_matches_independent_oracle_and_reference(
                    'initial', 1, ordered, 'B'
                )
            assert minimum.call_count == 1
            assert candidate.call_count == 2
            assert [call.kwargs['ordered'] for call in candidate.call_args_list] == [False, True]
    finally:
        phase4._comparison_reference.cache_clear()
