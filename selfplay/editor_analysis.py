"""シナリオエディタの分析とランダム生成（ユーザー「入力したシナリオの勝ち筋などの分析」「ランダムだけどある程度バランスとれたシナリオ」2026-10-22）。

- analyze: ループ1の開始時の負け筋の一覧（routes.enumerate_routes）と、脚本を知る主人公が伏せ札の中身が何であれ守りきれるか
  （route_calc.pc_holds。上限＝脚本家に有利なことを数える。守れるなら毎ループ守れる＝脚本家が1ループも取れない、易しすぎの目安）。
- generate: engine.generate で作り、pc_holds が「守りきれる」と出たものは作り直す。
重い前方探索（selfplay/pc_solve）と下限（forced_plan、外れる例がある）は使わない。
"""
import random
import re

from engine.generate import generate as _generate
from engine.resolve import CHARS
from engine.route_calc import pc_holds
from engine.routes import INF, enumerate_routes
from engine.scripts import INCIDENTS, normalize, report, to_game
from engine import sets

AREA = {'HOS': '病院', 'SHR': '神社', 'CIT': '都市', 'SCH': '学校'}
KIND = {'par': '不安', 'int': '暗躍', 'gw': '友好'}
ROUTE = {'KILLER_KEY': 'キラー（キーパーソンを殺害）', 'KILLER_PROT': 'キラー（主人公死亡）', 'SK_KILL': 'シリアルキラー',
         'ML_PROT': 'メインラバーズ', 'TT_LOSS': 'タイムトラベラー', 'HOSPITAL_PROT': '病院の事件', 'HOSPITAL_KILL': '病院の事件',
         'FRIEND': 'フレンドの死亡'}
HOLD_BUDGET = 8.0  # 秒。読み切れなければ「判定できず」


def _nm(c):
    return CHARS.get(c, {}).get('name', c)


def _names(text):
    return re.sub(r'C\d\d', lambda m: _nm(m.group(0)), str(text))


def _cond(c):
    k, d = c['kind'], c['detail']
    if k == 'board':
        s = f'{AREA.get(d[0], d[0])}に暗躍{d[1]}以上'
    elif k == 'counter':
        s = f'{_nm(d[0])}に{KIND.get(d[1], d[1])}{d[2]}以上'
    elif k == 'counter_max':
        s = f'{_nm(d[0])}の{KIND.get(d[1], d[1])}が{d[2]}以下'
    elif k == 'same_area':
        s = f'{_nm(d[0])}と{_nm(d[1])}が同じエリア'
    elif k == 'alone_with':
        s = f'{_nm(d[0])}と{_nm(d[1])}が2人きり'
    elif k == 'in_area':
        s = f'{_nm(d[0])}が{AREA.get(d[1], d[1])}にいる'
    elif k == 'days' and d[0] == '事件':
        s = f"{d[2]}日目の事件「{INCIDENTS.get(d[1], {}).get('name', d[1])}」"
    elif k == 'days' and d[0] == '登場':
        s = f'{_nm(d[1])}が{d[2]}日目に登場'
    elif k == 'days':
        s = f'{d[0]}（{d[1]}日目）'
    else:
        s = _names(f'{k} {d}')
    return {'text': s, 'met': c['deficit'] == 0}


CARD = {'PAR+': '不安+1', 'INT1': '暗躍+1', 'INT2': '暗躍+2', 'MOVE': '移動'}


def _tname(t):
    return AREA[t[2:]] if t.startswith('B:') else _nm(t)


def plan(s, r, days, start=None):
    """負け筋 r を、主人公に邪魔されないときに最短で成立させる脚本家の手順（目安）。
    脚本家は1日3枚・同じ対象に1枚・不安+1 は2枚・暗躍+1 は1枚・暗躍+2 はループに1回。毎日+1 する脚本家能力（route_calc._supply、持ち主の位置は今のまま）、
    不穏な噂（ループ1回）、事件の効果（犯人を臨界に押して起こす。不安拡大・邪気の汚染・行方不明・蝶の羽ばたき）、
    脚本家能力フェイズのキャラクター能力（医者: 友好無視・友好2以上で不安±1、ご神木: 友好無視なら自身のカウンターを移す）も数える。
    位置の条件は要る移動の札の枚数だけ数える（誰をどちらへ動かすかは書かない）。返り値 {'steps': [文], 'done': 文} か {'none': 理由}。
    start: この日まで脚本家は何もしない（札・能力・事件を使わない）。押し始めの期限を調べるのに使う。
    返り値には期限 'deadline'（文）・'last'（その日）、間に合う最も遅い押し始めの日 'latest'、最短で成立する日 'day'、
    判定の時 'by'（'turn' 毎日のターン終了／'incident' 事件の日／'end' ループ終了時）も付ける（start を省いたときだけ。画面のグラフ用）。"""
    from engine.route_calc import _supply
    tg, need, cur, appear = [], [], [], {}
    when, by = None, 'turn'
    for c in r['conds']:
        k, d = c['kind'], c['detail']
        if k == 'counter' and d[1] in ('par', 'int'):
            tg.append((d[0], d[1])); need.append(d[2]); cur.append(s['chars'][d[0]][d[1]] if d[0] in s['chars'] else 0)
        elif k == 'board':
            tg.append(('B:' + d[0], 'int')); need.append(d[1]); cur.append(s['boards'][d[0]])
        elif k in ('same_area', 'alone_with', 'in_area'):
            if c['deficit'] >= INF:
                return {'none': '位置の条件を満たせない（禁止エリアなど）'}
            tg.append(('P:' + _cond(c)['text'], 'pos')); need.append(int(c['deficit'])); cur.append(0)
        elif k == 'days' and d[0] == '事件':
            when, by = d[2], 'incident'
        elif k == 'days' and d[0] == '登場':
            appear[d[1]] = d[2]
        elif k == 'days':
            when, by = d[1], 'end'
        elif k == 'counter_max':
            when, by = (when, by) if when else (days, 'end')  # 友好が上がらなければ満たしたまま（主人公が上げなければ）
        else:
            return {'none': '手順を数えられない条件がある'}
    if sets.rule(s, r['id']).get('loop_end_loss') and when is None:
        when, by = days, 'end'
    n_main = len(tg)
    # 事件で負け筋の対象にカウンターが入るもの（engine/data/incidents.json の効果。犯人が臨界なら起きる）。犯人の不安は補助の対象として押す
    incs = []
    for inc in s['script']['incidents']:
        cul = inc['culprit']
        if inc['day'] < s['day'] or cul not in s['chars'] or not s['chars'][cul]['alive']:
            continue
        gain = []  # [(対象の番号, 量)]
        for i, (tt, k) in enumerate(tg[:n_main]):
            board = tt[2:] if tt.startswith('B:') else None
            if inc['id'] == 'SPREAD' and not board and tt in s['chars'] and tt != cul and k in ('par', 'int'):
                gain.append((i, 2 if k == 'par' else 1))
            elif inc['id'] == 'CORRUPT' and board == 'SHR' and k == 'int':
                gain.append((i, 2))
            elif inc['id'] == 'MISSING' and board and k == 'int':
                gain.append((i, 1))
            elif (inc['id'] == 'BUTTERFLY' and not board and tt in s['chars'] and tt != cul and k in ('par', 'int')
                  and s['chars'][tt].get('area') == s['chars'][cul].get('area')):
                gain.append((i, 1))
        if not gain:
            continue
        j = next((i for i, x in enumerate(tg) if x == (cul, 'par')), None)
        if j is None:
            tg.append((cul, 'par')); need.append(CHARS[cul]['limit']); cur.append(s['chars'][cul]['par'])
            j = len(tg) - 1
        incs.append((inc, j, gain))
    rumor = 'X_RUMOR' in s['script']['rules'] and 'X_RUMOR' not in s.get('ability_used_loop', [])
    from engine import phases as ph
    sup = [('脚本家能力', idx) for idx in _supply(s, tg)]  # 毎日+1（持ち主ごと）
    # 脚本家能力フェイズに脚本家が使えるキャラクター能力（engine/data/characters_public.json）。邪魔が無い＝主人公が友好を置かない前提
    doc = s['chars'].get('C08')  # 医者: 友好無視を持ち友好2以上なら、同一エリアの自身以外に不安±1
    if doc and doc['alive'] and doc.get('present', True) and doc['gw'] >= 2 and sets.refusal(s, ph.role(s, 'C08')):
        idx = tuple(i for i, (tt, k) in enumerate(tg) if k == 'par' and tt in s['chars'] and tt != 'C08'
                    and s['chars'][tt].get('area') == doc['area'])
        if idx:
            sup.append(('医者の能力', idx))
    tree = s['chars'].get('C30')  # ご神木: 友好無視を持つなら、自身の上のカウンター1つを同一エリアの他のキャラへ移す（強制）
    tree_idx = [i for i, (tt, k) in enumerate(tg) if k in ('par', 'int') and tt in s['chars'] and tt != 'C30'
                and tree and s['chars'][tt].get('area') == tree['area']]
    if not (tree and tree['alive'] and tree.get('present', True) and sets.refusal(s, ph.role(s, 'C30'))):
        tree_idx = []
    pool0 = (tree['par'], tree['int']) if tree_idx else (0, 0)
    day0 = s['day']

    def ok(v, day):
        return all(x >= n for x, n in zip(v[:n_main], need[:n_main])) and (when is None or day == when)

    def moves(day, int2):
        """その日の札の割り当て [(対象の番号, 札)]（足りない対象だけ）"""
        if start and day < start:
            return [[]]
        opts = []
        for i, (t, k) in enumerate(tg):
            if t in appear and day < appear[t]:
                continue  # 登場前には置けない
            if k == 'par':
                opts.append([(i, 'PAR+')])
            elif k == 'int':
                opts.append([(i, 'INT1')] + ([(i, 'INT2')] if int2 else []))
            else:
                opts.append([(i, 'MOVE')])
        if tree_idx:  # ご神木に札を置き、脚本家能力フェイズに移す（番号 -1 ＝ご神木）
            opts.append([(-1, 'PAR+'), (-1, 'INT1')] + ([(-1, 'INT2')] if int2 else []))
        # 幻想には行動カードを置けない。同一エリアのボードの札が幻想にも効く（カードの特性）＝ボードへの1枚として数える
        il = 'B:' + str(s['chars'].get('C20', {}).get('area'))
        name = lambda i: 'C30' if i < 0 else il if tg[i][0] == 'C20' else tg[i][0]  # noqa: E731
        out = [[]]
        for o in opts:
            out = out + [x + [y] for x in out for y in o]
        return [x for x in out if len(x) <= 3 and sum(c == 'PAR+' for _, c in x) <= 2 and sum(c == 'INT1' for _, c in x) <= 1
                and sum(c == 'INT2' for _, c in x) <= 1 and len({name(i) for i, _ in x}) == len(x)]  # 同じ対象に1枚

    frontier = {(tuple(min(c, n) for c, n in zip(cur, need)), True, rumor, tuple(min(3, x) for x in pool0)): []}
    for day in range(day0, days + 1):
        nxt = {}
        for (v, int2, rum, pool), hist in frontier.items():
            for mv in moves(day, int2):
                w, pl = list(v), list(pool)
                for i, c in mv:
                    if i < 0:
                        pl[0 if c == 'PAR+' else 1] = min(3, pl[0 if c == 'PAR+' else 1] + (2 if c == 'INT2' else 1))
                    else:
                        w[i] = min(need[i], w[i] + (2 if c == 'INT2' else 1))  # 要る量で頭打ち（状態を減らす）
                abl = []  # 脚本家能力フェイズ → 事件フェイズ の順に、足りない対象のうち最初の1つへ（脚本家に良い使い方の目安）
                for how, idx in (sup if not start or day >= start else []):  # 毎日+1 の能力（持ち主ごと）
                    j = next((i for i in idx if w[i] < need[i]), None)
                    if j is not None:
                        w[j] += 1
                        abl.append((how, j, 1))
                j = next((i for i in tree_idx if w[i] < need[i] and pl[0 if tg[i][1] == 'par' else 1] > 0), None)
                if j is not None:  # ご神木の特性: カウンター1つを移す
                    pl[0 if tg[j][1] == 'par' else 1] -= 1
                    w[j] += 1
                    abl.append(('ご神木の特性（移す）', j, 1))
                r2 = rum
                if rum and (not start or day >= start):  # 不穏な噂（ループ1回、任意のボードに暗躍1）
                    j = next((i for i in range(n_main) if tg[i][0].startswith('B:') and w[i] < need[i]), None)
                    if j is not None:
                        w[j] += 1
                        r2 = False
                        abl.append(('不穏な噂', j, 1))
                for inc, cj, gain in incs:
                    if inc['day'] == day and w[cj] >= need[cj] and (not start or day >= start):
                        g = next(((i, n) for i, n in gain if w[i] < need[i]), None)
                        if g is not None:
                            w[g[0]] = min(need[g[0]], w[g[0]] + g[1])
                            abl.append((f"事件「{INCIDENTS[inc['id']]['name']}」（犯人 {_nm(inc['culprit'])}）", g[0], g[1]))
                key = (tuple(w), int2 and all(c != 'INT2' for _, c in mv), r2, tuple(pl))
                h = hist + [(day, mv, abl)]
                if ok(key[0], day):
                    out = {'steps': _steps(h, tg), 'done': f"{day}日目" + {'incident': 'の事件で成立', 'end': 'のループ終了時に成立'}.get(
                        by, 'のターン終了フェイズに成立')}
                    if start is None:
                        out.update(_deadline(s, r, days, when, by, day), day=day, by=by)
                    return out
                if key not in nxt or len(h[-1][1]) < len(nxt[key][-1][1]):
                    nxt[key] = h
        frontier = nxt
    return {'none': 'このループの日数では、邪魔が無くても届かない'}


def _deadline(s, r, days, when, by, earliest):
    """負け筋の期限と、邪魔が無いとき間に合う最も遅い押し始めの日。"""
    last = when or days
    text = {'incident': f'{last}日目の事件まで', 'end': f'ループ終了時（{last}日目）まで'}.get(by, f'最終日（{last}日目）のターン終了まで')
    lo, hi = s['day'], last  # 遅く始めるほど不利（単調）なので二分探索。lo は間に合う
    while lo < hi:
        k = (lo + hi + 1) // 2
        if 'steps' in plan(s, r, days, start=k):
            lo = k
        else:
            hi = k - 1
    return {'deadline': text, 'latest': lo, 'last': last}


def _steps(h, tg):
    out = []
    for day, mv, abl in h:
        parts = [f"ご神木に{CARD[c]}" if i < 0 else f"移動（{tg[i][0][2:]}）" if tg[i][1] == 'pos' else
                 f"幻想のいるボードに{CARD[c]}（幻想にも効く）" if tg[i][0] == 'C20' else f"{_tname(tg[i][0])}に{CARD[c]}" for i, c in mv]
        parts += [f"{how}で{_tname(tg[j][0])}に{'暗躍' if tg[j][1] == 'int' else '不安'}+{n}" for how, j, n in abl]
        if parts:
            out.append(f"{day}日目: " + '・'.join(parts))
    return out or ['札は要らない（いまの盤面のまま成立する）']


def _holds_all(states, days, budget, mm=None):
    """ループの準備のすべての選び方で守れるときだけ守れる（1つでも守れなければ守れない、読み切れなければ不明）。"""
    import time
    t_end, skipped, res = time.time() + budget, [], True
    for s in states:
        h = pc_holds(s, days, budget=max(0.1, t_end - time.time()), **({'mm': mm} if mm is not None else {}))
        skipped += h['skipped']
        if h['holds'] is False:
            return {'holds': False, 'skipped': skipped}
        if h['holds'] is None:
            res = None
    return {'holds': res, 'skipped': sorted(set(skipped))}


PC_KEY_NAME = {'kill': 'シリアルキラーなどで要の人物を殺す', 'ability': '友好を積んで使う友好能力（射程へ動かす）', 'guard': '刑事の護衛',
               'revive': '異世界人の蘇生', 'servant': '従者の身代わり', 'immortal': '軍人の「主人公は死亡しない」',
               'suppress': '手先の「自身が犯人の事件を起こさない」'}
MM_KEY_NAME = {'incident': '事件の効果', 'rumor': '不穏な噂', 'tree_doctor': 'ご神木・医者の能力', 'cultist': 'カルティスト（暗躍禁止を無視）',
               'alone_death': '殺して2人きりを作る', 'reach': '補給の持ち主を動かす'}


def loop_keys(game, budget=10.0, mm_side=True):
    """ループ1の鍵（ユーザー「読み合いを分類するとしたら」2026-10-23）。
    kind: 'forced'（守れない）／'holds'（必ず守れる）／'mind'（読み合い）。読み合いなら:
    pc: 外すと「守れない」に戻る主人公の手（route_calc.LOWER_KEYS）。pc_note: 'multi'（1つずつでは戻らないが全部外すと戻る＝どれかで足りる。
        pc_any にその手1つだけで足りる手）／
        'pure'（全部外しても戻らない＝札だけの読み合い）
    mm: 外すと「必ず守れる」になる脚本家の手（UPPER_KEYS）。mm_note: 'multi'／'cards'（全部外しても守れない＝札だけで崩せる）／None（読み切れず）"""
    from engine.route_calc import LOWER_KEYS, UPPER_KEYS, forced_lower
    from selfplay.script_routes import start_states
    ss, days = start_states(game), game['days']

    def lower(use):
        return any(forced_lower(x, days, use=use) for x in ss)

    def upper(mm):
        return _holds_all(ss, days, budget, mm=mm)['holds']
    if lower(LOWER_KEYS):
        return {'kind': 'forced'}
    full = _holds_all(ss, days, budget)
    if full['holds'] and not full['skipped']:
        return {'kind': 'holds'}
    pc = [k for k in LOWER_KEYS if lower(tuple(x for x in LOWER_KEYS if x != k))]
    pc_note = None if pc else 'multi' if lower(()) else 'pure'
    pc_any = [k for k in LOWER_KEYS if not lower((k,))] if pc_note == 'multi' else []  # その手1つだけで「守れない」を崩せる手
    if not mm_side:  # エディタ: 脚本家の側（上限を何度も読み切る）は重いので省く
        return {'kind': 'mind', 'pc': pc, 'pc_note': pc_note, 'pc_any': pc_any}
    mm = [k for k in UPPER_KEYS if upper(tuple(x for x in UPPER_KEYS if x != k)) is True]
    base = upper(())
    mm_note = None if mm else 'multi' if base is True else 'cards' if base is False else None
    return {'kind': 'mind', 'pc': pc, 'pc_note': pc_note, 'pc_any': pc_any, 'mm': mm, 'mm_note': mm_note}


def route_list(s, days, set_id=None):
    """盤面 s でありえる敗北（負け筋）を近い順に。シナリオエディタ（ループ1の朝）と、人が脚本家の盤面（いまの盤面）が使う。"""
    st = sets.load(set_id)
    routes = []
    for r in sorted(enumerate_routes(s), key=lambda r: r['total']):
        if r['total'] >= INF:
            continue
        name = ROUTE.get(r['id']) or st['rules'].get(r['id'], {}).get('name') or INCIDENTS.get(r['id'], {}).get('name') or r['id']
        if r['via'] and r['id'] not in ('FRIEND',):
            name += f"（{_nm(r['via'])}）"
        routes.append({'name': name, 'goal': _names(r['goal']), 'conds': [_cond(c) for c in r['conds']],
                       'total': round(r['total'], 1), 'note': r.get('note', ''), 'plan': plan(s, r, days)})
    return routes


def analyze(sc):
    """{'errors'} か {'routes': [{name, goal, conds, total}], 'holds': True|False|None, 'holds_text', 'skipped'}。
    sc['quick'] が真なら負け筋の一覧だけ（すぐ返る。守りきれるかの読み切りは最大 HOLD_BUDGET 秒かかる）。"""
    quick = bool(isinstance(sc, dict) and sc.get('quick'))
    rep = report(sc)
    if rep['errors']:
        return {'errors': rep['errors']}
    from selfplay.script_routes import start_state
    game = to_game(normalize(sc))
    s = start_state(game)
    routes = route_list(s, game['days'], game.get('set'))
    if quick:
        return {'routes': routes, 'days': game['days'], 'incidents': [{'day': i['day'], 'name': INCIDENTS[i['id']]['name']}
                                                                       for i in game['incidents']]}
    from engine.route_calc import forced_lower
    from selfplay.script_routes import start_states
    ss = start_states(game)
    # 下限（主人公が伏せ札の中身まで見て最善に応じ、殺してよい人物をシリアルキラーなどで殺せるとしても、脚本家が必ず取れる）。
    # ループの準備は脚本家が選ぶので、どれか1つで取れれば取れる
    if any(forced_lower(x, game['days']) for x in ss):
        return {'routes': routes, 'holds': False, 'forced': True, 'skipped': 0,
                'holds_text': '脚本家が必ず取れるループです。主人公が脚本を知っていて、伏せ札の中身まで見えても守れません（ループ1が脚本家寄りなのは健全です）'}
    h = _holds_all(ss, game['days'], HOLD_BUDGET)
    if h['holds'] is None:
        text = '判定できませんでした（負け筋が多く、時間内に読み切れません）'
    elif h['holds'] and h['skipped']:
        text = '数えた負け筋の範囲では、脚本を知る主人公が守りきれます。ただし数えられなかった負け筋があり、確かめきれていません'
    elif h['holds']:
        text = ('脚本を知る主人公なら、伏せ札の中身が何であれ毎ループ守りきれます。脚本家が1ループも取れないので、易しすぎるかもしれません'
                if routes else '負け筋がありません。脚本家はループを取れません')
    else:
        text = '読み合いになるループです（目安）。脚本家に必ず取れる手順は無く、脚本を知る主人公にも、伏せ札の中身が何であれ守れる手順は見つかりません'
        k = loop_keys(game, mm_side=False)
        if k.get('kind') == 'mind':
            if k['pc']:
                text += '。主人公が守るための鍵: ' + '・'.join(PC_KEY_NAME[x] for x in k['pc'])
            elif k['pc_any']:
                text += '。主人公は次のどれか1つがあれば必勝を崩せます: ' + '・'.join(PC_KEY_NAME[x] for x in k['pc_any'])
            elif k['pc_note'] == 'multi':
                text += '。主人公は特別な手をいくつか組み合わせれば必勝を崩せます'
            elif k['pc_note'] == 'pure':
                text += '。主人公の特別な手は要りません（札だけの読み合い）'
    return {'routes': routes, 'holds': h['holds'], 'forced': False, 'holds_text': text, 'skipped': len(h['skipped'])}


def generate(seed=None, tries=10, check_holds=False):
    """エディタに読み込める形の脚本。check_holds: pc_holds が「守りきれる」と出たものは作り直す（易しすぎ。重いので既定は省き、
    エディタは生成の後に「守れるか」を裏で調べて、易しすぎなら作り直しを勧める。ユーザー「生成は早く。長いのは裏で回しつつ」2026-10-23）。"""
    from selfplay.script_routes import start_state
    rng = random.Random(seed)
    last = None
    for _ in range(tries):
        sc = _generate(rng)
        if not sc:
            continue
        out = {'title': 'ランダムなシナリオ', 'loops': sc['loops'], 'days': sc['days'], 'rules': sc['rules'], 'characters': sc['characters'],
               'roles': sc['roles'], 'incidents': sc['incidents'], 'appear': sc.get('appear', {}), 'init': dict(sc.get('start', {}))}
        if sc.get('territory'):
            out['territory'] = sc['territory']
        last = out
        if not check_holds:
            return out
        game = to_game(normalize(out))
        from selfplay.script_routes import start_states
        if _holds_all(start_states(game), game['days'], 3.0)['holds'] is not True:
            return out
    return last


if __name__ == '__main__':
    sc = generate(1)
    a = analyze(sc)
    assert sc and not report(sc)['errors'], report(sc)
    assert 'routes' in a and 'holds_text' in a, a
    print(sc['rules'], len(a['routes']), a['holds_text'])
