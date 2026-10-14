"""品質ゲートの失敗伝播とモデル漏れ検出。改変はpytestの一時領域だけに行う。"""

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import quality

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
        for item in report['steps'][2:-1]
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
    assert report['steps'][-1]['name'] == 'snapshot'
    assert not report['steps'][-1]['passed']


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
