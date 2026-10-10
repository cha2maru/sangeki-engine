"""負け筋の逆算（plan/route-calc.md の v2）: 抽象化した小さな盤面で、そのループを脚本家が必ず取れるかを後ろ向きに計算する。

v2.0 の範囲（下限＝「取れる」と出たら、主人公がどう止めても取れる）:
- 対象: 負け筋の条件に出るカウンター（人物の不安・暗躍、ボードの暗躍）。位置の条件は「今満たしていれば満たしたまま」、満たしていない筋は数えない。
- 脚本家の札（wiki/cards/mastermind-hand.md）: 不安+1 は2枚、暗躍+1 は1枚、暗躍+2 はループに1回、不安禁止（重なった不安±を無効化）。1日3枚、同じ対象に1枚。
- 主人公の札（wiki/cards/protagonist-hand.md）: 不安−1 は各主人公ループに1回、暗躍禁止は1日に1か所だけ効く（2枚出すと両方無効）。各主人公1日1枚、同じ対象に2人は置けない。
  主人公は伏せ札の中身を知っているものとして最善に応じる（下限のため）。
- 数えないもの（どちらに働くか）: 脚本家の能力・補給の事件（脚本家の味方を数えない＝下限のまま）、主人公の能力・移動（主人公の味方を数えない＝下限が崩れる。
  ユーザーの方針で能力は手を選ぶときに補正。移動は位置の条件が今満たされている筋で崩されうる → 呼び出し側で灰色に扱う）。
- 判定の時: 役職のターン終了の筋（メインラバーズ等）は毎日の行動解決の後、事件の筋は事件の日、ループ終了時の筋は最終日。

    from engine.route_calc import forced_plan
    r = forced_plan(state, days)   # {'forced': bool, 'move': [(対象, 札), ...] or None, 'routes': [...]}
"""
from functools import lru_cache
from itertools import combinations

from .routes import EFFECTS, INF, enumerate_routes

MAX_TARGETS = 5
MAX_ROUTES = 8


def _raw(c):
    return c.get('supply', {}).get('before', c['deficit'])


def _route_spec(r, state, days, positional=True):
    """筋を (判定の日, [(対象, 種類, 必要量)]) に直す。扱えない筋は None。positional=False なら位置の条件を含む筋も None。"""
    when, need = None, []
    for c in r['conds']:
        k = c['kind']
        if c.get('fixed'):
            return None  # 事件でボードに置く分を差し引いた筋（v2.0 では数えない）
        if k == 'days':
            if c['detail'][0] == '事件':
                when = c['detail'][2]
            else:
                return None
        elif k == 'counter' and c['detail'][1] in ('par', 'int'):
            ch = state['chars'].get(c['detail'][0])
            if ch is None or not ch.get('present', True):
                # ponytail: 登場前（転校生など）には札を置けない（d23 で反則の置き方になった）。登場後に押せる分は数えない＝下限のまま
                return None
            need.append((c['detail'][0], c['detail'][1], c['detail'][2]))
        elif k == 'board':
            need.append(('B:' + c['detail'][0], 'int', c['detail'][1]))
        elif k in ('same_area', 'alone_with', 'in_area'):
            if _raw(c) > 0 or not positional:
                return None  # 位置がまだ揃っていない筋は数えない（下限）
        else:
            return None  # incident_par・counter_max など v2.0 では扱わない
    if r['id'] in EFFECTS['rules'] and when is None:
        when = days  # ループ終了時
    if when is None:
        when = 'turn_end'  # 役職の能力（メインラバーズ・キラーなど）は毎日
    if when != 'turn_end' and when < state['day']:
        return None
    return when, need


def _current(state, t, kind):
    if t.startswith('B:'):
        return state['boards'][t[2:]]
    return state['chars'][t][kind]


def _abstract(rs, state, days, positional=True):
    """筋を抽象化した盤面に直す: (対象 [(対象, 種類)], 筋 [(判定の日, ((対象の番号, 必要量), ...))], [(筋, spec)]) or None。"""
    specs = []
    for r in sorted(rs, key=lambda r: r['total']):
        if r['total'] >= INF:
            continue
        sp = _route_spec(r, state, days, positional)
        if sp is not None:
            specs.append((r, sp))
        if len(specs) >= MAX_ROUTES:
            break
    targets = []
    for _, (_, need) in specs:
        for t, kind, _ in need:
            if (t, kind) not in targets:
                targets.append((t, kind))
    targets = targets[:MAX_TARGETS]
    specs = [(r, sp) for r, sp in specs if all((t, k) in targets for t, k, _ in sp[1])]
    if not specs:
        return None
    return targets, [(sp[0], tuple((targets.index((t, k)), n) for t, k, n in sp[1])) for _, sp in specs], specs


def forced_plan(state, days, hidden=False, watch=None):
    """このループを脚本家が必ず取れるか（下限）と、取れるなら今日の手（[(対象, 札)]）。
    hidden: 伏せ札の中身が主人公に見えない場合の、ループを取る確率 p と今日の混ぜ方 mix [(確率, [(対象, 札)])] も返す。
    watch: {置き先: 主人公に見張られている度合い}。賭けで同じ値の手のうち、暗躍+2 を見張られていない側に置く手を選ぶ。"""
    rs = enumerate_routes(state)
    abst = _abstract(rs, state, days)
    if abst is None:
        return {'forced': False, 'move': None, 'locked': False, 'routes': [], **({'p': 0.0, 'mix': None} if hidden else {})}
    targets, route_need, specs = abst
    args = (targets, [_current(state, t, k) for t, k in targets], route_need, state['day'], days,
            tuple('PAR-' not in state['used'][p] for p in 'ABC'), 'INT2' not in state['used']['M'])
    ok, move = solve(*args)
    extra = {}
    if hidden:
        # 賭けの計算では位置の条件を含む筋を数えない（主人公の移動で崩される。従者の身代わりで 0.95 と出た）
        h = _abstract(rs, state, days, positional=False)
        il = state['chars'].get('C20')
        place = lambda nm: 'B:' + il['area'] if nm == 'C20' and il and il.get('area') else nm  # noqa: E731
        extra['p'], extra['mix'] = (1.0, [(1.0, move)]) if ok else (0.0, None) if h is None else solve_hidden(
            h[0], [_current(state, t, k) for t, k in h[0]], h[1], *args[3:],
            watch={nm: (watch or {}).get(place(nm), 0) for nm, _ in h[0]})
    # 幻想には札を置けない（カードの特性）。同じエリアのボードの札が幻想にも効く（engine/resolve.py）→ 置き先をボードに直す
    il = state['chars'].get('C20')
    if il and il.get('area'):
        def fix(mv):
            out = []
            for t, c in mv or []:
                t = 'B:' + il['area'] if t == 'C20' else t
                if t not in {x for x, _ in out}:  # ボードにも札があれば1枚だけ
                    out.append((t, c))
            return out if mv is not None else None
        move = fix(move)
        if extra.get('mix'):
            extra['mix'] = [(pr, fix(mv)) for pr, mv in extra['mix']]
    # locked: 残りの日に何も押さなくても取れる（確定）。forced だけなら、今日は要らなくても明日以降の札が要る
    return {'forced': ok, 'move': move, 'locked': ok and solve(*args, idle=True)[0],
            'routes': [r['id'] + ':' + str(r['via']) for r, _ in specs], **extra}


def solve(targets, current, route_need, day0, days, par_left, int2, idle=False):
    """targets: [(対象, 'par'|'int')]、route_need: [(判定の日 or 'turn_end', ((対象の番号, 必要量), ...))]。
    返り値 (取れるか, 今日の手 [(対象, 札)] or None)。idle: 脚本家が何も押さない場合だけを見る。
    取れる手が複数あれば、押す札の多い手を返す（数えていない主人公の能力への余裕）。"""
    key = (tuple(targets), tuple(route_need), days, idle)  # 同じ抽象化の盤面は解いた値を使い回す（賭けの計算が1手ごとに呼ぶ）
    if key not in _DP:
        if len(_DP) > 256:
            _DP.clear()
        _DP[key] = _dp(*key)
    win, cap = _DP[key]
    ok, mm = win(day0, tuple(min(v, c) for v, c in zip(current, cap)), tuple(par_left), int2)
    return ok, ([(targets[i][0], c) for i, c in mm.items()] if ok else None)


_DP = {}


def _dp(targets, route_need, days, idle):
    # 不安は主人公の不安−1（ループに最大3枚）で減るので、必要量より3つ上まで余りを数える（切ると不安4 の余りが消える）
    cap = [max([n for _, need in route_need for i, n in need if i == j] or [0]) + (3 if k == 'par' else 0)
           for j, (_, k) in enumerate(targets)]
    par_idx = [i for i, (t, k) in enumerate(targets) if k == 'par']
    int_idx = [i for i, (t, k) in enumerate(targets) if k == 'int']

    def lost(vals, day):
        for when, need in route_need:
            due = when == 'turn_end' or when == day
            if due and all(vals[i] >= n for i, n in need):
                return True
        return False

    def mm_moves(int2_ok):
        """脚本家の札の割り当て（関わる対象だけ）: {対象の番号: 札}。不安+1 は2枚まで、暗躍は1枚（暗躍+2 の日は暗躍+1 と合わせて2か所）、計3枚。"""
        out = [{}]
        opts = {i: ['PAR+', 'PARX'] for i in par_idx}
        for i in int_idx:
            opts[i] = ['INT1'] + (['INT2'] if int2_ok else [])
        idx = list(opts)

        def rec(j, cur):
            if j == len(idx):
                return
            for card in [None] + opts[idx[j]]:
                if card is None:
                    rec(j + 1, cur)
                    continue
                nxt = dict(cur)
                nxt[idx[j]] = card
                cs = list(nxt.values())
                if len({targets[i][0] for i in nxt}) < len(nxt):
                    continue  # 同じ対象に2枚は置けない（人物の不安と暗躍は同じ対象）
                if len(cs) > 3 or cs.count('PAR+') > 2 or cs.count('INT1') > 1 or cs.count('INT2') > 1:
                    continue
                out.append(nxt)
                rec(j + 1, nxt)
        rec(0, {})
        return [{}] if idle else sorted(out, key=len, reverse=True)

    def pc_replies(mm, vals, pl):
        """主人公の応手: 各主人公1枚、同じ対象に2人は置かない。不安−1 は残っている人だけ、暗躍禁止は1か所だけ。"""
        par_t = [i for i in par_idx if mm.get(i) == 'PAR+' or vals[i] > 0]
        int_t = [i for i in int_idx if mm.get(i) in ('INT1', 'INT2')]
        res = [((), None)]  # (不安−1 を置く対象の組, 暗躍禁止の対象)
        people = [p for p in range(3) if pl[p]]
        for k in range(1, min(len(people), len(par_t)) + 1):
            for ts in combinations(par_t, k):
                res.append((ts, None))
        out = []
        for ts, _ in res:
            out.append((ts, None))
            if len(ts) < 3:
                for i in int_t:
                    if targets[i][0] not in {targets[j][0] for j in ts}:
                        out.append((ts, i))
        return out

    def apply(vals, mm, reply, pl):
        ts, ix = reply
        v = list(vals)
        for i in par_idx:
            plus = mm.get(i) == 'PAR+'
            lock = mm.get(i) == 'PARX'
            minus = i in ts
            if lock:
                continue
            v[i] = v[i] + (1 if plus else 0) - (1 if minus else 0)
            v[i] = max(0, min(v[i], cap[i]))
        for i in int_idx:
            c = mm.get(i)
            if c and ix != i:
                v[i] = min(v[i] + (2 if c == 'INT2' else 1), cap[i])
        npl = list(pl)
        for _ in ts:  # 不安−1 を使った人の数だけ、残っている人から減らす（誰が使ったかは結果に効かない）
            j = npl.index(True)
            npl[j] = False
        return tuple(v), tuple(npl)

    @lru_cache(maxsize=None)
    def win(day, vals, pl, i2):
        if day > days:
            return False, None
        for mm in mm_moves(i2):
            ok = True
            for reply in pc_replies(mm, vals, pl):
                if len(reply[0]) > sum(pl):
                    continue
                v2, pl2 = apply(vals, mm, reply, pl)
                if lost(v2, day):
                    continue
                if not win(day + 1, v2, pl2, i2 and 'INT2' not in mm.values())[0]:
                    ok = False
                    break
            if ok:
                return True, mm
        return False, None

    return win, cap


BLUFF = 'BLUFF'  # 効果の無い伏せ札（友好禁止・移動など）。主人公からは本物と見分けられない


def solve_hidden(targets, current, route_need, day0, days, par_left, int2, watch=None, fixed_T=None):
    """今日の賭け: 伏せ札の中身が主人公に見えない（置き先だけ見える）として、今日の札の後に「必ず取れる」盤面（solve）になる確率と、今日の混ぜ方。
    置き先の組ごとに、割り当て（行）× 主人公の応手（列）の行列ゲームを LP で解く。値は 翌日 solve が取れる→1、取れない→0。
    ponytail: 1日だけの賭け。何日も続けて賭ける値（暗躍+2 の温存など）は数えない（全日の版は d16 で80秒かかり、刈り込みで値が動いた）。
    主人公は負け筋（役職）を知っているものとする。
    fixed_T: 脚本家の置き先が決まっている（主人公から見た今日）。その組だけを見て、主人公の応手ごとの「取られる割合」
    {(不安−1 の置き先の組, 暗躍禁止の置き先): (割り当ての最悪, 平均)} を返す（block_rates）。"""
    import numpy as np
    from scipy.optimize import linprog
    cap = [max([n for _, need in route_need for i, n in need if i == j] or [0]) + (3 if k == 'par' else 0)
           for j, (_, k) in enumerate(targets)]
    vals = tuple(min(v, c) for v, c in zip(current, cap))
    pl = tuple(par_left)
    names = sorted({t for t, _ in targets})
    idx_of = {nm: [i for i, (t, _) in enumerate(targets) if t == nm] for nm in names}

    def lost(v, day):
        return any((w == 'turn_end' or w == day) and all(v[i] >= n for i, n in need) for w, need in route_need)

    def options(nm):
        out = [(BLUFF, None)]
        for i in idx_of.get(nm, []):  # 負け筋に関わらない置き先は効果の無い札だけ
            out += [('PAR+', i), ('PARX', i)] if targets[i][1] == 'par' else [('INT1', i)] + ([('INT2', i)] if int2 else [])
        return out

    def rows_for(T):
        res = [[]]
        for nm in T:
            res = [a + [o] for a in res for o in options(nm)]
        return [a for a in res if all([c for c, _ in a].count(x) <= m for x, m in (('PAR+', 2), ('INT1', 1), ('INT2', 1), ('PARX', 1)))]

    def cols_for(T):
        par_t = [i for i in range(len(targets)) if targets[i][1] == 'par' and (targets[i][0] in T or vals[i] > 0)]
        int_t = [i for nm in T for i in idx_of.get(nm, []) if targets[i][1] == 'int']
        out = []
        for k in range(0, min(sum(pl), len(par_t)) + 1):
            for ts in combinations(par_t, k):
                chars = {targets[j][0] for j in ts}
                out.append((ts, None))
                if k < 3:
                    out += [(ts, i) for i in int_t if targets[i][0] not in chars]
        return out

    def value(a, y):
        ts, ix = y
        v = list(vals)
        mm = {i: c for c, i in a if i is not None}
        for i in range(len(targets)):
            if targets[i][1] == 'par':
                if mm.get(i) != 'PARX':
                    v[i] = max(0, min(v[i] + (mm.get(i) == 'PAR+') - (i in ts), cap[i]))
            elif mm.get(i) in ('INT1', 'INT2') and ix != i:
                v[i] = min(v[i] + (2 if mm[i] == 'INT2' else 1), cap[i])
        if lost(v, day0):
            return 1.0
        if day0 >= days:
            return 0.0
        npl = list(pl)
        for _ in ts:
            npl[npl.index(True)] = False
        return float(solve(targets, v, route_need, day0 + 1, days, tuple(npl), int2 and all(c != 'INT2' for c, _ in a))[0])

    watch = watch or {}

    def real(a, T):  # 同じ値の手の選び方: 本物の札が多い・暗躍+2 が見張られていない側
        return sum(c != BLUFF for c, _ in a) + 0.5 * sum(c == 'INT2' for c, _ in a) \
            - 0.01 * sum(watch.get(nm, 0) for nm, (c, _) in zip(T, a) if c == 'INT2')

    if fixed_T is not None:
        T = tuple(fixed_T)
        rows, cols = rows_for(T), cols_for(T)
        M = np.array([[value(a, y) for y in cols] for a in rows])
        return {(frozenset(targets[j][0] for j in ts), None if ix is None else targets[ix][0]): (float(M[:, c].max()), float(M[:, c].mean()))
                for c, (ts, ix) in enumerate(cols)}
    best, best_real, best_mix = 0.0, -1.0, None
    for T in combinations(names, min(3, len(names))):  # 3か所: 効果の無い札で少ない置き方を真似られ、主人公に見える情報も増えない
        rows, cols = rows_for(T), cols_for(T)
        M = np.array([[value(a, y) for y in cols] for a in rows])
        pure = M.min(axis=1)
        r0 = max(range(len(rows)), key=lambda r: (pure[r], real(rows[r], T)))
        val, mix = float(pure[r0]), [(1.0, rows[r0])]
        if val < 1.0 and M.max() > max(val, best) + 1e-9:
            uniq = sorted({tuple(row): r for r, row in enumerate(M)}.values())  # 結果が同じ割り当てはまとめる
            Mu, nr = M[uniq], len(uniq)
            res = linprog(np.r_[np.zeros(nr), -1.0], A_ub=np.c_[-Mu.T, np.ones(len(cols))], b_ub=np.zeros(len(cols)),
                          A_eq=np.r_[np.ones(nr), 0.0][None, :], b_eq=[1.0], bounds=[(0, None)] * nr + [(None, None)], method='highs')
            if res.status == 0 and -res.fun > val + 1e-9:
                val = -res.fun
                mix = [(float(x), rows[uniq[r]]) for r, x in enumerate(res.x[:nr]) if x > 1e-6]
        rl = sum(pr * real(a, T) for pr, a in mix)
        if val > best + 1e-9 or (val > best - 1e-9 and rl > best_real):
            best, best_real, best_mix = val, rl, [(pr, [(nm, c) for nm, (c, _) in zip(T, a)]) for pr, a in mix]
    return best, (best_mix if best > 0 else None)


def block_rates(state, days, mm_targets):
    """主人公から見た今日: 脚本家の伏せ札の置き先（mm_targets）は見え、中身は見えない。応手ごとに、今日の後で脚本家が
    このループを必ず取れるようになる割合 {(不安−1 の置き先, 暗躍禁止の置き先): (最悪, 平均)}。負け筋が無ければ None。
    位置の条件は今の位置のまま（主人公の移動は数えない）。"""
    abst = _abstract(enumerate_routes(state), state, days)
    if abst is None:
        return None
    targets, route_need, _ = abst
    return solve_hidden(targets, [_current(state, t, k) for t, k in targets], route_need, state['day'], days,
                        tuple('PAR-' not in state['used'][p] for p in 'ABC'), 'INT2' not in state['used']['M'],
                        fixed_T=[t for t in mm_targets if t != 'C20'])
