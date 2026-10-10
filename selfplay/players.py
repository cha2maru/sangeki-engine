"""ルールベースのプレイヤー。

RouteMastermind: 勝ち筋（engine/routes.py）までの距離が縮む手を選ぶ脚本家。
  候補の手をエンジンで試し、その後の盤面を評価する（1手先読み）。主人公の妨害は考えない（次の段階で足す）。
  評価 = Σ 1/(1+距離)（本命1本だけでなく、複数の筋を同時に進める手を好む）
"""
import copy

from engine import phases as ph
from engine.abilities import UNREFUSABLE
from engine.resolve import CARDS, card_targets, CHARS, ONCE, IllegalPlacement, copy_state, resolve_actions
from engine.routes import enumerate_routes

# 主人公の札の代わりに置く、効果の無いダミー（ボードへの友好+1）。脚本家の手だけの効果を測るため
DUMMY_PC = [{'by': 'A', 'target': 'B:HOS', 'card': 'GW1'}, {'by': 'B', 'target': 'B:SHR', 'card': 'GW1'},
            {'by': 'C', 'target': 'B:CIT', 'card': 'GW1'}]


_SCORE = {}


def score(state):
    """負け筋の近さの和。同じ盤面は覚えておく（1試合で百万回以上呼ばれる）。鍵に主人公の使用済みの札は入れない（負け筋は脚本家の分だけ読む）。"""
    key = (id(state['script']), tuple(state['used']['M']), repr({k: v for k, v in state.items() if k not in ('script', 'init', 'used')}))
    if key not in _SCORE:
        if len(_SCORE) > 200000:
            _SCORE.clear()
        _SCORE[key] = _score(state)
    return _SCORE[key]


def _score(state):
    try:
        rs = enumerate_routes(state)
    except Exception:
        return 0.0
    return sum(_weight(state, r) / (1 + r['total']) for r in rs)


def _weight(state, r):
    """筋の重み。タイムトラベラーの最終日の敗北は、友好が3に近づくほど止めやすいので軽くする（友好0→1→2 にも価値を付ける。
    そうしないと、友好3に届くまで評価が変わらず、主人公が友好を積み始めない＝s05 の全敗の原因）。"""
    if r['id'] == 'TT_LOSS' and r['via'] in state['chars']:
        return max(0.0, 3 - state['chars'][r['via']]['gw']) / 3
    return 1.0


class RouteMastermind:
    def __init__(self, rng, samples=60):
        self.rng, self.samples = rng, samples

    def _targets(self, s):
        return card_targets(s)

    def mm_cards(self, s):
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        best, best_sc = None, -1
        for _ in range(self.samples):
            ts = self.rng.sample(self._targets(s), 3)
            cards = self.rng.sample(hand, 3)
            mm = [{'by': 'M', 'target': t, 'card': c} for t, c in zip(ts, cards)]
            try:
                after, _ = resolve_actions(s, mm + DUMMY_PC)
            except IllegalPlacement:
                continue
            sc = score(after)
            if sc > best_sc:
                best, best_sc = mm, sc
        return best

    def mm_ability(self, s, actor, ability=None):
        a = s['chars'][actor]
        opts = [c for c in ph.alive_in(s, a['area'])]
        if (ability or ph.role(s, actor)) == 'KUROMAKU':
            opts.append('B:' + a['area'])
        best, best_sc = None, score(s)
        for t in opts:
            trial = copy_state(s)
            try:
                ph.mm_ability(trial, actor, t, ability)
            except IllegalPlacement:
                continue
            sc = score(trial)
            if sc > best_sc:
                best, best_sc = t, sc
        return best

    def incident_choice(self, s, inc):
        cul = inc['culprit']
        alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
        if inc['id'] == 'MURDER':
            cand = [c for c in ph.alive_in(s, s['chars'][cul]['area']) if c != cul]
            keys = [c for c in cand if ph.has_ability(s, c, 'KEY')]
            return {'target': (keys or cand)[0]} if cand else None
        if inc['id'] == 'SPREAD':
            best, best_sc = None, -1
            for p in alive:
                for i in alive:
                    if p == i:
                        continue
                    trial = copy_state(s)
                    trial['chars'][p]['par'] += 2
                    trial['chars'][i]['int'] += 1
                    sc = score(trial)
                    if sc > best_sc:
                        best, best_sc = {'par_target': p, 'int_target': i}, sc
            return best
        # 遠隔殺人・行方不明・流布・蝶の羽ばたき: 合法な選択のうち、勝ち筋の評価が最も上がるもの
        from engine.resolve import IllegalPlacement as _IP
        from selfplay.run import incident_options
        opts = incident_options(s, inc)
        if not opts:
            return None
        best, best_sc = opts[0], None
        for o in opts:
            trial = copy_state(s)
            try:
                ph.run_incident(trial, inc, o)
            except ph.LoopEnd:
                return o  # その場でループを取れる
            except _IP:
                continue
            sc = score(trial)
            if best_sc is None or sc > best_sc:
                best, best_sc = o, sc
        return best

    def killer_pick(self, s):
        return ph.killer_options(s)  # 使える勝ち手は全部使う

    def loop_setup(self, s, script):
        """ループの準備で脚本家が決めること（手先の初期エリア・学者のカウンター）。勝ち筋の評価が最も高い組を選ぶ。"""
        inits = [{'C18': a} for a in ph.AREAS if a not in CHARS['C18'].get('forbidden', [])] if 'C18' in script['init'] else [{}]
        sch = ['gw', 'par', 'int'] if 'C19' in script['init'] else [None]
        best, best_sc = None, None
        for i in inits:
            for k in sch:
                ch = {'init': i, **({'scholar': k} if k else {})}
                try:
                    after, _ = ph.loop_start(s, script['init'], ch)
                except IllegalPlacement:
                    continue
                sc = score(after)
                if best_sc is None or sc > best_sc:
                    best, best_sc = ch, sc
        return best or {}

    def cultist_options(self, s, placements):
        """カルティストが無視できる暗躍禁止（同一エリアのキャラクターかボード。範囲は移動の解決後）。"""
        cul = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True) and ph.base_role(s, c) == 'CULTIST']
        if not cul or not any(p['card'] == 'INTX' for p in placements):
            return []
        try:
            moved, _ = resolve_actions(s, placements)
        except IllegalPlacement:
            return []
        out = []
        for p in placements:
            if p['card'] != 'INTX':
                continue
            t = p['target']
            for c in cul:
                a = moved['chars'][c]['area']
                if (t.startswith('B:') and t[2:] == a) or (t in moved['chars'] and moved['chars'][t]['area'] == a):
                    out.append({'char': c, 'ability': 'CULTIST', 'target': t})
                    break
        return out

    def cultist_pick(self, s, placements):
        """暗躍禁止を無視すると勝ち筋の評価が上がるなら無視する。"""
        best, best_sc = [], None
        for o in [None] + self.cultist_options(s, placements):
            opt = [o] if o else []
            try:
                after, _ = resolve_actions(s, placements, opt)
            except IllegalPlacement:
                continue
            sc = score(after) - self._leak_cost(s, o)
            if best_sc is None or sc > best_sc:
                best, best_sc = opt, sc
        return best

    def _leak_cost(self, s, o):
        return 0.0

    def mm_extra_uses(self, s):
        """医者[友好2]（友好無視を持ち友好2以上なら脚本家も使える）と、ご神木の強制の特性。勝ち筋の評価で選ぶ（ご神木は必ず1つ）。"""
        uses = []
        d = s['chars'].get('C08')
        if d and d['alive'] and d.get('present', True) and ph.role(s, 'C08') in ph.REFUSERS and d['gw'] >= 2:
            best, best_sc = None, score(s)
            for t in ph.alive_in(s, d['area']):
                for mode in ('place', 'remove'):
                    u = {'char': 'C08', 'ability': 'DOCTOR', 'target': t, 'mode': mode}
                    trial = copy_state(s)
                    try:
                        ph.mm_phase(trial, [u] + ([self._tree_use(trial)] if ph.tree_forced(trial) else []))
                    except IllegalPlacement:
                        continue
                    sc = score(trial) - self._leak_cost_ability(s, 'C08')
                    if sc > best_sc:
                        best, best_sc = u, sc
            if best:
                uses.append(best)
        if ph.tree_forced(s):
            uses.append(self._tree_use(s))
        return uses

    def _tree_use(self, s):
        tree = s['chars']['C30']
        best, best_sc = None, None
        for t in [c for c in ph.alive_in(s, tree['area']) if c != 'C30']:
            for k in ('par', 'gw', 'int'):
                if tree.get(k, 0) < 1:
                    continue
                u = {'char': 'C30', 'ability': 'SHINBOKU', 'target': t, 'counter': k}
                trial = copy_state(s)
                try:
                    ph.mm_phase(trial, [u])
                except IllegalPlacement:
                    continue
                sc = score(trial)
                if best_sc is None or sc > best_sc:
                    best, best_sc = u, sc
        return best

    def _leak_cost_ability(self, s, cid):
        return 0.0

    def mm_rumor(self, s):
        """不穏な噂: 勝ち筋の評価が上がるボードがあれば置く。"""
        best, best_sc = None, score(s)
        for a in ph.AREAS:
            trial = copy_state(s)
            ph.rumor(trial, 'B:' + a)
            sc = score(trial)
            if sc > best_sc:
                best, best_sc = 'B:' + a, sc
        return best

    def refuse(self, s, cid):
        return ph.role(s, cid) in ('KILLER', 'KUROMAKU', 'FACTOR')  # 友好無視は常に拒否（情報を与えない）


class OracleBlocker:
    """配役は知っているが、脚本家の伏せ札の中身は知らない主人公（比較用の仮の相手）。
    伏せ札の中身を脚本家の残りの手札からサンプリングし、脚本家の評価（score）の期待値が最も小さくなる置き方を選ぶ。
    推理する主人公（公開情報だけで配役を絞る）ができるまでの、妨害の強さの目安。"""
    def __init__(self, rng, samples=40, worlds=6):
        self.rng, self.samples, self.worlds = rng, samples, worlds
        self.revealed = {}

    def _targets(self, s):
        return card_targets(s)

    def pc_cards(self, s, order, mm_targets=None):
        mm_targets = mm_targets or []
        hand_m = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        worlds = [[{'by': 'M', 'target': t, 'card': c} for t, c in zip(mm_targets, self.rng.sample(hand_m, len(mm_targets)))]
                  for _ in range(self.worlds)] if mm_targets else [[]]
        best, best_sc = None, None
        for _ in range(self.samples):
            taken, pc = set(), []
            for p in order:
                t = self.rng.choice([t for t in self._targets(s) if t not in taken])
                taken.add(t)
                hand = [c for c in CARDS['protagonist_hand'] if not (c in ONCE[p] and c in s['used'][p])]
                pc.append({'by': p, 'target': t, 'card': self.rng.choice(hand)})
            tot, n = 0.0, 0
            for w in worlds:
                if len(w) != 3:
                    continue
                try:
                    after, _ = resolve_actions(s, w + pc)
                except IllegalPlacement:
                    continue
                tot += score(after); n += 1
            if n and (best_sc is None or tot / n < best_sc):
                best, best_sc = pc, tot / n
        return best or pc

    def ability_candidates(self, s):
        return []  # 友好能力は当面使わない

    def final_guesses(self, s):
        """仮の推理: 友好能力などで明かされた役職はそのまま、それ以外はパーソンと宣言する。"""
        return [{'char': c, 'role': self.revealed.get(c, 'PERSON')} for c in s['chars']]


class PublicObserver:
    """公開される出来事だけを推理（engine/deduce.Deduction）に取り込む。主人公の推理と、評価用の理想の観測者が共用する。"""
    def __init__(self):
        self.ded = None
        self.revealed = {}
        self.culprits = {}      # 刑事[友好4] で明かされた犯人 {事件ID: キャラクター}
        self.inc_history = []   # 事件の発生・不発と判定時の不安臨界以上の生存者（犯人の推理用）

    def _ensure(self, s):
        if self.ded is None:
            from engine.deduce import Deduction
            self.ded = Deduction(list(s['chars']))

    def observe(self, s, phase, events):
        """公開される出来事だけを観測として取り込む（役職や理由は見ない）。"""
        self._ensure(s)
        for e in events:
            k = e.get('kind')
            if k == 'ability' and (e['char'], e.get('ability')) not in UNREFUSABLE:  # 主人公能力フェイズで拒否されなかった（絶対友好無視ではない）
                # 拒否されない能力（ナース・妹など、カードの文面）は手がかりにならない（数えると真の脚本を消していた: d30 のカルティストのナース）
                self.ded.observe('not_refused', char=e['char'])
            if k == 'ability' and 'reveal_role' in e:
                (c, r), = e['reveal_role'].items()
                self.ded.observe('role_revealed', char=c, role=r)
                self.revealed[c] = r
            elif k == 'incident':
                twin = lambda xs: xs + (['C32'] if 'C33' in xs and 'C32' not in xs else [])  # noqa: E731  アルバイト？はアルバイトと同じ犯人
                rec = {'id': e['id'], 'day': e.get('day'), 'occurred': e['occurred'], 'critical': twin(list(e['critical']))}
                if e['occurred']:
                    where = self._culprit_where(s, e, events)
                    if where is not None:
                        rec['culprit_in'] = twin(where)
                self.inc_history.append(rec)
            elif k == 'ability' and 'reveal_culprit' in e:
                self.culprits.update(e['reveal_culprit'])
            elif k == 'ability' and isinstance(e.get('reveal_rule_x'), str):
                self.ded.observe('rule_x_revealed', rule=e['reveal_rule_x'])
            elif k == 'par' and e.get('by') == 'X_THREAD':
                self.ded.observe('loop_start_par')
            elif k == 'refused':
                self.ded.observe('refused', char=e['char'])
                # 拒否した者は友好無視を持つ（役職は変わらない）ので、同じ試合で能力を試し直しても脚本家はまた拒否できる
                self.refused_chars = getattr(self, 'refused_chars', set()) | {e['char']}
            elif k == 'mm_ability' and e.get('role') in ('KUROMAKU', 'MISLEADER', 'X_RUMOR'):
                # 公開されるのは「どこからともなく」置かれたカウンターだけ。ボードの暗躍は不穏な噂でもありうる
                t = e['target']
                area = t[2:] if t.startswith('B:') else s['chars'][t]['area']
                present = [c for c in ph.alive_in(s, area)]
                self.ded.observe('mm_effect', counter='int' if 'int' in e else 'par', present=present,
                                 school_int=s['boards']['SCH'], rumor_possible=t.startswith('B:'))
            elif k == 'mm_ability' and e.get('role') in ('DOCTOR', 'SHINBOKU'):
                # 脚本家が医者[2]・ご神木の特性を使った＝その者は友好無視を持つ（公開）
                self.ded.observe('refused', char=e.get('actor', 'C30'))
            elif k == 'intx_ignored':  # 行動解決の後の位置で判定する（カルティストの射程は移動の解決後）
                tg = e['target']
                area = tg[2:] if tg.startswith('B:') else s['chars'][tg]['area']
                self.ded.observe('intx_ignored', present=ph.alive_in(s, area))
            elif k == 'gwx_ignored':  # 友好禁止を無視した＝タイムトラベラー（早見表 役職）
                self.ded.observe('role_revealed', char=e['target'], role='TT')
            elif k == 'nullified' and e.get('by') == 'GWX' and e['target'] in s['chars']:  # 友好禁止で止まった＝タイムトラベラーではない
                self.ded.observe('not_role', char=e['target'], roles=('TT',))
            elif k == 'no_death':
                self.ded.observe('no_death', char=e['char'])
            elif k == 'par' and e.get('by') in ('LOVERS', 'MAIN_LOVERS') and e.get('n') == 6:
                dead = [x['char'] for x in events if x.get('kind') == 'death']
                if len(dead) == 1:  # 同時に複数死んだ場合は、誰の相手かが公開情報では分からない
                    self.ded.observe('lovers_par', char=e['target'], dead=dead[0])
            elif k == 'loop_end' and (e.get('reason') == 'TT_LOSS' or e.get('reason', '').startswith('敗北（タイムトラベラー）')):
                # 以前は理由の文の書き方が合わず（エンジンは 'TT_LOSS'）、この観測が一度も働いていなかった
                self.ded.observe('tt_loss', gw={c: v['gw'] for c, v in s['chars'].items() if v['alive'] and v.get('present', True)})
            elif k == 'role_public':
                self.ded.observe('role_revealed', char=e['char'], role=e['role'])
                self.revealed[e['char']] = e['role']
            elif k == 'death' and phase in ('turn_end', 'loop_end') and ':' in e.get('cause', '') and e['cause'].split(':')[0] in ('SK', 'KILLER'):
                # 公開されるのは「ターン終了フェイズに、そのエリアで死亡した」ことだけ（原因の役職は見ない）
                v = e['char']
                sub = next((x['for'] for x in events if x.get('kind') == 'substitute' and x.get('char') == v), None)
                if sub:  # 従者の身代わり: 狙われたのはお嬢様・大物の方（従者を被害者として読むと真の脚本を消していた: d24）
                    v = sub[0]
                area = s['chars'][v]['area']
                others = [c for c, x in s['chars'].items() if c != v and x['area'] == area and (x['alive'] or c in [y['char'] for y in events if y.get('kind') == 'death'])]
                self.ded.observe('turn_end_death', victim=v, others=others, victim_int=s['chars'][v]['int'],
                                 par={c: s['chars'][c]['par'] for c in others})
            elif k == 'loop_end' and e.get('reason', '').startswith('主人公死亡') and (
                    e.get('at_phase') == 'turn_end' or (e.get('at_phase') is None and not any(x.get('kind') == 'incident' for x in events))):
                # ターン終了フェイズの主人公死亡（理由の役職は公開されないが、終わったフェイズは公開情報＝ユーザー裁定）。
                # 事件フェイズなら病院の事件なので、役職の推理には使わない
                self.ded.observe('prot_death_turn_end', counters={c: (v['int'], v['par']) for c, v in s['chars'].items() if v['alive'] and v.get('present', True)})
            elif k == 'loop_end' and 'キーパーソン' in e.get('reason', ''):
                dead = [x['char'] for x in events if x.get('kind') == 'death']
                if dead:
                    self.ded.observe('key_death', chars=dead, city_int=s['boards']['CIT'])

        if phase == 'turn_end':
            self._observe_no_sk(s, events)
        for e in events:
            if e.get('kind') == 'loop_end_check':
                # 盤面（ボードの暗躍・キャラクターの暗躍と生死・初期エリア・このループの事件）はすべて公開
                self.ded.observe('loop_end_check', loss=e['loss'], boards=dict(s['boards']),
                                 ints={c: v['int'] for c, v in s['chars'].items()},
                                 dead=[c for c, v in s['chars'].items() if not v['alive']],
                                 butterfly=any(i['id'] == 'BUTTERFLY' and i['occurred'] and i['loop'] == s['loop'] for i in s.get('incident_log', [])),
                                 init=dict(s.get('init') or {}), revealed=list(self.revealed))

    @staticmethod
    def _culprit_where(s, inc_ev, events):
        """事件の効果が公開の盤面に現れた場所から、犯人の候補を絞る（公開情報）。
        自殺→死んだ者が犯人、行方不明→動いた者が犯人、殺人事件→死んだ者と同じエリアにいた（死者以外）、
        蝶の羽ばたき→カウンターが置かれた者と同じエリアにいた（本人を含む）。"""
        i = events.index(inc_ev)
        after = []
        for x in events[i + 1:]:
            if x.get('kind') == 'incident':
                break
            after.append(x)
        iid = inc_ev['id']
        by = lambda x: str(x.get('cause', x.get('by', '')))  # noqa: E731
        if iid == 'SUICIDE':
            d = [x['char'] for x in after if x.get('kind') == 'death' and by(x).startswith('SUICIDE')]
            return d or None
        if iid == 'MISSING':
            m = [x['char'] for x in after if x.get('kind') == 'move' and x.get('by') == 'MISSING']
            return m or None
        if iid == 'MURDER':
            d = [x['char'] for x in after if x.get('kind') == 'death' and by(x).startswith('MURDER')]
            if d:
                # 身代わりが死んだときは、狙われた元の被害者だけが犯人ではない（身代わり本人は犯人でありうる）
                sub = [x['for'] for x in after if x.get('kind') == 'substitute' and x.get('char') == d[0]]
                victims = set(sub[0]) if sub else {d[0]}
                area = s['chars'][d[0]]['area']
                return [c for c, v in s['chars'].items() if c not in victims and v.get('present', True) and v['area'] == area]
        if iid == 'BUTTERFLY':
            t = [x['target'] for x in after if x.get('by') == 'BUTTERFLY' and x.get('target') in s['chars']]
            if t:
                area = s['chars'][t[0]]['area']
                return [c for c, v in s['chars'].items() if v.get('present', True) and v['area'] == area]
        return None

    def _observe_no_sk(self, s, events):
        """シリアルキラーの能力は【強制】。ターン終了時に2人きりのエリアで、どちらも死なず護衛も減らなければ、どちらもシリアルキラーではない。
        判定の時点の顔ぶれ = いま生存している者 + このフェイズで死んだ者（キラーの任意能力はシリアルキラーの後に解決される）。"""
        # タイムトラベラーは死亡しない（no_death）。シリアルキラーの能力は働いているので、関わった者に数える
        # （数えないと「どちらもシリアルキラーでない」と誤って読み、真の脚本を消していた: d12 で推理が空になった）
        involved = {e['char'] for e in events if e.get('kind') in ('death', 'guarded', 'no_death')}
        died = {e['char'] for e in events if e.get('kind') == 'death'}
        by_area = {}
        for c, v in s['chars'].items():
            if (v['alive'] and v.get('present', True)) or c in died:
                by_area.setdefault(v['area'], []).append(c)
        for area, cs in by_area.items():
            if len(cs) == 2 and not (set(cs) & involved):
                self.ded.observe('no_sk', chars=cs, par={c: s['chars'][c]['par'] for c in cs})


class DeductiveProtagonist(OracleBlocker, PublicObserver):
    """最後の戦いの宣言を、公開情報だけからの推理（engine/deduce.py）で行う主人公。
    情報を得る友好能力（サラリーマン[3]・巫女[5]・情報屋[5]）を優先して使う。
    注意: 札の置き方（妨害）はまだ OracleBlocker（配役を知っている）のまま。推理で妨害する形は次の段階。"""
    INFO = {('C06', 0), ('C04', 1), ('C07', 0), ('C05', 0)}

    def __init__(self, rng, **kw):
        OracleBlocker.__init__(self, rng, **kw)
        PublicObserver.__init__(self)

    def ability_candidates(self, s):
        from engine import abilities as ab
        out = []
        for (cid, idx) in self.INFO:
            if cid not in s['chars']:
                continue
            args = [None]
            if (cid, idx) == ('C04', 1):
                args = [{'target': t} for t in ph.alive_in(s, s['chars'][cid]['area']) if t != cid]
            if cid == 'C07':
                args = [{'declare': 'X_CIRCLE'}]
            if (cid, idx) == ('C05', 0):  # このループで発生した事件の犯人を知る（まだ知らない事件）
                args = [{'incident': i['id']} for i in s.get('incident_log', [])
                        if i['loop'] == s['loop'] and i['occurred'] and i['id'] not in self.culprits]
            for a in args:
                try:
                    ab.check_declaration(s, cid, idx, a)
                    out.append((cid, idx, a))
                except IllegalPlacement:
                    pass
        return out

    def final_guesses(self, s):
        self._ensure(s)
        return self.ded.final_guesses()


# 情報を得る友好能力（キャラクター, 番号）: 必要な友好。主人公が友好を積む価値の目安に使う
INFO_ABILITIES = {('C06', 0): 3, ('C04', 1): 5, ('C07', 0): 5, ('C05', 0): 4,
                  # 第1陣・第2陣（役職・犯人を知る能力）
                  ('C11', 0): 3, ('C13', 0): 3, ('C16', 0): 5, ('C21', 1): 5, ('C23', 1): 4, ('C28', 0): 3, ('C29', 1): 4, ('C33', 0): 3}


def info_progress(s, used_loop=()):
    """情報を得る友好能力に、どれだけ近づいたか（0〜能力の数）。★で使用済みのものは数えない。"""
    v = 0.0
    for (cid, idx), need in INFO_ABILITIES.items():
        ch = s['chars'].get(cid)
        if ch is None or not ch['alive'] or f'{cid}#{idx}' in used_loop:
            continue
        v += min(ch['gw'], need) / need
    return v


class DeductiveBlocker(DeductiveProtagonist):
    """配役を知らない主人公。妨害も推理で行う: 推理で残った仮説から配役とルールをいくつか引き（重みつき）、
    事件の犯人は生存者から仮に引いて、仮説ごとの脚本家の評価（score）の平均が最も小さい置き方を選ぶ。
    これで主人公の判断はすべて公開情報だけに基づく。"""
    def __init__(self, rng, hyps=12, info_weight=0.3, **kw):
        super().__init__(rng, **kw)
        self.nh = hyps
        self.info_weight = info_weight  # 妨害（脚本家の評価を下げる）と、情報を得る友好能力に近づくことの釣り合い

    def _worlds(self, s):
        self._ensure(s)
        picks = self.ded.sample(self.rng, self.nh)
        if not picks:
            # 推理が矛盾した（本来は起きない）。本当の脚本は使わず、役職の無い仮の脚本で妨害する
            return [{'rules': [], 'roles': {}, 'incidents': list(s['script']['incidents'])}]
        alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
        # 犯人は、推理した候補（事件の発生・不発、刑事[4]、犯人の相異＝deduce.incident_candidates）から引く。
        # 以前は生存者から無作為に引いていて、ループ1で犯人が割れても次のループの守りに使えていなかった
        from engine.deduce import incident_candidates
        cands = incident_candidates(list(s['chars']), self.inc_history, self.culprits)
        scripts = []
        for combo, roles in picks:
            incs, taken = [], set()
            for i in s['script']['incidents']:  # 事件の名前と日付は公開
                key = f"{i['id']}@{i['day']}"
                pool = [c for c in sorted(cands.get(key, cands.get(i['id'], alive))) if c not in taken] or [c for c in alive if c not in taken] or alive
                cul = self.rng.choice(pool)
                taken.add(cul)
                incs.append(dict(i, culprit=cul))
            scripts.append({'rules': list(combo), 'roles': dict(roles), 'incidents': incs})
        return scripts

    def pc_cards(self, s, order, mm_targets=None):
        scripts = self._worlds(s)
        mm_targets = mm_targets or []
        hand_m = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        best, best_sc, pc = None, None, None
        # 狙いを絞った候補: 伏せ札の対象と、仮説の世界で近い勝ち筋に関わるキャラクターに、止める札を置く
        focus = list(dict.fromkeys(list(mm_targets) + [x for sc in scripts for r in enumerate_routes(dict(s, script=sc))[:3]
                                                        for x in ([r['via']] if r['via'] else []) + [c['detail'][0] for c in r['conds']
                                                        if isinstance(c['detail'][0], str) and c['detail'][0].startswith('C')]]))
        focus = [f for f in focus if f in s['chars'] and s['chars'][f]['alive'] and s['chars'][f].get('present', True) and f != 'C20'
                 or str(f).startswith('B:')]
        STOP = ('MVX', 'PAR-', 'INTX', 'MV_V', 'MV_H')

        def gen(focused):
            taken, out = set(), []
            for p in order:
                hand = [c for c in CARDS['protagonist_hand'] if not (c in ONCE[p] and c in s['used'][p])]
                pool = [t for t in (focus if focused else self._targets(s)) if t not in taken] or [t for t in self._targets(s) if t not in taken]
                t = self.rng.choice(pool)
                taken.add(t)
                stops = [c for c in STOP if c in hand]
                out.append({'by': p, 'target': t, 'card': self.rng.choice(stops if focused and stops else hand)})
            return out

        for k in range(self.samples + 40):
            pc = gen(focused=k < 40)
            vals = []
            for sc in scripts:
                w = [{'by': 'M', 'target': t, 'card': c} for t, c in zip(mm_targets, self.rng.sample(hand_m, len(mm_targets)))]
                try:
                    after, _ = resolve_actions(dict(s, script=sc), w + pc)
                except IllegalPlacement:
                    continue
                vals.append(score(after) - self.info_weight * info_progress(after, s.get('ability_used_loop', [])))
            if not vals:
                continue
            tot, n = (sum(vals) / len(vals) + max(vals)) / 2, 1  # 平均と最悪の中間（悪い側に備える）
            if n and (best_sc is None or tot / n < best_sc):
                best, best_sc = pc, tot / n
        return best or pc


class HiddenMastermind(RouteMastermind):
    """情報を隠す脚本家（Claude が脚本家を担当した試合の学び）。
    - クロマク・ミスリーダーの能力は、その場で勝ち筋が成立する（距離0になる）ときだけ使う（位置を漏らさない）
    - 成功すると配役が明かされる筋（キラー）より、明かしにくい筋（妄想拡大ウイルスのシリアルキラー）を重く見る"""
    WEIGHT = {'KILLER_KEY': 0.5, 'KILLER_PROT': 0.5, 'SK_KILL': 1.5}

    def _score(self, st):
        try:
            rs = enumerate_routes(st)
        except Exception:
            return 0.0
        return sum(self.WEIGHT.get(r['id'], 1.0) / (1 + r['total']) for r in rs)

    def mm_cards(self, s):
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        best, best_sc = None, -1
        for _ in range(self.samples):
            ts = self.rng.sample(self._targets(s), 3)
            cards = self.rng.sample(hand, 3)
            mm = [{'by': 'M', 'target': t, 'card': c} for t, c in zip(ts, cards)]
            try:
                after, _ = resolve_actions(s, mm + DUMMY_PC)
            except IllegalPlacement:
                continue
            sc = self._score(after)
            if sc > best_sc:
                best, best_sc = mm, sc
        return best

    def mm_ability(self, s, actor, ability=None):
        a = s['chars'][actor]
        opts = [c for c in ph.alive_in(s, a['area'])]
        if (ability or ph.role(s, actor)) == 'KUROMAKU':
            opts.append('B:' + a['area'])
        for t in opts:
            trial = copy_state(s)
            try:
                ph.mm_ability(trial, actor, t, ability)
            except IllegalPlacement:
                continue
            if min((r['total'] for r in enumerate_routes(trial)), default=99) == 0:
                return t  # 決め手になるときだけ使う
        return None
