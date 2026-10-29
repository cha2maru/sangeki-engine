"""資源の割り当て（予約）を持つ脚本を知る主人公（2026-10-29、ユーザー「目標の範囲＋守り続ける制約」「思考方法の構造化」）。

主人公の考え方の層（plan/protagonist_defense_plan.md、メモリ structured-protagonist-thinking）のうち ④ 資源の割り当て を受け持つ。
① 負け筋（engine/routes.enumerate_routes）から ② 締め切りつきの不安の条件（犯人・フレンドの不安を、事件の日に臨界未満）を拾い、
③ 条件ごとに崩し方を選ぶ: 不安を下げる友好能力の道（持ち主への友好の投資＋射程へ入れる移動）があればそれ、無ければ一度きりの不安−1。
④ 予約: 投資は友好を奪う事件（噂の流布）の後に始める・射程は締め切りの前日までに入れて移動禁止で保つ・不安−1 は道の無い条件に締め切りの早い順に残す・
   補給の源（ミスリーダー・クロマク）が守る人物と同じエリアにいれば動かして外す。
⑤ 毎日: 予約の手を先に置き、残りは oreadKQLm（selfplay.league）。予約を壊す手（予約した不安−1 の流用・射程から外す移動）は使わせない。
d30・d12 の Claude の手順（runs/pc_hand_*）の学び 1〜4・6 を一般の形にしたもの。学び 5（友好禁止に備えて持ち主を2人）・6（タイムトラベラーの友好を締め切りから逆に）・7（シリアルキラーを人の多いエリアに）も入れた。
ponytail: 予約は目安（区間の見積もり。脚本家の押しと札の取り合いは厳密には数えない）。暗躍の条件（暗躍禁止・巫女[3] など）は oreadKQLm に任せる。
"""
from engine import abilities as ab
from engine import phases as ph
from engine.resolve import CHARS, ONCE, combine_moves, copy_state, destination
from engine.routes import INF, enumerate_routes

MOVES = ('MV_V', 'MV_H')


def _move_to(s, c, area):
    """c を1枚の移動札で area へ動かせるなら、その札（禁止エリアは除く）。"""
    a = s['chars'][c].get('area')
    for mv in MOVES:
        if a and destination(a, combine_moves([mv])) == area and area not in CHARS.get(c, {}).get('forbidden', []):
            return mv
    return None


def par_reducers(s, x):
    """x の不安を下げられる主人公の友好能力 [(持ち主, 番号, 要る友好)]。持ち主と x を同じエリアに置き、友好を足りる量にし、x を臨界にした試しの盤面で、
    合法な宣言を本物のエンジンで解決して x の不安が減るものを拾う（ナース[2]・医者[2]・学者[3] など。拒否できる役職の持ち主は除く）。"""
    out = []
    lim = CHARS[x]['limit']
    for (h, i), (need, _once, _k) in ab.ABILITIES.items():
        hv = s['chars'].get(h)
        if not hv or not hv['alive'] or not hv.get('present', True) or not hv.get('area') or (h, i) in [(o[0], o[1]) for o in out]:
            continue
        if (h, i) not in ab.UNREFUSABLE and ab.REFUSAL.get(ph.role(s, h)):
            continue
        if ph.role(s, h) in ('SK', 'KILLER'):  # 射程に入れると殺されうる（d12 の男子学生＝シリアルキラー）
            continue
        if any(inc['id'] == 'MURDER' and inc['culprit'] == h and inc['day'] >= s['day'] for inc in s['script']['incidents']):
            continue  # 殺人事件の犯人（同じエリアの人物を殺す）も、事件が残っている間は射程に入れない（d09 の男子学生）
        b = copy_state(s)
        b['ability_used_today'], b['ability_used_loop'] = [], []
        b['chars'][h]['gw'] = max(b['chars'][h]['gw'], need)
        b['chars'][h]['area'] = b['chars'][x]['area']
        b['chars'][x]['par'] = max(b['chars'][x]['par'], lim)
        for c, ii, a in ab.declaration_options(b):
            if (c, ii) != (h, i):
                continue
            t = copy_state(b)
            try:
                ab.use_ability(t, c, ii, a, refuse=False)
            except Exception:
                continue
            if t['chars'][x]['par'] < b['chars'][x]['par']:
                out.append((h, i, need))
                break
    return out


def _lowers(s, c, k, a, x):
    """宣言 (c, k, a) を写しの盤面で解決すると x の不安が減るか（拒否されない前提）。"""
    t = copy_state(s)
    try:
        ab.use_ability(t, c, k, a, refuse=False)
    except Exception:
        return False
    return t['chars'][x]['par'] < s['chars'][x]['par']


def par_clauses(s):
    """不安の条件 [(人物, 締め切りの日, 臨界)]。犯人が臨界なら起きる事件のうち、主人公の負け・フレンドの死につながる筋の犯人（route の par の条件）と、
    毎日判定される筋（日の条件が無い。締め切りは今日）の不安の対象。"""
    out = {}
    for r in enumerate_routes(s):
        if r['total'] >= INF:
            continue
        day = next((c['detail'][2] for c in r['conds'] if c['kind'] == 'days' and c['detail'][0] == '事件'), None)
        if day is None and not any(c['kind'] == 'days' for c in r['conds']):
            day = s['day']  # 毎日のターン終了に判定される筋（メインラバーズの主人公死亡など）は締め切りが今日（d18: 拾わず、不安−1 を巫女に予約して大物に使えなかった）
        if day is None or day < s['day']:
            continue
        for c in r['conds']:
            if c['kind'] == 'counter' and c['detail'][1] == 'par' and c['detail'][0] in s['chars']:
                x, need = c['detail'][0], c['detail'][2]
                d0, n0 = out.get(x, (99, 99))
                out[x] = (min(d0, day), min(n0, need))  # 筋の条件の必要量（メインラバーズの閾値は不安臨界と別）
    return [(x, d, n) for x, (d, n) in sorted(out.items(), key=lambda kv: kv[1][0])]


def steal_day(s, upto):
    """友好を奪う事件（噂の流布）で、upto 日までに起きうる最後の日（無ければ 0）。"""
    return max([i['day'] for i in s['script']['incidents'] if i['id'] == 'RUMOR_SPREAD' and s['day'] <= i['day'] <= upto] or [0])


def make_plan(s):
    """③④: 条件ごとの崩し方と予約。{'paths': [(x, 日, 持ち主, 番号, 要る友好, 投資を始める日, 2番手 (持ち主, 要る友好) or None)], 'reserve': {x: 不安−1 の枚数}, 'cut': [補給の源]}。"""
    plan = {'paths': [], 'reserve': {}, 'cut': []}
    left = sum('PAR-' not in s['used'][p] for p in 'ABC')
    used_h = {}  # 持ち主 → 使う日（友好能力は1日1回。締め切りの日が違えば同じ持ち主を使い回す: d30 のナース[2] を5日目に入院患者・6日目に巫女）
    for x, d, lim in par_clauses(s):
        best, cands = None, []
        for h, i, need in par_reducers(s, x):
            if d in used_h.get(h, set()) and h != x:
                continue
            same = h == x or s['chars'][h]['area'] == s['chars'][x]['area']
            mv = same or _move_to(s, x, s['chars'][h]['area']) or _move_to(s, h, s['chars'][x]['area'])
            if not mv:
                continue
            gap = max(0, need - s['chars'][h]['gw'])
            if gap > 2 * (d - s['day'] + 1):  # 友好+2・+1 で締め切りまでに届かない
                continue
            cands.append(((gap + (0 if same else 1), (h, i) not in ab.UNREFUSABLE and h != x, h), h, i, need))
        cands.sort()
        best = cands[0] if cands else None
        if best:
            _, h, i, need = best
            used_h.setdefault(h, set()).add(d)
            start = max(s['day'], steal_day(s, d) + 1)
            # 2番手の持ち主（脚本家の友好禁止は1日1枚なので、1番手に伏せ札が来た日は2番手に積む。d12 の学び5）
            alt = next(((c[1], c[3]) for c in cands[1:] if c[1] != h), None)
            plan['paths'].append((x, d, h, i, need, start, alt))
        else:
            n = min(left, max(1, s['chars'][x]['par'] + (d - s['day'] + 1) - (lim - 1)))
            if n > 0:
                plan['reserve'][x] = n
                left -= n
    # 締め切りの友好（タイムトラベラー: 最終日に友好3以上）。消されない投資なので、締め切りから逆に置く（d12 の学び6）
    plan['gw_need'] = {r['via']: 3 for r in enumerate_routes(s) if r['id'] == 'TT_LOSS' and r['total'] < INF}
    targets = {x for x, _, _ in par_clauses(s)}
    for c, v in s['chars'].items():  # 補給の源: 守る人物と同じエリアのミスリーダー・クロマク
        if v['alive'] and v.get('present', True) and v.get('area') and any(ph.has_ability(s, c, a) for a in ('MISLEADER', 'KUROMAKU')) \
                and any(s['chars'][x]['area'] == v['area'] for x in targets if x != c):
            plan['cut'].append(c)
    return plan


def plan_pc(rng, script, base_kind='oreadKQLm', fair=False):
    """league の oplan: oreadKQLm＋予約（このモジュールの説明）。fair: 脚本を知らない主人公（base_kind は推理する主人公）。
    予約は推理の世界ごとに作って多数決、配役に頼る日々の判断も推理の世界で行う（本当の脚本は見ない）。"""
    from selfplay.league import make
    base = make(base_kind, rng, script)
    sv = None
    orig_cards = base.pc_cards
    base.plan_log = []

    def plan_of(s):
        """予約。脚本を知る主人公は本当の脚本で、知らない主人公は推理の世界を4つ引いて世界ごとに作り、半分以上の世界に出る予約だけを採る（多数決）。"""
        if not fair:
            return make_plan(s)
        ws = base._worlds(s)[:4] or [s['script']]
        plans = [make_plan(dict(s, script=w)) for w in ws]
        need = (len(plans) + 1) // 2
        cnt = lambda key, items: sum(1 for p in plans if key in items(p))  # noqa: E731
        out = {'paths': [], 'reserve': {}, 'cut': [], 'gw_need': {}}
        for p in plans:
            for q in p['paths']:
                if q[0] not in {r[0] for r in out['paths']} and cnt((q[0], q[2]), lambda pp: {(r[0], r[2]) for r in pp['paths']}) >= need:
                    out['paths'].append(q)
            for x, n in p['reserve'].items():
                if x not in out['reserve'] and cnt(x, lambda pp: pp['reserve']) >= need:
                    out['reserve'][x] = min(pp['reserve'][x] for pp in plans if x in pp['reserve'])
            for c in p['cut']:
                if c not in out['cut'] and cnt(c, lambda pp: pp['cut']) >= need:
                    out['cut'].append(c)
            for c, v in p.get('gw_need', {}).items():
                if cnt(c, lambda pp: pp.get('gw_need', {})) >= need:
                    out['gw_need'][c] = v
        return out

    def pc_cards(s, order, mm_targets=None):
        T = set(mm_targets or [])
        nonlocal sv
        sv = s if not fair else dict(s, script=(base._worlds(s)[:1] or [s['script']])[0])  # 配役に頼る判断の世界（知らない主人公は推理の世界）
        if getattr(base, '_plan_loop', None) != s['loop'] or s['day'] == 1:
            base._plan_loop, base._plan = s['loop'], plan_of(s)
        else:  # 毎朝組み直す（脚本家の手で事情が変わる）が、決めた持ち主は変えない
            old = base._plan
            new = plan_of(s)
            keep = {p[0]: p for p in old['paths'] if s['chars'][p[2]]['alive']}
            new['paths'] = [keep.get(p[0], p) for p in new['paths']] + [p for x, p in keep.items() if x not in {q[0] for q in new['paths']}]
            base._plan = new
        plan, ch, used, day = base._plan, s['chars'], s['used'], s['day']
        free, out, taken = list(order), [], set()

        def put(t, cards):
            if t in taken or t == 'C20' or (t in ch and (not ch[t]['alive'] or not ch[t].get('present', True))):
                return False  # 幻想（C20）には行動カードを置けない
            for p in list(free):
                for c in cards:
                    if not (c in ONCE[p] and c in used[p]):
                        out.append({'by': p, 'target': t, 'card': c}); free.remove(p); taken.add(t)
                        return True
            return False
        # 暗躍の条件（層②の句のうち暗躍・ボード）: 負け筋の暗躍の対象に伏せ札があり、暗躍+2 で必要量に届きうるなら暗躍禁止を最優先
        # （d14: 予約の手が札の枠を取り、学校の暗躍2（巨大時限爆弾X）を止めずにループ終了で負けた）。暗躍禁止は1日1枚だけ
        cult_areas = {v['area'] for c, v in ch.items() if v['alive'] and ph.base_role(sv, c) == 'CULTIST'}
        int_need = {}
        for r in enumerate_routes(sv):
            if r['total'] >= INF:
                continue
            for cnd in r['conds']:
                if cnd['kind'] == 'board':
                    t, n = 'B:' + cnd['detail'][0], cnd['detail'][1]
                elif cnd['kind'] == 'counter' and cnd['detail'][1] == 'int' and cnd['detail'][0] in ch:
                    t, n = cnd['detail'][0], cnd['detail'][2]
                else:
                    continue
                int_need[t] = min(int_need.get(t, 99), n)
        base_cards = orig_cards(s, order, mm_targets)  # oreadKQLm の手（応手表で選んだ守り）。暗躍禁止の置き先はこちらに従う
        bx = next((c['target'] for c in base_cards if c['card'] == 'INTX'), None)
        if bx is not None:  # d14: 残り量の少ない病院より、取り返せない学校（巨大時限爆弾X）を止めるべきだった。その判断は応手表がしている
            put(bx, ['INTX'])
        for t in [] if bx is not None else sorted((t for t in int_need if t in T), key=lambda t: int_need[t] - (s['boards'][t[2:]] if t.startswith('B:') else ch[t]['int'])):
            cur = s['boards'][t[2:]] if t.startswith('B:') else ch[t]['int']
            area = t[2:] if t.startswith('B:') else ch[t]['area']
            if cur + 2 >= int_need[t] and area not in cult_areas:
                put(t, ['INTX'])
                break
        for c, need in plan.get('gw_need', {}).items():  # 締め切りの友好: 残りの日数が足りない友好＋1 以下になったら最優先
            lack = need - ch[c]['gw']
            if ch[c]['alive'] and lack > 0 and s['script']['days'] - day + 1 <= lack + 1:
                put(c, ['GW2', 'GW1'] if lack >= 2 else ['GW1', 'GW2'])
        # 人数で2人きりを防ぐ（学び7、d12）: シリアルキラーは、ほかに2人以上いるエリアに置く。1人だけのエリアにいると、
        # そこへ動かされた人物と2人きりで殺される（脚本家の一度きりの斜めの移動はいつ来るか分からない）。人の多い隣へ動かし、戻されそうなら移動禁止
        def others(a, me):
            return [c for c, v in ch.items() if v['alive'] and v.get('present', True) and v.get('area') == a and c != me]
        for k, v in ch.items():
            if not (v['alive'] and v.get('present', True) and v.get('area')) or ph.role(sv, k) != 'SK':
                continue
            if len(others(v['area'], k)) < 2:
                for mv in MOVES:
                    to = destination(v['area'], combine_moves([mv]))
                    if to not in CHARS.get(k, {}).get('forbidden', []) and len(others(to, k)) >= 2:
                        put(k, [mv])
                        break
            elif k in T:
                put(k, ['MVX'])
        def danger_here(c):
            """c のエリアで、これからの殺人事件・シリアルキラーの殺しが起きうるか（その犯人か、犯人と同じエリア）。"""
            a = ch[c]['area']
            for inc in sv['script']['incidents']:
                cu = inc['culprit']
                if inc['id'] == 'MURDER' and inc['day'] >= day and cu in ch and ch[cu]['alive'] and ch[cu]['area'] == a:
                    return True
            return any(v['alive'] and v.get('area') == a and ph.role(sv, k) == 'SK' and k != c for k, v in ch.items())
        hold_pos = set()  # 射程に入っていて動かさない人物
        done = base.__dict__.setdefault('_cut_done', set())
        for c in plan['cut']:  # 補給の源を守る人物のエリアから出す（1ループに1回。戻されそうなら移動禁止）
            tg = {ch[x]['area'] for x, _, _ in par_clauses(sv) if x != c}
            if ch[c]['area'] in tg and (s['loop'], day, c) not in done:
                done.add((s['loop'], day, c))  # 入り直したらまた外す（d18 の Claude 戦: 従者が大物を追って神社に入り直し、毎日不安を足した）
                for mv in MOVES:
                    if destination(ch[c]['area'], combine_moves([mv])) not in tg | set(CHARS.get(c, {}).get('forbidden', [])):
                        put(c, [mv])
                        break
            elif c in T:
                put(c, ['MVX'])
        for x, d, h, i, need, start, alt in sorted(plan['paths'], key=lambda p: p[1]):
            if day > d or not ch[x]['alive']:
                continue
            same = h == x or ch[h]['area'] == ch[x]['area']
            if ch[h]['gw'] < need and day >= start:  # 投資: 1番手に伏せ札（友好禁止のおそれ）があれば2番手へ。射程を保つ移動禁止より先
                if h in T and alt and ch[alt[0]]['gw'] < alt[1]:
                    put(alt[0], ['GW2', 'GW1'])
                else:
                    put(h, ['GW2', 'GW1'])
            if not same and (day >= d - 1 or ch[x]['par'] >= 1) and ch[h]['gw'] + 2 * (d - day + 1) >= need:
                # 射程へ入れる: 締め切りの前日か、不安が溜まり始めたら（d12: 前日まで待つと、その日に伏せ札の来た対象に不安−1 を置いて移動が置けず、
                # アイドルが都市に残って自殺した）。持ち主の能力が締め切りまでに使える見込みなら、不安−1 より先に置く（能力は毎日使える）
                mv = _move_to(s, x, ch[h]['area'])
                if mv:
                    put(x, [mv])
                else:
                    mv = _move_to(s, h, ch[x]['area'])
                    if mv:
                        put(h, [mv])
            lim = next((n for xx, _, n in par_clauses(sv) if xx == x), CHARS[x]['limit'])
            ready = same and ch[h]['gw'] >= need
            if x in T and ch[x]['par'] + 1 >= lim - (d - day) and (not ready or ch[x]['par'] + 1 >= lim):
                put(x, ['PAR-'])  # 能力がまだ届かない・届いても1日1回では下げきれない（臨界の1つ手前）なら、移動禁止より先に不安−1
            if same and not danger_here(x) and not danger_here(h):
                hold_pos |= {x, h}
                for c in {x, h}:
                    if c in T:
                        put(c, ['MVX'])  # 射程を保つ（同じエリアに殺されうる事件がある間は保たない。位置を崩す手を優先: d18 の Claude 戦で巫女を殺人事件の場に留めた）
        for x, n in plan['reserve'].items():
            d, lim = next(((dd, n) for xx, dd, n in par_clauses(sv) if xx == x), (day, CHARS[x]['limit']))
            if x in T and ch[x]['par'] + 1 >= lim - (d - day):
                put(x, ['PAR-'])
        reserved = sum(plan['reserve'].values())
        par_left = sum('PAR-' not in used[p] for p in 'ABC')
        if free:
            for c in base_cards:
                if c['by'] not in free or c['target'] in taken:
                    continue
                if c['card'] == 'PAR-' and c['target'] not in plan['reserve'] and par_left - sum(x['card'] == 'PAR-' for x in out) <= reserved:
                    continue  # 予約した不安−1 をほかの対象に使わない
                if c['card'] in MOVES and c['target'] in hold_pos:
                    continue  # 射程に入れた人物を動かさない
                if c['card'] == 'INTX' and any(x['card'] == 'INTX' for x in out):
                    continue  # 暗躍禁止は2枚以上で全部無効（d14 の3日目に2枚置いた）
                out.append(c); free.remove(c['by']); taken.add(c['target'])
        for p in list(free):
            for t in ('B:CIT', 'B:SCH', 'B:HOS', 'B:SHR'):
                if t not in taken:
                    out.append({'by': p, 'target': t, 'card': 'GW1'}); taken.add(t); free.remove(p)
                    break
        base.plan_log.append((s['loop'], day, plan['paths'], plan['reserve'], plan['cut'], [(x['target'], x['card']) for x in out]))
        from engine.options import pc_placement_error
        if pc_placement_error(s, out):  # 反則の置き方（d18: 幻想に札）は oreadKQLm の手に戻す
            return orig_cards(s, order, mm_targets)
        return sorted(out, key=lambda x: order.index(x['by']))

    orig_abl = base.abilities_for_day

    def abilities_for_day(s):
        """予約した能力を先に: 締め切りの近い条件から、持ち主の能力を対象へ（合法なら。臨界の条件などはエンジンが確かめる）。残りは oreadKQLm。"""
        plan, decl, used = getattr(base, '_plan', None), [], set()
        for x, d, h, i, need, start, alt in sorted((plan or {}).get('paths', []), key=lambda p: p[1]):
            if d < s['day'] or (h, i) in used or s['chars'][x]['par'] == 0:
                continue
            for hh, ii in ((h, i),) + (((alt[0], None),) if alt else ()):
                cands = [(c, k, a) for c, k, a in ab.declaration_options(s) if c == hh and (ii is None or k == ii)
                         and (a or {}).get('target', c) == x and (c, k) not in used and _lowers(s, c, k, a, x)]
                if cands:  # 対象の不安が実際に減る宣言だけ（d14: 医者[2] を「置く」で使い、毎日不安を足していた）
                    decl.append(cands[0]); used.add((cands[0][0], cands[0][1]))
                    break
        rest = [x for x in orig_abl(s) if (x[0], x[1]) not in used]
        return decl + rest

    base.pc_cards, base.abilities_for_day = pc_cards, abilities_for_day
    return base
