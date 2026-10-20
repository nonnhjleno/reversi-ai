"""Fullを代替しない、直列の選択テストと再照合可能な補助証拠を実行する。"""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tools import evidence, quality

AI_TEST_FILES = frozenset(
    {
        'tests/test_ai_compatibility.py',
        'tests/test_ai_engine.py',
        'tests/test_invariants.py',
        'tests/test_result.py',
        'tests/test_stdin_commands.py',
    }
)
QUICK_HARNESS = (
    'response_contract_accepts_exact_wire_format_without_mutation',
    'response_contract_rejects_malformed_output_without_mutation',
    'temporal_popen_return_is_not_child_initialization_barrier',
    'temporal_model_load_evidence_distinguishes_missing_from_invalid',
    'temporal_qpc_boundaries_preserve_ambiguity',
    'temporal_bad_type_survives_missing_clock_or_endpoint',
    'finite_population_interval_handles_constant_scores_and_census',
    'budget_config_rejects_invalid_values_and_exemption_hash_mismatch',
)
QUICK_QUALITY = (
    'analysis_typing_boundary_guard_rejects_mutations',
    'analysis_typing_limits_operation_views',
    'budget_typing_boundary_guard_rejects_mutations',
    'budget_typing_limits_cast_to_documented_local_views',
    'contract_typing_boundary_guard_rejects_mutations',
    'contract_typing_limits_views_and_preserves_raw_boundary',
    'documentation_file_links_resolve',
    'evaluate_typing_boundary_guard_rejects_mutations',
    'evaluate_typing_limits_operation_views',
    'model_matches_current_imports_and_formal_entry_edges',
    'protocol_typing_boundary_guard_rejects_mutations',
    'protocol_typing_limits_cast_to_observed_exit_view',
)
STANDARD_EXTRA = (
    'schema5_reference_generation_roundtrip',
    'schema5_cli_saves_reloads_and_reanalyses',
    'paired_complete_games_are_reproducible_and_serializable',
    'comparison_rejects_changed_conditions_or_opponent',
    'opening_evaluation_replays_forced_moves_to_both_players',
    'temporal_normal_schema5_reaches_all_formal_entries',
    'evaluation_records_faults_and_continues',
    'bad_player_is_terminated',
    'startup_has_its_own_timeout',
    'genmove_uses_its_longer_deadline',
    'total_response_time_is_checked_after_validation',
)


def strings(value: object) -> list[str]:
    """識別子配列の型を検証する。欠損や異型値を空配列へ補修しない。"""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError('string_list_required')
    return [item for item in value if isinstance(item, str)]


def read_json(path: Path) -> dict[str, object]:
    """サイズ制限付きJSON辞書を読む。非辞書は明示的に拒否する。"""
    value: object = json.loads(evidence._read(path))
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError('mapping_required')
    return {key: item for key, item in value.items() if isinstance(key, str)}


def policy_hash() -> str:
    """選択規則を含む実装全体の版を固定し、旧規則での再解釈を防ぐ。"""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def select_tests(nodeids: list[str], profile: str) -> list[str]:
    """明示基底名とAI専用ファイル全件を選択する。重複・必須欠損・空は例外。"""
    if profile not in ('quick', 'standard-ai') or len(set(nodeids)) != len(nodeids):
        raise ValueError('profile_or_duplicate_selection')
    required = {f'tests/test_harness.py::test_{name}' for name in QUICK_HARNESS}
    required.update(f'tests/test_quality.py::test_{name}' for name in QUICK_QUALITY)
    if profile == 'standard-ai':
        required.update(f'tests/test_harness.py::test_{name}' for name in STANDARD_EXTRA)
    bases = {nodeid.split('[', 1)[0] for nodeid in nodeids}
    if not required <= bases or not AI_TEST_FILES <= {nodeid.split('::')[0] for nodeid in nodeids}:
        raise ValueError('required_selection_missing')
    selected = [
        nodeid
        for nodeid in nodeids
        if nodeid.split('::')[0] in AI_TEST_FILES | {'tests/test_feedback.py'}
        or nodeid.split('[', 1)[0] in required
    ]
    if not selected:
        raise ValueError('empty_selection')
    return selected


def full_reasons(changes: list[tuple[str, str]]) -> list[str]:
    """AI専用境界外・追加/削除/renameをFullへ戻す。名前の部分一致で選ばない。"""
    allowed = AI_TEST_FILES | {'reversi_player/ai_engine.py', 'reversi_player/reversi.py'}
    return [
        status + ':' + path
        for status, path in changes
        if status != 'M'
        or not (path in allowed or (path.endswith('.md') and path.startswith('docs/')))
    ]


def changed_paths(root: Path, base: str) -> dict[str, object]:
    """baseから作業ツリーまでの差分と未追跡を取得する。Git不明はFullへ戻す。"""

    def git(*arguments: str) -> bytes:
        """認証や更新をせず、読み取り専用のGit結果だけを取得する。"""
        return subprocess.run(
            ['git', *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=30,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
        ).stdout

    try:
        commit = git('rev-parse', '--verify', base + '^{commit}').decode().strip()
        fields = (
            git('diff', '--name-status', '--no-renames', '-z', commit, '--')
            .decode('utf-8')
            .split('\0')
        )
        changes = list(zip(fields[:-1:2], fields[1::2], strict=True))
        changes.extend(
            ('?', path)
            for path in git('ls-files', '--others', '--exclude-standard', '-z')
            .decode('utf-8')
            .split('\0')
            if path
        )
        return {
            'base': base,
            'base_commit': commit,
            'changes': changes,
            'full_reasons': full_reasons(changes),
        }
    except (OSError, ValueError, subprocess.SubprocessError) as failure:
        return {'base': base, 'full_reasons': ['git_unavailable:' + type(failure).__name__]}


def pytest_addoption(parser: pytest.Parser) -> None:
    """計画取得と計画照合の内部オプションを登録する。Fullの選択は変更しない。"""
    parser.addoption('--feedback-collect', default=None)
    parser.addoption('--feedback-selection', default=None)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """collect-onlyでは全件保存、実行時は同一collectionを確認してから絞る。"""
    nodeids = [item.nodeid for item in items]
    collect = config.getoption('feedback_collect')
    selection = config.getoption('feedback_selection')
    if collect:
        evidence.save_new(Path(str(collect)), evidence.encoded_json({'all_nodeids': nodeids}))
    if selection:
        plan = read_json(Path(str(selection)))
        if strings(plan['all_nodeids']) != nodeids or plan['policy_hash'] != policy_hash():
            raise pytest.UsageError('feedback collection/policy mismatch')
        selected = select_tests(nodeids, str(plan['profile']))
        if selected != strings(plan['selected_nodeids']):
            raise pytest.UsageError('feedback planned selection mismatch')
        keep = set(selected)
        deselected = [item for item in items if item.nodeid not in keep]
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if item.nodeid in keep]


def retained_paths(
    output: Path, references: list[object], *, capture: bool
) -> tuple[list[dict[str, object]], dict[str, Path]]:
    """原path/hashを保ち同一bytesをrun内へ保管する。再解析では保管先だけを使う。"""
    records: list[dict[str, object]] = []
    paths: dict[str, Path] = {}
    for item in references:
        reference = evidence.mapping(item)
        original = str(reference['path'])
        digest = str(reference['sha256'])
        if re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            raise ValueError('artifact_digest_invalid')
        relative = 'artifacts/' + digest
        target = output / relative
        target.resolve().relative_to(output.resolve())
        if capture and not target.exists():
            data = evidence._read(Path(original))
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('original_artifact_changed')
            with target.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        if hashlib.sha256(evidence._read(target)).hexdigest() != digest:
            raise ValueError('retained_artifact_changed')
        records.append({'path': original, 'sha256': digest, 'retained_path': relative})
        paths[original] = target
    return records, paths


def feedback_errors(output: Path, report: dict[str, object]) -> list[str]:
    """保存flagではなく選択規則・元journal/JUnit・保管bytesを再照合する。"""
    errors: list[str] = []
    try:
        if (
            report['report_kind'] != 'quality_feedback'
            or type(report['format_version']) is not int
            or report['format_version'] != 1
            or report['full_quality_passed'] is not False
        ):
            raise ValueError('feedback_kind_or_full_claim_invalid')
        plan = read_json(output / 'selection.json')
        manifest = evidence.mapping(report['manifest'])
        if (
            manifest.get('run_id') != output.name
            or type(report['measurement_only']) is not bool
            or report['environment'] != manifest.get('environment')
        ):
            raise ValueError('feedback_manifest_mismatch')
        if report.get('execution_error') is not None:
            errors.append('execution_failed')
        all_ids = strings(plan['all_nodeids'])
        selected = select_tests(all_ids, str(report['profile']))
        if (
            plan['profile'] != report['profile']
            or plan['policy_hash'] != policy_hash()
            or plan['policy_version'] != 1
            or selected != strings(plan['selected_nodeids'])
            or [nodeid for nodeid in all_ids if nodeid not in set(selected)]
            != strings(plan['deselected_nodeids'])
            or read_json(output / 'collection.json')['all_nodeids'] != all_ids
        ):
            raise ValueError('selection_plan_mismatch')
        if (
            report['selection_sha256']
            != hashlib.sha256(evidence._read(output / 'selection.json')).hexdigest()
        ):
            errors.append('selection_hash_mismatch')
        if report['source_hashes'] != report['source_hashes_end']:
            errors.append('source_snapshot_mismatch')
        sources = evidence.mapping(report['source_hashes'])
        if (
            sources != evidence.mapping(report['manifest']).get('source_hashes')
            or not {'pyproject.toml', 'tools/feedback.py', 'tools/quality.py'} <= sources.keys()
            or any(
                not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None
                for digest in sources.values()
            )
        ):
            errors.append('source_manifest_invalid')
        steps = report['steps']
        if not isinstance(steps, list):
            raise ValueError('steps_invalid')
        records = [evidence.mapping(step) for step in steps]
        if (
            manifest.get('steps')
            != [{'name': step['name'], 'command': step['command']} for step in records]
            or manifest.get('source_hashes_end') != report['source_hashes_end']
        ):
            raise ValueError('manifest_steps_or_snapshot_mismatch')
        if [step['name'] for step in records] != [
            'environment',
            'model',
            'typing_scope',
            'dependencies',
            'lint',
            'format',
            'typing',
            'collection',
            'pytest',
            'snapshot',
        ]:
            raise ValueError('required_steps_mismatch')
        for step in records:
            if (
                step['passed'] is not True
                or step['exit_code'] != 0
                or step['error'] is not None
                or step['seconds'] is None
            ):
                errors.append('step_not_passed:' + str(step['name']))
            path = (output / str(step['log'])).resolve()
            path.relative_to(output.resolve())
            hashes = evidence.mapping(report['log_hashes'])
            if hashlib.sha256(evidence._read(path)).hexdigest() != hashes.get(str(step['log'])):
                errors.append('log_hash_mismatch')
        source = evidence.mapping(report['test_step'])
        artifacts = source['artifacts']
        if not isinstance(artifacts, list):
            raise ValueError('artifacts_invalid')
        retained, paths = retained_paths(output, artifacts, capture=False)
        if report['retained_artifacts'] != retained:
            errors.append('retained_manifest_mismatch')
        fresh = evidence.collect_step(output, output.name, records[-2], paths)
        if fresh != source:
            errors.append('test_summary_mismatch')
        tests = fresh['tests']
        if not isinstance(tests, list):
            raise ValueError('tests_invalid')
        actual = [evidence.mapping(test) for test in tests]
        if [test['test_id'] for test in actual] != selected:
            errors.append('executed_selection_mismatch')
        if fresh['collection_status'] != 'complete':
            errors.append('test_evidence_incomplete')
        if any(test['outcome'] != 'passed' for test in actual):
            errors.append('test_failed_or_skipped')
        if strings(report['skipped_nodeids']) != [
            str(test['test_id']) for test in actual if test['outcome'] == 'skipped'
        ]:
            errors.append('skip_summary_mismatch')
    except (OSError, ValueError, KeyError, TypeError) as failure:
        errors.append('feedback_unavailable:' + type(failure).__name__ + ':' + str(failure))
    return errors


def verify_feedback(output: Path) -> list[str]:
    """公開済み補助証拠を再解析する。過去の証拠やsummaryを修復しない。"""
    try:
        report = read_json(output / 'feedback.json')
        errors = feedback_errors(output, report)
        if report.get('passed') is not True or report.get('errors') != []:
            errors.append('feedback_not_passed')
        return errors
    except (OSError, ValueError) as failure:
        return ['feedback_unavailable:' + type(failure).__name__]


def run_feedback(
    root: Path, output: Path, profile: str, routing: dict[str, object], measure: bool = False
) -> dict[str, object]:
    """独立profileを直列実行し、Fullと異なるkindで最後に排他公開する。"""
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'pytest').mkdir()
    (output / 'artifacts').mkdir()
    before = quality._source_hashes(root)
    started = time.perf_counter()
    results = []
    for name, check in (
        ('environment', quality._environment_errors),
        ('model', quality.validate_model),
        ('typing_scope', quality.validate_scope),
    ):
        local_started = time.perf_counter()
        errors = check(root)
        results.append(
            quality._local_result(
                name, errors, output / (name + '.txt'), time.perf_counter() - local_started
            )
        )
    manifest = evidence.manifest(root, output, before, [])
    manifest['reproduce_gate'] = [
        sys.executable,
        '-B',
        '-m',
        'tools.feedback',
        '--profile',
        profile,
        '--base',
        str(routing.get('base', 'origin/main')),
        *(['--measure-profile'] if measure else []),
    ]
    environment: dict[str, object] = {
        'python': sys.version,
        'executable': sys.executable,
        'platform': sys.platform,
        'prefix': sys.prefix,
        'base_prefix': sys.base_prefix,
        'packages': {
            distribution.name: distribution.version for distribution in metadata.distributions()
        },
    }
    manifest['environment'] = environment
    report: dict[str, object] = {
        'report_kind': 'quality_feedback',
        'format_version': 1,
        'profile': profile,
        'full_quality_passed': False,
        'measurement_only': measure,
        'routing': routing,
        'manifest': manifest,
        'environment': environment,
        'source_hashes': before,
    }
    try:
        if any(not result['passed'] for result in results):
            raise ValueError('feedback_precondition_failed')
        for step in quality.required_steps(root, output)[:4]:
            results.append(quality.run_step(step, root, output / (step.name + '.txt')))
        pytest_step = quality.required_steps(root, output)[4]
        collect_command = (
            sys.executable,
            '-B',
            '-m',
            'pytest',
            '-q',
            '-p',
            'no:cacheprovider',
            '--strict-config',
            '--strict-markers',
            '--collect-only',
            '-p',
            'tools.feedback',
            '--feedback-collect=' + str(output / 'collection.json'),
        )
        results.append(
            quality.run_step(
                quality.Step('collection', collect_command, 120), root, output / 'collection.txt'
            )
        )
        if not results[-1]['passed']:
            raise ValueError('collection_failed')
        all_ids = strings(read_json(output / 'collection.json')['all_nodeids'])
        selected = select_tests(all_ids, profile)
        plan: dict[str, object] = {
            'profile': profile,
            'policy_version': 1,
            'policy_hash': policy_hash(),
            'all_nodeids': all_ids,
            'selected_nodeids': selected,
            'deselected_nodeids': [nodeid for nodeid in all_ids if nodeid not in set(selected)],
        }
        evidence.save_new(output / 'selection.json', evidence.encoded_json(plan))
        command = (
            *pytest_step.command,
            '-p',
            'tools.feedback',
            '--feedback-selection=' + str(output / 'selection.json'),
        )
        results.append(
            quality.run_step(quality.Step('pytest', command), root, output / 'pytest.txt')
        )
        summary = evidence.collect_step(output, output.name, {**results[-1]})
        artifacts = summary['artifacts']
        if not isinstance(artifacts, list):
            raise ValueError('artifact_list_invalid')
        retained, paths = retained_paths(output, artifacts, capture=True)
        summary = evidence.collect_step(output, output.name, {**results[-1]}, paths)
        tests = summary['tests']
        if not isinstance(tests, list):
            raise ValueError('tests_invalid')
        report.update(
            test_step=summary,
            retained_artifacts=retained,
            skipped_nodeids=[
                evidence.mapping(test)['test_id']
                for test in tests
                if evidence.mapping(test)['outcome'] == 'skipped'
            ],
            selection_sha256=hashlib.sha256(evidence._read(output / 'selection.json')).hexdigest(),
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as failure:
        report['execution_error'] = type(failure).__name__ + ':' + str(failure)
    snapshot_started = time.perf_counter()
    after = quality._source_hashes(root)
    snapshot_errors = ([] if before == after else ['source_changed']) + quality.validate_scope(root)
    results.append(
        quality._local_result(
            'snapshot',
            snapshot_errors,
            output / 'snapshot.txt',
            time.perf_counter() - snapshot_started,
        )
    )
    report.update(
        steps=results,
        source_hashes_end=after,
        log_hashes={
            result['log']: hashlib.sha256(evidence._read(output / result['log'])).hexdigest()
            for result in results
        },
        wall_seconds=time.perf_counter() - started,
    )
    manifest['ended_at_utc'] = evidence.utc_now()
    manifest['source_hashes_end'] = after
    manifest['steps'] = [
        {'name': result['name'], 'command': result['command']} for result in results
    ]
    errors = feedback_errors(output, report)
    report.update(errors=errors, passed=not errors)
    evidence.save_new(output / 'feedback.json', evidence.encoded_json(report))
    return report


def main() -> int:
    """補助CLI。未知変更は別Fullへ戻し、計測指定だけは未合格を明示して単独実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('quick', 'standard-ai'), default='quick')
    parser.add_argument('--base', default='origin/main')
    parser.add_argument('--measure-profile', action='store_true')
    parser.add_argument('--verify', type=Path)
    arguments = parser.parse_args()
    if arguments.verify:
        errors = verify_feedback(arguments.verify.resolve())
        print(evidence.encoded_json({'errors': errors}))
        return 1 if errors else 0
    output = quality.ROOT / 'evaluation_results' / ('feedback-' + uuid.uuid4().hex)
    routing = changed_paths(quality.ROOT, arguments.base)
    try:
        if routing['full_reasons'] and not arguments.measure_profile:
            output.mkdir(parents=True, exist_ok=False)
            evidence.save_new(output / 'routing.json', evidence.encoded_json(routing))
            full_output = output / ('quality-' + output.name.removeprefix('feedback-'))
            full = quality.run_gate(quality.ROOT, full_output)
            print(f'FULL fallback: {full_output / "quality.json"}')
            return 0 if full['passed'] else 1
        report = run_feedback(
            quality.ROOT, output, arguments.profile, routing, arguments.measure_profile
        )
        print(
            f'FEEDBACK {"PASS" if report["passed"] else "FAIL"} (Full未合格): {output / "feedback.json"}'
        )
        return 0 if report['passed'] else 1
    except (OSError, ValueError, subprocess.SubprocessError) as failure:
        print(f'FEEDBACK FAIL: {failure}; {output}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
