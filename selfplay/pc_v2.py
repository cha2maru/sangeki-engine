"""札の置き方を改良した主人公（plan/engine-smarter.md 段C）。DeductiveBlocker との違い:

- C1 伏せ札の置き場所を読む
  - 推理から仮説を多めに引く。そのうえで、その日の伏せ札の置き場所が、仮説の世界で近い勝ち筋に関わる対象と重なるほど、
    その仮説を重く見る。脚本家は勝ち筋に関わる対象に札を置きやすい、という読み。
  - 置き場所の履歴（このループで何度札が置かれたか）も同じように重みに入れる。
- 伏せ札の対象に止める札を置く候補を、漏れなく作る（対象×止める札の全組）。
- 無駄な札を避ける
  - 置いても盤面が変わらない札（ボードへの移動禁止など）には小さな費用を付ける。
  - 1ループ1回の札（移動禁止・不安−1・友好+2）には温存の費用 hold を付ける。
    ただし、使うと評価が hold 以上に良くなるなら使う。
"""
import math
import copy

from engine.resolve import CARDS, ONCE, IllegalPlacement, copy_state, resolve_actions
from engine.routes import enumerate_routes
from selfplay.players import DeductiveBlocker, _weight, info_progress, score


def score_cut(state, known=None, sc=None):
    """score と同じ負け筋の近さの和を、ループを途中で終わらせる筋（キーパーソン・主人公の死）だけで（ループ終了時の筋は数えない）。
    known(r, sc) があれば、主人公が知っている筋だけ。"""
    from selfplay.search_mm import is_loop_end
    try:
        rs = enumerate_routes(state)
    except Exception:
        return 0.0
    return sum(_weight(state, r) / (1 + r['total']) for r in rs if not is_loop_end(r) and (known is None or known(r, sc)))

_AFTER = {}


def after_incidents(after):
    """その日の事件を、脚本家が最も得をする選択で解決した盤面（ループが終わる事件はその場の盤面のまま、距離0 として score が高く出る）。
    同じ盤面（候補の手の多くは同じ盤面になる）は覚えておき、写しを返す（1試合で約10万回呼ばれ、時間の半分を占めていた）。"""
    # 鍵に主人公の使用済みの札は入れない（この先読み＝脚本家能力・事件・負け筋の評価が読むのは脚本家の分だけ。書き換えもしない）
    key = (id(after['script']), after['used']['M'], repr({k: v for k, v in after.items() if k not in ('script', 'init', 'used')}))
    key = (key[0], tuple(key[1]), key[2])
    if key not in _AFTER:
        if len(_AFTER) > 50000:
            _AFTER.clear()
        _AFTER[key] = _after_incidents(after)
    out = copy_state(_AFTER[key])
    out['used'] = {p: list(v) for p, v in after['used'].items()}
    return out


def _after_incidents(after):
    from engine import phases as ph
    from selfplay.players import RouteMastermind
    out = copy_state(after)
    # 事件の前に脚本家能力フェイズ（クロマク・ミスリーダーが行動解決の後に暗躍・不安を足す。定石「ミスリーダーで事件を無理やり起こす」）。
    # 以前はここを飛ばしていて、犯人を臨界の1つ手前に抑えても事件が起きる筋が見えていなかった（次の一手問題 2026-10-07）
    for actor, abl in [(c, a) for c in out['chars'] for a in ('KUROMAKU', 'MISLEADER')
                       if out['chars'][c]['alive'] and ph.has_ability(out, c, a)]:
        try:
            t = RouteMastermind.mm_ability(None, out, actor, abl)
            if t:
                ph.mm_ability(out, actor, t, abl)
        except IllegalPlacement:
            continue
    for inc in out['script']['incidents']:
        if inc['day'] != out['day'] or not ph.incident_occurs(out, inc):
            continue
        ch = RouteMastermind.incident_choice(None, out, inc)
        if ch is None and inc['id'] in ('SPREAD', 'MURDER', 'MISSING', 'REMOTE', 'RUMOR_SPREAD'):
            continue  # 置き先が選べない（対象がいない・評価が上がらない）事件は、この先読みでは効果なしとみる
        if ph.acting_culprit(out, inc) == 'C29':  # 教祖が犯人の事件は効果を2回解決する（選択は2要素の列。先読みでは同じ選び方を2回）
            ch = [ch, ch]
        try:
            ph.run_incident(out, inc, ch)
        except ph.LoopEnd as e:
            out['_loss'] = e.loss  # 事件でループが終わった（主人公の負けか）。prio の勝ち目に使う
            break
        except IllegalPlacement:
            break
    return out


NULLED_BY = {'PAR+': 'PARX', 'PAR-': 'PARX', 'GW1': 'GWX', 'GW2': 'GWX'}  # 主人公の札 → それを打ち消す脚本家の札
STOP = ('MVX', 'PAR-', 'INTX', 'MV_V', 'MV_H')
NULL_COST = 0.02


def involved(s, sc, top=4):
    """仮説の世界 sc で、近い勝ち筋に関わるキャラクターとボード。"""
    out = set()
    try:
        rs = enumerate_routes(dict(s, script=sc))[:top]
    except Exception:
        return out
    for r in rs:
        if r['via']:
            out.add(r['via'])
        for c in r['conds']:
            d = c['detail'][0]
            if isinstance(d, str) and d.startswith('C'):
                out.add(d)
            if c['kind'] == 'board':
                out.add('B:' + str(d))
    return out


DEFENSE_ABILITIES = {('C01', 0): 2, ('C02', 0): 2, ('C08', 0): 2, ('C17', 0): 2, ('C23', 0): 3, ('C04', 0): 3,
                     ('C13', 1): 5, ('C24', 0): 2, ('C21', 0): 2,
                     ('C19', 0): 3}  # 不安・暗躍を取り除く友好能力と必要な友好（engine/abilities.py）


def defense_progress(s):
    v = 0.0
    for (cid, _), need in DEFENSE_ABILITIES.items():
        ch = s['chars'].get(cid)
        if ch and ch['alive'] and ch.get('present', True):
            v += min(ch['gw'], need) / need
    return v


TARGET_ROLE = {('C04', 1): 'any_here', ('C21', 1): 'dead', ('C23', 1): 'student_here', ('C16', 0): 'territory', ('C29', 1): 'here',
               ('C11', 0): 'self', ('C33', 0): 'self', ('C06', 0): 'self', ('C28', 0): 'self'}


def info_progress_targeted(s, ent, used_loop=()):
    """情報の友好能力への友好の近さを、調べられる相手の不確かさ（ビット、上限3で割る）で重み付けした和。"""
    from selfplay.players import INFO_ABILITIES
    from engine.resolve import CHARS as _C
    v = 0.0
    for (cid, idx), need in INFO_ABILITIES.items():
        ch = s['chars'].get(cid)
        if ch is None or not ch['alive'] or not ch.get('present', True) or f'{cid}#{idx}' in used_loop:
            continue
        kind = TARGET_ROLE.get((cid, idx))
        here = [c for c, x in s['chars'].items() if x['alive'] and x.get('present', True) and x['area'] == ch['area'] and c != cid]
        if kind == 'self':
            e = ent(cid)
        elif kind in ('any_here', 'here'):
            e = max((ent(c) for c in here), default=0.0)
        elif kind == 'student_here':
            e = max((ent(c) for c in here if '学生' in _C[c]['tags']), default=0.0)
        elif kind == 'territory':
            e = max((ent(c) for c, x in s['chars'].items() if x['alive'] and x['area'] == s['script'].get('territory') and c != cid), default=0.0)
        elif kind == 'dead':
            e = max((ent(c) for c, x in s['chars'].items() if not x['alive']), default=0.0)
        else:  # 犯人・ルールXを知る能力
            e = 1.0
        v += min(ch['gw'], need) / need * min(1.0, e / 3.0)
    return v


def incident_info(after, day_incs, cands):
    """今日の事件ごとに、発生・不発を観測したときの犯人の候補の情報量（ビット）。候補は公開の推理（incident_candidates）、
    起きるかは札の解決後の不安が臨界以上か。候補を一様とみなす（ponytail: 推理の重みは使わない）。"""
    import math
    from engine.resolve import CHARS as _C
    v = 0.0
    for key in day_incs:
        c = [x for x in cands.get(key, after['chars']) if after['chars'].get(x, {}).get('alive') and after['chars'][x].get('present', True)]
        n = len(c)
        k = sum(1 for x in c if after['chars'][x]['par'] >= _C[x]['limit'])
        if 0 < k < n:
            v += math.log2(n) - (k / n * math.log2(k) + (n - k) / n * math.log2(n - k))
    return v


class ReadingBlocker(DeductiveBlocker):
    def __init__(self, rng, hyps=40, beta=0.7, hold=0.15, null_cost=0.1, model_mm=True, tt_weight=1.0, soft_gamma=0.0, pairs=True, memory=0.0, info_sched=False, loops=3, info_last=None, intent=0.0, urgency=0.3, reach=True, crit=False, info_adapt=9.0, reach_mv=False, safe=0.0, chain=False, zero=0.0, obs=0.0, clear=False, defense=0.0, itarget=False, probe=0.0, reveal=True, nulled=0.3, defplan=False, lookday=False, recall_mm=0.0, reach_info=0.0, board_waste=0.0, probe_broad=False, cut_w=0.0, lock_block=0.0, lock_probe=0.0, lock_danger=0.0, danger_lr=4.0, card_lr=0.0, prio=0.0, lost_th=0.05, prio_info=1.0, prio_gate=0.0, split=0.0, split_info=2.0, split_known=False, assume=0.0, **kw):
        super().__init__(rng, hyps=hyps, **kw)
        # nulled: 脚本家の不安禁止・友好禁止で打ち消された (対象, 禁止) の回数を覚え、同じ対象へ不安±・友好の札を置く手を回数×nulled 減点する（ループをまたいで残す）。
        # 中身を知る Claude が脚本家の d27（seed 51）: 無関係の C07 に毎ループ不安禁止を置かれ、不安−1 を4回打ち消されて学者を守る札が足りなくなった
        self.nulled_w, self.nulled = nulled, {}
        # defplan: 推理で役職がほぼ確定（p>0.9）した人物を、カウンターを取り除く友好能力で守る計画。ループの初めに持ち主を決めて友好を集中する（readreveal と同じ形）。
        # 中身を知る Claude が脚本家の d27（readguard 戦）: メインラバーズと割れた学者に毎日不安+1 で不安−1 が尽き、学者[3] で暗躍を消す正答手順を使わなかった
        self.defplan = defplan
        # lookday: 手の評価で、札の解決後にその日の事件（脚本家が最も得をする選択）まで進めた盤面を見る。
        # 中身を知る Claude が脚本家の d27: 不安拡大の不安+2＋不安+1 でメインラバーズの学者が臨界に届くのを1日先まで読めず、毎ループ2日目に負けた
        self.lookday = lookday
        # recall_mm: 行動解決で公開された脚本家の札の中身を (対象 → 札の回数) で覚え、伏せ札の中身の見積もりで、同じ対象に同じ札が来やすいとみる（重み 1+回数×recall_mm）。
        # 行動解決では6枚すべてが公開される（turn-phases.md）。Claude の脚本家は同じ人物に不安禁止のおとりを毎ループ置いていた（d27）
        self.recall_mm, self.mm_hist = recall_mm, {}
        # reach_info: 今日の友好で閾値に届く情報の能力（このループで未使用、持ち主に脚本家の伏せ札なし）に友好を置く手に加点（情報の問題: 閾値まで1〜2手の能力を取りに行かなかった）
        # board_waste: ボードに暗躍禁止以外の札を置く手を減点（ボードで効くのは暗躍禁止だけ。札は公開されるのでブラフにもならない）
        self.reach_info, self.board_waste = reach_info, board_waste
        # probe_broad: 「このループは負け」とみなす条件を、ループ終了時の負けが成立している世界（契約の暗躍2・爆弾の都市暗躍2 など）まで広げる
        # （ユーザー「ループを捨てて情報に使う判断」。以前の条件は未来改変・フレンドの死・神社の暗躍3 だけで、切り替えがほとんど起きなかった）
        self.probe_broad = probe_broad
        # cut_w: ループの途中で終わる負け筋（主人公・キーパーソンの死）の脅威を止める手に、残りの日数の割合×cut_w を上乗せ
        # （止め損なうと残りの日の情報も失う。ループ終了時の負けは止められなくても情報は取れる。ユーザーの指摘 2026-10-08）
        self.cut_w = cut_w
        # 脚本家の逆算（engine/route_calc）を主人公の側から読む（ユーザー「強くなった脚本家のロジック参考に主人公の強化」2026-10-09）
        # lock_block: 応手ごとに「今日の後で脚本家がこのループを必ず取れるようになる割合」（伏せ札の置き先だけ見える）を減点
        # lock_probe: このループが脚本家に確定した世界の割合が 0.7 以上なら、情報を取りに行く（probe と同じ切り替え。重みはこの値）
        self.lock_block, self.lock_probe = lock_block, lock_probe
        # lock_danger: 伏せ札の人物が「キーパーソン・フレンドなら取り返せない」場合に備える。推理の確率を、脚本家がそこに札を置いた
        # ことで danger_lr 倍に寄せ（脚本家の意図）、その役職の世界だけで応手ごとの「確定される割合」を見る（d27 の契約: 委員長に暗躍禁止）
        self.lock_danger, self.danger_lr = lock_danger, danger_lr
        # card_lr: 公開された脚本家の暗躍の札を尤度にして推理を更新する（Deduction.card_evidence。暗躍+2 は1.5倍）。0 で使わない
        self.card_lr, self._card_seen = card_lr, set()
        # prio: 優先順で選ぶ（このループの勝ち目 → 従来の評価）。値は勝ち目の許容差（0 で使わない）。lost_th 未満なら情報に（prio_info 倍）
        self.prio, self.lost_th, self.prio_info, self.prio_gate = prio, lost_th, prio_info, prio_gate
        # split: ループを途中で終わらせる筋だけを評価し（score_cut）、その脅威が届く世界の割合が split 未満なら情報に（重み split_info 倍）
        self.split, self.split_info, self.split_known = split, split_info, split_known
        # assume: 仮定ごとに世界を枝分かれさせる（脚本家が置いたカウンターから仮定を立て、否定されたら捨てる）。値は足す世界の割合
        self.assume = assume
        # obs: 事件の観測の価値。今日の事件が起きるか起きないかで犯人の候補がどれだけ割れるか（ビット）×obs を足す
        # （中身を知らない Claude が d01 で「自殺をわざと起こさせて巫女[5] で役職を見た」。ユーザー「わざと事件を起こすなどもある」）
        self.obs = obs
        # clear: 伏せ札のあるボードに暗躍禁止を置く手と、そのボードにいる人物を移動で外へ出す手の組を候補に足す
        # （カルティストは同じエリアの暗躍禁止を無視できる。中身を知る Claude が d01 で教祖を神社から出して守りきった）
        self.clear = clear
        # defense: 守りの友好能力（不安・暗躍を取り除く、回数制限の無いものが中心）に友好を積む価値。友好が必要数に近いほど大きい
        # （中身を知る Claude が d01 で女子学生[2] を毎日使って学校の犯人の不安を抑えた。今までは情報の能力にしか価値を付けていなかった）
        self.defense = defense
        # itarget: 情報の友好能力に友好を積む価値を、その能力が今調べられる相手の推理のエントロピーで重み付けする
        # （d10 で Claude が脚本家のとき、自動の主人公はキーパーソン 50:50 のまま教師[4] を一度も使わなかった。一律の重みでは要の不確かさに向かない）
        self.itarget = itarget
        # probe: 仮説の世界の多くでこのループの負けが確定していたら（ループ終了時の条件が成立して戻せない）、残りの日は守りをやめて
        # 情報の重みを probe 倍・事件の観測を1にする（Claude が主人公のとき、負けるループを情報に変えた: d03 でタイムトラベラーの候補を
        # 病院の事件に集めて不死で割った、d08 で神格[3] を捨てたループで使った）。最終ループでは使わない
        self.probe = probe
        # reveal: 最終ループ以外で、役職を公開する友好能力の持ち主のうち友好の不足が最も小さい者に、友好+2 と友好+1 を2人で重ねる組を候補に足し、
        # その持ち主への友好の価値を強める（中身を知らない Claude が d16 で教師[4] に友好を集中し、ループ1の2日目に使ってルールXを割った）
        self.reveal = reveal
        self.zero = zero  # 札の解決後に距離0の負け筋（ターン終了の殺害など）が残る世界に足す罰（readzero。s08 で Claude が脚本家の点検:
        # 距離1の筋3本の和が距離0の1本より重く、キーをシリアルキラーと2人きりにする移動を止めなかった）
        self.info_adapt = info_adapt  # 情報の重みを 配役のエントロピー／info_adapt 倍にする（0 で無効。readadapt）
        self.chain = chain  # 事件の連鎖の前の事件の犯人も止める対象に含める（readchain）
        self.safe = safe  # 危険の近さで情報の重みを弱める割合（0 で無効。readsafe）
        self.reach_mv = reach_mv  # 今日1回の移動でそろう筋に伏せ札が来たら移動禁止に加点（readmv）
        self.crit = crit  # 臨界に届いている対象（不足0）も止める対象に含める（readcrit）
        self.reach = reach  # 脚本家が締め切りまでに届く対象に今日伏せ札が置かれたら、止める札に加点する（readreach）
        self.urgency = urgency  # 世界ごとに、今日置かないと間に合わない対象を止める札へ加点する（脚本家の searchurg の裏返し）
        # intent: 脚本家の意図を読む。これまでの全ての伏せ札の対象が勝ち筋に関わる世界ほど重くして、多めに引いた世界から引き直す
        self.intent = intent
        self.info_sched, self.loops, self.info_last = info_sched, loops, info_last
        self.pairs = pairs
        # memory: 前のループの同じ日に伏せ札が置かれた対象を、この重みで見込む（ユーザーの観点: 脚本家の過去の行動を意識する）
        self.memory, self.history = memory, {}  # {(ループ, 日): [対象]}
        self.beta, self.hold = beta, hold
        # model_mm: 伏せ札の中身を、脚本家の評価を上げる札ほど選ばれやすいとして引く（ReadingBlocker2。点検の試合の弱点2）
        self.null_cost, self.model_mm = null_cost, model_mm
        self.tt_weight = tt_weight
        self.soft_gamma = soft_gamma  # 伏せ札の対象から役職を弱く読む（Deduction.soft_target）
        self.targeted = {}  # このループで伏せ札が置かれた回数 {対象: 回数}
        self._loop = None

    def _intent_worlds(self, s, pool=5):
        """推理から nh×pool 個の世界を引き、これまでの伏せ札の対象（history、全ループ）が近い勝ち筋に関わる数で重みを付け、
        nh 個に引き直す（点検の試合: 脚本家が毎日同じ所を狙っても、推理の事前の確率が低い世界は引かれず、守らなかった）。"""
        nh = self.nh
        self.nh = nh * pool
        try:
            big = self._worlds(s)
        finally:
            self.nh = nh
        past = [t for ts in self.history.values() for t in ts]
        if not past or len(big) <= nh:
            return big[:nh]
        ws = []
        for sc in big:
            inv = involved(s, sc)
            ws.append(math.exp(self.intent * sum(t in inv for t in past)))
        return self.rng.choices(big, weights=ws, k=nh)

    def _past(self, s):
        """前のループの同じ日（と翌日）に伏せ札が置かれた対象。"""
        out = []
        for (lp, d), ts in self.history.items():
            if lp < s['loop'] and d in (s['day'], s['day'] + 1):
                out += ts
        return out

    def _weights(self, s, scripts, mm_targets):
        ws = []
        past = self._past(s) if self.memory else []
        for sc in scripts:
            inv = involved(s, sc)
            hit = sum(t in inv for t in mm_targets) + 0.5 * sum(n for t, n in self.targeted.items() if t in inv)
            hit += self.memory * sum(t in inv for t in past)
            ws.append(math.exp(self.beta * hit))
        tot = sum(ws)
        return [w / tot for w in ws]

    def pc_cards(self, s, order, mm_targets=None):
        if self._loop != s['loop']:
            self._loop, self.targeted = s['loop'], {}
        mm_targets = list(mm_targets or [])
        self.last_debug = []
        self._ensure(s)  # OracleReader は _worlds で推理を使わないので、ここで用意する
        if self.soft_gamma:
            self._ensure(s)
            for tg in mm_targets:
                if not tg.startswith('B:'):
                    self.ded.soft_target(tg, self.soft_gamma)
        scripts = self._intent_worlds(s) if self.intent else self._worlds(s)
        if self.assume:  # 仮定ごとに世界を枝分かれさせる（生きている仮定に合う世界を足す）
            scripts = scripts + self._assume_worlds(s, len(scripts))
        ws = self._weights(s, scripts, mm_targets)
        for t in mm_targets:
            self.targeted[t] = self.targeted.get(t, 0) + 1
        self.history[(s['loop'], s['day'])] = list(mm_targets)
        hand_m = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        # 世界ごとに伏せ札の中身を1通り引いておく（候補の比較を同じ条件で行う）。中身は一様ではなく、その世界で
        # 脚本家の評価を上げる札ほど選ばれやすいとする（点検の試合で、キラーへの移動札を読めずに自分の移動札と重ねていた）
        # 世界が少ない（脚本を知る OracleReader は1つ）と、1回引いた中身で手の良し悪しが決まってしまう。
        # 世界の数が 16 に満たなければ、世界ごとに中身を複数回引いて重みを分ける（点検: oread が病院の暗躍+2 を止めなかった）
        reps = max(1, -(-16 // max(1, len(scripts))))
        worlds = []
        for sc, wt in zip(scripts, ws):
            for _ in range(reps):
                fill = (self._fill(s, sc, mm_targets, hand_m) if self.model_mm else
                        [{'by': 'M', 'target': t, 'card': c} for t, c in zip(mm_targets, self.rng.sample(hand_m, len(mm_targets)))])
                worlds.append((sc, fill, wt / reps))
        mm_fill = [f for _, f, _ in worlds]
        past = self._past(s) if self.memory else []
        focus = list(dict.fromkeys(mm_targets + past + [x for sc in scripts[:12] for x in sorted(involved(s, sc))]))
        focus = [f for f in focus if (f in s['chars'] and s['chars'][f]['alive'] and s['chars'][f].get('present', True) and f != 'C20')
                 or str(f).startswith('B:')]
        allt = self._targets(s)
        hands = {p: [c for c in CARDS['protagonist_hand'] if not (c in ONCE[p] and c in s['used'][p])] for p in order}

        def fill(*firsts):
            fixed = {f[0]: f for f in firsts if f}
            taken, out = {f[1] for f in fixed.values()}, []
            for p in order:
                if p in fixed:
                    out.append({'by': p, 'target': fixed[p][1], 'card': fixed[p][2]})
                    continue
                pool = [t for t in focus if t not in taken] or [t for t in allt if t not in taken]
                t = self.rng.choice(pool)
                taken.add(t)
                # 暗躍禁止は2枚以上で全部無効になり候補ごと捨てられるので、決めた札に暗躍禁止があれば残りの者には引かせない
                # （d27 の readdefplan 戦 L5D3: 「委員長に暗躍禁止＋学者に不安−1」の組が、残りの1枚に暗躍禁止を引いて捨てられ候補に残らなかった）
                no_intx = any(x['card'] == 'INTX' for x in out) or any(f[2] == 'INTX' for f in fixed.values())
                stops = [c for c in STOP if c in hands[p] and not (no_intx and c == 'INTX')]
                out.append({'by': p, 'target': t, 'card': self.rng.choice(stops or [c for c in hands[p] if not (no_intx and c == 'INTX')] or hands[p])})
            return out

        # タイムトラベラーの候補への友好: 推理でのタイムトラベラーの確率 × 友好3に足りない分（Claude が主人公を担当した点検の学び）
        m, tot = self.ded.marginals()
        ptt = {c: m.get(c, {}).get('TT', 0) / tot for c in s['chars']} if tot else {}
        cands, singles = [], []
        for t in mm_targets:  # 伏せ札の対象×止める札（リーダーから順に、持っている者が置く）
            for card in STOP:
                p = next((p for p in order if card in hands[p]), None)
                if p:
                    singles.append((p, t, card))
                    cands.append(fill((p, t, card)))
        if self.pairs:  # 2枚の脅威を同時に止める組（点検の試合: キラーの移動とキーパーソンへの暗躍を片方しか止めなかった）
            for i, a in enumerate(singles):
                for b in singles[i + 1:]:
                    if a[1] != b[1]:
                        pb = next((p for p in order if p != a[0] and b[2] in hands[p]), None)
                        if pb:
                            cands.append(fill(a, (pb, b[1], b[2])))
        if self.clear:
            for t in mm_targets:
                if not t.startswith('B:'):
                    continue
                pa = next((p for p in order if 'INTX' in hands[p]), None)
                if not pa:
                    continue
                for c, v in s['chars'].items():
                    if v['area'] != t[2:] or not v['alive'] or not v.get('present', True) or c == 'C20':
                        continue
                    for mv in ('MV_H', 'MV_V'):
                        pb = next((p for p in order if p != pa and mv in hands[p]), None)
                        if pb:
                            cands.append(fill((pa, t, 'INTX'), (pb, c, mv)))
        for c in sorted(ptt, key=lambda c: -ptt[c])[:4]:  # タイムトラベラーらしい者に友好を積む候補
            if ptt[c] > 0.05 and s['chars'][c]['alive'] and s['chars'][c].get('present', True) and c != 'C20':
                for card in ('GW2', 'GW1'):
                    p = next((p for p in order if card in hands[p]), None)
                    if p:
                        cands.append(fill((p, c, card)))
        reveal_c = None
        if self.reveal and s['loop'] < (s['script'].get('loops') or self.loops):
            from selfplay.players import INFO_ABILITIES
            used = s.get('ability_used_loop', [])
            rm_, rt_ = self.ded.marginals()

            def rv_ent(c, _m=rm_, _t=rt_):
                ps = [w / _t for w in _m.get(c, {}).values() if w > 0] if _t else []
                return -sum(p * math.log2(p) for p in ps)
            best = None
            for (cid, idx), need in INFO_ABILITIES.items():
                ch = s['chars'].get(cid)
                if ch is None or not ch['alive'] or not ch.get('present', True) or f'{cid}#{idx}' in used or ch['gw'] >= need:
                    continue
                from engine.abilities import ABILITIES as _AB
                if _AB.get((cid, idx), (0, 0, ''))[2] == 'loop2' and s['loop'] < 2:  # イレギュラー・コピーキャットは第2ループから
                    continue
                kind = TARGET_ROLE.get((cid, idx))
                here = [c for c, x in s['chars'].items() if x['alive'] and x.get('present', True) and x['area'] == ch['area'] and c != cid]
                if kind == 'self':
                    e = rv_ent(cid)
                elif kind in ('any_here', 'here'):
                    e = max((rv_ent(c) for c in here), default=0.0)
                elif kind == 'student_here':
                    from engine.resolve import CHARS as _C
                    e = max((rv_ent(c) for c in here if '学生' in _C[c]['tags']), default=0.0)
                else:
                    e = 0.5
                sc_ = e / (need - ch['gw'])  # 割れる不確かさ ÷ 足りない友好（d16 の Claude は教師[4] を選んだ）
                if best is None or sc_ > best[0]:
                    best = (sc_, cid)
            if best and best[0] <= 0.05:
                best = None
            plan = getattr(self, '_reveal_plan', None)  # ループの中では一度決めた持ち主に集中する（日ごとに変えると友好が散った）
            if plan and plan[0] == s['loop'] and s['chars'].get(plan[1], {}).get('alive') and not any(
                    u.startswith(plan[1] + '#') for u in used):
                best = (1.0, plan[1])
            elif best:
                self._reveal_plan = (s['loop'], best[1])
            if best:
                reveal_c = best[1]
                p1 = next((p for p in order if 'GW2' in hands[p]), None)
                p2 = next((p for p in order if p != p1 and 'GW1' in hands[p]), None)
                if p1:
                    cands.append(fill((p1, reveal_c, 'GW2')))
                if p2:
                    cands.append(fill((p2, reveal_c, 'GW1')))
        def_c = self._defense_holder(s) if self.defplan else None
        if def_c:
            p1 = next((p for p in order if 'GW2' in hands[p]), None)
            p2 = next((p for p in order if p != p1 and 'GW1' in hands[p]), None)
            if p1:
                cands.append(fill((p1, def_c, 'GW2')))
            if p2:
                cands.append(fill((p2, def_c, 'GW1')))
        for k in range(self.samples + 40):
            cands.append(fill(None) if k < 40 else [{'by': p, 'target': self.rng.choice(allt), 'card': self.rng.choice(hands[p])} for p in order])
        iw = self.info_weight
        loops = s['script'].get('loops') or self.loops  # ループ数は公開情報
        if self.info_sched and loops > 1:  # 序盤のループは情報を重く、最終ループは守りだけ（ユーザーの観点: 情報と守りの釣り合い）
            iw = self.info_weight * 2 * (loops - s['loop']) / (loops - 1)
        if self.safe:  # 危険の近さで情報の重みを弱める: 伏せ札が止められずに通ると負け筋の距離が0になる世界の割合（Claude が脚本家の
            # 点検の試合 toukou_001: バランス版がキーへの暗躍・キラーの移動を止めず友好を積んだ）
            dz, tw = 0.0, 0.0
            for sc, w, wt in worlds:
                try:
                    after, _ = resolve_actions(dict(s, script=sc), w)
                    rs = enumerate_routes(after)
                except IllegalPlacement:
                    continue
                tw += wt
                if rs and min(r['total'] for r in rs) <= 0:
                    dz += wt
            if tw:
                iw_scale = 1 - self.safe * dz / tw
            else:
                iw_scale = 1.0
        else:
            iw_scale = 1.0
        if self.info_adapt:  # 推理の残り（配役のエントロピー）が大きいほど情報を重く（ユーザーの観点: 知識が白紙なら情報を取る方が強い。
            # 最後の戦いの勝ちもループを守るのと同じ価値で、自動の主人公は最後の戦いの8割を隠しきられていた）
            iw = iw * min(3.0, max(0.3, self.ded.role_entropy() / self.info_adapt))
        iw *= iw_scale
        if self.info_last is not None and s['loop'] >= loops:  # 最終ループの情報の重み（既定 0＝守りだけ。readfinal0 の結果）
            iw = self.info_last
        probing = False
        if self.split:  # ユーザーの案（2026-10-09）: ループを途中で終わらせる筋（キーパーソン・主人公の死）だけを守り、
            # それが近くに無ければ（ループの最後まで中断されないなら）札を情報に使う。ループ終了時の筋（フレンド・爆弾・契約など）は守らない
            from selfplay.search_mm import reachable_targets as _rt2
            days_ = s['script'].get('days') or 8
            if self.split_known:  # 知っている筋だけ（ユーザー「知ってるループ敗北条件が…その筋だけ守る」）
                kf = self._known_route_fn(s, th=0.7 if self.split_known is True else float(self.split_known))
                self._known_fn = kf
                pc_ = sum(wt for sc, wt in zip(scripts, ws)
                          if any(kf(r, sc) and r['total'] <= days_ - s['day'] + 1 for r in enumerate_routes(dict(s, script=sc))))
            else:
                self._known_fn = None
                pc_ = sum(wt for sc, wt in zip(scripts, ws) if _rt2(dict(s, script=sc), days_, crit=True, only='cut'))
            tw_ = sum(ws) or 1.0
            self.last_split = pc_ / tw_
            if pc_ / tw_ < self.split:
                probing = True
                iw = max(iw, 0.3) * self.split_info
        if self.probe and s['loop'] < loops:
            from engine import phases as _ph
            lk = 0.0
            for sc, _w, wt in worlds:
                st = dict(s, script=sc)
                r = _ph.loop_end_loss(copy_state(st))
                if r and (self.probe_broad or {'Y_FUTURE', 'FRIEND'} & set(r) or any(v >= 3 for v in [s['boards'].get('SHR', 0)])):
                    lk += wt
            tw = sum(wt for _, _, wt in worlds) or 1.0
            if lk / tw >= 0.7:
                probing = True
                iw = max(iw, 0.3) * self.probe
        lock_w = []  # [(応手の割合 {(不安−1 の置き先, 暗躍禁止の置き先): (最悪, 平均)}, 重み)]
        if (self.lock_block or self.lock_probe) and s['loop'] <= loops:
            from engine.route_calc import block_rates, forced_plan
            days = s['script'].get('days') or 8
            lk, tw = 0.0, 0.0
            for sc, wt in zip(scripts, ws):
                st = dict(s, script=sc)
                if self.lock_probe and s['loop'] < loops:
                    lk += wt * forced_plan(st, days)['locked']
                    tw += wt
                if self.lock_block:
                    br = block_rates(st, days, mm_targets)
                    if br:
                        lock_w.append((br, wt))
            if self.lock_probe and tw and lk / tw >= 0.7:  # このループはもう取られている → 札は情報に（ル構-06）
                probing = True
                iw = max(iw, 0.3) * self.lock_probe
        danger_w = self._danger_rates(s, mm_targets) if self.lock_danger else []
        urg_w = []
        if self.urgency:
            from selfplay.search_mm import urgent_targets
            days = s['script'].get('days') or 8
            urg_w = [(urgent_targets(dict(s, script=sc), days), wt) for sc, wt in zip(scripts, ws)]
            if self.reach:  # 脚本家が届く対象に今日伏せ札が置かれた（主人公にとっての締め切り）
                from selfplay.search_mm import reachable_targets
                urg_w = [(u | {(t, k) for t, k in reachable_targets(dict(s, script=sc), days, crit=self.crit, chain=self.chain) if t in mm_targets}, wt)
                         for (u, wt), sc in zip(urg_w, scripts)]
        cut_w = []
        if self.cut_w and self.urgency:
            from selfplay.search_mm import reachable_targets as _rt
            days = s['script'].get('days') or 8
            cut_w = [({(t, k) for t, k in _rt(dict(s, script=sc), days, crit=self.crit, only='cut') if t in mm_targets}, wt)
                     for sc, wt in zip(scripts, ws)]
            frac_left = (days - s['day'] + 1) / days
        if self.itarget:  # 人物ごとの配役のエントロピー（公開の推理）
            mm_, tt_ = self.ded.marginals()

            def ent_of(c, _m=mm_, _t=tt_):
                ps = [w / _t for w in _m.get(c, {}).values() if w > 0] if _t else []
                return -sum(p * math.log2(p) for p in ps)
        obs_c, obs_days = {}, []
        if self.obs or self.probe:  # 今日の事件（公開の予定の日と名前だけを使う）と、犯人の候補（公開の推理）
            from engine.deduce import incident_candidates
            obs_c = incident_candidates(list(s['chars']), self.inc_history, self.culprits)
            obs_days = [f"{i['id']}@{i['day']}" for i in s['script']['incidents'] if i['day'] == s['day']]
        best, best_v = None, None
        recs = []
        reach_now = {}
        if self.reach_info:
            from selfplay.players import INFO_ABILITIES
            used_l = s.get('ability_used_loop', [])
            for (cid, idx), need in INFO_ABILITIES.items():
                ch = s['chars'].get(cid)
                if ch and ch['alive'] and ch.get('present', True) and f'{cid}#{idx}' not in used_l and cid not in mm_targets and 0 < need - ch['gw'] <= 2:
                    reach_now[cid] = min(need, reach_now.get(cid, 99))
            for c in reach_now:  # その持ち主に友好を置く手を候補に必ず入れる
                for card in ('GW2', 'GW1'):
                    p = next((p for p in order if card in hands[p]), None)
                    if p:
                        cands.append(fill((p, c, card)))
        for pc in cands:
            if len({x['target'] for x in pc}) < len(pc):
                continue
            if sum(x['card'] == 'INTX' for x in pc) > 1:  # 暗躍禁止は2枚以上で全部無効（点検の試合で3枚置いていた）
                continue
            tt_bonus = sum(ptt.get(x['target'], 0) * min(3 - s['chars'][x['target']]['gw'], 2 if x['card'] == 'GW2' else 1)
                           for x in pc if x['card'] in ('GW1', 'GW2') and x['target'] in s['chars'] and s['chars'][x['target']]['gw'] < 3)
            vals, wsum = [], 0.0
            safe_w, info_w = 0.0, 0.0
            for sc, w, wt in worlds:
                try:
                    after, _ = resolve_actions(dict(s, script=sc), w + pc)
                    if self.lookday:
                        after = after_incidents(after)
                except IllegalPlacement:
                    continue
                z = self.zero * any(r['total'] <= 0 for r in enumerate_routes(after)) if self.zero else 0.0
                o = (1.0 if probing else self.obs) * incident_info(after, obs_days, obs_c) if obs_days else 0.0
                o += self.defense * defense_progress(after) if self.defense else 0.0
                ip = info_progress_targeted(after, ent_of, s.get('ability_used_loop', [])) if self.itarget else info_progress(after, s.get('ability_used_loop', []))
                vals.append(((score_cut(after, getattr(self, '_known_fn', None), sc) if self.split else score(after)) + z - iw * ip - o, wt))
                wsum += wt
                if self.prio:
                    safe_w += wt * self._safe(after)
                    info_w += wt * (iw * ip + o)
            if not vals:
                continue
            mean = sum(v * wt for v, wt in vals) / wsum
            # 悪い側への備え: 以前は最悪の1世界（max）を使っていたが、1つの外れ値に引きずられて無意味な手を選んでいた。
            # 重みつきで悪い方から25%の平均（CVaR）にする
            acc, tail = 0.0, 0.0
            for x, wt in sorted(vals, key=lambda y: -y[0]):
                take = min(wt, 0.25 * wsum - acc)
                if take <= 0:
                    break
                tail += x * take
                acc += take
            v = (mean + tail / acc) / 2
            v += self.hold * sum(1 for x in pc if x['card'] in ONCE[x['by']])
            if reveal_c:  # 役職を公開する能力の持ち主に友好を積む手を強める（ponytail: 固定の加点 0.3/友好）
                v -= 0.3 * sum({'GW1': 1, 'GW2': 2}.get(x['card'], 0) for x in pc if x['target'] == reveal_c)
            if def_c:  # 守りの能力の持ち主に友好を積む手を強める（readreveal と同じ固定の加点）
                v -= 0.3 * sum({'GW1': 1, 'GW2': 2}.get(x['card'], 0) for x in pc if x['target'] == def_c)
            v -= self.tt_weight * tt_bonus
            if cut_w:
                v -= self.cut_w * frac_left * sum(wt * sum(1 for x in pc if (x['card'] == 'PAR-' and (x['target'], 'par') in u)
                                                           or (x['card'] == 'INTX' and (x['target'], 'int') in u)
                                                           or (x['card'] == 'MVX' and (x['target'], 'mv') in u)) for u, wt in cut_w)
            if (lock_w or danger_w) and not probing:
                pm = frozenset(x['target'] for x in pc if x['card'] == 'PAR-')
                ix = [x['target'] for x in pc if x['card'] == 'INTX']
                ix = ix[0] if len(ix) == 1 else None

                def rate(br):
                    names = {n for k in br for n in k[0]} | {k[1] for k in br if k[1]}
                    key = (frozenset(pm & names), ix if ix in names else None)
                    # 載っていない組: 暗躍禁止が負け筋の暗躍でない所なら暗躍禁止なしと同じ。不安−1 が足りない等は最悪とみなす。
                    # 最悪はたいてい1（数えていない割り当てのどれかで取られる）で見分けがつかないので平均で比べる
                    return (br.get(key) or br.get((key[0], None)) or max(br.values()))[1]
                if lock_w:
                    v += self.lock_block * sum(wt * rate(br) for br, wt in lock_w) / (sum(wt for _, wt in lock_w) or 1.0)
                if danger_w:  # 正規化しない: 取り返せない世界の確からしさ q そのものを掛ける
                    v += self.lock_danger * sum(q * rate(br) for br, q in danger_w)
            if urg_w:
                v -= self.urgency * sum(wt * sum(1 for x in pc if (x['card'] == 'PAR-' and (x['target'], 'par') in u)
                                                 or (x['card'] == 'INTX' and (x['target'], 'int') in u)
                                                 or (self.reach_mv and x['card'] == 'MVX' and (x['target'], 'mv') in u)) for u, wt in urg_w)
            v += self.null_cost * self._nulls(s, scripts[0], mm_fill[0], pc)
            if self.reach_info:
                v -= self.reach_info * sum(1 for x in pc if x['target'] in reach_now and x['card'] in ('GW1', 'GW2')
                                           and s['chars'][x['target']]['gw'] + (2 if x['card'] == 'GW2' else 1) >= reach_now[x['target']])
            if self.board_waste:
                v += self.board_waste * sum(1 for x in pc if x['target'].startswith('B:') and x['card'] != 'INTX')
            if self.nulled:
                v += self.nulled_w * sum(self.nulled.get((x['target'], NULLED_BY.get(x['card'])), 0) for x in pc)
            if getattr(self, 'debug', False):
                self.last_debug.append((round(v, 4), round(mean, 4), round(max(x for x, _ in vals), 4), [(x['target'], x['card'], x['by']) for x in pc]))
            if self.prio:
                recs.append((safe_w / wsum, v, info_w / wsum, pc))
            if best_v is None or v < best_v:
                best, best_v = pc, v
        if self.prio and recs:
            # 優先順（脚本家の calcG と同じ形。ユーザー合意 2026-10-09）: ① このループの勝ち目（今日の後で脚本家が必ず取れるとは
            # 言えない世界の割合）が最大に近い手 ② その中で従来の評価。勝ち目がどの手でも無いなら、このループは捨てて情報（ル構-06）
            top = max(r[0] for r in recs)
            base = min(recs, key=lambda r: r[1])  # 従来の評価で選んだ手
            if top < self.lost_th:
                best = min(recs, key=lambda r: r[1] - self.prio_info * r[2])[3]
            elif not self.prio_gate or top - base[0] > self.prio_gate:
                # prio_gate: 従来の手がループの勝ち目をはっきり落とす（差が prio_gate を超える）ときだけ乗り換える。
                # 乗り換えを常にすると情報を取りに行く手が消えた（問題 32問×3: 53 → 33）
                best = min((r for r in recs if r[0] >= top - self.prio), key=lambda r: r[1])[3]
            else:
                best = base[3]
            self.last_prio = top
        return best or super().pc_cards(s, order, mm_targets)

    def _safe(self, after):
        """今日（行動・脚本家能力・事件）の後で、脚本家がこのループを必ず取れるとは言えないなら 1（engine.route_calc。主人公は伏せ札を知る下限の読み）。"""
        from engine import phases as ph
        from engine.route_calc import forced_plan
        if after.get('_loss'):
            return 0.0
        days = after['script'].get('days') or 8
        if after['day'] >= days:
            return 0.0 if ph.loop_end_loss(copy_state(after)) else 1.0
        return 0.0 if forced_plan(dict(after, day=after['day'] + 1), days)['forced'] else 1.0

    def _known_route_fn(self, s, th=0.7):
        """主人公が「知っている」途中で終わる負け筋かを判定する関数 known(r, sc) を返す。
        知っている＝その筋の役職（要の人物の役職と、キーパーソンの犠牲者）が推理で th 以上、事件の筋は犯人が1人に絞れている。"""
        import re
        from engine import phases as ph
        from engine.deduce import incident_candidates
        from selfplay.search_mm import is_loop_end
        self._ensure(s)
        m, tot = self.ded.marginals()
        cand = incident_candidates(list(s['chars']), self.inc_history, self.culprits)
        inc_ids = {i['id'] for i in s['script']['incidents']}

        def p(c, role):
            return (m.get(c, {}).get(role, 0) / tot) if tot else 0.0

        def known(r, sc):
            if is_loop_end(r):
                return False
            via = r.get('via')
            if r['id'] in inc_ids:
                day = next((c['detail'][2] for c in r['conds'] if c['kind'] == 'days' and c['detail'][0] == '事件'), None)
                cs = cand.get(f"{r['id']}@{day}", cand.get(r['id']))
                if via is None or not cs or set(cs) != {via}:
                    return False
            elif via in s['chars']:
                if p(via, ph.base_role(dict(s, script=sc), via)) < th:
                    return False
            g = re.match(r'(C\d\d)', r.get('goal') or '')
            if g and 'キーパーソン' in r['goal']:
                v = g.group(1)
                if max(p(v, 'KEY'), p(v, 'FACTOR')) < th:
                    return False
            return True
        return known

    DANGER_ROLES = ('KEY', 'FRIEND')

    def _danger_rates(self, s, mm_targets, k=3):
        """[(block_rates, 重み)]: 伏せ札の人物 t ごとに、t が取り返せない役職（キーパーソン・フレンド）の世界を k 個引き、
        応手ごとの「確定される割合」を返す。重みは q = pL/(pL+1-p)（p は推理の確率、L は脚本家がそこに札を置いた尤度比）。"""
        import numpy as np
        from engine.deduce import COMBOS, ROLES
        from engine.route_calc import block_rates
        self._ensure(s)
        R, C = self.ded._sync()
        if not len(C):
            return []
        w = self.ded._w(C)
        days = s['script'].get('days') or 8
        base = self._worlds(s)[:1]  # 事件の犯人の引き方は _worlds と同じ（1つ借りて役職とルールを差し替える）
        out = []
        dz = [ROLES.index(r) for r in self.DANGER_ROLES]
        for t in mm_targets:
            if t not in self.ded.chars:
                continue
            j = self.ded.chars.index(t)
            mask = np.isin(R[:, j], dz)
            p = float(w[mask].sum() / w.sum()) if w.sum() else 0.0
            if p <= 0:
                continue
            q = p * self.danger_lr / (p * self.danger_lr + 1 - p)
            idx = np.nonzero(mask)[0]
            pick = self.rng.choices(idx.tolist(), weights=w[idx].tolist(), k=k)
            for i in pick:
                sc = dict(base[0], rules=list(COMBOS[C[i]]), roles={self.ded.chars[jj]: ROLES[x] for jj, x in enumerate(R[i].tolist()) if x})
                br = block_rates(dict(s, script=sc), days, mm_targets)
                if br:
                    out.append((br, q / k))
        return out

    def _defense_holder(self, s):
        """役職がほぼ確定した人物（PERSON 以外、p>0.9）のカウンターを取り除ける守りの能力の持ち主。ループ中は固定。
        自身を対象にできる能力（学者[3]）はその人物自身、それ以外は同じエリアにいる持ち主（ponytail: 移動して合流する計画は立てない）。"""
        cut = {c for lp, c in getattr(self, '_gwx_loop', set()) if lp == s['loop']}  # このループで友好禁止を当てられた持ち主は外す
        plan = getattr(self, '_def_plan', None)
        if plan and plan[0] == s['loop'] and plan[1] not in cut and s['chars'].get(plan[1], {}).get('alive') and s['chars'][plan[1]]['gw'] < plan[2]:
            return plan[1]
        from engine.abilities import NOT_SELF, UNREFUSABLE
        m, tot = self.ded.marginals()
        best = None
        for c, rs in m.items():
            ch = s['chars'].get(c)
            if not tot or not ch or not ch['alive'] or not ch.get('present', True):
                continue
            role, w = max(rs.items(), key=lambda x: x[1])
            if role == 'PERSON' or w / tot < 0.9:
                continue
            for (h, idx), need in DEFENSE_ABILITIES.items():
                hc = s['chars'].get(h)
                if not hc or not hc['alive'] or not hc.get('present', True) or hc['gw'] >= need or h in cut:
                    continue
                if h == c and (h, idx) in NOT_SELF or h != c and (hc['area'] != ch['area'] or (h, idx) == ('C19', 0)):
                    continue
                # 自身を守る能力（学者[3]: 移動で離されない）と拒否されない能力（ナース[2]）を先に、その中で足りない友好の少ない順
                # （d27 の readdefplan 戦: 不足の少なさだけで医者[2] を選び、友好禁止1枚で計画が止まった）
                k = (0 if h == c or (h, idx) in UNREFUSABLE else 1, need - hc['gw'], h, need)
                if best is None or k < best:
                    best = k
        if best:  # 見つからない日は翌日また探す
            self._def_plan = (s['loop'], best[2], best[3])
        return best[2] if best else None

    def observe(self, s, phase, events):
        super().observe(s, phase, events)
        for e in events:
            if e.get('kind') == 'revealed':
                for x in e['cards']:
                    if x['by'] == 'M':
                        h = self.mm_hist.setdefault(x['target'], {})
                        h[x['card']] = h.get(x['card'], 0) + 1
                        if (self.card_lr or self.assume) and x['card'] in ('INT1', 'INT2', 'PAR+'):
                            self._evidence(s, x['target'], x['card'])
            if e.get('by') == 'SCHOLAR' and (self.card_lr or self.assume) and e.get('kind') in ('par', 'int'):  # 学者の特性: 脚本家が選んだカウンター
                self._evidence(s, 'C19', 'PAR+' if e['kind'] == 'par' else 'INT1')
            if e.get('kind') == 'nullified' and e.get('by') in ('PARX', 'GWX'):
                k = (e['target'], e['by'])
                self.nulled[k] = self.nulled.get(k, 0) + 1
                if e['by'] == 'GWX':
                    self._gwx_loop = getattr(self, '_gwx_loop', set()) | {(s['loop'], e['target'])}

    def _assume_worlds(self, s, n):
        """生きている仮定（裏付けの多い順に3つまで）ごとに、その仮定に合う世界を引く。合わせて n×assume 個（仮定の間は等分）。"""
        live = self.ded.live_assumptions()
        if not live:
            return []
        k = max(1, round(n * self.assume / len(live)))
        picks = [p for a, _ in live for p in self.ded.sample_in(self.rng, a, k)]
        return self._scripts_from_picks(s, picks) if picks else []

    def _evidence(self, s, target, card):
        """脚本家が選んで置いたカウンターを推理の手がかりにする（Deduction.card_evidence）。同じ所・同じ種類は1ループ1回だけ（毎日のおとりで膨らまない）。"""
        kind = 'par' if card == 'PAR+' else 'int'
        if getattr(self, 'ded', None) is None or (s['loop'], target, kind) in self._card_seen:
            return
        self._card_seen.add((s['loop'], target, kind))
        if self.assume:
            self.ded.assume_card(target, card, s.get('init'), f"L{s['loop']}D{s['day']} {card}")
        if self.card_lr:
            self.ded.card_evidence(target, card, s.get('init'), self.card_lr * (1.5 if card == 'INT2' else 1.0))

    def _fill(self, s, sc, targets, hand, temp=0.3, n=24):
        """伏せ札の中身を1通り引く。脚本家の評価を上げる組ほど選ばれやすい。
        以前は1枚ずつ評価していたが、脚本家の札は3枚でないと解決できない（validate）ため毎回反則になり、
        重みが全部0で無作為に選ばれていた。3枚の組をまとめて n 通り引いて評価する。"""
        from selfplay.players import DUMMY_PC
        world = dict(s, script=sc)
        cands, ws = [], []
        draws = [self.rng.sample(hand, len(targets)) for _ in range(n)]
        if self.recall_mm and self.mm_hist:  # 公開された札の履歴で最も多い札を当てた組も候補に入れる
            fav = [max(self.mm_hist[t], key=self.mm_hist[t].get) if self.mm_hist.get(t) else None for t in targets]
            if any(fav):
                left = [c for c in hand if c not in fav]
                fill = [f if f in hand else None for f in fav]
                if len(set(x for x in fill if x)) == len([x for x in fill if x]):
                    rest = self.rng.sample(left, sum(1 for x in fill if not x)) if len(left) >= sum(1 for x in fill if not x) else None
                    if rest is not None:
                        it = iter(rest)
                        draws.append([x or next(it) for x in fill])
        for cards in draws:
            mm = [{'by': 'M', 'target': t, 'card': c} for t, c in zip(targets, cards)]
            try:
                after, _ = resolve_actions(world, mm + DUMMY_PC)
            except IllegalPlacement:
                continue
            cands.append(mm)
            w = math.exp(score(after) / temp)
            if self.recall_mm:
                for x in mm:
                    w *= 1 + self.recall_mm * self.mm_hist.get(x['target'], {}).get(x['card'], 0)
            ws.append(w)
        if not cands:
            return [{'by': 'M', 'target': t, 'card': c} for t, c in zip(targets, self.rng.sample(hand, len(targets)))]
        return self.rng.choices(cands, weights=ws)[0]

    def _nulls(self, s, sc, w, pc):
        """置いても盤面が変わらない札の数（1つの世界で近似）。"""
        n = 0
        try:
            full, _ = resolve_actions(dict(s, script=sc), w + pc)
        except IllegalPlacement:
            return 0
        for i in range(len(pc)):
            rest = pc[:i] + pc[i + 1:]
            try:
                other, _ = resolve_actions(dict(s, script=sc), w + rest)
            except IllegalPlacement:
                continue
            if other['chars'] == full['chars'] and other['boards'] == full['boards']:
                n += 1
        return n

    # ---- 友好能力（段C2）: 情報の見込みと、盤面への効き目で選ぶ ----
    def abilities_for_day(self, s):
        import copy
        from engine import abilities as ab
        from engine import phases as ph
        self._ensure(s)
        m, tot = self.ded.marginals()

        def ent(c):
            ps = [w / tot for w in m.get(c, {}).values() if w > 0] if tot else []
            return -sum(p * math.log2(p) for p in ps)
        scripts = self._worlds(s)[:8]
        cands = []
        # 情報屋は最も確からしいルールXを宣言する（入っていれば、もう1つが公開される）
        R, C = self.ded._sync()
        from engine.deduce import COMBOS
        import numpy as np
        w = self.ded._w(C)
        xs = ('X_CIRCLE', 'X_LOVE', 'X_KILLER', 'X_RUMOR', 'X_VIRUS', 'X_THREAD', 'X_FACTOR')
        pres = {x: float(w[np.isin(C, [i for i, cb in enumerate(COMBOS) if x in cb])].sum()) for x in xs}
        best_x = max(pres, key=pres.get)
        refused = getattr(self, 'refused_chars', set())
        for cid, idx, a in ab.declaration_options(s):
            if cid in refused:  # 点検の試合: 拒否された能力を同じループで3回試した
                continue
            if ab.ABILITIES[(cid, idx)][2] == 'ruleX' and a['declare'] != best_x:
                continue
            if (cid, idx) == ('C05', 0) and a['incident'] in self.culprits:
                continue
            if True:
                v = 0.0
                if (cid, idx) in (('C04', 1), ('C21', 1), ('C23', 1), ('C16', 0), ('C29', 1)):  # 対象の役職を知る
                    v += ent(a['target'])
                elif cid in ('C11', 'C33'):  # 自身の役職を知る
                    v += ent(cid)
                elif cid == 'C28':  # 同じ役職の者の名前を知る（自身の役職の不確かさの目安）
                    v += 0.5 * ent(cid)
                elif cid == 'C10':  # 使用済みの1ループ1回の札を戻す（温存の費用 hold 相当）
                    v += self.hold
                elif cid == 'C06':
                    v += ent(cid)
                elif cid == 'C07':
                    v += 1.0
                elif (cid, idx) == ('C05', 0):
                    v += 0.3
                else:  # 盤面に効く能力: 仮説の世界での脚本家の評価の下がり幅
                    ds = []
                    for sc in scripts:
                        s2 = copy_state(dict(s, script=sc))
                        try:
                            ab.use_ability(s2, cid, idx, a, refuse=False)
                        except IllegalPlacement:
                            continue
                        except ph.LoopEnd as e:  # 自分でキーパーソンを殺す、など
                            ds.append(-10.0 if e.loss else 0.0)
                            continue
                        ds.append(score(dict(s, script=sc)) - score(s2))
                    v += 3 * (sum(ds) / len(ds)) if ds else 0.0
                if v > 0.05:
                    cands.append((v, (cid, idx, a)))
        out, used = [], set()
        for v, (cid, idx, a) in sorted(cands, key=lambda x: -x[0]):
            if (cid, idx) in used:
                continue
            used.add((cid, idx))
            out.append((cid, idx, a))
        return out


def ReadingBlocker2(rng, **kw):
    """点検の試合（plan「節目の点検: Claude が脚本家を担当」）の弱点1・2を直した版: 無駄札の費用 0.1、伏せ札の中身の模型。"""
    return ReadingBlocker(rng, null_cost=0.1, model_mm=True, **kw)


class OracleReader(ReadingBlocker):
    """脚本（配役・ルール・犯人）を知っている ReadingBlocker。脚本の難しさ（盤面で勝つ手があるか＝タブーの①）を測る上限の目安。
    伏せ札の中身は知らない。"""
    def _worlds(self, s):
        return [s['script']] * 8
