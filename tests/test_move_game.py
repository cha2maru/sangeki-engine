"""位置の 1 日の読み合い（engine.route_calc.move_game、作業計画 A-3a）。"""
import random

from engine.resolve import resolve_actions, IllegalPlacement
from engine.route_calc import _dest, _minimax, move_game
from engine.scripts import by_id
from selfplay.script_routes import start_state


def _state():
    return start_state(by_id('generated/gen_s11_009'))  # C02 学校・C05 都市・C27 学校（学校の外は禁止）・C22 都市（都市の外は禁止）


def _route(*conds):
    return {'id': 'T', 'total': 1, 'conds': [{'kind': k, 'detail': d, 'deficit': 1} for k, d in conds]}


def test_dest_matches_resolve_actions():
    """行き先の計算が行動の解決（移動）と一致する（無作為の 200 通り）。"""
    s, rng = _state(), random.Random(3)
    chars = [c for c in s['chars'] if c != 'C34']  # 従者の付き添いは move_game で数えない
    n = 0
    while n < 200:
        ts = rng.sample(chars, 3)
        mm = [{'by': 'M', 'target': t, 'card': rng.choice(['MV_V', 'MV_H', 'MV_D', 'PAR+', 'INT1'])} for t in ts]
        pts = rng.sample(chars, 3)
        pc = [{'by': p, 'target': t, 'card': rng.choice(['MVX', 'MV_V', 'MV_H', 'GW1'])} for p, t in zip('ABC', pts)]
        try:
            after = resolve_actions(s, mm + pc)[0]
        except IllegalPlacement:
            continue
        n += 1
        for c in chars:
            m = next((x['card'] for x in mm if x['target'] == c), None)
            p = next((x['card'] for x in pc if x['target'] == c), None)
            assert _dest(s, c, m, p) == after['chars'][c]['area'], (c, m, p)


def test_minimax_matches_linprog():
    from scipy.optimize import linprog
    rng = random.Random(5)
    for _ in range(5):
        t = [[float(rng.random() < 0.5) for _ in range(6)] for _ in range(4)]
        # 最大化 v、各列 j で Σ x_i t_ij >= v、Σ x = 1
        res = linprog([0] * 4 + [-1], A_ub=[[-t[i][j] for i in range(4)] + [1] for j in range(6)], b_ub=[0] * 6,
                      A_eq=[[1] * 4 + [0]], b_eq=[1], bounds=[(0, 1)] * 4 + [(None, None)])
        assert abs(_minimax(t, iters=2000)[0] - (-res.fun)) < 0.03


def test_forbidden_area_is_zero():
    """学校の外が禁止の C27 を病院へ動かす筋は、どの手でも成り立たない（値 0、止めるのに札は要らない）。"""
    g = move_game(_state(), [_route(('in_area', ('C27', 'HOS')))])
    assert g and all(p == 0 and k == 0 for p, _, k in g.values())


def test_one_step_meeting_stop_card_vs_guess():
    """学校の C02 と都市の C05 を同じエリアにする（どちらかを 1 手）。
    置き先が C02 だけなら、主人公は C05 を学校へ動かす 1 枚で止まる（C02 は学校を出るので会えない。p=0、k=1）。
    置き先が 2 人で移動禁止が残っていれば 2 枚で止まる（p=0、k=2）。移動禁止を使い切ると、3 枚でも止めきれず読み合いになる（0<p<1、k=4）"""
    s = _state()
    r = [_route(('same_area', ('C02', 'C05')))]
    g = move_game(s, r)
    assert g[('C02',)][0] == 0 and g[('C02',)][2] == 1
    assert g[('C02', 'C05')][0] == 0 and g[('C02', 'C05')][2] == 2
    for q in 'ABC':
        s['used'][q] = list(s['used'][q]) + ['MVX']
    p, mix, k = move_game(s, r)[('C02', 'C05')]
    assert 0.2 < p < 0.4 and k == 4 and len(mix) >= 2
