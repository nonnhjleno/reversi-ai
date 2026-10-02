import pickle

'''
棋譜のデータ形式
data := game*   # 複数の棋譜がリスト形式で入っています
game := (moves, result)   # 1個の棋譜は(着手座標の一覧, 対戦結果)です
moves := (x, y)*   # 黒/白の着手座標が交互に入ったリストになっています(パスは(-1, -1))
result := INT   # 駒数の差分が入っています(黒が多いと正数、白が多いと負数となる)。
'''

INDEX = 2


def main():
    with open('102400-games.pkl', 'rb') as reader:
        data = pickle.load(reader)

    print('number of data: {}'.format(len(data)))
    print('game index: {}'.format(INDEX))
    moves = data[INDEX][0]
    result = data[INDEX][1]

    print('moves:')
    for i, move in enumerate(moves):
        print('{} {}'.format(('B', 'W')[i % 2], move))

    print('result: {}'.format(result))


if __name__ == '__main__':
    main()
