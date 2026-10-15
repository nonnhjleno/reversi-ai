"""品質ゲートの失敗伝播とモデル漏れ検出。改変はpytestの一時領域だけに行う。"""

import ast
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tools import quality
from tools import evidence

ROOT = Path(__file__).resolve().parents[1]


def _model_copy(root: Path) -> None:
    """モデル検査に必要な原ファイルを新規一時領域へ複製する。原証拠は変更しない。"""
    shutil.copytree(
        ROOT / 'harness', root / 'harness', ignore=shutil.ignore_patterns('__pycache__')
    )
    (root / 'docs').mkdir()
    shutil.copy2(ROOT / 'docs/harness_architecture.md', root / 'docs/harness_architecture.md')
    shutil.copy2(ROOT / 'docs/harness_contracts.md', root / 'docs/harness_contracts.md')
    (root / 'tests/fixtures').mkdir(parents=True)
    shutil.copy2(ROOT / 'tests/fixtures/budget_player.py', root / 'tests/fixtures/budget_player.py')


def _fake_result(step: quality.Step, log: Path, code: int) -> quality.StepResult:
    """集約の単体試験用に既知の終了値を作る。実コマンド検証は別の試験で行う。"""
    log.write_text(f'exit={code}\n', encoding='utf-8')
    if step.name in ('pytest', 'smoke'):
        directory = log.parent / step.name
        nodeid = 'tests/test_simulated.py::test_command'
        payloads = [
            {'kind': 'session_start'},
            {'kind': 'selected', 'nodeids': [nodeid]},
            {'kind': 'test_start', 'nodeid': nodeid},
        ]
        payloads.extend(
            {
                'kind': 'phase',
                'nodeid': nodeid,
                'when': when,
                'outcome': 'failed' if code and when == 'call' else 'passed',
                'duration': 0.0,
            }
            for when in ('setup', 'call', 'teardown')
        )
        payloads.extend(
            [
                {'kind': 'test_finish', 'nodeid': nodeid},
                {'kind': 'session_finish', 'exit_code': code, 'capture_errors': []},
            ]
        )
        events = [
            {
                **item,
                'format_version': 1,
                'sequence': index,
                'run_id': log.parent.name,
                'step': step.name,
            }
            for index, item in enumerate(payloads)
        ]
        (directory / 'events.jsonl').write_text(
            '\n'.join(json.dumps(event) for event in events) + '\n', encoding='utf-8'
        )
        failure = '<failure message="simulated" />' if code else ''
        (directory / (step.name + '.xml')).write_text(
            '<testsuites><testsuite tests="1"><testcase classname="tests.test_simulated" name="test_command" time="0">'
            + failure
            + '</testcase></testsuite></testsuites>',
            encoding='utf-8',
        )
    return {
        'name': step.name,
        'command': list(step.command),
        'exit_code': code,
        'seconds': 0.0,
        'log': log.name,
        'error': None,
        'passed': code == 0,
    }


def test_model_matches_current_imports_and_formal_entry_edges():
    assert quality.validate_model(ROOT) == []


@pytest.mark.parametrize(
    'mutation', ['missing_edge', 'extra_edge', 'new_module', 'cycle', 'bypass']
)
def test_model_detects_import_and_formal_entry_contract_drift(tmp_path, mutation):
    """図だけ/実装だけを変更した場合に、過不足・循環・共通入口の迂回を検出する。"""
    _model_copy(tmp_path)
    document = tmp_path / 'docs/harness_architecture.md'
    if mutation == 'missing_edge':
        document.write_text(
            document.read_text(encoding='utf-8').replace('  A --> C\n', ''), encoding='utf-8'
        )
    elif mutation == 'extra_edge':
        document.write_text(
            document.read_text(encoding='utf-8').replace(
                'flowchart LR\n', 'flowchart LR\n  P --> A\n', 1
            ),
            encoding='utf-8',
        )
    elif mutation == 'new_module':
        (tmp_path / 'harness/unmodeled.py').write_text(
            '"""モデル未記載の試験module。"""\n', encoding='utf-8'
        )
    elif mutation == 'cycle':
        path = tmp_path / 'harness/referee.py'
        path.write_text(
            path.read_text(encoding='utf-8') + '\nfrom harness import evaluate\n', encoding='utf-8'
        )
    else:
        path = tmp_path / 'harness/analysis.py'
        path.write_text(
            path.read_text(encoding='utf-8').replace(
                'contracts.inspect_evaluation(', 'unvalidated_source('
            ),
            encoding='utf-8',
        )
    assert quality.validate_model(tmp_path)


@pytest.mark.parametrize(
    'failing_name', ['dependencies', 'lint', 'format', 'typing', 'pytest', 'smoke']
)
def test_gate_propagates_each_required_nonzero_and_keeps_other_results(tmp_path, failing_name):
    """各必須検査の単独失敗でも、他の成功結果や参考集計でgateを成功にしない。"""
    called = []

    def runner(step, root, log):
        """一種類だけnonzeroを返し、検査順と失敗後の実行継続を記録する。"""
        called.append(step.name)
        return _fake_result(step, log, 7 if step.name == failing_name else 0)

    report = quality.run_gate(ROOT, tmp_path / 'run', runner)
    assert not report['passed']
    assert report['model_errors'] == []
    assert called == ['dependencies', 'lint', 'format', 'typing', 'pytest', 'smoke']
    assert [item['name'] for item in report['steps']] == report['planned_steps']
    assert next(item for item in report['steps'] if item['name'] == failing_name)['exit_code'] == 7
    assert (tmp_path / 'run/quality.json').is_file()


def test_gate_environment_unavailable_is_not_executed_or_successful(tmp_path, monkeypatch):
    monkeypatch.setattr(quality, '_environment_errors', lambda root: ['clock runtime未対応'])

    def forbidden_runner(step, root, log):
        """対応環境を失った場合は外部検査を開始しないことを確認する。"""
        raise AssertionError('外部検査が実行された')

    report = quality.run_gate(ROOT, tmp_path / 'run', forbidden_runner)
    assert not report['passed']
    assert all(
        item['exit_code'] is None and item['seconds'] is None and not item['passed']
        for item in report['steps']
        if item['name'] in {'dependencies', 'lint', 'format', 'typing', 'pytest', 'smoke'}
    )


def test_gate_missing_model_cannot_be_covered_by_successful_commands(tmp_path, monkeypatch):
    def missing_model(root):
        """モデル読込不能は既知の過不足とは別の理由でgateを失敗にする。"""
        raise FileNotFoundError('missing model')

    def runner(step, root, log):
        """全コマンド成功だけではモデル未確認を補えない。"""
        return _fake_result(step, log, 0)

    monkeypatch.setattr(quality, 'validate_model', missing_model)
    report = quality.run_gate(ROOT, tmp_path / 'run', runner)
    assert not report['passed']
    assert report['model_errors']


@pytest.mark.parametrize('mutation', ['unpinned', 'undeclared', 'version', 'missing'])
def test_environment_detects_missing_or_changed_development_contract(
    tmp_path, monkeypatch, mutation
):
    """依存不在・宣言欠損・非固定版・版違いは対応環境として認可しない。"""
    source = (ROOT / 'requirements-dev.txt').read_text(encoding='utf-8')
    if mutation == 'unpinned':
        source = source.replace('ruff==0.17.0', 'ruff>=0.17.0')
    elif mutation == 'undeclared':
        source = source.replace('ruff==0.17.0\n', '')
    elif mutation == 'version':
        source = source.replace('ruff==0.17.0', 'ruff==0.0.0')
    else:

        def missing_package(name):
            """環境に必要toolがない場合のmetadata診断を再現する。"""
            raise quality.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(quality.metadata, 'version', missing_package)
    (tmp_path / 'requirements-dev.txt').write_text(source, encoding='utf-8')
    assert quality._environment_errors(tmp_path)


def test_gate_positive_aggregation_and_existing_output_protection(tmp_path):
    def runner(step, root, log):
        """集約正常系。実際の全mandatoryコマンドはCLI検証で別途実行する。"""
        return _fake_result(step, log, 0)

    output = tmp_path / 'run'
    report = quality.run_gate(ROOT, output, runner)
    assert report['passed']
    before = (output / 'quality.json').read_bytes()
    with pytest.raises(FileExistsError):
        quality.run_gate(ROOT, output, runner)
    assert (output / 'quality.json').read_bytes() == before
    from harness import analysis

    assert analysis.assess_evaluation(report)['formal_eligibility'] == 'ineligible'


def test_gate_detects_source_changes_between_required_checks(tmp_path):
    """model検査後に追加した未検証moduleを、終了hashの更新だけで成功にしない。"""
    root = tmp_path / 'source'
    _model_copy(root)
    shutil.copy2(ROOT / 'requirements-dev.txt', root / 'requirements-dev.txt')

    def runner(step, source, log):
        """コマンド成功を返すが検査中のsource変更を明示的に注入する。"""
        if step.name == 'lint':
            (source / 'harness/changed.py').write_text('"""未検証の変更。"""\n', encoding='utf-8')
        return _fake_result(step, log, 0)

    report = quality.run_gate(root, tmp_path / 'run', runner)
    assert report['model_errors'] == []
    assert not report['passed']
    assert report['source_hashes'] != report['source_hashes_end']
    snapshot = next(item for item in report['steps'] if item['name'] == 'snapshot')
    assert not snapshot['passed']


@pytest.mark.parametrize('kind', ['nonzero', 'missing', 'timeout'])
def test_run_step_rejects_real_nonzero_unavailable_and_timeout(tmp_path, kind):
    """単体検査のmockに加えて、実OSプロセスの終了/不在/timeoutを検証する。"""
    command = (sys.executable, '-B', '-c', 'raise SystemExit(7)')
    timeout = 5.0
    if kind == 'missing':
        command = (str(tmp_path / 'does-not-exist.exe'),)
    elif kind == 'timeout':
        command = (sys.executable, '-B', '-c', 'import time; time.sleep(20)')
        timeout = 0.2
    result = quality.run_step(quality.Step(kind, command, timeout), tmp_path, tmp_path / 'step.txt')
    assert not result['passed']
    if kind == 'nonzero':
        assert result['exit_code'] == 7
    else:
        assert result['exit_code'] is None
        assert result['error']


def test_timeout_stops_owned_child_process_tree(tmp_path):
    """Windowsで孫プロセスを残さず停止する。AIやユーザーの既存processは対象外。"""
    program = (
        'import subprocess, sys, time; '
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"]); '
        'print(child.pid, flush=True); time.sleep(20)'
    )
    log = tmp_path / 'tree.txt'
    result = quality.run_step(
        quality.Step('tree', (sys.executable, '-B', '-c', program), 1.0), tmp_path, log
    )
    assert not result['passed'] and 'timeout' in result['error']
    child_pid = log.read_text(encoding='utf-8').splitlines()[0]
    listing = subprocess.run(
        ['tasklist', '/FI', f'PID eq {child_pid}', '/FO', 'CSV', '/NH'],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert all(
        len(row) < 2 or row[1] != child_pid for row in csv.reader(listing.stdout.splitlines())
    )


def test_taskkill_unavailable_still_collects_direct_child_and_reports_failure(
    tmp_path, monkeypatch
):
    """tree回収toolがない場合も直接子を待ち続けず、未確認の回収を成功にしない。"""
    original_run = subprocess.run

    def without_taskkill(command, *arguments, **keywords):
        """taskkillだけを不在にし、PID確認のtasklistは実際に実行する。"""
        if command[0] == 'taskkill':
            raise FileNotFoundError('taskkill unavailable')
        return original_run(command, *arguments, **keywords)

    monkeypatch.setattr(quality.subprocess, 'run', without_taskkill)
    log = tmp_path / 'missing-taskkill.txt'
    program = 'import os, time; print(os.getpid(), flush=True); time.sleep(20)'
    result = quality.run_step(
        quality.Step('missing-taskkill', (sys.executable, '-B', '-c', program), 1.0), tmp_path, log
    )
    assert not result['passed'] and 'taskkill unavailable' in result['error']
    child_pid = log.read_text(encoding='utf-8').splitlines()[0]
    listing = original_run(
        ['tasklist', '/FI', f'PID eq {child_pid}', '/FO', 'CSV', '/NH'],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert all(
        len(row) < 2 or row[1] != child_pid for row in csv.reader(listing.stdout.splitlines())
    )


def test_gate_main_propagates_output_failure(monkeypatch, tmp_path):
    def fail_output(root, output):
        """保存不能を注入し、必須検査が成功したという仮定でもCLI0を返さない。"""
        raise PermissionError('output denied')

    monkeypatch.setattr(quality, 'ROOT', tmp_path)
    monkeypatch.setattr(quality, 'run_gate', fail_output)
    assert quality.main() == 1


def test_real_quality_cli_rejects_intentional_lint_type_and_test_failures(tmp_path):
    """一時repoの壊れたコードで実CLIを呼び、個々のnonzeroと総合終了1を確認する。"""
    _model_copy(tmp_path)
    (tmp_path / 'tools').mkdir()
    shutil.copy2(ROOT / 'tools/quality.py', tmp_path / 'tools/quality.py')
    shutil.copy2(ROOT / 'tools/__init__.py', tmp_path / 'tools/__init__.py')
    shutil.copy2(ROOT / 'tools/evidence.py', tmp_path / 'tools/evidence.py')
    shutil.copy2(ROOT / 'tools/evidence_plugin.py', tmp_path / 'tools/evidence_plugin.py')
    shutil.copy2(ROOT / 'requirements-dev.txt', tmp_path / 'requirements-dev.txt')
    (tmp_path / 'pyproject.toml').write_text(
        '[tool.ruff]\ninclude=["broken.py"]\n[tool.mypy]\nstrict=true\nfiles=["broken.py"]\n',
        encoding='utf-8',
    )
    (tmp_path / 'broken.py').write_text(
        'def broken()->str:\n return 3\nprint(undefined_name)\n', encoding='utf-8'
    )
    (tmp_path / 'pytest.ini').write_text(
        '[pytest]\ntestpaths=tests\nmarkers=\n smoke: negative\n', encoding='utf-8'
    )
    (tmp_path / 'tests/test_bad.py').write_text(
        'import pytest\n@pytest.mark.smoke\ndef test_bad():\n assert False\n', encoding='utf-8'
    )
    process = subprocess.run(
        [sys.executable, '-B', 'tools/quality.py'],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=90,
    )
    assert process.returncode == 1, process.stdout + process.stderr
    path = next((tmp_path / 'evaluation_results').glob('quality-*/quality.json'))
    report = json.loads(path.read_text(encoding='utf-8'))
    assert not report['passed'] and report['model_errors'] == []
    for name in ('lint', 'format', 'typing', 'pytest', 'smoke'):
        item = next(item for item in report['steps'] if item['name'] == name)
        assert not item['passed'] and item['exit_code'] != 0


def _sample_evidence(tmp_path, program, name='pytest', timeout=20.0):
    """一時pytestを実OSプロセスで実行し、標準JUnitと同じrunのjournalを得る。"""
    project = tmp_path / ('project-' + name)
    project.mkdir()
    (project / 'test_sample.py').write_text(program, encoding='utf-8')
    output = tmp_path / 'bundle'
    output.mkdir(exist_ok=True)
    directory = output / name
    directory.mkdir()
    command = (
        sys.executable,
        '-B',
        '-m',
        'pytest',
        '-q',
        '-p',
        'no:cacheprovider',
        '--tb=short',
        '-p',
        'tools.evidence_plugin',
        '--junitxml=' + str(directory / (name + '.xml')),
        '--verification-evidence-dir=' + str(directory),
        '--verification-run-id=bundle',
        '--verification-step=' + name,
    )
    original = os.environ.get('PYTHONPATH')
    os.environ['PYTHONPATH'] = str(ROOT)
    try:
        result = quality.run_step(
            quality.Step(name, command, timeout), project, output / (name + '.txt')
        )
    finally:
        if original is None:
            os.environ.pop('PYTHONPATH', None)
        else:
            os.environ['PYTHONPATH'] = original
    return output, result


@pytest.mark.parametrize(
    'program,expected,exit_code',
    [
        ('def test_pass():\n assert 1 == 1\n', 'passed', 0),
        ('def test_fail():\n assert 1 == 2\n', 'failed', 1),
        (
            'import pytest\ndef test_skip():\n pytest.skip("not available")\ndef test_pass():\n assert True\n',
            'skipped',
            0,
        ),
        (
            'import pytest\n@pytest.fixture\ndef broken():\n raise RuntimeError("setup")\ndef test_setup(broken):\n assert True\n',
            'error',
            1,
        ),
        (
            'import pytest\n@pytest.fixture\ndef skipped():\n pytest.skip("setup")\ndef test_setup(skipped):\n assert True\ndef test_pass():\n assert True\n',
            'skipped',
            0,
        ),
        (
            'import pytest\n@pytest.fixture\ndef broken():\n yield\n raise RuntimeError("teardown")\ndef test_teardown(broken):\n assert True\n',
            'error',
            1,
        ),
        (
            'import pytest\n@pytest.fixture\ndef broken():\n yield\n raise RuntimeError("teardown")\ndef test_both(broken):\n assert False\n',
            'error',
            1,
        ),
    ],
)
def test_evidence_matches_real_pytest_phases_and_junit(tmp_path, program, expected, exit_code):
    """call成功後のteardown失敗と負例を含み、元pytest結果と証拠の完全性を分離する。"""
    output, result = _sample_evidence(tmp_path, program)
    record = evidence.collect_step(output, 'bundle', {**result})
    assert result['exit_code'] == exit_code
    assert record['collection_status'] == 'complete', record['errors']
    assert record['tests'][0]['outcome'] == expected


@pytest.mark.parametrize(
    'program,exit_code,outcome',
    [
        ('raise RuntimeError("collection")\n', 2, 'failed'),
        ('import pytest\npytest.skip("module", allow_module_level=True)\n', 5, 'skipped'),
    ],
)
def test_evidence_collection_failure_is_not_a_passing_test(tmp_path, program, exit_code, outcome):
    """収集前の例外/skipも整合した証拠として保持し、正常callを補造しない。"""
    output, result = _sample_evidence(tmp_path, program)
    record = evidence.collect_step(output, 'bundle', {**result})
    assert result['exit_code'] == exit_code
    assert record['collection_status'] == 'complete', record['errors']
    assert record['tests'] == []
    assert record['collection'][0]['outcome'] == outcome


@pytest.mark.parametrize('kind', ['crash', 'timeout'])
def test_evidence_abrupt_pytest_preserves_partial_journal_and_log(tmp_path, kind):
    """強制終了でJUnit/session終了が欠けても開始済み情報を保持し、完成とは呼ばない。"""
    program = (
        'import os\ndef test_crash():\n os._exit(7)\n'
        if kind == 'crash'
        else 'import time\ndef test_slow():\n time.sleep(20)\n'
    )
    output, result = _sample_evidence(tmp_path, program, timeout=2.0)
    record = evidence.collect_step(output, 'bundle', {**result})
    assert not result['passed']
    assert record['collection_status'] == 'incomplete'
    assert (output / 'pytest.txt').is_file()
    assert record['errors']


@pytest.mark.parametrize(
    'mutation',
    [
        'missing_xml',
        'broken_xml',
        'wrong_count',
        'wrong_outcome',
        'missing_journal',
        'broken_journal',
        'wrong_run',
        'wrong_sequence',
    ],
)
def test_evidence_missing_or_mismatched_reports_never_become_complete(tmp_path, mutation):
    """新しい正常成果物への改変で欠損・破損・集計・実行対応の不一致を検出する。"""
    output, result = _sample_evidence(tmp_path, 'def test_pass():\n assert True\n')
    xml = output / 'pytest/pytest.xml'
    journal = output / 'pytest/events.jsonl'
    if mutation == 'missing_xml':
        xml.unlink()
    elif mutation == 'broken_xml':
        xml.write_text('<testsuites>', encoding='utf-8')
    elif mutation == 'wrong_count':
        xml.write_text(
            xml.read_text(encoding='utf-8').replace('tests="1"', 'tests="2"'), encoding='utf-8'
        )
    elif mutation == 'wrong_outcome':
        document = ElementTree.fromstring(xml.read_bytes())
        case = next(document.iter('testcase'))
        ElementTree.SubElement(case, 'failure').text = 'fake'
        xml.write_bytes(ElementTree.tostring(document, encoding='utf-8'))
    elif mutation == 'missing_journal':
        journal.unlink()
    elif mutation == 'broken_journal':
        journal.write_text('{', encoding='utf-8')
    else:
        events = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
        events[0]['run_id' if mutation == 'wrong_run' else 'sequence'] = 'other'
        journal.write_text(
            '\n'.join(json.dumps(event) for event in events) + '\n', encoding='utf-8'
        )
    assert evidence.collect_step(output, 'bundle', {**result})['collection_status'] == 'incomplete'


def test_evidence_same_nodeid_in_different_steps_has_distinct_execution_ids(tmp_path):
    """full/smokeで同じnodeidを使っても実行対応を上書きしない。"""
    records = []
    for name in ('pytest', 'smoke'):
        output, result = _sample_evidence(tmp_path, 'def test_pass():\n assert True\n', name=name)
        records.append(evidence.collect_step(output, 'bundle', {**result}))
    assert records[0]['tests'][0]['test_id'] == records[1]['tests'][0]['test_id']
    assert records[0]['tests'][0]['execution_id'] != records[1]['tests'][0]['execution_id']


@pytest.mark.parametrize('target', ['evidence.json', 'evidence.md'])
def test_evidence_save_failure_changes_gate_not_original_test_results(
    tmp_path, monkeypatch, target
):
    """必須派生保存の障害を注入し、旧成功結果を残したままgateだけを失敗にする。"""
    original = evidence.save_new

    def denied(path, text):
        """指定した派生証拠だけを保存不能にする。qualityの失敗記録は許可する。"""
        if path.name == target:
            raise PermissionError('injected evidence failure')
        original(path, text)

    def runner(step, root, log):
        """旧必須検査と対応JUnit/journalの成功を模擬する。"""
        return _fake_result(step, log, 0)

    monkeypatch.setattr(evidence, 'save_new', denied)
    report = quality.run_gate(ROOT, tmp_path / 'run', runner)
    assert not report['passed']
    assert all(item['passed'] for item in report['steps'] if item['name'] != 'evidence')
    assert not report['steps'][-1]['passed']
    assert (tmp_path / 'run/quality.json').is_file()
    assert evidence.verify_publication(tmp_path / 'run')


def test_evidence_publication_hashes_and_json_markdown_agree(tmp_path):
    """最後のquality markerと派生・元step bytesが一致する正常保存を確認する。"""

    def runner(step, root, log):
        """集約正常系の既知結果を返す。"""
        return _fake_result(step, log, 0)

    output = tmp_path / 'run'
    report = quality.run_gate(ROOT, output, runner)
    assert report['passed']
    assert evidence.verify_publication(output) == []
    bundle = json.loads((output / 'evidence.json').read_text(encoding='utf-8'))
    markdown = (output / 'evidence.md').read_text(encoding='utf-8')
    assert bundle['manifest']['run_id'] == 'run'
    assert 'test_command' in markdown
    assert bundle['validation_passed']
    assert len(bundle['manifest']['steps']) == 10
    assert len(bundle['manifest']['checks']) == 9
    before = (output / 'evidence.json').read_bytes()
    with pytest.raises(FileExistsError):
        evidence.save_new(output / 'evidence.json', 'changed')
    assert (output / 'evidence.json').read_bytes() == before
    (output / 'pytest/pytest.xml').write_text('changed', encoding='utf-8')
    assert 'step_artifact_hash_mismatch' in evidence.verify_publication(output)
    (output / 'typing.txt').write_text('changed', encoding='utf-8')
    assert 'check_log_hash_mismatch' in evidence.verify_publication(output)


def test_evidence_junit_preexisting_output_is_not_overwritten(tmp_path):
    """標準JUnitのwモードを使う前に、既存ファイルを検知して子を起動しない。"""
    path = tmp_path / 'pytest.xml'
    path.write_text('protected', encoding='utf-8')
    step = quality.Step(
        'pytest', (sys.executable, '-c', 'raise SystemExit(0)', '--junitxml=' + str(path))
    )
    result = quality.run_step(step, tmp_path, tmp_path / 'step.txt')
    assert not result['passed'] and result['exit_code'] is None
    assert path.read_text(encoding='utf-8') == 'protected'


@pytest.mark.parametrize('mutation', ['normal', 'hash', 'run'])
def test_evidence_explicit_expectation_and_original_artifact_correspondence(tmp_path, mutation):
    """期待・観測を明示登録し、原checkpointを変更せずrun/hash対応を照合する。"""
    source = tmp_path / 'source'
    source.mkdir()
    checkpoint = source / 'run-state.json'
    checkpoint.write_text(
        json.dumps({'run_id': 'other' if mutation == 'run' else 'source-run', 'schema_version': 5}),
        encoding='utf-8',
    )
    program = (
        'from tools.evidence_plugin import record_expectation, record_observation, record_harness_artifacts\n'
        'def test_record():\n'
        ' record_expectation("check", inputs=[1], expected=2, expectation_kind="fixed", model="model", contract="contract", implementation="implementation")\n'
        ' actual = 1 + 1\n'
        ' record_observation("check", actual)\n'
        ' assert actual == 2\n'
        f' record_harness_artifacts({{"run_id":"source-run","schema_version":5,"evidence":{{"root":{str(source)!r}}}}})\n'
    )
    output, result = _sample_evidence(tmp_path, program)
    if mutation == 'hash':
        checkpoint.write_text('changed', encoding='utf-8')
    record = evidence.collect_step(output, 'bundle', {**result})
    assert result['passed']
    assert record['tests'][0]['checks'][0]['expected'] == 2
    assert record['tests'][0]['observations'][0]['actual'] == 2
    assert record['collection_status'] == ('complete' if mutation == 'normal' else 'incomplete'), (
        record['errors']
    )


def test_evidence_secret_metadata_failure_does_not_modify_pytest_pass(tmp_path):
    """明示metadataの秘密キー拒否は証拠失敗とし、元assertの成功を変更しない。"""
    program = 'from tools.evidence_plugin import record_observation\ndef test_pass():\n record_observation("x", {"api_key":"do-not-save"})\n assert True\n'
    output, result = _sample_evidence(tmp_path, program)
    record = evidence.collect_step(output, 'bundle', {**result})
    assert result['passed']
    assert record['collection_status'] == 'incomplete'
    assert 'do-not-save' not in (output / 'pytest/events.jsonl').read_text(encoding='utf-8')


@pytest.mark.parametrize(
    'program,reason',
    [
        (
            'def test_all_eight_directions_flip_together():\n assert True\n',
            'important_evidence_missing',
        ),
        (
            'from tools.evidence_plugin import record_observation\ndef test_pass():\n record_observation("missing", 2)\n assert True\n',
            'observation_before_expectation',
        ),
        (
            'import pytest\ndef test_skip():\n pytest.skip("only skipped")\n',
            'successful_execution_not_demonstrated',
        ),
        (
            'import os\ndef test_pass():\n os.fsync = lambda descriptor: (_ for _ in ()).throw(OSError("fsync denied"))\n assert True\n',
            'plugin_save_error_in_log',
        ),
    ],
)
def test_evidence_missing_explicit_or_final_save_evidence_cannot_certify_pass(
    tmp_path, program, reason
):
    """元pytestの終了0でも、期待/観測や最終保存の不足はgate用証拠へ昇格しない。"""
    output, result = _sample_evidence(tmp_path, program)
    record = evidence.collect_step(output, 'bundle', {**result})
    assert result['passed']
    assert record['collection_status'] == 'incomplete'
    assert any(reason in error for error in record['errors'])


def test_evidence_missing_junit_with_success_exit_fails_public_gate(tmp_path):
    """終了0だけを返す公開runnerでも、必須JUnit不足をquality.jsonの失敗へ伝播する。"""

    def runner(step, root, log):
        """一つの生成済みJUnitを失わせ、旧検査の成功値はそのまま返す。"""
        result = _fake_result(step, log, 0)
        if step.name == 'pytest':
            (log.parent / 'pytest/pytest.xml').unlink()
        return result

    report = quality.run_gate(ROOT, tmp_path / 'run', runner)
    assert all(item['passed'] for item in report['steps'] if item['name'] != 'evidence')
    assert not report['passed']
    stored = json.loads((tmp_path / 'run/quality.json').read_text(encoding='utf-8'))
    assert not stored['passed']
    assert stored['steps'][-1]['name'] == 'evidence'


def test_evidence_final_quality_save_failure_leaves_partial_not_publication(tmp_path, monkeypatch):
    """派生JSONが書けても、最後のquality markerが書けない場合は完成としない。"""
    original = evidence.save_new

    def denied(path, text):
        """最後のpublicationだけを失敗させ、取得済み派生証拠を保護する。"""
        if path.name == 'quality.json':
            raise PermissionError('publication denied')
        original(path, text)

    def runner(step, root, log):
        """正常な旧検査と証拠を生成する。"""
        return _fake_result(step, log, 0)

    monkeypatch.setattr(evidence, 'save_new', denied)
    with pytest.raises(PermissionError):
        quality.run_gate(ROOT, tmp_path / 'run', runner)
    assert (tmp_path / 'run/evidence.json').is_file()
    assert not (tmp_path / 'run/quality.json').exists()
    assert evidence.verify_publication(tmp_path / 'run')


@pytest.mark.parametrize('name', ['quality.json', 'evidence.json', 'evidence.md'])
def test_evidence_fsync_failure_never_publishes_complete_filename(tmp_path, monkeypatch, name):
    """完成bytesを書いた後のfsync失敗でも、完成名のファイルが残る反例を防ぐ。"""

    def denied(descriptor):
        """書込後の実fsync呼出だけを失敗させる。"""
        raise OSError('fsync denied')

    monkeypatch.setattr(evidence.os, 'fsync', denied)
    target = tmp_path / name
    with pytest.raises(OSError, match='fsync denied'):
        evidence.save_new(target, '{"passed":true}\n')
    assert not target.exists()


def test_evidence_link_failure_does_not_publish_or_replace(tmp_path, monkeypatch):
    """排他公開が使えない保存先では、非排他的replaceにfallbackしない。"""

    def denied(source, target):
        """hardlink公開だけを保存不能にする。"""
        raise OSError('hardlink unavailable')

    monkeypatch.setattr(evidence.os, 'link', denied)
    target = tmp_path / 'evidence.json'
    with pytest.raises(OSError, match='hardlink unavailable'):
        evidence.save_new(target, 'new')
    assert not target.exists()


def test_evidence_publication_race_preserves_winner(tmp_path, monkeypatch):
    """preflight後に現れたwinnerを、pending公開時にも上書きしない。"""
    original = evidence.os.link

    def race(source, target):
        """公開直前に別writerの完成ファイルを作る。"""
        Path(target).write_text('winner', encoding='utf-8')
        original(source, target)

    monkeypatch.setattr(evidence.os, 'link', race)
    target = tmp_path / 'quality.json'
    with pytest.raises(FileExistsError):
        evidence.save_new(target, 'new')
    assert target.read_text(encoding='utf-8') == 'winner'


def test_evidence_pending_of_another_writer_is_protected(tmp_path):
    """新規作成に失敗したpendingをowned扱いで削除しない。"""
    target = tmp_path / 'evidence.json'
    pending = target.with_name(target.name + '.pending')
    pending.write_text('protected', encoding='utf-8')
    with pytest.raises(FileExistsError):
        evidence.save_new(target, 'new')
    assert pending.read_text(encoding='utf-8') == 'protected'
    assert not target.exists()


def test_evidence_real_fsync_failure_at_public_cli_cannot_leave_positive_marker(
    tmp_path, monkeypatch
):
    """実run_gate→main経路でfsync障害後の偽完成markerを拒否する。"""
    original_save = evidence.save_new
    original_gate = quality.run_gate

    def denied(descriptor):
        """最終保存中のfsyncを失敗させる。"""
        raise OSError('final fsync denied')

    def save(path, text):
        """qualityの最後のflush後だけで障害を起こす。"""
        if path.name == 'quality.json':
            with monkeypatch.context() as patch:
                patch.setattr(evidence.os, 'fsync', denied)
                original_save(path, text)
        else:
            original_save(path, text)

    def runner(step, root, log):
        """旧検査の成功はそのまま保存する。"""
        return _fake_result(step, log, 0)

    def gate(root, output):
        """実公開集約と最終保存を使う。外部検査だけを模擬する。"""
        return original_gate(ROOT, output, runner)

    monkeypatch.setattr(evidence, 'save_new', save)
    monkeypatch.setattr(quality, 'run_gate', gate)
    monkeypatch.setattr(quality, 'ROOT', tmp_path)
    assert quality.main() == 1
    output = next((tmp_path / 'evaluation_results').glob('quality-*'))
    assert not (output / 'quality.json').exists()
    assert evidence.verify_publication(output)


@pytest.fixture(scope='module')
def journal_template(tmp_path_factory):
    """実pytestでfixture内metadataと2つの直列testを記録し、改変前の原本にする。"""
    program = (
        'import pytest\n'
        'from tools.evidence_plugin import record_expectation, record_observation\n'
        '@pytest.fixture\n'
        'def observed():\n'
        ' record_expectation("fixture", inputs=1, expected=1, expectation_kind="fixed", '
        'model="fixture lifecycle", contract="journal v1", implementation="observed")\n'
        ' record_observation("fixture", 1)\n'
        ' yield\n'
        ' record_observation("fixture", 1)\n'
        'def test_first(observed):\n assert True\n'
        'def test_second():\n assert True\n'
    )
    return _sample_evidence(tmp_path_factory.mktemp('journal-template'), program)


@pytest.fixture
def journal_sample(tmp_path, journal_template):
    """原本は変更せず、各改変ケース専用の新規領域へ複製する。"""
    source, result = journal_template
    output = tmp_path / 'bundle'
    shutil.copytree(source, output)
    return output, {**result}


def _mutate_journal(path, mutation):
    """元のphase結果とJUnitを変えず、連番を振り直して状態違反だけを作る。"""
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    start = next(index for index, event in enumerate(events) if event['kind'] == 'test_start')
    finish = next(index for index, event in enumerate(events) if event['kind'] == 'test_finish')
    nodeid = events[start]['nodeid']
    if mutation == 'duplicate_finish':
        events.insert(finish, dict(events[finish]))
    elif mutation == 'early_finish':
        event = events.pop(finish)
        events.insert(start + 1, event)
    elif mutation in ('late_teardown', 'late_observation'):
        index = next(
            index
            for index, event in enumerate(events)
            if (
                event['kind'] == 'phase' and event.get('when') == 'teardown'
                if mutation == 'late_teardown'
                else event['kind'] == 'observation'
            )
        )
        event = events.pop(index)
        finish = next(index for index, item in enumerate(events) if item['kind'] == 'test_finish')
        events.insert(finish + 1, event)
    elif mutation == 'late_expectation':
        event = dict(next(event for event in events if event['kind'] == 'expectation'))
        events.insert(finish + 1, event)
    elif mutation == 'late_artifact':
        source = path.parent / 'referenced.json'
        source.write_text('{"run_id":"source","schema_version":5}', encoding='utf-8')
        events.insert(
            finish + 1,
            {
                **events[start],
                'kind': 'artifact',
                'path': str(source),
                'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'artifact_kind': 'checkpoint',
                'source_run_id': 'source',
                'source_schema': 5,
            },
        )
    elif mutation == 'overlap':
        index = next(
            index
            for index, event in enumerate(events)
            if event['kind'] == 'test_start' and index > start
        )
        event = events.pop(index)
        events.insert(start + 1, event)
    elif mutation == 'duplicate_session_start':
        events.insert(1, dict(events[0]))
    elif mutation == 'duplicate_session_finish':
        events.insert(len(events) - 1, dict(events[-1]))
    elif mutation == 'early_session_finish':
        events.insert(start, events.pop())
    elif mutation in ('duplicate_selected', 'late_selected'):
        index = next(index for index, event in enumerate(events) if event['kind'] == 'selected')
        if mutation == 'duplicate_selected':
            events.insert(index, dict(events[index]))
        else:
            event = events.pop(index)
            start = next(index for index, item in enumerate(events) if item['kind'] == 'test_start')
            events.insert(start + 1, event)
    elif mutation == 'late_collection':
        events.insert(
            finish + 1,
            {**events[0], 'kind': 'collection', 'outcome': 'skipped', 'collection_nodeid': 'late'},
        )
    elif mutation == 'unknown_kind':
        events.insert(1, {**events[0], 'kind': 'not_supported'})
    elif mutation in ('unknown_version', 'missing_version', 'boolean_version'):
        for event in events:
            if mutation == 'missing_version':
                event.pop('format_version', None)
            else:
                event['format_version'] = True if mutation == 'boolean_version' else 999
    elif mutation == 'nodeless_phase':
        event = dict(next(event for event in events if event['kind'] == 'phase'))
        events.insert(finish, {**event, 'nodeid': None})
    elif mutation == 'session_with_nodeid':
        events[-1]['nodeid'] = nodeid
    elif mutation == 'invalid_selected':
        next(event for event in events if event['kind'] == 'selected')['nodeids'] = [{}]
    elif mutation == 'after_session':
        events.append(dict(events[finish]))
    else:
        raise AssertionError('unknown test mutation: ' + mutation)
    for index, event in enumerate(events):
        event['sequence'] = index
    path.write_text('\n'.join(json.dumps(event) for event in events) + '\n', encoding='utf-8')


JOURNAL_MUTATIONS = [
    ('duplicate_finish', 'duplicate_test_finish'),
    ('early_finish', 'test_finish_before_phases'),
    ('late_teardown', 'event_outside_active_test'),
    ('late_expectation', 'event_outside_active_test'),
    ('late_observation', 'event_outside_active_test'),
    ('late_artifact', 'event_outside_active_test'),
    ('overlap', 'overlapping_test_start'),
    ('duplicate_session_start', 'duplicate_session_start'),
    ('duplicate_session_finish', 'duplicate_session_finish'),
    ('early_session_finish', 'session_finish_before_end'),
    ('duplicate_selected', 'duplicate_selected'),
    ('late_selected', 'test_before_selection'),
    ('late_collection', 'collection_after_selection'),
    ('unknown_kind', 'unknown_event_kind'),
    ('unknown_version', 'unsupported_journal_version'),
    ('missing_version', 'unsupported_journal_version'),
    ('boolean_version', 'unsupported_journal_version'),
    ('nodeless_phase', 'event_outside_active_test'),
    ('session_with_nodeid', 'session_nodeid_invalid'),
    ('invalid_selected', 'invalid_selected_nodeids'),
    ('after_session', 'event_outside_session'),
]


@pytest.mark.parametrize('mutation,reason', JOURNAL_MUTATIONS)
def test_evidence_journal_lifecycle_rejects_resequenced_events(journal_sample, mutation, reason):
    """正しい連番・phase/JUnit合格だけでは、不正な開始終了をcompleteにしない。"""
    output, result = journal_sample
    _mutate_journal(output / 'pytest/events.jsonl', mutation)
    record = evidence.collect_step(output, 'bundle', result)
    assert result['passed']
    assert record['collection_status'] == 'incomplete'
    assert any(reason in error for error in record['errors']), record['errors']


def test_evidence_journal_lifecycle_accepts_fixture_metadata_and_serial_tests(journal_sample):
    """setup報告前/teardown報告前のmetadataと同一checkの複数観測を正常受理する。"""
    output, result = journal_sample
    record = evidence.collect_step(output, 'bundle', result)
    assert record['collection_status'] == 'complete', record['errors']
    assert [test['outcome'] for test in record['tests']] == ['passed', 'passed']
    assert len(record['tests'][0]['observations']) == 2


@pytest.mark.parametrize('step_name', ['pytest', 'smoke'])
@pytest.mark.parametrize('mutation', ['duplicate_finish', 'early_finish', 'unknown_version'])
def test_evidence_journal_lifecycle_failure_reaches_public_gate(tmp_path, step_name, mutation):
    """実公開集約で不正journalのhashを取得しても、元検査を保ってgateを失敗させる。"""

    def runner(step, root, log):
        """外部品質コマンドのみ模擬し、対象stepの新規journalを改変する。"""
        result = _fake_result(step, log, 0)
        if step.name == step_name:
            _mutate_journal(log.parent / step_name / 'events.jsonl', mutation)
        return result

    output = tmp_path / 'run'
    report = quality.run_gate(ROOT, output, runner)
    assert all(item['passed'] for item in report['steps'] if item['name'] != 'evidence')
    assert not report['steps'][-1]['passed']
    assert not report['passed']
    stored = json.loads((output / 'quality.json').read_text(encoding='utf-8'))
    bundle = json.loads((output / 'evidence.json').read_text(encoding='utf-8'))
    assert not stored['passed']
    assert bundle['collection_status'] == 'incomplete'
    assert evidence.verify_publication(output) == ['collection_incomplete']


@pytest.mark.parametrize('mutation', ['duplicate_finish', 'early_finish'])
def test_evidence_journal_lifecycle_failure_reaches_cli(tmp_path, monkeypatch, mutation):
    """外部コマンド模擬でも実main→run_gate→保存を通し、不正証拠で終了0にしない。"""
    original_gate = quality.run_gate

    def runner(step, root, log):
        """旧検査結果を成功のまま保ち、pytest journalだけに状態違反を作る。"""
        result = _fake_result(step, log, 0)
        if step.name == 'pytest':
            _mutate_journal(log.parent / 'pytest/events.jsonl', mutation)
        return result

    def gate(root, output):
        """実際のモデル/環境照合と集約を保ち、保存先のみ一時領域にする。"""
        return original_gate(ROOT, output, runner)

    monkeypatch.setattr(quality, 'ROOT', tmp_path)
    monkeypatch.setattr(quality, 'run_gate', gate)
    assert quality.main() == 1
    output = next((tmp_path / 'evaluation_results').glob('quality-*'))
    assert not json.loads((output / 'quality.json').read_text(encoding='utf-8'))['passed']


@pytest.mark.parametrize(
    'relative_path',
    [
        'harness/statistics.py',
        'reversi_player/reversi.py',
        'harness/openings.py',
        'harness/protocol.py',
        'harness/search_budget.py',
        'harness/contracts.py',
        'harness/analysis.py',
    ],
)
def test_promoted_modules_are_checked_by_configured_strict_gate(tmp_path, relative_path):
    """通常のgate argvで昇格済moduleの未注釈関数を拒否し、対象漏れを検出する。"""
    source = ROOT / relative_path
    shadow = tmp_path / source.name
    shadow.write_text(
        source.read_text(encoding='utf-8')
        + '\n\ndef _strict_scope_probe(value):\n'
        + '    """型検査対象からの漏れを検出するための一時的な未注釈関数。"""\n'
        + '    return value\n',
        encoding='utf-8',
    )
    step = next(step for step in quality.required_steps(ROOT) if step.name == 'typing')
    result = subprocess.run(
        [
            *step.command,
            '--cache-dir',
            str(tmp_path / 'mypy-cache'),
            '--shadow-file',
            str(source),
            str(shadow),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert source.name + ':' in result.stdout
    assert '[no-untyped-def]' in result.stdout


def test_openings_typing_keeps_unchecked_generator_metadata_opaque(tmp_path):
    """未検証metadataからseedを安全に取得できると型検査が誤認する変更を検出する。"""
    source = ROOT / 'harness/openings.py'
    shadow = tmp_path / 'openings.py'
    shadow.write_text(
        source.read_text(encoding='utf-8')
        + '\n\ndef _unchecked_generator_probe(population: _Population) -> int:\n'
        + '    return population["generator"]["seed"]\n',
        encoding='utf-8',
    )
    step = next(step for step in quality.required_steps(ROOT) if step.name == 'typing')
    result = subprocess.run(
        [
            *step.command,
            '--cache-dir',
            str(tmp_path / 'mypy-cache'),
            '--shadow-file',
            str(source),
            str(shadow),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'openings.py:' in result.stdout
    assert '[index]' in result.stdout


def _check_contract_type_boundaries(source):
    """原構造のcastを許さず、旧26＋Phase 8の7局所操作ビューを固定する。"""
    tree = ast.parse(source, type_comments=True)
    assert not tree.type_ignores
    assert not any(
        isinstance(node, ast.Name)
        and node.id in ('Any', 'no_type_check')
        or isinstance(node, ast.Attribute)
        and node.attr in ('Any', 'cast', 'no_type_check')
        or isinstance(node, ast.alias)
        and (
            node.name in ('Any', 'no_type_check') or node.name == 'cast' and node.asname is not None
        )
        for node in ast.walk(tree)
    )
    actual = sorted(
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'cast'
    )
    expected = [
        'cast(str, case_id)',
        "cast('list[str]', record.get('response'))",
        "cast(str, game['error']['kind'])",
        "cast('int | float', item['search_elapsed_ns'])",
        "cast('int | float', item['search_elapsed_ns'])",
        "cast('Iterable[object]', first)",
        "cast('Iterable[object]', second)",
        "cast(str, error['kind'])",
        "cast('Position', tuple(cast('Iterable[int]', move['position'])))",
        "cast('Iterable[int]', move['position'])",
        "cast('Iterable[int]', move['position'])",
        "cast('Position', tuple(position))",
        "cast(str, conditions.get('budget_policy'))",
        "cast(str, item['code'])",
        "cast(str, item['code'])",
        "cast(str, item['code'])",
        "cast(str, item.get('path', 'timing'))",
        "cast(str, item['status'])",
        'cast(str, incompatibility)',
        "cast(int, manifest.get('selection_seed'))",
        "cast(int, manifest.get('selection_seed'))",
        "cast(str, validation.get('reason', 'trace_not_verified'))",
        "cast('_Role', label)",
        "cast('_Role', label)",
        "cast('_BudgetState', trace['status'])",
        "cast(str, validation.get('reason', 'not_verified'))",
        "cast('Callable[..., object]', getattr(value, 'get'))",
        "cast('Callable[[object, str | int], object]', operator.getitem)",
        "cast('Iterable[object]', value)",
        "cast('Callable[[object, str, object], None]', operator.setitem)",
        "cast('Callable[[str, object], object]', getattr(_get_field(conditions, 'budget_config_hashes', {}), 'pop'))",
        "cast(int, before.get('schema_version', 1))",
        "cast(int, after.get('schema_version', 1))",
    ]
    assert actual == sorted(expected)


def test_contract_typing_limits_views_and_preserves_raw_boundary():
    _check_contract_type_boundaries((ROOT / 'harness/contracts.py').read_text(encoding='utf-8'))


@pytest.mark.parametrize('mutation', ['any', 'whole_structure', 'alias', 'ignore'])
def test_contract_typing_boundary_guard_rejects_mutations(mutation):
    source = (ROOT / 'harness/contracts.py').read_text(encoding='utf-8')
    additions = {
        'any': '\nfrom typing import Any\n',
        'whole_structure': "\n_extra = cast('_SourceReport', {})\n",
        'alias': "\nfrom typing import cast as unchecked\n_extra = unchecked('_Timing', {})\n",
        'ignore': '\n_extra = None  # type: ignore\n',
    }
    with pytest.raises(AssertionError):
        _check_contract_type_boundaries(source + additions[mutation])


@pytest.mark.parametrize('field', ['decoded', 'snapshot', 'reference_ns'])
@pytest.mark.parametrize('result_type', ['object', 'int'])
def test_contract_typing_does_not_certify_raw_metadata(tmp_path, field, result_type):
    source = ROOT / 'harness/contracts.py'
    argument, expression = {
        'decoded': ('decoded: _Decoded', "decoded['conditions']['seed']"),
        'snapshot': ('inspection: _Inspection', "inspection['snapshot']['seed']"),
        'reference_ns': ('summary: _Summary', "summary['A']['search_sum_ns']"),
    }[field]
    shadow = tmp_path / source.name
    shadow.write_text(
        source.read_text(encoding='utf-8')
        + f'\n\ndef _unchecked_contract_probe({argument}) -> {result_type}:\n'
        + '    """未検証metadataの数値保証を拒否する。"""\n'
        + f'    return {expression}\n',
        encoding='utf-8',
    )
    step = next(step for step in quality.required_steps(ROOT) if step.name == 'typing')
    result = subprocess.run(
        [
            *step.command,
            '--cache-dir',
            str(tmp_path / 'mypy-cache'),
            '--shadow-file',
            str(source),
            str(shadow),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    if result_type == 'int':
        assert result.returncode == 1 and '[return-value]' in result.stdout, (
            result.stdout + result.stderr
        )
    else:
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('mutation', [False, True])
def test_contract_typing_dependency_accepts_readonly_command_sequence(tmp_path, mutation):
    """commandsのlist castを不要にし、依存側が読み取り専用という前提を型で固定する。"""
    source = ROOT / 'harness/search_budget.py'
    body = (
        '    commands.append({})\n'
        if mutation
        else '    validate_child_lifecycle(None, commands, None, {})\n'
    )
    shadow = tmp_path / source.name
    shadow.write_text(
        source.read_text(encoding='utf-8')
        + '\n\ndef _readonly_commands_probe(commands: Sequence[_CommandRecord]) -> None:\n'
        + '    """列の非変更契約を検査する。"""\n'
        + body,
        encoding='utf-8',
    )
    step = next(step for step in quality.required_steps(ROOT) if step.name == 'typing')
    result = subprocess.run(
        [
            *step.command,
            '--cache-dir',
            str(tmp_path / 'mypy-cache'),
            '--shadow-file',
            str(source),
            str(shadow),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    if mutation:
        assert result.returncode == 1 and '[attr-defined]' in result.stdout, (
            result.stdout + result.stderr
        )
    else:
        assert result.returncode == 0, result.stdout + result.stderr


def _check_budget_type_boundaries(source):
    """raw全体の型逃げを認めず、局所演算と既存検査後の6ビューへ限定する。"""
    tree = ast.parse(source, type_comments=True)
    assert not tree.type_ignores
    assert not any(
        isinstance(node, ast.Name)
        and node.id in ('Any', 'no_type_check')
        or isinstance(node, ast.Attribute)
        and node.attr in ('Any', 'cast', 'no_type_check')
        or isinstance(node, ast.alias)
        and node.name in ('Any', 'no_type_check')
        for node in ast.walk(tree)
    )
    actual = sorted(
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'cast'
    )
    expected = [
        "cast(str, status['reasons'][0]['code'])",
        'cast(int, search_interval[0])',
        'cast(int, search_interval[1])',
        "cast('_WorkLimitKey', limit_key)",
        "cast(int, event['model_load_end_ns'])",
        "cast(int, event['search_start_ns'])",
    ]
    assert actual == sorted(expected)


def test_budget_typing_limits_cast_to_documented_local_views():
    _check_budget_type_boundaries((ROOT / 'harness/search_budget.py').read_text(encoding='utf-8'))


@pytest.mark.parametrize('mutation', ['any', 'cast', 'ignore'])
def test_budget_typing_boundary_guard_rejects_mutations(mutation):
    source = (ROOT / 'harness/search_budget.py').read_text(encoding='utf-8')
    additions = {
        'any': '\nfrom typing import Any\n',
        'cast': '\nfrom typing import cast\n_extra = cast(object, None)\n',
        'ignore': '\n_extra = None  # type: ignore\n',
    }
    with pytest.raises(AssertionError):
        _check_budget_type_boundaries(source + additions[mutation])


@pytest.mark.parametrize('field', ['search', 'measurement'])
@pytest.mark.parametrize('result_type', ['object', 'int'])
def test_budget_typing_does_not_certify_opaque_metadata(tmp_path, field, result_type):
    """原値をobjectとして使うことは許し、静的int保証だけを拒否する。"""
    source = ROOT / 'harness/search_budget.py'
    if field == 'search':
        signature = 'config: _BudgetConfig'
        expression = "config['search']['model_id']"
    else:
        signature = 'validation: _TraceValidation'
        expression = "validation['measurements'][0]['nodes']"
    shadow = tmp_path / source.name
    shadow.write_text(
        source.read_text(encoding='utf-8')
        + f'\n\ndef _unchecked_budget_probe({signature}) -> {result_type}:\n'
        + '    """未検証metadataの過剰な保証を検出する静的プローブ。"""\n'
        + f'    return {expression}\n',
        encoding='utf-8',
    )
    step = next(step for step in quality.required_steps(ROOT) if step.name == 'typing')
    result = subprocess.run(
        [
            *step.command,
            '--cache-dir',
            str(tmp_path / 'mypy-cache'),
            '--shadow-file',
            str(source),
            str(shadow),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    if result_type == 'int':
        assert result.returncode == 1 and '[return-value]' in result.stdout, (
            result.stdout + result.stderr
        )
    else:
        assert result.returncode == 0, result.stdout + result.stderr


def _check_protocol_type_boundaries(source):
    """動的な型逃げを、承認した終了時刻転記のcast1か所以外へ拡大させない。"""
    tree = ast.parse(source, type_comments=True)
    assert not tree.type_ignores
    assert not any(
        isinstance(node, ast.Name)
        and node.id in ('Any', 'no_type_check')
        or isinstance(node, ast.Attribute)
        and node.attr in ('Any', 'cast', 'no_type_check')
        for node in ast.walk(tree)
    )
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'cast'
    ]
    assert len(calls) == 1
    assert ast.unparse(calls[0]) == "cast(int, self.lifecycle['exit_observed_ns'])"
    player = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Player'
    )
    finish = next(
        node for node in player.body if isinstance(node, ast.FunctionDef) and node.name == 'finish'
    )
    assert calls[0] in list(ast.walk(finish))


def test_protocol_typing_limits_cast_to_observed_exit_view():
    _check_protocol_type_boundaries((ROOT / 'harness/protocol.py').read_text(encoding='utf-8'))


@pytest.mark.parametrize('mutation', ['any', 'cast', 'ignore', 'decorator'])
def test_protocol_typing_boundary_guard_rejects_mutations(mutation):
    """原本を変えずにguardへ改変を与え、抑制の追加を見逃さないことを確かめる。"""
    source = (ROOT / 'harness/protocol.py').read_text(encoding='utf-8')
    if mutation == 'any':
        changed = source + '\n_opaque: Any\n'
    elif mutation == 'cast':
        changed = source + '\n_other = cast(int, 1)\n'
    elif mutation == 'ignore':
        changed = source.replace('import os', 'import os  # type: ignore', 1)
    else:
        changed = source.replace('    def finish', '    @no_type_check\n    def finish', 1)
    assert changed != source
    with pytest.raises(AssertionError):
        _check_protocol_type_boundaries(changed)


@pytest.mark.parametrize('overclaim', [False, True])
def test_protocol_typing_legacy_frequency_is_not_certified(tmp_path, overclaim):
    """旧v1の未検証周波数をintとする型の過剰保証を、実gate argvで検出する。"""
    source = ROOT / 'harness/protocol.py'
    text = source.read_text(encoding='utf-8')
    if overclaim:
        changed = text.replace(
            'qpc_frequency_hz: NotRequired[object]', 'qpc_frequency_hz: NotRequired[int | None]', 1
        )
        assert changed != text
        text = changed
    shadow = tmp_path / 'protocol.py'
    shadow.write_text(
        text
        + '\n\ndef _unchecked_frequency_probe(clock: _ClockDescriptor) -> int:\n'
        + '    frequency = clock.get("qpc_frequency_hz")\n'
        + '    if frequency is None:\n        return 0\n'
        + '    return frequency\n',
        encoding='utf-8',
    )
    step = next(step for step in quality.required_steps(ROOT) if step.name == 'typing')
    result = subprocess.run(
        [
            *step.command,
            '--cache-dir',
            str(tmp_path / 'mypy-cache'),
            '--shadow-file',
            str(source),
            str(shadow),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    assert result.returncode == (0 if overclaim else 1), result.stdout + result.stderr
    if not overclaim:
        assert '[return-value]' in result.stdout


def _check_reversi_dynamic_boundary(source):
    """本体の限定的動的境界を検査する。type ignoreもASTへ取り込んで拒否する。"""
    tree = ast.parse(source, type_comments=True)
    position = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Pos'
    )
    equality = next(
        node
        for node in position.body
        if isinstance(node, ast.FunctionDef) and node.name == '__eq__'
    )
    allowed = [equality.args.args[1].annotation, equality.returns]
    assert all(
        node in allowed
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == 'Any'
    )
    assert not tree.type_ignores
    assert not any(
        isinstance(node, ast.Name)
        and node.id in ('cast', 'no_type_check')
        or isinstance(node, ast.Attribute)
        and node.attr in ('cast', 'no_type_check')
        for node in ast.walk(tree)
    )


def _check_analysis_type_boundaries(source, module):
    """限定操作以外のcast/Any/ignoreを拒否し、原値を有効構造へ昇格させない。"""
    tree = ast.parse(source, type_comments=True)
    assert not tree.type_ignores
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in ('Any', 'cast', 'no_type_check')
        or isinstance(node, ast.Name)
        and node.id == 'no_type_check'
        or isinstance(node, ast.alias)
        and (node.name == 'no_type_check' or node.name == 'cast' and node.asname is not None)
        for node in ast.walk(tree)
    )
    any_uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == 'Any']
    any_imports = [
        node for node in ast.walk(tree) if isinstance(node, ast.alias) and node.name == 'Any'
    ]
    if module == 'statistics':
        assert len(any_uses) == len(any_imports) == 1
        assert any(
            ast.unparse(node) == 'values: list[Any] = []'
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign)
        )
    else:
        assert not any_uses and not any_imports
    actual = sorted(
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'cast'
    )
    expected = {
        'analysis': [
            "cast('Sized', value)",
            "cast('Callable[[Mapping[str, object]], object]', getattr(result, 'update'))",
            "cast('Callable[[], Iterable[object]]', getattr(configs, 'values'))",
            "cast(int, inspection['population']['verified_N'])",
            "cast(float, assessment['confidence'])",
            "cast(int, conditions['planned_games'])",
            "cast(int, inspections[0]['population']['verified_N'])",
            'cast(float, confidence)',
            "cast(float, _item_field(interval, 'lower'))",
            "cast(str, result['analysis_id'])",
        ],
        'statistics': [
            "cast('Callable[[str], object]', getattr(game.get('colors', {}), 'get'))",
            "cast('Callable[[], Iterable[tuple[object, object]]]', getattr(game.get('seeds', {}), 'items'))",
        ],
    }
    assert actual == sorted(expected[module])


@pytest.mark.parametrize('module', ['analysis', 'statistics'])
def test_analysis_typing_limits_operation_views(module):
    _check_analysis_type_boundaries(
        (ROOT / 'harness' / (module + '.py')).read_text(encoding='utf-8'), module
    )


@pytest.mark.parametrize('mutation', ['raw', 'result', 'any', 'ignore', 'alias', 'decorator'])
def test_analysis_typing_boundary_guard_rejects_mutations(mutation):
    source = (ROOT / 'harness/analysis.py').read_text(encoding='utf-8')
    addition = {
        'raw': "\n_bad = cast('_SourceReport', raw)\n",
        'result': "\n_bad = cast('Mapping[str, object]', result)\n",
        'any': '\nfrom typing import Any\n',
        'ignore': '\n_bad = None  # type: ignore\n',
        'alias': "\nfrom typing import cast as unchecked\n_bad = unchecked('_Inspection', raw)\n",
        'decorator': '\nfrom typing import no_type_check as unchecked\n',
    }[mutation]
    with pytest.raises(AssertionError):
        _check_analysis_type_boundaries(source + addition, 'analysis')


def test_analysis_typing_opaque_values_are_not_numeric_certificates(tmp_path):
    """派生game・未検証case ID・解析Mappingが、型だけでintへ昇格しない。"""
    probe = tmp_path / 'opaque_probe.py'
    probe.write_text(
        'from harness.contracts import _Inspection, _SourceReport\n'
        'from harness.statistics import _PairScore\n'
        'from harness.analysis import estimate\n'
        'def valid_game(inspection: _Inspection) -> object:\n'
        ' """派生fieldを未知値のまま返す。"""\n'
        ' return inspection["derived"]["games"][0]["score_a"]\n'
        'def invalid_game(inspection: _Inspection) -> int:\n'
        ' """型だけで未検証fieldを数値認定できないことを確認する。"""\n'
        ' return inspection["derived"]["games"][0]["score_a"]\n'
        'def valid_case(pair: _PairScore) -> object:\n'
        ' """未検証の原case IDを返す。"""\n'
        ' return pair["case_id"]\n'
        'def invalid_case(pair: _PairScore) -> int:\n'
        ' """算術のcase IDを整数へ過剰保証しない。"""\n'
        ' return pair["case_id"]\n'
        'def valid_result(report: _SourceReport) -> object:\n'
        ' """解析結果を原field型のまま読む。"""\n'
        ' return estimate(report)["point_rate"]\n'
        'def invalid_result(report: _SourceReport) -> int:\n'
        ' """解析の状態確認なしに整数認定できないことを確認する。"""\n'
        ' return estimate(report)["point_rate"]\n',
        encoding='utf-8',
    )
    step = next(step for step in quality.required_steps(ROOT) if step.name == 'typing')
    result = subprocess.run(
        [*step.command, '--cache-dir', str(tmp_path / 'mypy-cache'), str(probe)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
    )
    assert result.returncode == 1 and result.stdout.count('[return-value]') == 3, (
        result.stdout + result.stderr
    )
    assert result.stdout.count(': error:') == 3, result.stdout + result.stderr


def test_reversi_dynamic_annotations_stay_at_legacy_comparison_boundary():
    """比較の既存動的境界以外へAnyや検査回避を広げる変更を検出する。"""
    _check_reversi_dynamic_boundary(
        (ROOT / 'reversi_player/reversi.py').read_text(encoding='utf-8')
    )


@pytest.mark.parametrize('mutation', ['argument', 'alias', 'ignore', 'cast', 'decorator'])
def test_reversi_dynamic_boundary_guard_rejects_mutated_source(mutation):
    """制限guard自体が無効になっていないことを、原本を変えずに検証する。"""
    source = (ROOT / 'reversi_player/reversi.py').read_text(encoding='utf-8')
    if mutation == 'argument':
        modified = source.replace('color: str', 'color: Any', 1)
    elif mutation == 'alias':
        modified = source + '\n_unbounded_type: Any\n'
    elif mutation == 'ignore':
        modified = source.replace('import random', 'import random  # type: ignore', 1)
    elif mutation == 'cast':
        modified = source + '\n_unbounded_value = typing.cast(object, None)\n'
    else:
        modified = source.replace('    def __eq__', '    @no_type_check\n    def __eq__', 1)
    assert modified != source
    with pytest.raises(AssertionError):
        _check_reversi_dynamic_boundary(modified)
