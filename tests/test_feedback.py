"""短時間フィードバックの選択境界と、Fullへの誤昇格を検出する回帰。"""

import importlib
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tools import quality

ROOT = Path(__file__).resolve().parents[1]


def _scope_copy(root):
    """本番3ルートと設定を複製し、未追跡の改変を元資産から隔離する。"""
    for name in ('harness', 'reversi_player', 'tools'):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT / 'pyproject.toml', root / 'pyproject.toml')


def test_production_scope_is_explicit_and_strict():
    """import経由で見えるだけではなく、全本番を明示strictへ登録する。"""
    assert quality.validate_scope(ROOT) == []


@pytest.mark.parametrize(
    'mutation',
    [
        'new',
        'nested',
        'exclude',
        'relax',
        'stub',
        'alias',
        'unknown',
        'exception',
        'initializer',
        'lint',
    ],
)
def test_scope_rejects_unregistered_or_suppressed_production(tmp_path, mutation):
    """新規未importモジュール・除外/緩和・stub・別名登録を拒否する。"""
    _scope_copy(tmp_path)
    config = tmp_path / 'pyproject.toml'
    if mutation in ('new', 'nested'):
        path = tmp_path / ('tools/unregistered.py' if mutation == 'new' else 'tools/new/a.py')
        path.parent.mkdir(exist_ok=True)
        path.write_text('def untyped(value): return value\n', encoding='utf-8')
    elif mutation == 'stub':
        (tmp_path / 'tools/quality.pyi').write_text('', encoding='utf-8')
    elif mutation == 'unknown':
        (tmp_path / 'unknown.py').write_text('', encoding='utf-8')
    elif mutation == 'initializer':
        (tmp_path / 'tools/__init__.py').unlink()
    else:
        text = config.read_text(encoding='utf-8')
        if mutation == 'exclude':
            text += '\nexclude = ["tools/.*"]\n'
        elif mutation == 'relax':
            text = text.replace('strict = true', 'strict = false')
        elif mutation == 'exception':
            text = text.replace('"tools/quality.py", ', '')
        elif mutation == 'lint':
            text = text.replace('select = ["E4", "E7", "E9", "F"]', 'select = []')
        else:
            text = text.replace('"tools/quality.py"', '"tools/../tools/quality.py"')
        config.write_text(text, encoding='utf-8')
    assert quality.validate_scope(tmp_path)


def test_scope_classifies_legacy_and_keeps_nested_production(tmp_path):
    """承認済み参考2件は別用途だが、本番内のtestsという名前で検査を逃れない。"""
    _scope_copy(tmp_path)
    for name in quality.REFERENCE_SCRIPTS:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text('', encoding='utf-8')
    assert quality.validate_scope(tmp_path) == []
    path = tmp_path / 'tools/tests/hidden.py'
    path.parent.mkdir()
    path.write_text('', encoding='utf-8')
    assert quality.validate_scope(tmp_path)


def test_scope_failure_and_late_addition_reach_full(tmp_path, monkeypatch):
    """mypy等が0でも、取得後の未登録追加をsnapshotで拒否する。"""
    root = tmp_path / 'source'
    root.mkdir()
    _scope_copy(root)
    shutil.copy2(ROOT / 'requirements-dev.txt', root / 'requirements-dev.txt')
    monkeypatch.setattr(quality, 'validate_model', lambda path: [])

    def runner(step, source, log):
        """pytest/smokeの完了証拠は作らず、scopeとsnapshotの判定だけを観測する。"""
        if step.name == 'typing':
            (source / 'tools/late.py').write_text('', encoding='utf-8')
        log.write_text('simulated\n', encoding='utf-8')
        return {
            'name': step.name,
            'command': list(step.command),
            'exit_code': 0,
            'seconds': 0.0,
            'log': log.name,
            'error': None,
            'passed': True,
        }

    report = quality.run_gate(root, tmp_path / 'run', runner)
    assert not report['passed']
    assert next(step for step in report['steps'] if step['name'] == 'typing_scope')['passed']
    assert not next(step for step in report['steps'] if step['name'] == 'snapshot')['passed']


def test_feedback_cli_falls_back_without_claiming_quick(tmp_path, monkeypatch):
    """未知変更でCLIがFullを実際に呼び、補助証拠をFull成功へ改名しない。"""
    feedback = importlib.import_module('tools.feedback')
    calls = []

    def full(root, output):
        """入口の委譲先のみ観測する。Fullの実検証は統合ゲートで別途行う。"""
        calls.append(output)
        return {'passed': False}

    monkeypatch.setattr(quality, 'ROOT', tmp_path)
    monkeypatch.setattr(quality, 'run_gate', full)
    monkeypatch.setattr(feedback, 'changed_paths', lambda root, base: {'full_reasons': ['unknown']})
    monkeypatch.setattr('sys.argv', ['feedback', '--profile', 'standard-ai'])
    assert feedback.main() == 1
    assert feedback.main() == 1
    assert calls[0].name.startswith('quality-')
    assert calls[0].name != calls[1].name
    assert calls[0].name.removeprefix('quality-') == calls[0].parent.name.removeprefix('feedback-')
    assert not list(tmp_path.rglob('feedback.json'))


def test_feedback_profiles_preserve_audit_candidates():
    """監査の明示候補を落とさず、StandardをQuickの上位集合に保つ。"""
    feedback = importlib.import_module('tools.feedback')
    audit = json.loads(
        (ROOT / 'docs/reports/20261020_quality_gate_audit_metrics.json').read_text(encoding='utf-8')
    )
    profiles = audit['inventory_analysis']['profiles']
    all_ids = [row[0] for row in audit['inventory_analysis']['inventory']]
    for profile, expected in zip(('quick', 'standard-ai'), profiles, strict=True):
        selected = feedback.select_tests(all_ids + ['tests/test_feedback.py::test_probe'], profile)
        assert set(expected['base_tests']) <= set(selected)
        assert 'tests/test_feedback.py::test_probe' in selected
    assert set(feedback.select_tests(all_ids, 'quick')) <= set(
        feedback.select_tests(all_ids, 'standard-ai')
    )


@pytest.mark.parametrize('mutation', ['empty', 'missing', 'duplicate', 'profile'])
def test_feedback_rejects_incomplete_selection(mutation):
    """空・必須基底欠損・重複・未知profileを成功として選択しない。"""
    feedback = importlib.import_module('tools.feedback')
    audit = json.loads(
        (ROOT / 'docs/reports/20261020_quality_gate_audit_metrics.json').read_text(encoding='utf-8')
    )
    ids = [row[0] for row in audit['inventory_analysis']['inventory']]
    if mutation == 'empty':
        ids = []
    elif mutation == 'missing':
        ids = [item for item in ids if 'test_temporal_qpc_boundaries' not in item]
    elif mutation == 'duplicate':
        ids += [ids[0]]
    with pytest.raises(ValueError):
        feedback.select_tests(ids, 'unknown' if mutation == 'profile' else 'quick')


@pytest.mark.parametrize(
    'path,full',
    [
        ('reversi_player/ai_engine.py', False),
        ('harness/contracts.py', True),
        ('pyproject.toml', True),
        ('tests/fixtures/new.py', True),
        ('unknown.py', True),
    ],
)
def test_feedback_unknown_and_shared_changes_require_full(path, full):
    """共有契約と未知変更を、AI専用の短縮選択へ誤分類しない。"""
    feedback = importlib.import_module('tools.feedback')
    assert bool(feedback.full_reasons([('M', path)])) is full


@pytest.fixture(scope='module')
def feedback_template(tmp_path_factory):
    """合成テストを実pytestで実行し、選択と証拠を検証する。静的検査だけ模擬する。"""
    feedback = importlib.import_module('tools.feedback')
    root = tmp_path_factory.mktemp('feedback-source')
    _scope_copy(root)
    (root / 'docs').mkdir()
    for name in ('harness_architecture.md', 'harness_contracts.md'):
        shutil.copy2(ROOT / 'docs' / name, root / 'docs' / name)
    shutil.copy2(ROOT / 'requirements-dev.txt', root / 'requirements-dev.txt')
    shutil.copy2(ROOT / 'pytest.ini', root / 'pytest.ini')
    (root / 'tests/fixtures').mkdir(parents=True)
    shutil.copy2(ROOT / 'tests/fixtures/budget_player.py', root / 'tests/fixtures/budget_player.py')
    source = root / 'original.json'
    source.write_text('{}', encoding='utf-8')
    artifact = {
        'path': str(source),
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'artifact_kind': 'population',
        'source_run_id': 'synthetic',
        'source_schema': 5,
    }
    bodies = {}
    for file in feedback.AI_TEST_FILES:
        bodies[file] = ['test_synthetic']
    bodies['tests/test_harness.py'] = ['test_' + name for name in feedback.QUICK_HARNESS]
    bodies['tests/test_quality.py'] = ['test_' + name for name in feedback.QUICK_QUALITY]
    for file, names in bodies.items():
        text = 'from tools import evidence_plugin as ep\n'
        for name in names:
            text += (
                f'\ndef {name}():\n'
                '    ep.record_expectation("synthetic", inputs=None, expected=True, '
                'expectation_kind="fixed", model="synthetic", contract="synthetic", '
                'implementation="synthetic")\n'
                '    ep.record_observation("synthetic", True)\n'
                f'    ep._explicit("artifact", {artifact!r})\n'
                '    assert True\n'
            )
        (root / file).write_text(text, encoding='utf-8')
    original = quality.run_step

    def runner(step, path, log):
        """collection/pytestは実行し、それ以外の外部検査のみ模擬して検証範囲を区別する。"""
        if step.name in ('collection', 'pytest'):
            return original(step, path, log)
        log.write_text('synthetic static check\n', encoding='utf-8')
        return {
            'name': step.name,
            'command': list(step.command),
            'exit_code': 0,
            'seconds': 0.0,
            'log': log.name,
            'error': None,
            'passed': True,
        }

    patch = pytest.MonkeyPatch()
    patch.setattr(quality, 'run_step', runner)
    output = tmp_path_factory.mktemp('feedback-result') / 'run'
    try:
        report = feedback.run_feedback(root, output, 'quick', {'full_reasons': []})
    finally:
        patch.undo()
    assert report['passed'], report
    assert report['manifest']['reproduce_gate'][1:4] == ['-B', '-m', 'tools.feedback']
    assert report['environment']['executable'] == report['manifest']['environment']['executable']
    assert feedback.verify_feedback(output) == []
    return output


@pytest.mark.parametrize(
    'mutation',
    [
        'none',
        'full_claim',
        'wrong_kind',
        'summary',
        'selection',
        'empty',
        'missing_journal',
        'missing_artifact',
        'artifact_bytes',
        'log',
        'source',
        'skip',
    ],
)
def test_feedback_rechecks_raw_evidence_and_selection(feedback_template, tmp_path, mutation):
    """hashを再計算してもsummary・選択・原bytesの矛盾を隠せない。正常証拠は再利用可。"""
    feedback = importlib.import_module('tools.feedback')
    output = tmp_path / 'run'
    shutil.copytree(feedback_template, output)
    report_path = output / 'feedback.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if mutation == 'full_claim':
        report['full_quality_passed'] = True
    elif mutation == 'wrong_kind':
        report['report_kind'] = 'quality_gate'
    elif mutation == 'summary':
        report['test_step']['tests'][0]['seconds'] = 999
    elif mutation in ('selection', 'empty'):
        plan = json.loads((output / 'selection.json').read_text(encoding='utf-8'))
        plan['selected_nodeids'] = [] if mutation == 'empty' else plan['selected_nodeids'][1:]
        (output / 'selection.json').write_text(json.dumps(plan), encoding='utf-8')
        report['selection_sha256'] = hashlib.sha256(
            (output / 'selection.json').read_bytes()
        ).hexdigest()
    elif mutation == 'missing_journal':
        (output / 'pytest/events.jsonl').unlink()
    elif mutation == 'missing_artifact':
        next((output / 'artifacts').iterdir()).unlink()
    elif mutation == 'artifact_bytes':
        next((output / 'artifacts').iterdir()).write_text('changed', encoding='utf-8')
    elif mutation == 'log':
        (output / 'typing.txt').write_text('changed', encoding='utf-8')
    elif mutation == 'source':
        report['source_hashes_end'] = {}
    elif mutation == 'skip':
        report['skipped_nodeids'] = ['not-executed']
    report_path.write_text(json.dumps(report), encoding='utf-8')
    errors = feedback.verify_feedback(output)
    assert bool(errors) is (mutation != 'none'), errors


def test_feedback_retained_bytes_survive_original_removal(feedback_template):
    """元のpytest一時領域を失っても、同一bytesの保管先で再収集できる。"""
    feedback = importlib.import_module('tools.feedback')
    report = json.loads((feedback_template / 'feedback.json').read_text(encoding='utf-8'))
    Path(report['retained_artifacts'][0]['path']).unlink(missing_ok=True)
    assert feedback.verify_feedback(feedback_template) == []


def test_feedback_saved_verification_cli(feedback_template, tmp_path, monkeypatch):
    """保存再解析の公開CLIでも、正常受理と不正Full主張の拒否を確認する。"""
    feedback = importlib.import_module('tools.feedback')
    output = tmp_path / 'run'
    shutil.copytree(feedback_template, output)
    monkeypatch.setattr('sys.argv', ['feedback', '--verify', str(output)])
    assert feedback.main() == 0
    path = output / 'feedback.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    report['full_quality_passed'] = True
    path.write_text(json.dumps(report), encoding='utf-8')
    assert feedback.main() == 1


def test_feedback_consistent_skip_is_not_success(feedback_template, tmp_path):
    """JUnitとjournalと保存集計が全て整合しても、skipを検証済み成功へ昇格しない。"""
    from xml.etree import ElementTree
    from tools import evidence

    feedback = importlib.import_module('tools.feedback')
    output = tmp_path / 'run'
    shutil.copytree(feedback_template, output)
    report = json.loads((output / 'feedback.json').read_text(encoding='utf-8'))
    journal = output / 'pytest/events.jsonl'
    events = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    first = report['test_step']['tests'][0]['test_id']
    for event in events:
        if (
            event.get('nodeid') == first
            and event.get('kind') == 'phase'
            and event['when'] == 'call'
        ):
            event['outcome'] = 'skipped'
    journal.write_text('\n'.join(json.dumps(event) for event in events) + '\n', encoding='utf-8')
    path = output / 'pytest/pytest.xml'
    xml = ElementTree.parse(path)
    suite = next(xml.iter('testsuite'))
    suite.set('skipped', '1')
    ElementTree.SubElement(next(xml.iter('testcase')), 'skipped')
    xml.write(path, encoding='utf-8')
    _, paths = feedback.retained_paths(output, report['test_step']['artifacts'], capture=False)
    report['test_step'] = evidence.collect_step(output, output.name, report['steps'][-2], paths)
    report['skipped_nodeids'] = [first]
    (output / 'feedback.json').write_text(json.dumps(report), encoding='utf-8')
    errors = feedback.verify_feedback(output)
    assert 'test_failed_or_skipped' in errors
    assert 'test_summary_mismatch' not in errors
    assert 'test_evidence_incomplete' not in errors


def test_feedback_plugin_rejects_collection_drift(feedback_template, tmp_path):
    """collect-only後に実行collectionが変われば、テスト実行前に拒否する。"""
    from types import SimpleNamespace

    feedback = importlib.import_module('tools.feedback')
    config = SimpleNamespace(
        getoption=lambda name: (
            str(feedback_template / 'selection.json') if name == 'feedback_selection' else None
        )
    )
    with pytest.raises(pytest.UsageError, match='collection/policy mismatch'):
        feedback.pytest_collection_modifyitems(config, [SimpleNamespace(nodeid='different')])


def test_registered_new_module_still_requires_real_strict(tmp_path):
    """scope登録を済ませても、実mypyが新規モジュールの型不整合を拒否する。"""
    import subprocess
    import sys

    _scope_copy(tmp_path)
    (tmp_path / 'tools/registered.py').write_text(
        'def value() -> int:\n    return "wrong"\n', encoding='utf-8'
    )
    path = tmp_path / 'pyproject.toml'
    path.write_text(
        path.read_text(encoding='utf-8').replace('files = [', 'files = ["tools/registered.py", '),
        encoding='utf-8',
    )
    assert quality.validate_scope(tmp_path) == []
    process = subprocess.run(
        [sys.executable, '-B', '-m', 'mypy', '--no-incremental', '--config-file', str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode != 0
    assert 'registered.py' in process.stdout and '[return-value]' in process.stdout
