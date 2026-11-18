"""独立子プロセスと既存HTTPエンドポイントのPhase 6接続を検証する。"""

import ast
from contextlib import contextmanager
from functools import partial
from http.server import HTTPServer
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness import referee
from reversi_player import ntuple, ntuple_io, web


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'reversi_player' / 'phase6_player.py'
ENV = 'REVERSI_NTUPLE_MODEL'


@pytest.fixture
def model_path(tmp_path):
    """空白と日本語を含む絶対パスで実モデルを指定する。"""
    path = tmp_path / '未学習 model.json'
    ntuple_io.save_model(path, ntuple.NtupleModel.zeros())
    return path


def _run(argv, script_input, cwd, path, **extra_env):
    """子プロセスを実際に起動し、応答を読み切って終了まで待つ。"""
    environment = os.environ.copy()
    environment.pop(ENV, None)
    environment.pop('REVERSI_EVAL_CONFIG', None)
    if path is not None:
        environment[ENV] = str(path)
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment.update(extra_env)
    return subprocess.run(
        [sys.executable, '-B', *argv],
        input=script_input,
        cwd=cwd,
        env=environment,
        encoding='utf-8',
        capture_output=True,
        timeout=10,
    )


@pytest.fixture(scope='module')
def frontier():
    """合法な棋譜から強制パス局面と終局局面を記録する。"""
    rng = random.Random(20261118)
    for _ in range(8):
        rows = referee.INITIAL_ROWS
        colors = ('B', 'W')
        positions = []
        before_pass = None
        while referee.result(rows) == 'NG':
            color = colors[len(positions) % 2]
            available = referee.legal_moves(rows, color)
            if not available:
                before_pass = (tuple(positions), rows)
                positions.append((-1, -1))
            else:
                move = rng.choice(available)
                rows = referee.apply_move(rows, color, move)
                positions.append(move)
        if before_pass:
            return before_pass, (tuple(positions), rows)
    raise AssertionError('強制パスを含む棋譜が得られなかった')


def _play_commands(history):
    """既存プロトコルは手番を引数で受け、パス中の盤面を保つ。"""
    return [f'play {"BW"[i % 2]} {x} {y}' for i, (x, y) in enumerate(history) if (x, y) != (-1, -1)]


def test_direct_run_all_commands_invalid_input_and_quit(model_path, tmp_path):
    rows = referee.INITIAL_ROWS
    after_play = referee.apply_move(rows, 'B', (3, 2))
    white = min(referee.legal_moves(after_play, 'W'), key=lambda move: move[::-1])
    after_genmove = referee.apply_move(after_play, 'W', white)
    batch = '\n'.join(
        [
            'name',
            'ask B 3 2',
            'ask B -1 2',
            'ask X 3 2',
            'play B 0 0',
            'play B 3 2',
            'genmove W',
            'showboard',
            'result',
            'quit',
            'name',
            '',
        ]
    )
    expected = [
        'ranger',
        'OK',
        'NG',
        'NG',
        'NG',
        'OK',
        f'{white[0]} {white[1]}',
        *after_genmove,
        'NG',
    ]
    for _ in range(2):
        process = _run([str(SCRIPT)], batch, tmp_path, model_path)
        assert (process.returncode, process.stdout.splitlines(), process.stderr) == (
            0,
            expected,
            '',
        )


def test_package_startup_eof_and_model_load_once_per_child(model_path):
    run = _run(['-m', 'reversi_player.phase6_player'], 'name\n', ROOT, model_path)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'ranger\n', '')
    env = {**os.environ, ENV: str(model_path)}
    env.pop('REVERSI_EVAL_CONFIG', None)
    with subprocess.Popen(
        [sys.executable, '-B', str(SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    ) as child:
        try:
            child.stdin.write('name\n')
            child.stdin.flush()
            assert child.stdout.readline() == 'ranger\n'
            model_path.write_bytes(b'bad after startup')
            out, err = child.communicate('genmove B\nquit\n', timeout=10)
            assert (child.returncode, out, err) == (0, '3 2\n', '')
        finally:
            if child.poll() is None:
                child.kill()


@pytest.mark.parametrize(
    'kind,diagnostic',
    [
        ('missing_env', 'requires_absolute_path'),
        ('relative', 'requires_absolute_path'),
        ('missing_file', 'FileNotFoundError'),
        ('corrupt', 'invalid_model_json'),
        ('search_config', 'phase6_does_not_support_search_config'),
    ],
)
def test_process_load_failure_explicit_and_no_stdout(kind, diagnostic, model_path, tmp_path):
    settings = {}
    path = model_path
    if kind == 'missing_env':
        path = None
    elif kind == 'relative':
        path = model_path.name
    elif kind == 'missing_file':
        path = tmp_path / 'missing.json'
    elif kind == 'corrupt':
        path.write_bytes(b'{broken')
    elif kind == 'search_config':
        config_path = tmp_path / 'search.json'
        config_path.write_text(
            '{"format_version":2,"budget":{"kind":"depth","limit_depth":2},"search":{}}',
            encoding='utf-8',
        )
        settings['REVERSI_EVAL_CONFIG'] = str(config_path)
    error = _run([str(SCRIPT)], 'name\ngenmove B\n', tmp_path, path, **settings)
    assert (error.returncode, error.stdout) == (2, '')
    assert error.stderr.startswith('phase6_model_load_error:')
    assert diagnostic in error.stderr


def test_process_harness_unknown_config_runs_without_trace(model_path, tmp_path):
    config_path = tmp_path / 'unknown.json'
    config_path.write_text(
        '{"format_version":2,"budget":{"kind":"unknown"},"search":{}}', encoding='utf-8'
    )
    trace_path = tmp_path / 'trace.jsonl'
    run = _run(
        [str(SCRIPT)],
        'name\nquit\n',
        tmp_path,
        model_path,
        REVERSI_EVAL_CONFIG=str(config_path),
        REVERSI_EVAL_TRACE=str(trace_path),
        REVERSI_EVAL_TRACE_CONTEXT='{}',
    )
    assert (run.returncode, run.stdout, run.stderr) == (0, 'ranger\n', '')
    assert not trace_path.exists()


def test_process_replay_pass_and_terminal_against_referee(model_path, tmp_path, frontier):
    (pass_history, pass_rows), (history, rows) = frontier
    turn = 'BW'[len(pass_history) % 2]
    other = 'WB'[len(pass_history) % 2]
    assert not referee.legal_moves(pass_rows, turn) and referee.legal_moves(pass_rows, other)
    batch = '\n'.join([*_play_commands(pass_history), f'genmove {turn}', 'showboard', 'result', ''])
    run = _run([str(SCRIPT)], batch, tmp_path, model_path)
    assert (run.returncode, run.stderr) == (0, '')
    assert run.stdout.splitlines() == ['OK'] * len(_play_commands(pass_history)) + [
        'NG',
        *pass_rows,
        'NG',
    ]
    batch = '\n'.join(
        [*_play_commands(history), 'genmove B', 'genmove W', 'showboard', 'result', 'quit', '']
    )
    run = _run([str(SCRIPT)], batch, tmp_path, model_path)
    assert (run.returncode, run.stderr) == (0, '')
    assert run.stdout.splitlines() == (
        ['OK'] * len(_play_commands(history)) + ['NG', 'NG', *rows, referee.result(rows)]
    )


@contextmanager
def _http_server():
    """既存のRequestHandlerを動的なローカルポートで実行する。"""
    with HTTPServer(('127.0.0.1', 0), web.RequestHandler) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01))
        worker.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            worker.join(timeout=5)


def _request(url, endpoint, positions=None):
    """本物のHTTPを通してHTMLまたはJSON応答を取得する。"""
    data = None if positions is None else json.dumps({'positions': positions}).encode()
    with urlopen(Request(url + endpoint, data=data), timeout=10) as response:
        if positions is None:
            return response.read()
        assert response.status == 200
        return json.load(response)


def _check_gui_response(reply, rows, history):
    """HTTP応答を独立refereeの盤面・着手・結果と照合する。"""
    turn = 'BW'[len(history) % 2]
    result = referee.result(rows)
    moves = referee.legal_moves(rows, turn)
    assert reply['positions'] == [list(move) for move in history]
    assert reply['board'] == [[{'E': 0, 'B': 1, 'W': -1}[cell] for cell in row] for row in rows]
    assert reply['valid'] == [[int((x, y) in moves) for x in range(8)] for y in range(8)]
    assert reply['end'] is (result != 'NG')
    assert reply['score'] == {'NG': 0, 'B': 1, 'W': -1, 'D': 0}[result]


def test_gui_per_request_process_initial_move_pass_terminal_and_model_failure(
    model_path, monkeypatch, frontier, tmp_path
):
    (pass_history, pass_rows), (history, rows) = frontier
    monkeypatch.delenv('REVERSI_EVAL_CONFIG', raising=False)
    monkeypatch.setenv(ENV, str(model_path))
    with _http_server() as url:
        assert b'<html' in _request(url, '/').lower()
        _check_gui_response(_request(url, '/play', []), referee.INITIAL_ROWS, [])
        _check_gui_response(
            _request(url, '/genmove', []),
            referee.apply_move(referee.INITIAL_ROWS, 'B', (3, 2)),
            [(3, 2)],
        )
        _check_gui_response(
            _request(url, '/genmove', pass_history), pass_rows, [*pass_history, (-1, -1)]
        )
        _check_gui_response(_request(url, '/play', history), rows, history)
        data = model_path.read_bytes()
        model_path.write_bytes(b'{')
        with pytest.raises(HTTPError) as corrupt:
            _request(url, '/play', [])
        assert corrupt.value.code == 500
        monkeypatch.setenv(ENV, str(tmp_path / 'missing.json'))
        with pytest.raises(HTTPError) as missing:
            _request(url, '/genmove', [])
        assert missing.value.code == 500
        model_path.write_bytes(data)
        monkeypatch.setenv(ENV, str(model_path))
        _check_gui_response(_request(url, '/play', []), referee.INITIAL_ROWS, [])
        monkeypatch.delenv(ENV)
        initial = _request(url, '/genmove', [])
        move = tuple(initial['positions'][0])
        assert move in referee.legal_moves(referee.INITIAL_ROWS, 'B')
        _check_gui_response(initial, referee.apply_move(referee.INITIAL_ROWS, 'B', move), [move])


def test_runtime_code_parses_in_python37_grammar():
    for name in ('ntuple.py', 'ntuple_io.py', 'phase6_player.py', 'web.py'):
        folder = SCRIPT.parent / name
        ast.parse(folder.read_text(encoding='utf-8'), str(folder), feature_version=(3, 7))
