"""評価契約を変更せず、モデル照合・静的検査・既存試験を直列実行する品質入口。"""

import ast
import hashlib
import importlib.metadata as metadata
import os
import platform
import re
import signal
import subprocess
import sys
import time
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Step:
    """必須検査の名前・argv・外部実行上限秒を保持する。AIの探索予算ではない。"""

    name: str
    command: tuple[str, ...]
    timeout: float = 1800.0


class StepResult(TypedDict):
    """各検査の実結果。未実行/起動不能ではexit_code=Noneと理由を保存する。"""

    name: str
    command: list[str]
    exit_code: int | None
    seconds: float | None
    log: str
    error: str | None
    passed: bool


class QualityReport(TypedDict):
    """品質ゲートの報告。正式AI評価資格や性能改善を表すデータではない。"""

    report_kind: str
    format_version: int
    passed: bool
    planned_steps: list[str]
    steps: list[StepResult]
    model_errors: list[str]
    environment: dict[str, object]
    source_hashes: dict[str, str]
    source_hashes_end: dict[str, str]


Runner = Callable[[Step, Path, Path], StepResult]

PRODUCTION_ROOTS = ('harness', 'reversi_player', 'tools')
STRICT_BASELINE = frozenset(
    (
        'harness/referee.py harness/seeded_player.py harness/statistics.py '
        'harness/openings.py harness/protocol.py harness/search_budget.py '
        'harness/contracts.py harness/analysis.py harness/evaluate.py '
        'reversi_player/reversi.py reversi_player/ai_engine.py '
        'tools/quality.py tools/evidence.py tools/evidence_plugin.py'
    ).split()
)
REFERENCE_SCRIPTS = frozenset({'reversi_01.py', '102400-games/data.py'})
NON_PRODUCTION_DIRS = frozenset(
    {
        '.git',
        '.venv',
        'venv',
        '__pycache__',
        '.pytest_cache',
        '.mypy_cache',
        '.ruff_cache',
        'evaluation_results',
        'baselines',
        'tests',
    }
)


def python_inventory(root: Path) -> list[Path]:
    """本番と未分類のPythonを列挙する。固定資産・テスト・依存/生成物は別用途。"""

    def unreadable(failure: OSError) -> None:
        """走査不能な下位領域を未存在として無視しない。"""
        raise failure

    paths: list[Path] = []
    for directory, children, files in os.walk(root, followlinks=False, onerror=unreadable):
        children[:] = [
            name
            for name in children
            if name != '__pycache__'
            and not (Path(directory) == root and name in NON_PRODUCTION_DIRS)
        ]
        paths.extend(
            Path(directory) / name for name in files if name.lower().endswith(('.py', '.pyi'))
        )
        paths.extend(
            Path(directory) / name
            for name in children
            if (Path(directory) / name).is_symlink() or (Path(directory) / name).is_junction()
        )
    return sorted(paths)


def validate_scope(root: Path) -> list[str]:
    """本番inventoryと明示strictを照合する。唯一の例外は配布GUI、緩和設定は許可しない。"""
    try:
        config = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
        mypy = config['tool']['mypy']
        if set(mypy) != {'python_version', 'strict', 'explicit_package_bases', 'files'}:
            return ['typing_scope: 未承認のmypy設定/抑制']
        if (
            mypy['strict'] is not True
            or mypy['explicit_package_bases'] is not True
            or mypy['python_version'] != '3.13'
        ):
            return ['typing_scope: strict設定不一致']
        files = mypy['files']
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            return ['typing_scope: files不正']
        errors = []
        for item in files:
            path = Path(item)
            if (
                path.is_absolute()
                or path.as_posix() != item
                or '..' in path.parts
                or not (root / path).is_file()
            ):
                errors.append('typing_scope: 非正規/欠損パス: ' + item)
        if len({item.casefold() for item in files}) != len(files):
            errors.append('typing_scope: 重複/別名パス')
        production = set()
        for path in python_inventory(root):
            name = path.relative_to(root).as_posix()
            if path.is_symlink() or path.is_junction() or path.suffix.lower() == '.pyi':
                errors.append('typing_scope: 未承認stub/別名: ' + name)
            elif name.split('/')[0] in PRODUCTION_ROOTS:
                production.add(name)
            elif name not in REFERENCE_SCRIPTS:
                errors.append('typing_scope: 未分類Python: ' + name)
        expected = production - {'reversi_player/web.py'}
        errors.extend('typing_scope: 未登録: ' + name for name in sorted(expected - set(files)))
        errors.extend(
            'typing_scope: 余分/例外登録: ' + name for name in sorted(set(files) - expected)
        )
        errors.extend(
            'typing_scope: 既存本番欠損: ' + name for name in sorted(STRICT_BASELINE - production)
        )
        ruff = config['tool']['ruff']
        if ruff['include'] != [
            'harness/**/*.py',
            'reversi_player/**/*.py',
            'tests/**/*.py',
            'tools/**/*.py',
        ] or any(key in ruff for key in ('exclude', 'extend-exclude', 'force-exclude')):
            errors.append('typing_scope: Lint対象不一致')
        if ruff['lint'] != {
            'select': ['E4', 'E7', 'E9', 'F'],
            'per-file-ignores': {'tests/fixtures/budget_player.py': ['E402']},
        }:
            errors.append('typing_scope: Lint抑制不一致')
        return errors
    except (OSError, ValueError, KeyError, TypeError) as failure:
        return ['typing_scope: 設定読取不能: ' + type(failure).__name__]


def _imports(path: Path) -> set[str]:
    """関数内も含めharnessへのimportを読み取る。コードを実行しない。"""
    result: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            result.update(
                alias.name.split('.')[1]
                for alias in node.names
                if alias.name.startswith('harness.')
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'harness' or (node.level == 1 and node.module is None):
                result.update(alias.name for alias in node.names)
            elif node.module and node.module.startswith('harness.'):
                result.add(node.module.split('.')[1])
            elif node.level == 1 and node.module and path.parent.name == 'harness':
                result.add(node.module.split('.')[0])
    return result


def _definitions(path: Path) -> dict[str, ast.FunctionDef | ast.ClassDef]:
    """トップレベルの関数・クラスを取得する。存在と呼出辺の検査用である。"""
    return {
        node.name: node
        for node in ast.parse(path.read_text(encoding='utf-8')).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }


def validate_model(root: Path) -> list[str]:
    """現行import図と主要fresh入口のAST辺を双方向照合する。意味全体は証明しない。"""
    document = (root / 'docs/harness_architecture.md').read_text(encoding='utf-8')
    if not (root / 'docs/harness_contracts.md').is_file():
        return ['現行契約文書が見つからない']
    blocks = re.findall(r'```mermaid\s*\n(.*?)```', document, re.S)
    if not blocks or not blocks[0].startswith('flowchart LR'):
        return ['現行import図が見つからない']
    graph = blocks[0]
    nodes = {
        identity: label.split('<br/>')[0]
        for identity, label in re.findall(r'(\w+)\["([^"]+)"\]', graph)
    }
    modules = {path.stem: path for path in (root / 'harness').glob('*.py')}
    modules.pop('__init__', None)
    modules['budget_player'] = root / 'tests/fixtures/budget_player.py'
    errors = [f'モデルにないmodule: {name}' for name in modules if name not in nodes.values()]
    drawn = {
        (nodes[source], nodes[target])
        for source, target in re.findall(
            r'^\s+(\w+)(?:\[[^\n]*?\])?\s+-->(?:\|[^|]*\|)?\s+(\w+)', graph, re.M
        )
        if source in nodes and target in nodes
    }
    actual = {(name, target) for name, path in modules.items() for target in _imports(path)}
    errors.extend(f'import図の不足: {edge}' for edge in sorted(actual - drawn))
    errors.extend(f'import図の過剰: {edge}' for edge in sorted(drawn - actual))
    active: set[str] = set()
    complete: set[str] = set()

    def visit(name: str) -> None:
        """現行import辺の循環を検出する。状態/通信の順序はここでは検証しない。"""
        if name in active:
            errors.append(f'import循環: {name}')
            return
        if name in complete:
            return
        active.add(name)
        for source, target in actual:
            if source == name:
                visit(target)
        active.remove(name)
        complete.add(name)

    for name in modules:
        visit(name)
    entry_edges = {
        'harness/analysis.py': {
            'assess_evaluation': 'contracts.inspect_evaluation',
            'estimate': 'contracts.inspect_evaluation',
            'reanalysis_report': 'contracts.inspect_evaluation',
            '_inspect_pair': 'contracts.inspect_evaluation',
            'assess_comparison': '_inspect_pair',
            'paired': '_inspect_pair',
            'compare': '_inspect_pair',
            'comparison_report': 'compare',
        },
        'harness/evaluate.py': {'compare_reports': 'analysis.compare'},
        'harness/contracts.py': {'inspect_evaluation': '_inspect_evaluation'},
    }
    for filename, required in entry_edges.items():
        definitions = _definitions(root / filename)
        for name, target in required.items():
            node = definitions.get(name)
            calls = (
                {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}
                if node is not None
                else set()
            )
            if target not in calls:
                errors.append(f'共通入口の呼出辺不足: {filename}:{name} -> {target}')
    return errors


def _environment_errors(root: Path) -> list[str]:
    """対応Pythonと固定dev依存を照合する。不適合を勝手にインストール/補修しない。"""
    errors = []
    if platform.python_implementation() != 'CPython' or sys.version_info[:3] != (3, 13, 5):
        errors.append('正式時計回帰の対応PythonはCPython3.13.5')
    if sys.platform != 'win32':
        errors.append('正式時計回帰の対応OSはWindows')
    if sys.prefix != sys.base_prefix:
        errors.append('venvランチャーはPID契約未対応。直接CPythonで実行する')
    declared: set[str] = set()
    for line in (root / 'requirements-dev.txt').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, separator, expected = line.partition('==')
        if not separator:
            errors.append(f'固定版でない開発依存: {line}')
            continue
        declared.add(name)
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            errors.append(f'開発依存が未導入: {name}')
            continue
        if actual != expected:
            errors.append(f'開発依存の版不一致: {name} {actual} != {expected}')
    errors.extend(
        f'必須開発依存の固定版宣言なし: {name}'
        for name in sorted({'pytest', 'ruff', 'mypy', 'hypothesis'} - declared)
    )
    return errors


def _terminate(process: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    """runner所有の子treeを停止する。tree回収不能は例外で報告し成功にしない。"""
    if process.poll() is not None:
        return
    try:
        if sys.platform == 'win32':
            killed = subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if killed.returncode != 0:
                raise OSError('taskkillによる子tree回収を確認できない')
        else:
            os.killpg(process.pid, signal.SIGKILL)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def run_step(step: Step, root: Path, log: Path) -> StepResult:
    """argvをshellなしで実行し、外部経過と終了値/診断を新規ログへ保存する。"""
    started = time.perf_counter()
    exit_code = None
    error = None
    environment = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    options = (
        {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == 'nt'
        else {'start_new_session': True}
    )
    with log.open('x', encoding='utf-8') as stream:
        try:
            for argument in step.command:
                if argument.startswith('--junitxml=') and Path(argument.partition('=')[2]).exists():
                    raise FileExistsError('JUnit output already exists')
            with subprocess.Popen(
                step.command,
                cwd=root,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                **options,
            ) as process:
                try:
                    exit_code = process.wait(timeout=step.timeout)
                except subprocess.TimeoutExpired:
                    error = f'timeout: {step.timeout}秒'
                    _terminate(process)
        except (OSError, subprocess.TimeoutExpired) as failure:
            error = f'{type(failure).__name__}: {failure}'
        if error:
            stream.write(error + '\n')
    return {
        'name': step.name,
        'command': list(step.command),
        'exit_code': exit_code,
        'seconds': time.perf_counter() - started,
        'log': log.name,
        'error': error,
        'passed': exit_code == 0 and error is None,
    }


def required_steps(root: Path, output: Path | None = None) -> tuple[Step, ...]:
    """省略不可の検査を返す。対局試験を並行させず全pytestの後に独立smokeを置く。"""
    python = sys.executable
    config = str(root / 'pyproject.toml')
    pytest = (
        python,
        '-B',
        '-m',
        'pytest',
        '-q',
        '-p',
        'no:cacheprovider',
        '--tb=short',
        '--strict-config',
        '--strict-markers',
    )

    def test_command(name: str) -> tuple[str, ...]:
        """owned stepへJUnitとjournalを出すargvを作る。既存テストの選択を変えない。"""
        selected = (*pytest, '-m', 'smoke') if name == 'smoke' else pytest
        if output is None:
            return selected
        return (
            *selected,
            '-p',
            'tools.evidence_plugin',
            '--junitxml=' + str(output / name / (name + '.xml')),
            '--verification-evidence-dir=' + str(output / name),
            '--verification-run-id=' + output.name,
            '--verification-step=' + name,
        )

    return (
        Step('dependencies', (python, '-B', '-m', 'pip', 'check'), 120.0),
        Step(
            'lint',
            (python, '-B', '-m', 'ruff', 'check', '.', '--no-cache', '--config', config),
            120.0,
        ),
        Step(
            'format',
            (
                python,
                '-B',
                '-m',
                'ruff',
                'format',
                '--check',
                '.',
                '--no-cache',
                '--config',
                config,
            ),
            120.0,
        ),
        Step(
            'typing',
            (python, '-B', '-m', 'mypy', '--no-incremental', '--config-file', config),
            300.0,
        ),
        Step('pytest', test_command('pytest')),
        Step('smoke', test_command('smoke'), 120.0),
    )


def _local_result(name: str, errors: list[str], log: Path, seconds: float) -> StepResult:
    """環境/モデルのローカル検査を論理終了0/1と理由で保存する。子PIDは補造しない。"""
    log.write_text('\n'.join(errors) if errors else 'PASS\n', encoding='utf-8')
    return {
        'name': name,
        'command': [f'internal:{name}'],
        'exit_code': 1 if errors else 0,
        'seconds': seconds,
        'log': log.name,
        'error': '\n'.join(errors) or None,
        'passed': not errors,
    }


def _source_hashes(root: Path) -> dict[str, str]:
    """対象コード・設定・モデル・課題仕様をfingerprintする。評価/学習資産は読まない。"""
    sources = [
        root / name
        for name in (
            'pyproject.toml',
            'requirements-dev.txt',
            'pytest.ini',
            'AGENTS.md',
            'docs/プログラム開発方法.md',
        )
    ]
    sources.extend(
        root / 'docs' / name for name in ('harness_architecture.md', 'harness_contracts.md')
    )
    sources.append(root / '.github/workflows/quality.yml')
    for directory in ('harness', 'reversi_player', 'tests', 'tools'):
        sources.extend((root / directory).rglob('*.py'))
    sources.extend(python_inventory(root))
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(sources))
        if path.is_file()
    }


def run_gate(root: Path, output: Path, runner: Runner = run_step) -> QualityReport:
    """全必須結果を集約し新規JSONへ保存する。既存出力は拒否、保存失敗は例外となる。"""
    from tools import evidence

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    hashes_before = _source_hashes(root)
    steps = required_steps(root, output)
    manifest_data = evidence.manifest(
        root,
        output,
        hashes_before,
        [
            {
                'name': step.name,
                'step_id': output.name + ':' + step.name,
                'command': list(step.command),
                'timeout_seconds': step.timeout,
            }
            for step in steps
        ],
    )
    for name in ('pytest', 'smoke'):
        (output / name).mkdir(exist_ok=False)
    started = time.perf_counter()
    try:
        environment_errors = _environment_errors(root)
    except (OSError, ValueError) as failure:
        environment_errors = [f'環境検査不能: {failure}']
    results = [
        _local_result(
            'environment',
            environment_errors,
            output / 'environment.txt',
            time.perf_counter() - started,
        )
    ]
    started = time.perf_counter()
    try:
        model_errors = validate_model(root)
    except (OSError, ValueError, SyntaxError) as failure:
        model_errors = [f'モデル照合不能: {failure}']
    results.append(
        _local_result('model', model_errors, output / 'model.txt', time.perf_counter() - started)
    )
    started = time.perf_counter()
    scope_errors = validate_scope(root)
    results.append(
        _local_result(
            'typing_scope', scope_errors, output / 'typing_scope.txt', time.perf_counter() - started
        )
    )
    scope_plan: dict[str, object] = {
        'name': 'typing_scope',
        'step_id': output.name + ':typing_scope',
        'command': ['internal:typing_scope'],
        'timeout_seconds': None,
    }
    manifest_steps = manifest_data['steps']
    if not isinstance(manifest_steps, list):
        raise ValueError('manifest_steps_invalid')
    manifest_steps.append(scope_plan)
    for step in steps:
        log = output / f'{step.name}.txt'
        if environment_errors:
            log.write_text('対応環境を確認できないため未実行\n', encoding='utf-8')
            result: StepResult = {
                'name': step.name,
                'command': list(step.command),
                'exit_code': None,
                'seconds': None,
                'log': log.name,
                'error': 'environment未確認のため未実行',
                'passed': False,
            }
        else:
            print(f'RUN {step.name}', flush=True)
            result = runner(step, root, log)
            print(
                f'{"PASS" if result["passed"] else "FAIL"} {step.name} '
                f'exit={result["exit_code"]} seconds={result["seconds"]}',
                flush=True,
            )
        results.append(result)
    started = time.perf_counter()
    hashes_after = _source_hashes(root)
    snapshot_errors = [] if hashes_before == hashes_after else ['検査中に対象sourceが変更された']
    snapshot_errors.extend(validate_scope(root))
    results.append(
        _local_result(
            'snapshot', snapshot_errors, output / 'snapshot.txt', time.perf_counter() - started
        )
    )
    report: QualityReport = {
        'report_kind': 'quality_gate',
        'format_version': 1,
        'passed': all(
            item['passed'] and item['exit_code'] == 0 and item['error'] is None for item in results
        ),
        'planned_steps': [
            'environment',
            'model',
            'typing_scope',
            *(step.name for step in steps),
            'snapshot',
        ],
        'steps': results,
        'model_errors': model_errors,
        'environment': {
            'python': sys.version,
            'executable': sys.executable,
            'platform': platform.platform(),
            'packages': {
                distribution.name: distribution.version for distribution in metadata.distributions()
            },
        },
        'source_hashes': hashes_before,
        'source_hashes_end': hashes_after,
    }
    manifest_data.update(
        ended_at_utc=evidence.utc_now(),
        environment=report['environment'],
        source_hashes_end=hashes_after,
    )
    started = time.perf_counter()
    try:
        bundle = evidence.collect(output, manifest_data, [{**item} for item in results])
        collection_errors = (
            [str(item) for item in bundle['errors']]
            if isinstance(bundle['errors'], list)
            else ['evidence_errors_invalid']
        )
        evidence_result = _local_result(
            'evidence', collection_errors, output / 'evidence.txt', time.perf_counter() - started
        )
        results.append(evidence_result)
        report['planned_steps'].append('evidence')
        report['passed'] = report['passed'] and evidence_result['passed']
        prepared_quality = evidence.encoded_json(report)
        bundle['quality_report_sha256'] = hashlib.sha256(
            prepared_quality.encode('utf-8')
        ).hexdigest()
        bundle['quality_report'] = 'quality.json'
        markdown = evidence.render_markdown(bundle)
        evidence.save_new(output / 'evidence.md', markdown)
        bundle['markdown_sha256'] = hashlib.sha256(markdown.encode('utf-8')).hexdigest()
        evidence.save_new(output / 'evidence.json', evidence.encoded_json(bundle))
    except (OSError, ValueError, TypeError) as failure:
        print(f'EVIDENCE FAIL: {type(failure).__name__}: {failure}', file=sys.stderr)
        failed_result: StepResult = {
            'name': 'evidence',
            'command': ['internal:evidence'],
            'exit_code': 1,
            'seconds': time.perf_counter() - started,
            'log': 'evidence.txt',
            'error': 'evidence_collection_or_save_failed',
            'passed': False,
        }
        if results[-1]['name'] == 'evidence':
            results[-1] = failed_result
        else:
            results.append(failed_result)
            report['planned_steps'].append('evidence')
        report['passed'] = False
    evidence.save_new(output / 'quality.json', evidence.encoded_json(report))
    return report


def main() -> int:
    """単一CLI入口。新規owned領域へ保存し、必須失敗/実行不能/保存不能では終了1を返す。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    output = ROOT / 'evaluation_results' / ('quality-' + uuid.uuid4().hex)
    try:
        report = run_gate(ROOT, output)
    except (OSError, ValueError, subprocess.SubprocessError) as failure:
        print(f'QUALITY FAIL: {failure}; evidence={output}', file=sys.stderr)
        return 1
    print(f'QUALITY {"PASS" if report["passed"] else "FAIL"}: {output / "quality.json"}')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
