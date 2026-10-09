import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path


class HarnessError(RuntimeError):
    def __init__(self, kind, message, command=None):
        super().__init__(message)
        self.kind = kind
        self.command = command
        self.player = None
        self.diagnostics = None


class Player:
    def __init__(self, script, seed=None, startup_timeout=5.0,
                 command_timeout=1.0, genmove_timeout=10.0):
        self.script = Path(script).resolve()
        self.command_timeout = command_timeout
        self.genmove_timeout = genmove_timeout
        self.commands = []
        self.stderr_tail = deque(maxlen=100)
        self._output = queue.Queue(maxsize=512)
        self._overflow = threading.Event()
        self._closed = False
        environment = os.environ.copy()
        environment.pop('PYTHONUNBUFFERED', None)
        environment['PYTHONIOENCODING'] = 'utf-8'
        command = [sys.executable, str(self.script)]
        if seed is not None:
            environment['PYTHONHASHSEED'] = str(seed % (2 ** 32))
            command = [sys.executable, str(Path(__file__).with_name('seeded_player.py')),
                       str(self.script), str(seed)]
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {
            'start_new_session': True,
        }
        started = time.perf_counter()
        try:
            self.process = subprocess.Popen(
                command, cwd=str(self.script.parent), env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', bufsize=1, **options,
            )
        except OSError as error:
            raise HarnessError('abnormal_exit', str(error)) from error
        self._readers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()
        try:
            remaining = startup_timeout - (time.perf_counter() - started)
            self.name = self._exchange('name', max(remaining, 0.0), startup=True)[0]
            self.startup_seconds = time.perf_counter() - started
        except BaseException as error:
            self.abort()
            if isinstance(error, HarnessError):
                error.diagnostics = {
                    'exit_code': self.process.returncode,
                    'stderr_tail': list(self.stderr_tail), 'commands': self.commands,
                }
            raise

    def _read_stdout(self):
        try:
            while True:
                line = self.process.stdout.readline(4097)
                try:
                    self._output.put_nowait(line if line else None)
                except queue.Full:
                    self._overflow.set()
                    return
                if not line:
                    return
        except (OSError, ValueError):
            return

    def _read_stderr(self):
        try:
            while True:
                line = self.process.stderr.readline(4097)
                if not line:
                    return
                self.stderr_tail.append(line.rstrip('\r\n'))
        except (OSError, ValueError):
            return

    def _exchange(self, command, timeout, startup=False):
        started = time.perf_counter()
        record = {'command': command, 'startup_probe': startup}
        self.commands.append(record)
        try:
            if self._overflow.is_set() or not self._output.empty():
                raise HarnessError('protocol_error', 'Unsolicited stdout before command', command)
            if self.process.poll() is not None:
                raise HarnessError('abnormal_exit', 'Player exited before command', command)
            try:
                self.process.stdin.write(command + '\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise HarnessError('abnormal_exit', str(error), command) from error
            lines = []
            for _ in range(8 if command == 'showboard' else 1):
                remaining = timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    raise HarnessError('timeout', 'Response deadline exceeded', command)
                try:
                    line = self._output.get(timeout=remaining)
                except queue.Empty as error:
                    raise HarnessError('timeout', 'Response deadline exceeded', command) from error
                if line is None:
                    raise HarnessError('abnormal_exit', 'EOF before full response', command)
                if not line.endswith('\n'):
                    raise HarnessError('protocol_error', 'Missing newline or excessive line length', command)
                lines.append(line.rstrip('\r\n'))
            if self._overflow.is_set():
                raise HarnessError('protocol_error', 'Excessive stdout', command)
            self._validate(command, lines)
            if time.perf_counter() - started > timeout:
                raise HarnessError('timeout', 'Response deadline exceeded', command)
            record['response'] = lines
            return lines
        except HarnessError as error:
            record['error'] = error.kind
            record['seconds'] = time.perf_counter() - started
            self.abort()
            raise
        finally:
            record.setdefault('seconds', time.perf_counter() - started)

    def _validate(self, command, lines):
        operation = command.split()[0]
        valid = False
        if operation == 'name':
            valid = bool(lines[0].strip())
        elif operation in ('ask', 'play'):
            valid = lines[0] in ('OK', 'NG')
        elif operation == 'genmove':
            valid = lines[0] == 'NG' or re.fullmatch(r'[0-7] [0-7]', lines[0]) is not None
        elif operation == 'showboard':
            valid = len(lines) == 8 and all(re.fullmatch(r'[BWE]{8}', row) for row in lines)
        elif operation == 'result':
            valid = lines[0] in ('B', 'W', 'D', 'NG')
        if not valid:
            raise HarnessError('protocol_error', 'Invalid response: {!r}'.format(lines), command)

    def command(self, command):
        if '\n' in command or '\r' in command or not command.strip():
            raise ValueError('Exactly one nonempty command is required')
        timeout = self.genmove_timeout if command.split()[0] == 'genmove' else self.command_timeout
        return self._exchange(command, timeout)

    def finish(self):
        if self._closed:
            return
        started = time.perf_counter()
        record = {'command': 'quit', 'startup_probe': False}
        self.commands.append(record)
        try:
            if self.process.poll() is not None:
                raise HarnessError('abnormal_exit', 'Player exited before quit', 'quit')
            self.process.stdin.write('quit\n')
            self.process.stdin.flush()
            remaining = self.command_timeout - (time.perf_counter() - started)
            self.process.wait(timeout=max(remaining, 0.0))
            record['seconds'] = time.perf_counter() - started
            if record['seconds'] > self.command_timeout:
                raise HarnessError('timeout', 'quit deadline exceeded', 'quit')
            for reader in self._readers:
                reader.join(timeout=0.5)
            if self.process.returncode != 0:
                raise HarnessError('abnormal_exit', 'Nonzero exit: {}'.format(self.process.returncode), 'quit')
            extra = []
            while not self._output.empty():
                line = self._output.get_nowait()
                if line is not None:
                    extra.append(line)
            if extra or self._overflow.is_set():
                raise HarnessError('protocol_error', 'Extra stdout: {!r}'.format(extra), 'quit')
            record['response'] = []
        except subprocess.TimeoutExpired as error:
            record['error'] = 'timeout'
            raise HarnessError('timeout', 'quit deadline exceeded', 'quit') from error
        except (BrokenPipeError, OSError) as error:
            record['error'] = 'abnormal_exit'
            raise HarnessError('abnormal_exit', str(error), 'quit') from error
        except HarnessError as error:
            record['error'] = error.kind
            raise
        finally:
            record.setdefault('seconds', time.perf_counter() - started)
            self.abort()

    def abort(self):
        if self._closed:
            return
        if self.process.poll() is None:
            if os.name == 'nt':
                try:
                    subprocess.run(
                        ['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                        capture_output=True, timeout=3,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=3)
        for reader in self._readers:
            reader.join(timeout=1)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        if exception_type is None:
            self.finish()
        else:
            self.abort()
