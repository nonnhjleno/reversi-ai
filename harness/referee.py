"""本体をimportしない、対戦検証専用のルール判定。"""

INITIAL_ROWS = (
    'EEEEEEEE', 'EEEEEEEE', 'EEEEEEEE', 'EEEWBEEE',
    'EEEBWEEE', 'EEEEEEEE', 'EEEEEEEE', 'EEEEEEEE',
)
DIRECTIONS = (
    (-1, -1), (0, -1), (1, -1), (-1, 0),
    (1, 0), (-1, 1), (0, 1), (1, 1),
)


def flipped_cells(rows, color, position):
    position_x, position_y = position
    if color not in ('B', 'W') or not (0 <= position_x < 8 and 0 <= position_y < 8):
        return []
    if rows[position_y][position_x] != 'E':
        return []
    opponent = 'W' if color == 'B' else 'B'
    captured = []
    for delta_x, delta_y in DIRECTIONS:
        current_x, current_y = position_x + delta_x, position_y + delta_y
        ray = []
        while 0 <= current_x < 8 and 0 <= current_y < 8:
            cell = rows[current_y][current_x]
            if cell == opponent:
                ray.append((current_x, current_y))
            else:
                if cell == color:
                    captured.extend(ray)
                break
            current_x += delta_x
            current_y += delta_y
    return captured


def legal_moves(rows, color):
    return [
        (position_x, position_y)
        for position_y in range(8) for position_x in range(8)
        if flipped_cells(rows, color, (position_x, position_y))
    ]


def apply_move(rows, color, position):
    captured = flipped_cells(rows, color, position)
    if not captured:
        raise ValueError('Illegal move: {} {}'.format(color, position))
    updated = [list(row) for row in rows]
    for position_x, position_y in [position] + captured:
        updated[position_y][position_x] = color
    return tuple(''.join(row) for row in updated)


def result(rows):
    if legal_moves(rows, 'B') or legal_moves(rows, 'W'):
        return 'NG'
    black_count = ''.join(rows).count('B')
    white_count = ''.join(rows).count('W')
    return 'B' if black_count > white_count else 'W' if white_count > black_count else 'D'
