"""Windowsのプロセス識別、排他ロックと状態ファイルの発行。"""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, IO, TypedDict

from tools.evidence import encoded_json

if TYPE_CHECKING:
    from collections.abc import Iterator

if os.name == 'nt':
    import msvcrt


class Busy(RuntimeError):
    """既に別の実行が同じ資源を使用している。"""


class Identity(TypedDict):
    """PIDとWindowsが与えた生成時刻の組。"""

    pid: int
    created: int


def extended(path: Path) -> Path:
    """通常の上限を越える長い原証拠パスをWindows APIへ明示する。"""
    path = path.resolve()
    value = str(path)
    if value.startswith('\\\\?\\'):
        return path
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


def display(path: Path) -> str:
    """利用者にはエクスプローラーで使う通常形式のパスを表示する。"""
    value = str(path)
    if value.startswith('\\\\?\\UNC\\'):
        return '\\\\' + value[8:]
    return value.removeprefix('\\\\?\\')


def identity(pid: int) -> Identity | None:
    """PIDを現在のプロセス世代に対応づけ、生存中のみ返す。"""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x101000, False, pid)
    if not handle:
        failure = ctypes.get_last_error()
        if failure in (87, 1168):
            return None
        raise ctypes.WinError(failure)
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status == 0:
            return None
        if status != 258:
            raise ctypes.WinError(ctypes.get_last_error())
        created, ended, cpu_kernel, cpu_user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, created, ended, cpu_kernel, cpu_user):
            raise ctypes.WinError(ctypes.get_last_error())
        stamp = created.dwHighDateTime << 32 | created.dwLowDateTime
        return {'pid': pid, 'created': stamp}
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def lease(path: Path) -> Iterator[None]:
    """OSがプロセスの終了時に解放する1バイトのロックを取る。"""
    with path.open('a+b') as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as failure:
            if failure.errno not in (13, 11, 36):
                raise
            raise Busy(
                f'前の処理が実行中です（{path.name}）。終了を待って状態を確認してください。'
            ) from failure
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def replace_json(path: Path, payload: object) -> None:
    """同じ場所の一時ファイルから可変の状態表示を原子的に更新する。"""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            newline='\n',
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded_json(payload))
            stream.flush()
            os.fsync(stream.fileno())
        for _ in range(10):
            try:
                temporary.replace(path)
            except PermissionError:
                # Windowsで読み手が共有削除を許可していない間だけ待つ。
                time.sleep(0.03)
            else:
                return
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def child(
    arguments: list[str], cwd: Path, log: Path, *, detached: bool = False
) -> subprocess.Popen[bytes]:
    """独立したコンソール群でプロセスを起動し、その出力を記録する。"""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    flags |= subprocess.DETACHED_PROCESS if detached else subprocess.CREATE_NO_WINDOW
    with log.open('xb') as stream:
        output: IO[bytes] = stream
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        return subprocess.Popen(
            arguments,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
        )
