"""対応のある比較（plan「主人公の改良の比較」）。同じ seed・同じ試合番号で回した2つの対戦表を、試合ごとに突き合わせる。
乱数は陣営ごと・試合ごとに分けてあるので、改良していない側の手は、改良した側の手が変わるまでは同じになる。

    cd play && python -m selfplay.paired selfplay/runs/league_A.json selfplay/runs/league_B.json [組]
    cd play && python -m selfplay.paired --within selfplay/runs/league_X.json 組A 組B   （同じ対戦表の2組を比べる）
    cd play && python -m selfplay.paired --cross selfplay/runs/league_X.json selfplay/runs/league_Y.json 組A 組B

出力: 組ごとに、主人公の勝ち（A だけ・B だけ・両方）と符号検定の p 値（両側）、確定数の差の平均。
"""
import json
import math
import sys


def sign_test(a, b):
    n = a + b
    if n == 0:
        return 1.0
    k = min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def compare(pa, pb, pairs=None, within=None):
    A, B = json.load(open(pa))['pairs'], json.load(open(pb))['pairs']
    todo = [(within[0], within[1])] if within else [(p, p) for p in (pairs or sorted(set(A) & set(B)))]
    for pair_a, pair_b in todo:
        pair = pair_a if pair_a == pair_b else f'{pair_a}→{pair_b}'
        ga = {g['game']: g for g in A[pair_a]['games']}
        gb = {g['game']: g for g in B[pair_b]['games']}
        common = sorted(set(ga) & set(gb))
        win = lambda g: g['result']['winner'] == 'protagonists'  # noqa: E731
        only_a = sum(win(ga[i]) and not win(gb[i]) for i in common)
        only_b = sum(win(gb[i]) and not win(ga[i]) for i in common)
        both = sum(win(ga[i]) and win(gb[i]) for i in common)
        conf = lambda g: (g['truth'].get('final') or g['truth'][max(g['truth'])])['confirmed']  # noqa: E731
        dconf = sum(conf(gb[i]) - conf(ga[i]) for i in common) / max(1, len(common))
        print(f'{pair:34} n={len(common)} 両方={both} Aだけ={only_a} Bだけ={only_b} p={sign_test(only_a, only_b):.3f} 確定数の差(B−A)={dconf:+.2f}')


if __name__ == '__main__':
    if sys.argv[1] == '--cross':  # 同じ seed の別の対戦表の組どうし: --cross A.json B.json 組A 組B
        compare(sys.argv[2], sys.argv[3], within=sys.argv[4:6])
    elif sys.argv[1] == '--within':
        compare(sys.argv[2], sys.argv[2], within=sys.argv[3:5])
    else:
        compare(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
