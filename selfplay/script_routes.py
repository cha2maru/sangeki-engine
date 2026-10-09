"""脚本の勝ち筋の多さ（ユーザーの感想「脚本が弱かった。勝ち筋が少ない」）。
ループ1の開始時点の盤面で、脚本家の負け筋（routes.enumerate_routes）を数える:
  n_near: 距離 max_dist 以下の筋の数 / n_via: 要の人物・ボード（via）の種類の数 / spread: via ごとの最も近い筋の Σ 1/(1+距離)。

    cd play && python -m selfplay.script_routes            # 生成脚本の一覧（via の少ない順）
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import phases as ph  # noqa: E402
from engine.routes import enumerate_routes  # noqa: E402
from engine.scripts import by_id  # noqa: E402


def start_state(script):
    s = {'loop': 0, 'day': 0, 'leader': 'A',
         'script': {**{k: script[k] for k in ('rules', 'roles', 'incidents')}, 'days': script['days'], 'appear': script.get('appear', {}),
                    'territory': script.get('territory'), 'start': script.get('start', {})},
         'chars': {c: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0} for c, a in script['init'].items()},
         'boards': {a: 0 for a in ph.AREAS}, 'used': {p: [] for p in 'MABC'}, 'ability_used_loop': [], 'incident_log': []}
    import random
    from selfplay.players import RouteMastermind
    setup = RouteMastermind(random.Random(0)).loop_setup(s, script)  # 手先の初期エリア・学者のカウンターは筋の評価が高い組
    s, _ = ph.loop_start(s, script['init'], setup)
    s['day'] = 1
    return s


def route_richness(script, max_dist=6):
    # 事件の日までの待ち日数（条件 'days'）は主人公が止められる手数ではないので、距離から外す
    # （遅い日の事件・未来改変プランの脚本を低く見ていた。設計のサブエージェントの指摘）
    rs = []
    for r in enumerate_routes(start_state(script)):
        d = r['total'] - sum(min(c['deficit'], 99) for c in r['conds'] if c['kind'] == 'days')
        if d <= max_dist:
            rs.append(dict(r, total=d))
    best = {}  # 要の人物（via）ごとの最も近い筋。別々の要に分かれた脅威の厚み＝Σ 1/(1+距離)
    for r in rs:
        best[r['via']] = min(best.get(r['via'], 99), r['total'])
    return {'n_near': len(rs), 'n_via': len(best), 'spread': round(sum(1 / (1 + d) for d in best.values()), 2),
            'kinds': sorted({r['id'] for r in rs})}


if __name__ == '__main__':
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    from engine.scripts import glob_scripts
    for f in glob_scripts('generated/*.json'):
        sc = by_id('generated/' + os.path.basename(f)[:-5])  # init などを補った形で読む
        try:
            rows.append((route_richness(sc), sc['id']))
        except Exception as e:  # 生成脚本の一部はループの準備に選択が要る
            rows.append(({'n_near': -1, 'n_via': -1, 'spread': -1, 'kinds': [repr(e)[:40]]}, sc['id']))
    for r, sid in sorted(rows, key=lambda x: x[0]['spread']):
        print(f"{sid:14} spread={r['spread']:5} via={r['n_via']:3} near={r['n_near']:3} {' '.join(r['kinds'])}")
