"""勝ち筋（脚本家から見た主人公の敗北への道筋）の列挙と、残りの距離。

data/effects.json（早見表の文面を条件と効果に構造化したもの）を、脚本（配役・事件・ルール）に当てはめ、
ゴール（主人公の敗北）から逆向きにたどって道筋を作る。学習は使わない。
距離は「条件を満たすまでに足りない量」の目安（カウンターの不足数、移動の回数、事件までの日数など）で、
主人公の妨害は考えない。特徴量として使うためのもの。
"""
import json
import os

from .phases import base_role, role
from .resolve import CHARS, GRID

EFFECTS = json.load(open(os.path.join(os.path.dirname(__file__), 'data', 'effects.json'), encoding='utf-8'))
INF = 99


def _rc(area):
    for r, row in enumerate(GRID):
        if area in row:
            return r, row.index(area)


def move_steps(frm, to, diag_available):
    """移動札で frm→to に行く最少の日数（1日に1回動く）。斜めは脚本家の移動斜め（1ループ1回）が残っていれば1。"""
    if frm is None or to is None:  # 盤面にいない（登場前など）
        return INF
    if frm == to:
        return 0
    (r1, c1), (r2, c2) = _rc(frm), _rc(to)
    if r1 != r2 and c1 != c2:
        return 1 if diag_available else 2
    return 1


def _can_enter(state, cid, area):
    return area not in CHARS[cid]['forbidden'] or cid in state.get('unbound', [])


def colocate(state, a, b):
    """a と b を同じエリアにするのに要る移動の日数と、そのエリア。どちらを動かしてもよい。"""
    diag = 'MV_D' not in state['used']['M']
    A, B = state['chars'][a]['area'], state['chars'][b]['area']
    best = (INF, None)
    for mover, frm, to in ((a, A, B), (b, B, A)):
        if _can_enter(state, mover, to):
            best = min(best, (move_steps(frm, to, diag), to))
    return best


def alone_with(state, a, b):
    """a と b が同じエリアにいて、ほかの生存者がいない状態までの距離（移動＋追い出す人数）。
    どちらのエリアで2人きりにするかは、移動と追い出しの合計が小さい方（colocate の移動だけの比較では、
    同じ日数のとき混んだ方のエリアを選ぶことがあった）。"""
    diag = 'MV_D' not in state['used']['M']
    best = INF
    for mover, stay in ((a, b), (b, a)):
        frm, area = state['chars'][mover]['area'], state['chars'][stay]['area']
        if not _can_enter(state, mover, area):
            continue
        others = [c for c, v in state['chars'].items() if v['alive'] and v.get('present', True) and v['area'] == area and c not in (a, b)]
        best = min(best, move_steps(frm, area, diag) + len(others))
    return best


def _cond(kind, detail, deficit):
    return {'kind': kind, 'detail': detail, 'deficit': deficit}


def enumerate_routes(state, days_in_loop=None):
    """主人公の敗北への道筋を列挙する。返り値は道筋のリスト（total が小さいほど近い）。"""
    sc = state['script']
    chars = state['chars']
    br = {c: base_role(state, c) for c in chars}  # 役職はこの中で何度も引くので1回だけ（1試合で数千万回呼ばれていた）
    alive = {c for c, v in chars.items() if v['alive'] and v.get('present', True)}
    keys = [c for c in chars if br[c] == 'KEY']
    # ファクターは都市に暗躍2以上でキーパーソンの能力を得る（早見表）。その死亡も敗北になるので、条件つきの「キー」として扱う
    fkeys = [c for c in chars if br[c] == 'FACTOR' and 'X_FACTOR' in sc['rules']]
    keys = keys + fkeys

    # フレンドの死亡はループ終了時の敗北（早見表 フレンド）。殺す筋はキーパーソンと同じ形で数える（ループ中ならいつ死んでもよい）
    friends = [c for c in chars if br[c] == 'FRIEND']
    victims = keys + [f for f in friends if f not in keys]

    def keycond(k):
        return [_cond('board', ('CIT', 2), max(0, 2 - state['boards']['CIT']))] if k in fkeys else []
    routes = []

    keyset = set(keys)

    def add(rid, via, goal, conds, note='', end=None):
        total = sum(min(c['deficit'], INF) for c in conds)
        # end: ループ終了時に負けが決まる筋か（フレンドの死はループの途中で起きても、負けはループ終了時。ループは続き、主人公は情報を取り続けられる）
        routes.append({'id': rid, 'via': via, 'goal': goal, 'conds': conds, 'total': min(total, INF), 'note': note, 'end': end})

    # 従者【特性】同一エリアのお嬢様・大物（従者[友好4] で足した者）が死亡する場合、代わりに死亡する（phases.kill_many）。
    # 従者がフレンド・キーパーソンなら、お嬢様・大物を殺す事件は従者の死＝負けの筋になる（d16 のループ1・2の負け。敵対的レビュー 2026-10-09）
    sv = chars.get('C34')
    servant_victim = sv is not None and 'C34' in alive and 'C34' in victims
    guarded = [c for c in ('C03', 'C16', *state.get('servant_targets', [])) if c in alive and c != 'C34'] if servant_victim else []

    def is_end(k):
        return k not in keyset  # キーパーソンの死はその場でループが終わる。フレンドだけの死はループ終了時

    # 役職の能力（キラー・シリアルキラー・メインラバーズ）
    for cid in [c for c in chars if c in alive]:  # 並びを固定する（集合の順は実行ごとに変わる）
        r = br[cid]
        for e in EFFECTS['roles'].get(r, []):
            if e.get('effect') == 'kill' and e['target'] == 'KEY':
                for k in keys:
                    if k not in alive:
                        continue
                    add(e['id'], cid, f'{k} 死亡（キーパーソン）', keycond(k) + [
                        _cond('same_area', (cid, k), colocate(state, cid, k)[0]),
                        _cond('counter', (k, 'int', 2), max(0, 2 - chars[k]['int']))])
            elif e.get('effect') == 'protagonists_die':
                conds = [_cond('counter', (cid, kind, n), max(0, n - chars[cid][kind]))
                         for _, _, kind, n in e['conds']]
                add(e['id'], cid, '主人公死亡', conds)

    # シリアルキラー（配役、または妄想拡大ウイルスでパーソンが変わるもの）がキーパーソンと2人きり
    sks = [c for c in chars if c in alive and br[c] == 'SK']
    virus = 'X_VIRUS' in sc['rules']
    persons = [c for c in chars if c in alive and br[c] == 'PERSON'] if virus else []
    for s in sks + persons:
        for k in victims:
            if k not in alive or k == s:
                continue
            conds = keycond(k) + [_cond('alone_with', (s, k), alone_with(state, s, k))]
            if s in persons:
                conds.insert(0, _cond('counter', (s, 'par', 3), max(0, 3 - chars[s]['par'])))
            add('SK_KILL', s, f'{k} 死亡（キーパーソン）', conds, '妄想拡大ウイルス' if s in persons else '')

    # 事件（殺人事件・自殺・遠隔殺人・病院の事件）
    for inc in sc['incidents']:
        cul = inc['culprit']
        days_left = inc['day'] - state['day']
        if days_left < 0 or cul not in alive:
            continue
        base = [_cond('days', ('事件', inc['id'], inc['day']), days_left),
                _cond('counter', (cul, 'par', CHARS[cul]['limit']), max(0, CHARS[cul]['limit'] - chars[cul]['par']))]
        sub = lambda g: _cond('same_area', (g, 'C34'), colocate(state, g, 'C34')[0])  # noqa: E731  身代わりの条件
        if inc['id'] == 'MURDER':
            for k in victims:
                if k in alive and k != cul:
                    add('MURDER', cul, f'{k} 死亡（キーパーソン）', base + keycond(k) + [_cond('same_area', (cul, k), colocate(state, cul, k)[0])], end=is_end(k))
            for g in guarded:
                if g != cul:
                    add('MURDER', cul, f'C34 死亡（{g} の身代わり）', base + [_cond('same_area', (cul, g), colocate(state, cul, g)[0]), sub(g)],
                        end=is_end('C34'))
        elif inc['id'] == 'SUICIDE':
            if cul in victims:  # ファクターは都市の暗躍2でキーパーソンの能力を得たときだけ負け（keycond。以前は条件なしで負けにしていた）
                add('SUICIDE', cul, f'{cul} 死亡（キーパーソン）', base + keycond(cul), end=is_end(cul))
            if cul in guarded:
                add('SUICIDE', cul, f'C34 死亡（{cul} の身代わり）', base + [sub(cul)], end=is_end('C34'))
        elif inc['id'] == 'REMOTE':
            for k in victims:
                if k in alive:
                    add('REMOTE', cul, f'{k} 死亡（キーパーソン）', base + keycond(k) + [_cond('counter', (k, 'int', 2), max(0, 2 - chars[k]['int']))],
                        end=is_end(k))
            for g in guarded:
                add('REMOTE', cul, f'C34 死亡（{g} の身代わり）', base + [_cond('counter', (g, 'int', 2), max(0, 2 - chars[g]['int'])), sub(g)],
                    end=is_end('C34'))
        elif inc['id'] == 'BUTTERFLY' and 'Y_FUTURE' in sc['rules']:
            add('Y_FUTURE', cul, 'ループ終了時の敗北（蝶の羽ばたき）', base)
        elif inc['id'] == 'HOSPITAL':
            add('HOSPITAL_PROT', cul, '主人公死亡', base + [_cond('board', ('HOS', 2), max(0, 2 - state['boards']['HOS']))])
            # 病院に暗躍1以上→病院にいる全員死亡（キーが病院にいればキーの死亡）
            diag = 'MV_D' not in state['used']['M']
            for k in victims:
                if k in alive and _can_enter(state, k, 'HOS'):
                    add('HOSPITAL_KILL', cul, f'{k} 死亡（キーパーソン）', base + keycond(k) + [
                        _cond('board', ('HOS', 1), max(0, 1 - state['boards']['HOS'])),
                        _cond('in_area', (k, 'HOS'), move_steps(chars[k]['area'], 'HOS', diag))])

    # タイムトラベラー: 最終日のターン終了フェイズに友好2以下なら敗北（任意）。主人公は友好を3以上にすれば止められる
    days = sc.get('days')
    if days:
        for c in chars:
            if c in alive and br[c] == 'TT':
                add('TT_LOSS', c, '敗北（タイムトラベラー）', [_cond('days', ('最終日', days), days - state['day']),
                                                              _cond('counter_max', (c, 'gw', 2), 0 if chars[c]['gw'] <= 2 else INF)])
    # 既に死亡しているフレンド、このループで発生した蝶の羽ばたき（未来改変プラン）: ループ終了時の敗北が確定している
    for f in friends:
        if f not in alive:
            add('FRIEND', f, 'ループ終了時の敗北（フレンド死亡）', [])
    if 'Y_FUTURE' in sc['rules'] and any(i['loop'] == state['loop'] and i['id'] == 'BUTTERFLY' and i['occurred']
                                         for i in state.get('incident_log', [])):
        add('Y_FUTURE', None, 'ループ終了時の敗北（蝶の羽ばたき）', [])
    # ルールYのループ終了時の敗北条件
    init = state.get('init') or {}
    for rid in sc['rules']:
        e = EFFECTS['rules'].get(rid)
        if not e:
            continue
        for c in e['conds']:
            if c[0] == 'board':
                areas = [c[1]]
                if c[1] == 'WITCH_START':  # 巨大時限爆弾X: ウィッチの初期エリア（生死を問わない＝ユーザー裁定）
                    areas = [init[w] for w in chars if br[w] == 'WITCH' and init.get(w)]
                for a in areas:
                    add(rid, None, 'ループ終了時の敗北', [_cond('board', (a, c[2]), max(0, c[2] - state['boards'].get(a, 0)))])
                    # 事件（邪気の汚染・行方不明）でボードに暗躍を置く筋。暗躍禁止では止まらず、犯人の不安で止める（目隠し4本目:
                    # 神社の暗躍を5日止められた脚本家が、邪気の汚染の犯人の不安を上げなかった）
                    for inc in sc['incidents']:
                        cul, (ib, n) = inc['culprit'], INC_INT_BOARD.get(inc['id'], ('-', 0))
                        if n and ib in (None, a) and cul in alive and inc['day'] >= state['day']:
                            rest = _cond('board', (a, c[2]), max(0, c[2] - n - state['boards'].get(a, 0)))
                            rest['fixed'] = True  # この事件の分はもう数えた（止められない供給で二重に数えない）
                            add(rid, cul, 'ループ終了時の敗北（事件）', [
                                _cond('days', ('事件', inc['id'], inc['day']), inc['day'] - state['day']),
                                _cond('counter', (cul, 'par', CHARS[cul]['limit']), max(0, CHARS[cul]['limit'] - chars[cul]['par'])), rest])
            elif c[0] == 'counter' and c[1] == 'KEY':
                for k in [k for k in keys if br[k] == 'KEY']:  # 契約: 役職キーパーソンだけ（ファクターは数えない＝ユーザー裁定）
                    add(rid, None, 'ループ終了時の敗北', [_cond('counter', (k, 'int', c[3]), max(0, c[3] - chars[k]['int']))])
    _apply_unstoppable(state, routes, days_in_loop or sc.get('days'))
    return sorted(routes, key=lambda r: r['total'])


# 行動フェイズより後のカウンターの増減は、主人公の札では止められない（ユーザーの観点）。
# 脚本家能力（クロマク・ミスリーダー・不穏な噂）と事件の効果（邪気の汚染・行方不明・不安拡大・蝶の羽ばたき）で、
# 締め切りまでに補える分を、条件の不足から差し引く。事件で補うなら、その犯人の不安の不足を費用として足す。
INC_INT_BOARD = {'CORRUPT': ('SHR', 2), 'MISSING': (None, 1)}  # (ボード（None は任意）, 暗躍の数)
SUPPLY_CREDIT = 0.5


def _unstoppable_supply(state, target, counter, n_days, deadline_day):
    """(能力で補える数, [(事件で補える数, 犯人の不安の不足, 事件ID)])"""
    from .phases import mm_areas
    chars = state['chars']
    alive = [c for c, v in chars.items() if v['alive'] and v.get('present', True)]
    board = target[2:] if isinstance(target, str) and target.startswith('B:') else None
    area = board or (chars[target]['area'] if target in chars else None)
    ab = 0
    if counter == 'int':
        for c in alive:
            if base_role(state, c) == 'KUROMAKU' and area in mm_areas(state, c):
                ab += n_days
            elif (base_role(state, c) == 'KUROMAKU' and board and _can_enter(state, c, board)
                  and move_steps(chars[c]['area'], board, 'MV_D' not in state['used']['M']) == 1):
                # 1回の移動で要のボードに入れるクロマク: 移った翌日から毎日置ける（割り引いて数える）。
                # 数えないと、クロマクを動かす手に価値が付かない（Claude が脚本家の点検の試合 s01_seal: クロマクを神社へ動かして勝った）
                ab += 0.5 * max(0, n_days - 1)
        if board and 'X_RUMOR' in state['script']['rules'] and 'X_RUMOR' not in state.get('ability_used_loop', []):
            ab += 1
    elif counter == 'par' and not board:
        for c in alive:
            r = base_role(state, c)
            if (r == 'MISLEADER' or (r == 'FACTOR' and state['boards'].get('SCH', 0) >= 2)) and area in mm_areas(state, c):
                ab += n_days
            elif (r == 'MISLEADER' and target in chars and _can_enter(state, target, chars[c]['area'])
                  and move_steps(area, chars[c]['area'], 'MV_D' not in state['used']['M']) == 1):
                # 1回の移動でミスリーダーのエリアに入れる対象: 移った翌日から毎日不安+1（割り引いて数える）。
                # Claude が脚本家の点検の試合 s02_contract: 殺人事件の犯人をミスリーダーの所へ運び、臨界にしてから被害者の所へ戻した
                ab += 0.5 * max(0, n_days - 1)
    incs = []
    for inc in state['script']['incidents']:
        cul = inc['culprit']
        if not (state['day'] <= inc['day'] <= deadline_day) or cul not in alive:
            continue
        need = max(0, CHARS[cul]['limit'] - chars[cul]['par'])
        iid = inc['id']
        n = 0
        if counter == 'int' and board and iid in INC_INT_BOARD and INC_INT_BOARD[iid][0] in (None, board):
            n = INC_INT_BOARD[iid][1]
        elif counter == 'int' and not board and iid == 'SPREAD':
            n = 1
        elif counter == 'par' and not board and iid == 'SPREAD' and target != cul:
            n = 2
        elif not board and iid == 'BUTTERFLY' and target != cul and chars[target]['area'] == chars[cul]['area']:
            n = 1
        if n:
            incs.append((n, need, iid, cul))
    return ab, incs


UNSTOPPABLE = os.environ.get('SANGEKI_NO_SUPPLY') != '1'  # 比較用: 1 なら止められない供給を数えない（旧い数え方）


def _apply_unstoppable(state, routes, days):
    if not days or not UNSTOPPABLE:
        return
    for r in routes:
        dl = next((c['deficit'] for c in r['conds'] if c['kind'] == 'days'), days - state['day'])
        extra = []
        for c in r['conds']:
            if c['deficit'] <= 0 or c['deficit'] >= INF or c.get('fixed'):
                continue
            if c['kind'] == 'counter' and c['detail'][1] in ('int', 'par'):
                target, counter = c['detail'][0], c['detail'][1]
            elif c['kind'] == 'board':
                target, counter = 'B:' + c['detail'][0], 'int'
            else:
                continue
            ab, incs = _unstoppable_supply(state, target, counter, dl + 1, state['day'] + dl)
            # 見込みの供給は半分だけ効くとする（能力は1日1つの対象にしか使えず、駒も動かされうる）。
            # 満額で差し引くと、今日能力を使っても距離が縮まらず、脚本家が能力を先送りする
            d = c['deficit']
            best = (d - SUPPLY_CREDIT * min(d, ab), 0, None, None)
            for n, need, iid, cul in incs:
                cand = (d - SUPPLY_CREDIT * min(d, ab + n), need, iid, cul)
                if cand[0] + cand[1] < best[0] + best[1]:
                    best = cand
            if best[0] + best[1] < c['deficit']:
                c['supply'] = {'ability': ab, 'incident': best[2], 'before': c['deficit']}
                c['deficit'] = best[0]
                if best[2]:
                    extra.append(_cond('incident_par', (best[2], best[3]), best[1]))  # (事件, 犯人): 連鎖の前の事件の犯人
        r['conds'] += extra
        r['total'] = min(sum(min(c['deficit'], INF) for c in r['conds']), INF)


def route_features(state):
    """学習用の特徴量: 道筋の種類ごとの最小距離。"""
    feats = {}
    for r in enumerate_routes(state):
        k = f"dist_{r['id']}"
        feats[k] = min(feats.get(k, INF), r['total'])
    feats['dist_min'] = min(feats.values()) if feats else INF
    return feats
