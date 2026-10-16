"""試作（2026-10-14）: 負け筋の署名（勝てば必ず公開される観測）と漏れ、ループの並べ方。試合は模擬しない。plan/mastermind_algorithm_evolution_plan.md §2e。
    cd play && python -m selfplay.route_sig p006 p009 p001 p002 [--write]（--write で指針 guides/calc/<id>.json を書く）"""
import sys
import time
import itertools
import numpy as np
sys.path.insert(0, '.')
from engine.scripts import by_id, init_areas  # noqa: E402
from engine.routes import enumerate_routes  # noqa: E402
from engine.deduce import Deduction, ROLES  # noqa: E402
from selfplay.script_routes import start_state  # noqa: E402

DIST = 8  # 近い筋だけ（ponytail: 取れるかは距離で代用。forced_plan に替える）


def need(r, kind, c=None):
    for x in r['conds']:
        if x['kind'] == 'counter' and x['detail'][1] == kind and (c is None or x['detail'][0] == c):
            return x['detail'][0], x['detail'][2]
    return None


def signature(r, sc):
    """筋 r で勝ったときに公開される観測 [(kind, kw)]（役職の推理に効くものだけ。事件の犯人は別の推理なので入れない）。"""
    g, obs = r['goal'], []
    boards = {x['detail'][0]: x['detail'][1] for x in r['conds'] if x['kind'] == 'board'}
    ints = {x['detail'][0]: x['detail'][2] for x in r['conds'] if x['kind'] == 'counter' and x['detail'][1] == 'int'}
    if g == 'ループ終了時の敗北':
        obs.append(('loop_end_check', dict(loss=True, boards=boards, ints={c: ints.get(c, 0) for c in sc['init']}, dead=[], init=sc['init'])))
    elif '（フレンド）' in g:
        v = g.split()[0]
        obs += [('role_revealed', dict(char=v, role='FRIEND')),
                ('loop_end_check', dict(loss=True, boards=boards, ints={c: ints.get(c, 0) for c in sc['init']}, dead=[v], revealed=[v], init=sc['init']))]
    elif '（ラバーズ）' in g or '（メインラバーズ）' in g:  # 相方に不安6 → 主人公死亡へ続く
        v = g.split()[0]
        ml = next(c for c, ro in sc['roles'].items() if ro in ('LOVERS', 'MAIN_LOVERS') and c != v)
        obs.append(('lovers_par', dict(char=ml, dead=v)))
    elif g == '主人公死亡' and r['id'] in ('ML_PROT', 'KILLER_PROT'):
        i = need(r, 'int', r['via'])
        p = need(r, 'par', r['via'])
        obs.append(('prot_death_turn_end', dict(counters={r['via']: (i[1] if i else 0, p[1] if p else 0)})))
    elif 'キーパーソン' in g or r['id'] == 'KILLER_KEY':
        v = g.split()[0]  # ファクター（都市に暗躍2以上でキーパーソンの能力）の死亡もこの筋に入るので、そのときは city_int を渡す
        obs.append(('key_death', dict(char=v, city_int=2 if sc['roles'].get(v) == 'FACTOR' else 0)))
    if r['id'] == 'SK_KILL':  # 2人きりで死亡（ターン終了）
        v = g.split()[0]
        p = need(r, 'par', r['via'])
        obs.append(('turn_end_death', dict(victim=v, others=[r['via']], victim_int=ints.get(v, 0), par={r['via']: p[1] if p else 0})))
    if r['id'] == 'KILLER_KEY':
        v = g.split()[0]
        obs.append(('turn_end_death', dict(victim=v, others=[r['via']], victim_int=2, par={})))
    inc = next((x['detail'][1] for x in r['conds'] if x['kind'] == 'days' and x['detail'][0] == '事件'), None)
    if inc and r['via']:  # 事件で勝つ: 起きたことは公開され、犯人の候補が絞られる（役職の推理には効かない。犯人の推理 incident_candidates の代用）
        obs.append(('incident', dict(id=inc, culprit=r['via'])))
    return obs


def exposure(ded, obs, o):
    """これまでの観測 obs の下で、主人公がその筋を予想している度合い。
    役職の署名: その署名が起こりうる仮説の重みの割合。事件の署名: 同じ犯人で事件が起きたことがあれば 1（犯人が割れた）、なければ 1/人数。
    ponytail: 事件が起きたとき限界以上だったのは犯人だけとみなす（おとりの不安で候補を増やす型4は数えない）。両方あれば大きい方。"""
    inc = [kw for k, kw in o if k == 'incident']
    e_inc = max((1.0 if any(k == 'incident' and kw2 == kw for k, kw2 in obs) else 1 / len(ded.chars) for kw in inc), default=0.0)
    o = [(k, kw) for k, kw in o if k != 'incident']
    obs = [(k, kw) for k, kw in obs if k != 'incident']
    if not o:
        return e_inc
    return max(e_inc, _exposure_roles(ded, obs, o))


def _exposure_roles(ded, obs, o):
    R, C = ded._sync()
    keep = np.ones(len(C), bool)
    for k, kw in obs:
        keep &= ded._mask(R, C, k, kw)
    m = keep.copy()
    for k, kw in o:
        m &= ded._mask(R, C, k, kw)
    w = ded._w(C)
    return float(w[m].sum() / w[keep].sum()) if w[keep].sum() else 0.0


THETA = 0.5
BEAM = 5  # 主人公がこの割合以上で起こりうると見ている筋は「予想されている」＝止めに来る


def _calc(s, days, routes):
    """逆算（engine.route_calc）: 主人公が routes を知っていて最善に止めるとき、必ず取れるか（forced）と、伏せ札の賭けで取れる確率 p。"""
    from engine import route_calc as rc
    ab = rc._abstract(routes, s, days, positional=True, pos_mode=True)
    if ab is None:
        return False, 0.0
    T, need, _ = ab
    cur = [rc._current(s, x, k) for x, k in T]
    # ループ開始時は友好0なので、主人公が友好+2 を1回置けば届く [2] の能力までは使えるとみなす
    # ponytail: 初日から使える扱い（主人公に甘い）。[3] 以上は数えない。友好を積む日数を逆算に入れるなら route_calc 側に
    from engine.resolve import copy_state
    s2 = copy_state(s)
    for ch in s2['chars'].values():
        ch['gw'] = max(ch['gw'], 2)
    pa = rc._pc_abilities(s2, T)  # 主人公の友好能力（ナース[2]・学生[2] の不安除去など、拒否されないもの）を止め手に数える
    ok, _ = rc.solve(T, cur, need, s['day'], days, (True, True, True), True, pcab=pa)
    if ok:
        return True, 1.0
    p, _ = rc.solve_hidden(T, cur, need, s['day'], days, (True, True, True), True, pcab=pa)
    return False, p


def winnable(s, sc, ded, obs, sigs):
    """このループで取れる筋 [(筋, 署名, 取り方)]。これまでの観測 obs の下で、
    - 予想されていない筋（起こりうる割合 < THETA）: 主人公は止めに来ないので、近ければ取れる（ponytail: 距離 DIST で代用）
    - 予想されている筋: 主人公はそれらを全部知って止める。予想されている筋の組を逆算し、必ず取れる（または賭けで p>=0.5）なら、その中の筋で取れる。"""
    ex = {id(r): exposure(ded, obs, o) for r, o in sigs}
    known = [r for r, o in sigs if ex[id(r)] >= THETA]
    out = [(r, o, f"予想{ex[id(r)]:.2f}") for r, o in sigs if ex[id(r)] < THETA and r['total'] <= DIST]
    if known:
        ok, p = _calc(s, sc['days'], known)
        if ok or p >= 0.5:
            out += [(r, o, f"予想{ex[id(r)]:.2f}・逆算{'必勝' if ok else f'賭け{p:.2f}'}") for r, o in sigs if ex[id(r)] >= THETA]
    return out


def report(ded, obs_all):
    obs_all = [(k, kw) for k, kw in obs_all if k != 'incident']
    R, C = ded._sync()
    keep = np.ones(len(C), bool)
    for k, kw in obs_all:
        keep &= ded._mask(R, C, k, kw)
    R2, C2 = R[keep], C[keep]
    ent = ded.role_entropy(R2, C2) if len(C2) else 0.0
    M, tot = ded._marg(R2, C2)
    sure = {c: ROLES[int(np.argmax(M[j]))] for j, c in enumerate(ded.chars) if tot and M[j].max() / tot >= 0.9 and ROLES[int(np.argmax(M[j]))] != 'PERSON'}
    ys = sorted({ded.T.combos[i][0] for i in set(C2.tolist())}) if len(C2) else []
    return ent, sure, ys


def main(sid, loops_override=None):
    sc = by_id(sid if '/' in sid else 'posted/' + sid)
    s = start_state(sc)
    t = time.time()
    ded = Deduction(list(s['chars']), set_id='BTX')
    base = ded.role_entropy()
    # 神格があとのループで登場する脚本: 登場ループからの盤面の筋も数え、ループごとに取れる筋を分ける（ユーザー 2026-10-16 水平展開）
    lp = sc.get('appear', {}).get('C13', {}).get('loop', 1)
    key = lambda r: (r['id'], r['via'], r['goal'])  # noqa: E731
    rs = [r for r in enumerate_routes(s) if r['total'] <= DIST]
    avail = {1: {key(r) for r in rs}}
    if 1 < lp <= (loops_override or sc['loops']):
        r2 = [r for r in enumerate_routes(start_state(sc, lp)) if r['total'] <= DIST]
        avail[lp] = {key(r) for r in r2}
        rs += [r for r in r2 if key(r) not in avail[1]]
        avail[lp] |= avail[1]  # 登場ループからは両方
    print(f"== {sid} {sc['title']}  仮説 {ded.n_hyp()}  エントロピー {base:.1f}  近い筋 {len(rs)}  ({time.time() - t:.1f}s)")
    sigs = []
    for r in rs:
        o = signature(r, sc)
        ent, sure, ys = report(ded, o)
        if o:  # 署名を組み立てられない筋（未対応の負け方）は外す
            sigs.append((r, o))
        print(f"  {r['total']:5}  {r['id']:12} {str(r['via']):5} {r['goal'][:16]:16} 漏れ {base - ent:5.1f}  割れる {sure}  Y候補 {ys}")
    # ループの並べ方: 各ループで取れる筋（距離で代用）を1本ずつ、累積の漏れが最小になる列（総当たり、筋が多ければビーム）
    L = loops_override or sc['loops']
    # 比べ方は優先順: 取りきれるループの数 → 必勝の筋を後に残す → 漏れの少なさ。行き止まり（次のループで取れる筋が無い）の並べ方も残し、最後に比べる
    beam, done = [([], [])], []
    for _ in range(L):
        nxt = []
        for seq, obs in beam:
            li = len(seq) + 1  # このループの番号
            ok_keys = avail[max(k for k in avail if k <= li)]
            opts = [x for x in winnable(s, sc, ded, obs, sigs) if key(x[0]) in ok_keys]
            if not opts:
                done.append((seq, obs))
            for r, o, how in opts:
                ent, sure, ys = report(ded, obs + o)
                if not ys:  # 推理が矛盾する（仮説0）並べ方は捨てる
                    continue
                nxt.append((-ent, seq + [(r, how)], obs + o))
        nxt.sort(key=lambda x: x[0])
        beam = [(x[1], x[2]) for x in nxt[:BEAM]]
        if not beam:
            break
    done += beam
    # 主人公が知っていても必勝の筋（予想されても止まらない）は、後のループの控えに残す（作者の p006: 止まらない女子学生の自殺を最後に）
    sure = {id(r) for r, o in sigs if _calc(s, sc['days'], [r])[0]}

    def reserve(seq):  # 必勝の筋を最初に使ったループ（遅いほどよい。使わなければループ数）
        return next((i for i, (r, _) in enumerate(seq) if id(r) in sure), L)
    # 同点なら、近い（取りやすい）筋を先のループで使う並べ方（主人公の推理が浅いうちに取りやすい筋で取る）
    # 漏れはループごとの残りエントロピーの合計で比べる（早く漏らすほど、後のループすべてで主人公の助けになる）
    def kept(seq):
        obs, tot = [], 0.0
        for r, _ in seq:
            obs = obs + next(o for r2, o in sigs if r2 is r)
            tot += report(ded, obs)[0]
        return tot
    seq, obs = min(done, key=lambda x: (-len(x[0]), -reserve(x[0]), -kept(x[0]), [r['total'] for r, _ in x[0]]))
    ent, sure, ys = report(ded, obs)
    print(f'  並べ方（{len(seq)}/{L} ループ）:', ' → '.join(f"{r['id']}({r['via']},{how})" for r, how in seq), f"  残りエントロピー {ent:.1f}/{base:.1f}  割れる {sure}  Y候補 {ys}")
    print(f"  計算 {time.time() - t:.1f}s")
    return sc, seq


W_PLAN = 1.5  # 並べ方でそのループに選んだ筋の重み（指針の routes の w。ほかの筋は 1）


def write_guide(sc, seq):
    """並べ方を指針（engine/scripts/guides/calc/<id>.json）に書く。league の calcGBg が読む。
    ponytail: 脚本の開始時に一度だけ計算した並べ方。ループの開始時に割れた情報から立て直すのは、脚本家の中で route_sig を呼ぶようにしてから"""
    import json
    import os
    g = {'script': sc['id'], 'generated': 'selfplay.route_sig',
         'routes': [{'id': r['id'], 'via': r['via'], 'loop': [i + 1], 'w': W_PLAN, 'note': f"ループ{i + 1}で取る筋（{how}）"} for i, (r, how) in enumerate(seq)]}
    d = os.path.join(os.path.dirname(__file__), '..', 'engine', 'scripts', 'guides', 'calc')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, sc['id'] + '.json'), 'w', encoding='utf-8') as f:
        json.dump(g, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--write']
    for a in args:
        sc, seq = main(a)
        if '--write' in sys.argv:
            write_guide(sc, seq)
