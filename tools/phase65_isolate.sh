#!/bin/bash
# WSL2上で授業データ用デコーダを実行し、stdoutだけを親に渡す。
set -euo pipefail
input="$1"
decoder="$2"
expected="$3"
jail="$(mktemp -d -p /tmp ranger65.XXXXXXXX)"
trap 'test -n "$jail" && rm -rf -- "$jail"' EXIT
mkdir "$jail/usr"
chmod 0555 "$jail"
ln -s usr/lib "$jail/lib"
ln -s usr/lib64 "$jail/lib64"
cp -- "$decoder" "$jail/decode.py"
if [ "$input" != '--probe' ]; then
    cp -- "$input" "$jail/input.pickle"
fi
cat > "$jail/drop.py" <<'PY'
import ctypes
import errno
import os
import sys

libc = ctypes.CDLL(None, use_errno=True)

def prctl(option, argument=0):
    result = libc.prctl(option, argument, 0, 0, 0)
    if result != 0:
        raise OSError(ctypes.get_errno(), 'prctl')
    return result

prctl(38, 1)  # PR_SET_NO_NEW_PRIVS
prctl(47, 4)  # PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL
for cap in range(64):
    try:
        prctl(24, cap)  # PR_CAPBSET_DROP
    except OSError as error:
        if error.errno != errno.EINVAL:
            raise
os.setgroups([])
os.setgid(65534)
os.setuid(65534)
os.execve('/usr/bin/python3', ['python3', '-I', '-B', *sys.argv[1:]], {})
PY
cat > "$jail/probe.py" <<'PY'
import ctypes
import errno
import json
import os
import socket
from pathlib import Path

status = {
    'only_root_entries': sorted(os.listdir('/')),
    'host_paths_hidden': all(not Path(p).exists() for p in ('/mnt', '/proc', '/init', '/home')),
    'expected_pid': os.getpid(),
}
assert status['host_paths_hidden']
assert os.getuid() == os.geteuid() == 65534 and not os.getgroups()
status['uid'] = os.getuid()
libc = ctypes.CDLL(None, use_errno=True)
assert libc.prctl(39, 0, 0, 0, 0) == 1  # PR_GET_NO_NEW_PRIVS
header = (ctypes.c_uint * 2)(0x20080522, 0)
caps = (ctypes.c_uint * 6)()
assert libc.capget(header, caps) == 0 and not any(caps)
status['no_new_privileges'] = True
status['capability_words'] = list(caps)
for path in ('/sentinel', '/usr/sentinel-ranger-phase65'):
    try:
        with open(path, 'xb'):
            raise AssertionError('unexpected_write')
    except OSError as e:
        assert e.errno == errno.EROFS
        status[path] = e.errno
with socket.socket() as connection:
    try:
        connection.settimeout(1)
        connection.connect(('192.0.2.1', 9))
        raise AssertionError('unexpected_network')
    except OSError as e:
        assert e.errno == errno.ENETUNREACH
        status['network_errno'] = e.errno
print(json.dumps(status), file=__import__('sys').stderr)
PY
before="$(readlink /proc/self/ns/mnt) $(readlink /proc/self/ns/net) $(readlink /proc/self/ns/pid)"
unshare --mount --net --pid --fork --kill-child --ipc --uts \
    /bin/bash -s -- "$jail" "$before" "$input" "$expected" <<'SH'
set -euo pipefail
jail="$1"
read -r host_mount host_net host_pid <<< "$2"
test "$(readlink /proc/self/ns/mnt)" != "$host_mount"
test "$(readlink /proc/self/ns/net)" != "$host_net"
test "$(readlink /proc/self/ns/pid)" != "$host_pid"
mount --bind "$jail" "$jail"
mount --bind /usr "$jail/usr"
mount -o remount,bind,ro "$jail/usr"
mount -o remount,bind,ro "$jail"
ulimit -v 900000
ulimit -t 210
runner=(env -i PATH=/usr/bin:/usr/sbin LANG=C.UTF-8 /usr/sbin/chroot "$jail"
        /usr/bin/python3 -I -B /drop.py)
"${runner[@]}" /probe.py
if [ "$3" != '--probe' ]; then
    exec "${runner[@]}" /decode.py /input.pickle "$4"
fi
SH
