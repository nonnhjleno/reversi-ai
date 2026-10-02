import http.server
import json
import os
import subprocess

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'web')
SCRIPT = os.path.join(os.path.dirname(__file__), 'reversi.py')


class Process(object):

    def __init__(self):
        self.proc = subprocess.Popen(
            ['python', SCRIPT], stdin=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True)

    def command(self, command, lines=1):
        print('[ in] {}'.format(command))

        self.proc.stdin.write('{}\n'.format(command))
        self.proc.stdin.flush()
        response = []

        for _ in range(lines):
            res = self.proc.stdout.readline().strip()
            print('[out] {}'.format(res))
            response.append(res)

        return '\n'.join(response)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.proc.stdin.write('quit\n')
        self.proc.stdin.flush()

        try:
            self.proc.wait(timeout=5)
        except BaseException:
            self.proc.kill()


class RequestHandler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, request, client_address, server):
        super().__init__(request, client_address, server, directory=STATIC_DIR)

    def do_POST(self):
        if self.path == '/play':
            def proc(r):
                return self._play(r)
        elif self.path == '/genmove':
            def proc(r):
                return self._genmove(r)
        else:
            return super().do_GET()

        try:
            size = int(self.headers.get('content-length'))
            req = json.loads(self.rfile.read(size).decode('utf-8'))
            res = json.dumps(proc(req))

            self.send_response(200)
            self.send_header('Content-Length', str(len(res)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(res.encode('utf-8'))
        except Exception as ex:
            self.send_response(500)
            self.end_headers()
            print(ex)

    def _play(self, req):
        with Process() as proc:
            self._make_board(proc, req)

            return self._make_response(proc, req)

    def _genmove(self, req):
        with Process() as proc:
            self._make_board(proc, req)

            pos = proc.command(
                'genmove {}'.format(self._next_color(len(req['positions']))))

            if pos == 'NG':
                req['positions'].append([-1, -1])
            else:
                req['positions'].append(list(map(int, pos.split())))

            return self._make_response(proc, req)

    def _next_color(self, index):
        return ('B', 'W')[index % 2]

    def _make_board(self, proc, req):
        for i, (x, y) in enumerate(req['positions']):
            if x != -1 and y != -1:
                proc.command('play {} {} {}'.format(self._next_color(i), x, y))

    def _make_response(self, proc, req):
        board = proc.command('showboard', lines=8).split('\n')
        board = [
            [1 if board[y][x] == 'B' else -1 if board[y][x] == 'W' else 0
             for x in range(8)] for y in range(8)]
        color = self._next_color(len(req['positions']))
        valid = [
            [int(proc.command('ask {} {} {}'.format(color, x, y)) == 'OK')
             for x in range(8)] for y in range(8)]
        result = proc.command('result')
        print('result', result)
        score = 1 if result == 'B' else -1 if result == 'W' else 0
        end = False if result == 'NG' else True

        return {
            'positions': req['positions'],
            'board': board,
            'valid': valid,
            'end': end,
            'score': score}


def main():
    print('start web server - http://localhost:8888/', flush=True)
    server = http.server.HTTPServer(('', 8888), RequestHandler)
    server.serve_forever()


if __name__ == '__main__':
    main()
