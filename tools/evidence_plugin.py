"""pytestの判定を変えず、段階別結果と明示された検証情報だけを逐次記録する。"""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TextIO

import pytest

IMPORTANT_TESTS = frozenset(
    {
        'test_all_eight_directions_flip_together',
        'test_response_contract_accepts_exact_wire_format_without_mutation',
        'test_response_contract_rejects_malformed_output_without_mutation',
        'test_live_rejected_move_preserves_board',
        'test_schema5_decode_diagnostics',
        'test_saved_trace_semantics_block_formal_statistics_even_with_new_hash',
        'test_temporal_normal_schema5_reaches_all_formal_entries',
        'test_paired_complete_games_are_reproducible_and_serializable',
    }
)


def mapping(value: object) -> dict[str, object]:
    """JSONの辞書を文字列キーで扱う。型が異なる場合は空辞書を返す収集用境界。"""
    return (
        {key: item for key, item in value.items() if isinstance(key, str)}
        if isinstance(value, dict)
        else {}
    )


def _safe_metadata(value: object) -> None:
    """秘密を示すキーと巨大な明示metadataを拒否する。任意localや環境は読まない。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or any(
                word in key.lower() for word in ('password', 'secret', 'token', 'api_key')
            ):
                raise ValueError('secret_or_invalid_metadata_key')
            _safe_metadata(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe_metadata(item)


class Recorder:
    """一つの直列pytestセッションのjournal。収集失敗は記録し、pytest結果を変更しない。"""

    def __init__(self, stream: TextIO, run_id: str, step: str):
        """新規作成済みstreamと識別子を保持する。全worker共有には対応しない。"""
        self.stream = stream
        self.run_id = run_id
        self.step = step
        self.sequence = 0
        self.nodeid: str | None = None
        self.errors: list[str] = []

    def emit(self, kind: str, **payload: object) -> None:
        """一行JSONを追記・flushする。失敗値を補修せずstderrと最終状態へ残す。"""
        try:
            encoded = json.dumps(
                {
                    'format_version': 1,
                    'run_id': self.run_id,
                    'step': self.step,
                    'sequence': self.sequence,
                    'kind': kind,
                    'nodeid': self.nodeid,
                    **payload,
                },
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            if len(encoded.encode('utf-8')) > 2_000_000:
                raise ValueError('journal_event_too_large')
            self.stream.write(encoded + '\n')
            self.stream.flush()
            self.sequence += 1
        except (OSError, ValueError, TypeError) as failure:
            self.errors.append(type(failure).__name__ + ': journal_write_failed')
            print('EVIDENCE ERROR: journal_write_failed', file=sys.stderr)


_recorder: Recorder | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    """品質ゲートだけが指定する証拠保存先・run・stepを登録する。"""
    parser.addoption('--verification-evidence-dir', default=None)
    parser.addoption('--verification-run-id', default=None)
    parser.addoption('--verification-step', default=None)


def pytest_configure(config: pytest.Config) -> None:
    """journalを排他新規作成する。保存不能でもpytestの合否には干渉しない。"""
    global _recorder
    _recorder = None
    directory = config.getoption('verification_evidence_dir')
    if directory is None:
        return
    try:
        stream = (Path(str(directory)) / 'events.jsonl').open('x', encoding='utf-8', newline='\n')
        _recorder = Recorder(
            stream,
            str(config.getoption('verification_run_id')),
            str(config.getoption('verification_step')),
        )
        _recorder.emit(
            'session_start',
            plugins=sorted(
                name
                for name, plugin in config.pluginmanager.list_name_plugin()
                if plugin is not None
            ),
        )
    except OSError:
        print('EVIDENCE ERROR: journal_prepare_failed', file=sys.stderr)


def pytest_collection_finish(session: pytest.Session) -> None:
    """選択されたnodeidを保存する。deselectedを実行済みテストへ変換しない。"""
    if _recorder:
        _recorder.emit('selected', nodeids=[item.nodeid for item in session.items])


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """collection段階の失敗・skipを通常のcall結果とは別に保存する。"""
    if _recorder and not report.passed:
        _recorder.emit(
            'collection',
            collection_nodeid=report.nodeid,
            outcome=report.outcome,
            diagnostic=str(report.longrepr)[:8192],
        )


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """テスト開始を保存し、限定明示APIの登録先を切り替える。"""
    if _recorder:
        _recorder.nodeid = nodeid
        name = nodeid.rsplit('::', 1)[-1].split('[', 1)[0]
        _recorder.emit('test_start', important=name in IMPORTANT_TESTS)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """setup/call/teardownの元outcomeと診断を保存する。合否の再判定はしない。"""
    if _recorder:
        diagnostic = str(report.longrepr) if report.longrepr is not None else None
        _recorder.emit(
            'phase',
            nodeid=report.nodeid,
            when=report.when,
            outcome=report.outcome,
            duration=report.duration,
            diagnostic=diagnostic[:8192] if diagnostic else None,
            diagnostic_truncated=bool(diagnostic and len(diagnostic) > 8192),
        )


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """通常のテスト終了を保存する。session強制停止でこの記録を補造しない。"""
    if _recorder:
        _recorder.emit('test_finish', nodeid=nodeid)
        _recorder.nodeid = None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    """pytestの元終了値と収集障害を保存してfsyncする。終了値を書き換えない。"""
    if _recorder:
        _recorder.emit(
            'session_finish', exit_code=int(exitstatus), capture_errors=list(_recorder.errors)
        )
        try:
            _recorder.stream.flush()
            os.fsync(_recorder.stream.fileno())
            _recorder.stream.close()
        except OSError:
            print('EVIDENCE ERROR: journal_finish_failed', file=sys.stderr)


def _explicit(kind: str, payload: dict[str, object]) -> None:
    """検証者が指定したJSONだけを記録する。pluginなしでは副作用なし。"""
    if _recorder is None:
        return
    try:
        _safe_metadata(payload)
        if kind == 'expectation' and (
            payload.get('expectation_kind')
            not in ('fixed', 'mathematical', 'independent_referee', 'consistency')
            or any(
                not isinstance(payload.get(key), str) or not payload.get(key)
                for key in ('model', 'contract', 'implementation')
            )
        ):
            raise ValueError('expectation_metadata_invalid')
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 65536:
            raise ValueError('metadata_too_large')
        _recorder.emit(kind, **payload)
    except (ValueError, TypeError, RecursionError):
        _recorder.errors.append('explicit_metadata_invalid')
        print('EVIDENCE ERROR: explicit_metadata_invalid', file=sys.stderr)


def record_expectation(
    check_id: str,
    *,
    inputs: object,
    expected: object,
    expectation_kind: str,
    model: str,
    contract: str,
    implementation: str,
) -> None:
    """対象呼出の前に独立期待・根拠を登録する。実測値から期待を生成しない。"""
    _explicit(
        'expectation',
        {
            'check_id': check_id,
            'input': inputs,
            'expected': expected,
            'expectation_kind': expectation_kind,
            'model': model,
            'contract': contract,
            'implementation': implementation,
        },
    )


def record_observation(check_id: str, actual: object) -> None:
    """assert前の明示観測を保存する。collectorに合否を決めさせない。"""
    _explicit('observation', {'check_id': check_id, 'actual': actual})


def record_harness_artifacts(report: dict[str, object]) -> None:
    """取得済みrunのcheckpoint・母集合・rawだけを参照登録する。原証跡は変更しない。"""
    if _recorder is None:
        return
    try:
        info = mapping(report.get('evidence'))
        root = Path(str(info['root'])).resolve()
        sources: list[tuple[Path, str, object]] = [(root / 'run-state.json', 'checkpoint', None)]
        population = mapping(info.get('population'))
        if population:
            sources.append((root / str(population['path']), 'population', population.get('sha256')))
        games = report.get('games')
        if isinstance(games, list):
            for game in games:
                for player in mapping(mapping(game).get('players')).values():
                    artifact = mapping(mapping(mapping(player).get('budget_trace')).get('artifact'))
                    if artifact.get('exists') is True:
                        sources.append(
                            (root / str(artifact['path']), 'trace', artifact.get('sha256'))
                        )
        for probe in mapping(report.get('determinism')).values():
            probe_sources = mapping(probe).get('trace_artifacts')
            if isinstance(probe_sources, list):
                for source in probe_sources:
                    reference = mapping(source)
                    sources.append(
                        (root / str(reference['path']), 'trace', reference.get('sha256'))
                    )
        for path, kind, expected_hash in sources:
            path = path.resolve()
            path.relative_to(root)
            captured_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if expected_hash is not None and expected_hash != captured_hash:
                raise ValueError('artifact_registration_hash_mismatch')
            _explicit(
                'artifact',
                {
                    'path': str(path),
                    'artifact_kind': kind,
                    'sha256': captured_hash,
                    'source_run_id': report.get('run_id'),
                    'source_schema': report.get('schema_version'),
                },
            )
    except (OSError, ValueError, KeyError, TypeError):
        _recorder.errors.append('artifact_registration_failed')
        print('EVIDENCE ERROR: artifact_registration_failed', file=sys.stderr)
