"""学習用の特徴量（plan「特徴量の見直し（1回目の通し対戦のあと）」の1〜5）。

盤面（完全な状態）から、脚本家の手の良し悪しを学ぶための数値をまとめる。勝ち筋は routes.enumerate_routes。
6（主人公の知識＝推理の絞り込み）は、推理する主人公ができてから足す。
"""
from .phases import base_role, has_ability
from .resolve import ONCE
from .routes import INF, enumerate_routes

ROUTE_IDS = ('KILLER_KEY', 'KILLER_PROT', 'SK_KILL', 'MURDER', 'SUICIDE', 'REMOTE', 'HOSPITAL_PROT', 'HOSPITAL_KILL',
             'Y_SEAL', 'Y_CONTRACT', 'Y_FUTURE', 'Y_BOMB', 'FRIEND', 'ML_PROT', 'TT_LOSS')


def _block_cards(route):
    """次の1日でこの筋を止めるのに主人公が要る札の目安。
    移動が要る条件（同じエリア・2人きり・そのエリアへ）→移動禁止か逆方向の移動で1枚、カウンターの条件→暗躍禁止・不安−1などで1枚。
    距離0の筋（すでに成立）は札では止められないので INF。"""
    if route['total'] == 0:
        return INF
    return sum(1 for c in route['conds'] if c['deficit'] > 0 and c['kind'] in ('same_area', 'alone_with', 'in_area', 'counter', 'board'))


def state_features(state, days_in_loop=8, loops=3):
    rs = enumerate_routes(state)
    f = {f'dist_{r}': INF for r in ROUTE_IDS}
    for r in rs:
        f[f"dist_{r['id']}"] = min(f.get(f"dist_{r['id']}", INF), r['total'])
    f['dist_min'] = min([r['total'] for r in rs], default=INF)
    # 1. 筋の本数（距離の分布）
    for d in (0, 1, 2):
        f[f'routes_le{d}'] = sum(r['total'] <= d for r in rs)
    # 2. 妨害の費用と主人公の残りの資源
    near = [r for r in rs if 1 <= r['total'] <= 2]
    f['block_need_near'] = sum(_block_cards(r) for r in near if r['total'] == 1)
    f['block_overload'] = max(0, f['block_need_near'] - 3)  # 主人公は1日3枚
    for card in ('MVX', 'PAR-', 'GW2'):
        f[f'pc_left_{card}'] = sum(card not in state['used'][p] for p in 'ABC')
    for card in ('MV_D', 'INT2'):
        f[f'mm_left_{card}'] = int(card not in state['used']['M'])
    f['cultist_alive'] = int(any(base_role(state, c) == 'CULTIST' and v['alive'] for c, v in state['chars'].items()))
    # 4. 時間
    f['days_left'] = days_in_loop - state['day']
    f['loops_left'] = loops - state['loop']
    nxt = [i['day'] - state['day'] for i in state['script']['incidents'] if i['day'] >= state['day']]
    f['next_incident_in'] = min(nxt, default=INF)
    # 5. カウンターの余裕
    keys = [c for c in state['chars'] if has_ability(state, c, 'KEY')]
    killers = [c for c in state['chars'] if base_role(state, c) == 'KILLER']
    f['key_int'] = max([state['chars'][k]['int'] for k in keys], default=0)
    f['killer_int'] = max([state['chars'][k]['int'] for k in killers], default=0)
    f['sk_candidates'] = sum(1 for c, v in state['chars'].items() if v['alive'] and v.get('present', True) and base_role(state, c) == 'PERSON'
                             and v['par'] >= 2 and any(state['chars'][k]['area'] == v['area'] for k in keys))
    return f


def card_relevance(state, target):
    """3. 伏せ札の危険度: この対象が関わる筋の最小距離（関わらなければ INF＝見せ札の可能性が高い）。"""
    best = INF
    for r in enumerate_routes(state):
        involved = {r['via']} | {x for c in r['conds'] for x in c['detail'] if isinstance(x, str) and x.startswith('C')}
        if target in involved or (target.startswith('B:') and any(c['kind'] == 'board' and c['detail'][0] == target[2:] for c in r['conds'])):
            best = min(best, r['total'])
    return best
