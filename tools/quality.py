"""評価契約を変更せず、モデル照合・静的検査・既存試験を直列実行する品質入口。"""

import ast
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
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


def required_steps(root: Path) -> tuple[Step, ...]:
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
        Step('pytest', pytest),
        Step('smoke', (*pytest, '-m', 'smoke'), 120.0),
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
    """対象コード・設定・モデルをfingerprintする。大きな評価/学習資産は読み込まない。"""
    sources = [
        root / name
        for name in ('pyproject.toml', 'requirements-dev.txt', 'pytest.ini', 'AGENTS.md')
    ]
    sources.extend(
        root / 'docs' / name for name in ('harness_architecture.md', 'harness_contracts.md')
    )
    sources.append(root / '.github/workflows/quality.yml')
    for directory in ('harness', 'reversi_player', 'tests', 'tools'):
        sources.extend((root / directory).rglob('*.py'))
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(sources)
        if path.is_file()
    }


def run_gate(root: Path, output: Path, runner: Runner = run_step) -> QualityReport:
    """全必須結果を集約し新規JSONへ保存する。既存出力は拒否、保存失敗は例外となる。"""
    output.mkdir(parents=True, exist_ok=False)
    hashes_before = _source_hashes(root)
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
    steps = required_steps(root)
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
        'planned_steps': ['environment', 'model', *(step.name for step in steps), 'snapshot'],
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
    with (output / 'quality.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    return report


def main() -> int:
    """単一CLI入口。新規owned領域へ保存し、必須失敗/実行不能/保存不能では終了1を返す。"""
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
