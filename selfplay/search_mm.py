"""先読みする脚本家（plan/engine-smarter.md 段B）。評価 = 勝ち筋の進み − λ × 主人公に漏れる情報。

- 伏せ札（B1・B2）
  - 置き場所の3つ組ごとに、主人公の応手を近似する。主人公は伏せ札の置き場所だけを見て置く（run.py）ので、応手は3つ組で決まる。
  - 近似の方法: 止める札を、置き場所と近い勝ち筋の担い手に置いた候補を作る。その中から、札の中身を知らない主人公が選ぶ手
    （中身の候補の平均で脚本家の評価が最小のもの）を求める。
  - その手に対する評価と、全候補の平均との中間を、その伏せ札の値とする（配役を知る主人公を仮定しているので、悲観に寄りすぎないように）。
- 1ループ1回の札の温存（B4）: 暗躍+2・移動斜めを使う手からは hold を引く。最終日だけは引かない。
- 脚本家能力（B3・B5）
  - 使う/使わないを「進み − λ × 漏れ」で選ぶ。
  - 漏れは、脚本家が手元に持つ公開情報の推理（PublicObserver）に、その能力で生じる観測（mm_effect）を試しに足したときの、
    配役のエントロピーの減り（ビット）。
  - 主人公の推理そのものではなく近似である（主人公の乱数や仮説の引き方までは再現しない）。
"""
import copy

from engine import phases as ph
from engine.resolve import CARDS, ONCE, IllegalPlacement, copy_state, resolve_actions
from engine.features import state_features
from engine.route_calc import forced_plan
from engine.routes import enumerate_routes
from selfplay.players import DUMMY_PC, PublicObserver, RouteMastermind, score

STOP = ('MVX', 'PAR-', 'INTX', 'MV_V', 'MV_H')


def urgent_targets(s, days, top=6):
    """近い勝ち筋のうち、締め切り（事件の日、なければループの最終日）までの日数と不足のカウンターが釣り合い、
    今日置かないと間に合わない (対象, カウンター) の集合。伏せ札は1日に同じ対象へ1枚なので、不足1につき1日かかるとみなす。
    脚本家は ここに置く札に加点し（searchurg）、主人公は ここを止める札に加点する（readurg）。"""
    out = set()
    for r in enumerate_routes(s)[:top]:
        dl = next((c['deficit'] for c in r['conds'] if c['kind'] == 'days'), days - s['day'])
        for c in r['conds']:
            if c['kind'] == 'counter' and c['detail'][1] in ('par', 'int') and 0 < c['deficit'] >= dl + 1:
                out.add((c['detail'][0], c['detail'][1]))
            elif c['kind'] == 'board' and 0 < c['deficit'] >= dl + 1:
                out.add(('B:' + c['detail'][0], 'int'))
    return out


# ループの終わりに負けが決まる筋（止められなくてもループは最後まで続き、主人公は情報を取り続けられる）。それ以外はループの途中で終わる（情報も断たれる）
LOOP_END_ROUTES = {'Y_SEAL', 'Y_CONTRACT', 'Y_FUTURE', 'Y_BOMB', 'FRIEND', 'TT_LOSS'}


def is_loop_end(r):
    """ループ終了時に負けが決まる筋か（フレンドの死の筋は routes が end=True を付ける）。"""
    return r['id'] in LOOP_END_ROUTES or bool(r.get('end'))


def reachable_targets(s, days, top=6, crit=False, chain=False, only=None):
    """近い勝ち筋のうち、脚本家が締め切りまでに1日1枚で届く (対象, カウンター)（不足 ≤ 締め切りまでの置ける日数）。
    主人公は、ここに今日伏せ札が置かれたら止める（urgent_targets は脚本家の「今日置かないと間に合わない」で、主人公には1日遅い。
    目隠し5本目の後の点検: s05_future で蝶の羽ばたきの犯人（臨界2）への不安+1 を2日目に止めず、3日目に手遅れになっていた）。"""
    out = set()
    for r in enumerate_routes(s)[:top]:
        if only == 'cut' and is_loop_end(r) or only == 'end' and not is_loop_end(r):
            continue
        dl = next((c['deficit'] for c in r['conds'] if c['kind'] == 'days'), days - s['day'])
        for c in r['conds']:
            if c['kind'] == 'counter' and c['detail'][1] in ('par', 'int') and 0 < c['deficit'] <= dl + 1:
                out.add((c['detail'][0], c['detail'][1]))
            elif crit and c['kind'] == 'counter' and c['detail'][1] == 'par' and c['deficit'] == 0:
                # 臨界に届いている犯人: 今日不安-1 で下げないと事件が起きる（Claude が脚本家の点検の試合 s01_seal: 医者が臨界のまま邪気の汚染）
                out.add((c['detail'][0], 'par'))
            elif c['kind'] == 'board' and 0 < c['deficit'] <= dl + 1:
                out.add(('B:' + c['detail'][0], 'int'))
            elif chain and c['kind'] == 'incident_par' and len(c['detail']) > 1 and c['detail'][1] and c['deficit'] <= dl + 1:
                # 事件の連鎖: 後の事件の条件を補う前の事件の犯人（臨界に届いているものも含む）を不安-1 で止める
                # （Claude が脚本家の点検 s03_bomb: 不安拡大で邪気の汚染の犯人を臨界にされた）
                out.add((c['detail'][1], 'par'))
            elif c['kind'] in ('same_area', 'alone_with', 'in_area') and c['deficit'] == 1 and r['total'] <= 1:
                # 今日1回の移動でそろう位置の筋（キラーとキー、シリアルキラーと2人きりなど）: 関わる人物への移動禁止で止める
                # （Claude が脚本家の点検の試合 toukou_001: キラーの移動とクロマクの連れ出しの2本をどちらも止めなかった）
                out.update((x, 'mv') for x in c['detail'] if isinstance(x, str) and x.startswith('C'))
    return out


def surplus(state):
    """距離0の勝ち筋のうち、ボード・カウンターの条件の余り（必要数を超えた分、各条件1まで）が最も大きいもの。"""
    best = 0.0
    for r in enumerate_routes(state):
        if r['total'] > 0:
            continue
        sp = []
        for c in r['conds']:
            if c['kind'] == 'board':
                area, n = c['detail']
                sp.append(min(1, state['boards'][area] - n))
            elif c['kind'] == 'counter' and len(c['detail']) == 3 and c['detail'][0] in state['chars']:
                ch, kind, n = c['detail']
                sp.append(min(1, state['chars'][ch].get(kind, 0) - n))
        if sp:
            best = max(best, min(sp))
    return best


class SearchMastermind(RouteMastermind):
    def __init__(self, rng, triples=16, cards=8, replies=16, lam=0.1, hold=0.25, days=8, loops=3, lam_choices=None,
                 explore=0.0, move_log=False, stop_replies=False, opp=0, urgency=1.0, force=1.0, stuck=0.3, v3=True, bluff=0.0, conceal=0.0, fit=True, bait=0.0, look=0, gamma=0.5, consp=0.0, stand=0.0, early=2.0, margin=0.0, hide=0.0, chain_ab=1.0, gate=None, enable=0.0, plan=0.0, plan_rank=0, recall=0.0, tempo=None, known=1.0, guide=None, worst=0.5, joseki=False, gwx_eve=0.0, hide_culprit=0.0, persist=0.0, joseki_top=6, cut_mm=0.0, lookday=False, calc=False, gamble=0.0):
        super().__init__(rng)
        # force: 止め手の枠を超える脅威（暗躍禁止は1日1枚、不安-1・移動禁止は各主人公1ループ1回）に加点。
        # stuck: このループで止められ続けている (対象, 種類) にまた置く手を減点（ユーザーの観点: 選択を迫りパワープレイを作る）
        self.force, self.stuck = force, stuck
        # recall: 前のループの同じ日に止められた (対象, 種類) へまた置く手を、止められた回数×recall 減点する（ループをまたぐ記憶）。
        # 目隠しの Claude が主人公の d27（seed 61）: 毎ループ1日目に委員長へ暗躍+2 を置き、暗躍禁止1枚で確実に潰された
        self.recall, self.blocked_day = recall, {}
        # v3: タイムトラベラーの候補全員の友好の手間を見込み、禁止エリアへの移動札を減点する（Claude が主人公の点検の試合 gen_s11_029）
        self.v3 = v3
        # bluff: 効くおとり。近い勝ち筋が暗躍を必要とするボードに、暗躍以外の札を置く手に加点する（主人公の暗躍禁止を引きつける。
        # Claude が主人公の点検の試合 gen_s11_014 で、病院のおとりに暗躍禁止を引かれて都市に暗躍+2 を通された）
        self.bluff = bluff
        self.int_boards = {}
        # conceal: 同じループで何度も伏せ札を置いた対象にまた置く手を減点（主人公は繰り返し狙われた対象を要と読む。
        # Claude が主人公の目隠し2試合で、キーパーソンの妹を毎日狙われて守る相手が割れた）
        self.conceal, self.tcount = conceal, {}  # {ループ: 本命の暗躍を置いたボード}（おとりは本命と違う場所に置く）
        # fit: ボードに置く札を暗躍の札に寄せる（ボードに不安・友好・移動の札は効かない。Claude が主人公の目隠し3本目で効かない札が多かった）
        self.fit = fit
        # bait: 主人公の不安-1（1ループ1回）を先に使わせる。脚本家が届く (対象, 不安) に不安を進めない札を伏せる手に加点
        # （主人公 readreach は届く対象の伏せ札に不安-1 を置く。不安-1 が残っている主人公がいる間だけ）
        self.bait = bait
        # look: 2日先読み（複数日の計画）。今日の上位 look 手それぞれについて、主人公の応手を解決した盤面から翌日の最善手を探し、
        # その評価を gamma 倍して足す（数日かけて脅威を仕込む手に価値を付ける。ユーザーとの対話で挙げた次の段階）
        self.look, self.gamma, self._reply_of = look, gamma, {}
        # consp: 目立ち方の減点。伏せ札の対象が主人公から役職持ちに見える確率（公開情報の推理）の和×consp
        # （段D の模型で重要度3位の k_tgt_nonperson。役職持ちに札を集めると主人公に要を読まれる）
        self.consp = consp
        # stand: 運ゲーの強要。今日止めないと負ける「立っている脅威」（不足0で締め切りが今日の筋）と今日の伏せ札の脅威の合計が、
        # 主人公の1日3枚を埋める／超える手に加点（ユーザーの観点。Claude が脚本家の s02_contract 最終日: 3枚すべての正解を要求して勝った）
        self.stand = stand
        # early: ループ1の押し切りの加点の倍率。バランス版の主人公はループ1で情報を取りに行き守りが薄い（Claude が脚本家の点検 toukou_001）
        self.early = early
        # margin: 余裕の加点。距離0になった勝ち筋のボード・カウンターの条件を、必要な数より1つ多く満たす手に加点
        # （主人公の友好能力〔神格[5] の暗躍除去・医者[2] など〕で1つ取られても崩れない。Claude が主人公の目隠し11本目:
        # 神社2のまま最終日に不安-1 を置き、神格[5] で1に戻されて守られた）
        self.margin = margin
        # hide: ループの勝ちが確定したら情報を隠す（ユーザーの観点「脚本家はループ勝利確定後は情報隠すのがつよい」）。
        # 確定の後は漏れの重み λ を hide 倍にし、役職の任意能力（クロマク・ミスリーダー・キラー）を使わない
        self.hide = hide
        # chain_ab: 脚本家の能力で、このループのこれからの事件の犯人に不安・暗躍を置く手の漏れの罰を chain_ab 倍にする（1 で今まで通り）。
        # 能力は札ではないので主人公は止められない。Claude が脚本家の d07 で、ミスリーダーが不安拡大の犯人自身に毎日不安を置いて事件を起こし、
        # 全ループを取った。search は漏れの罰でこの手を控え、刑事などへ回して事件を不発にしていた
        self.chain_ab = chain_ab
        # gate: 役職の能力は、使った後の最も近い負け筋の距離が gate 以下になるときだけ使う（漏れの罰は掛けない）。
        # それ以外の日は使わない（Claude の脚本家は d07・d27 で、勝ちが近い日にだけ能力を使い、割れた役職の筋で勝って隠しきった）
        self.gate = gate
        # enable: 補給の事件（不安拡大・行方不明・蝶の羽ばたき・邪気の汚染・流布）の犯人に、事件の日までに臨界へ届く不安+1 を置く手に加点。
        # 事件の効果は札ではないので主人公は止められない（Claude が脚本家の d27: 不安拡大を毎ループ起こし、不安2 と暗躍1 を別々の負け筋に振った。
        # 自動の脚本家は不安拡大の犯人を臨界にせず、事件が毎ループ不発だった）
        self.enable = enable
        # plan: ループ単位の計画。ループの1日目に最も近い負け筋を本命に決め、本命に関わる人物・ボードへの札に plan を加点する。
        # 本命の距離が2日続けて縮まなければ選び直す（Claude が脚本家の d27: ループの初めに「不安拡大で学者と委員長を同時に進め、3日目に仕上げる」と決め、
        # 日々の札をそれに合わせた。1日ごとの評価への加点（chain・gate・enable）は効きが小さかった）
        # plan_rank=1: 本命を2番目に近い（最も近いものと別の事件・ルールの）負け筋にする。主人公の守りは最も近い筋に集まるので、それを割る
        self.plan_w, self._plan, self.plan_rank = plan, None, plan_rank
        # ループ単位の計画（ユーザー「ループ単位で決めるやり方やってみて。何個か試さないと」2026-10-04）
        # tempo: 仕上げの日 F を決め、F より前は「F までに間に合う距離」より近づいても評価を上げない（距離を max(距離, F−今日) で数える）。
        #   脅威を早く見せずに複数の筋を間に合う所まで育て、F の前後で同時に仕上げる。'last'＝最終日、'mid'＝ループの中ほど。
        #   （目隠しの Claude が主人公の d27: 初日の暗躍+2 で脅威を明かし、1本ずつ止められた。中身を知る Claude の脚本家は仕上げの日を決めていた）
        self.tempo = tempo
        # known: 主人公に役職が割れた（公開の推理で p>0.9）人物が要の筋の重み。<1 は割れた筋を避ける（守られている）、>1 は割れた筋で勝って未公開の筋を温存
        self.known = known
        # guide: 脚本ごとの指針（engine/scripts/guides/<id>.json。中身を知る Claude が書く）。筋の重み・置く札の加点・事件の置き先・隠す人物
        self.guide = guide or {}
        # worst: 主人公の最善の応手での評価の重み（残りは応手の平均）。強い主人公は伏せ札の所を正確に止めるので、最悪の応手を重く見る
        # （目隠しの Claude が主人公の d16: 毎日都市ボードに暗躍を置き、暗躍禁止1枚で6日とも止められた。応手を無作為に引くので止める応手が候補に入らず、通ると見積もっていた）
        self.worst = worst
        # joseki: 定石の形の手を必ず候補に入れる（ユーザー「定石はお互いの知識として持っておきたい」2026-10-07）。
        # いまは「二枚押し」: 暗躍で負ける別々の2か所に暗躍+2 と暗躍+1 を同じ日に置く（暗躍禁止は1日1か所しか効かない）
        self.joseki = joseki
        # gwx_eve: 主人公が今日の友好+1/+2 で閾値に届く情報・守りの能力の持ち主に友好禁止を置く手に加点（定石 行-12・J35「閾値の前日に友好禁止」）
        # hide_culprit: ループの勝ちが確定した後、事件の犯人に不安−1・不安禁止を置いて事件を止める手を減点（実-09。札は公開されるので犯人を教える）
        self.gwx_eve, self.hide_culprit = gwx_eve, hide_culprit
        # persist: 暗躍の条件（人物・ボードの暗躍）をすでに満たした負け筋に、残りの不足が少ないほど大きい点を足す。暗躍は主人公が消しにくく、
        # ループの残りずっと効く脅威になる（d16 L2D1: 従者に暗躍2 を確保すれば5日目の遠隔殺人の標的。1日分の距離の評価では小さく見えた）
        # joseki_top: 定石の対象を取る負け筋の数（多いと候補が散る。d16 L1D1 の二枚押しが崩れた）
        self.persist, self.joseki_top = persist, joseki_top
        # cut_mm: ループの途中で終わる負け筋（主人公・キーパーソンの死）の重みを、残りの日数の割合×cut_mm だけ上げる。
        # 途中で終わらせれば主人公の残りの日の情報を断てる。ループ終了時の負けは主人公に全日分の情報を与える（ユーザーの指摘 2026-10-08）
        self.cut_mm = cut_mm
        # lookday: 手の評価で、札の解決後に脚本家能力フェイズとその日の事件まで進めた盤面を見る（主人公の readlook と同じ。
        # 学者（暗躍1・不安2）への不安+1 でターン終了に主人公が死ぬ、不安拡大の犯人を臨界にして学者へ不安2、を1日分の評価で見落としていた）
        self.lookday = lookday
        # calc: 負け筋の逆算（engine/route_calc）でこのループを必ず取れると出たら、その札を最優先で置き、残りの枠を探索の手で埋める
        # （ユーザー「脚本家は常にループ勝利が最優先」2026-10-08）
        self.calc = calc
        self.gamble, self.intx_seen, self.int_reached = gamble, {}, set()  # 取れないループでも、今日の賭けでこの確率以上なら賭ける（0 で使わない）
        if self.guide.get('lock_hide') and not self.hide:  # 勝ちが確定したら押さずに隠す（searchhide と同じ）
            self.hide = self.guide['lock_hide']
        self.blocked, self._last_mm = {}, []
        self.urgency = urgency  # 今日置かないと締め切りに間に合わない札への加点（点検の試合: 2日がかりの仕込みを始めなかった）
        self.stop_replies = stop_replies
        # opp: 主人公の模型（公開情報だけで推理する ReadingBlocker、世界 opp 個）を持ち、置き場所ごとの応手をそれで予想する
        self.shadow = None
        if opp:
            import random
            from selfplay.pc_v2 import ReadingBlocker
            self.shadow = ReadingBlocker(random.Random(rng.random()), hyps=opp)
        self.nt, self.nc, self.nr = triples, cards, replies
        self.lam, self.hold, self.days, self.loops = lam, hold, days, loops
        self.pub = PublicObserver()  # 公開情報だけの推理（主人公から見た自分の漏れを測る）
        # 段D（文脈付きバンディット）: ループごとに λ を lam_choices から無作為に選び、ループ開始時の局面と一緒に記録する
        self.lam_choices, self.loop_log, self._lam_loop = lam_choices, [], None
        self.explore, self.move_log = explore, (None if move_log is False else [])

    def _start_loop(self, s):
        if self._lam_loop == s['loop']:
            return
        self._lam_loop = s['loop']
        if self.lam_choices:
            self.lam = self.rng.choice(self.lam_choices)
        from engine.features import state_features
        self.pub._ensure(s)
        rec = {'loop': s['loop'], 'lam': self.lam, 'ent_start': self.pub.ded.role_entropy(), 'n_hyp': self.pub.ded.n_hyp()}
        rec.update({f'f_{k}': v for k, v in state_features(s, self.days, self.loops).items()})
        self.loop_log.append(rec)

    def observe(self, s, phase, events):
        self.pub.observe(s, phase, events)
        for e in events:  # 主人公が暗躍禁止を置いた所（公開された札）。賭けで暗躍+2 を見張られていない側へ置く
            if e.get('kind') == 'revealed':
                for x in e['cards']:
                    if x['by'] != 'M' and x['card'] == 'INTX':
                        self.intx_seen[x['target']] = self.intx_seen.get(x['target'], 0) + 1
        if phase == 'actions':  # 暗躍が2に届いた所（盤面は公開）。前のループで届いた筋は主人公に割れているとみなす
            for a, n in s['boards'].items():
                if n >= 2:
                    self.int_reached.add((s['loop'], 'B:' + a))
            for c, v in s['chars'].items():
                if v['int'] >= 2:
                    self.int_reached.add((s['loop'], c))
        if phase == 'actions' and self._last_mm:
            self._note_blocked(s, events)
        if self.shadow is not None:
            self.shadow.observe(s, phase, events)

    # ---- 伏せ札 ----
    def _focus(self, s):
        out = []
        for r in enumerate_routes(s)[:6]:
            if r['via']:
                out.append(r['via'])
            out += [c['detail'][0] for c in r['conds'] if isinstance(c['detail'][0], str) and c['detail'][0].startswith('C')]
            out += ['B:' + c['detail'][0] for c in r['conds'] if c['kind'] == 'board' and c['detail'][0]]
        alive = {c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)}
        return [t for t in dict.fromkeys(out) if (t in alive and t != 'C20') or t.startswith('B:')]  # 幻想には札を置けない

    def _triples(self, s):
        focus, allt = self._focus(s), self._targets(s)
        seen, out = set(), []
        # 指針の札の置き先を必ず候補に入れる（無作為の組だけでは指針の (対象, 札) がほとんど引かれず、加点が効かなかった。d27 の目隠し戦）
        gt = [t for t, _ in self._guide_today(s) if t in allt]
        gt = list(dict.fromkeys(gt))
        for k in range(min(len(gt), 6)):
            ts = gt[k:k + 3]
            rest = [x for x in allt if x not in ts]
            ts = ts + self.rng.sample(rest, 3 - len(ts))
            if frozenset(ts) not in seen:
                seen.add(frozenset(ts))
                out.append(ts)
        for k in range(self.nt * 3):
            pool = focus if k % 4 != 3 and len(focus) >= 1 else allt
            ts = self.rng.sample(pool, min(3, len(pool)))
            ts += self.rng.sample([t for t in allt if t not in ts], 3 - len(ts))
            key = frozenset(ts)
            if key not in seen:
                seen.add(key)
                out.append(ts)
            if len(out) >= self.nt:
                break
        return out

    def _shadow_reply(self, s, ts, order):
        """主人公の模型が、置き場所 ts を見て選ぶ応手（模型の記憶と乱数は元に戻す）。"""
        sh = self.shadow
        keep = (dict(sh.targeted), dict(sh.history), sh._loop, sh.rng.getstate())
        try:
            return sh.pc_cards(s, order, list(ts))
        finally:
            sh.targeted, sh.history, sh._loop = keep[0], keep[1], keep[2]
            sh.rng.setstate(keep[3])

    @staticmethod
    def _kind(card):
        return 'int' if card in ('INT1', 'INT2') else 'par' if card == 'PAR+' else 'mv' if card.startswith('MV_') else None

    def _note_blocked(self, s, events):
        """自分の伏せ札が止められたかを、行動解決の出来事から数える（暗躍は無効化、不安+1 は不安-1 で相殺、移動は起きなかった）。"""
        for x in self._last_mm:
            k = self._kind(x['card'])
            if not k:
                continue
            t = x['target']
            if k == 'int':
                hit = any(e.get('kind') == 'nullified' and e.get('target') == t for e in events)
            elif k == 'par':
                hit = any(e.get('kind') == 'par' and e.get('target') == t and e.get('card') == 'PAR-' for e in events)
            else:
                hit = not any(e.get('kind') == 'move' and e.get('char') == t for e in events)
            key = (s['loop'], t, k)
            self.blocked[key] = self.blocked.get(key, 0) + 1 if hit else 0
            if hit:
                self.blocked_day[(s['day'], t, k)] = self.blocked_day.get((s['day'], t, k), 0) + 1
                self.blocked_day[('any', t, k)] = self.blocked_day.get(('any', t, k), 0) + 1
        self._last_mm = []

    def _relevant(self, s, top=6):
        """近い勝ち筋が必要とする (対象, 種類)。脅威として数える札の判定に使う。"""
        out = set()
        for r in enumerate_routes(s)[:top]:
            for c in r['conds']:
                d = c['detail']
                if c['kind'] == 'counter' and d[1] in ('par', 'int') and c['deficit'] > 0:
                    out.add((d[0], d[1]))
                elif c['kind'] == 'board' and c['deficit'] > 0:
                    out.add(('B:' + d[0], 'int'))
                elif c['kind'] in ('same_area', 'alone_with', 'in_area') and c['deficit'] > 0:
                    out.update((x, 'mv') for x in d if isinstance(x, str) and x.startswith('C'))
        return out

    def _standing(self, s):
        """立っている脅威の数: 不足0で、締め切り（days の条件）が今日の筋。主人公は今日1枚使って崩さないと負ける。"""
        seen = set()
        for r in enumerate_routes(s):
            if r['total'] > 0:
                break
            if any(c['kind'] == 'days' and c['deficit'] == 0 for c in r['conds']):
                seen.add((r['id'], r['via']))
        return len(seen)

    def _plan_targets(self, s):
        """このループの本命の負け筋と、それに関わる人物・ボード（'B:XXX'）。"""
        from engine.routes import enumerate_routes as _er
        rs = _er(s)
        if not rs:
            return set()
        cur = {(r['id'], r['via']): r['total'] for r in rs}
        p = self._plan
        if p is None or p['loop'] != s['loop'] or (p['key'] not in cur):
            p = None
        elif cur[p['key']] >= p['best']:
            p['stale'] += 1
            if p['stale'] >= 2:
                p = None
        else:
            p['best'], p['stale'] = cur[p['key']], 0
        if p is None:
            srt = sorted(rs, key=lambda r: r['total'])
            r = srt[0]
            if self.plan_rank:
                r = next((x for x in srt if x['id'] != srt[0]['id']), r)
            p = {'loop': s['loop'], 'key': (r['id'], r['via']), 'best': r['total'], 'stale': 0}
        self._plan = p
        r = next(r for r in rs if (r['id'], r['via']) == p['key'])
        out = set()
        if r['via']:
            out.add(r['via'])
        for c in r['conds']:
            d = c['detail'][0] if isinstance(c['detail'], tuple) else c['detail']
            if isinstance(d, str) and d.startswith('C'):
                out.add(d)
            if c['kind'] == 'board':
                out.add('B:' + str(d))
            if c['kind'] in ('same_area', 'alone_with') and isinstance(c['detail'], tuple):
                out.update(x for x in c['detail'] if isinstance(x, str) and x.startswith('C'))
        return out

    def _enable_bonus(self, s, mm):
        from engine.resolve import CHARS as _C
        n = 0
        for i in s['script']['incidents']:
            if i['id'] not in ('SPREAD', 'MISSING', 'BUTTERFLY', 'CORRUPT', 'RUMOR_SPREAD') or i['day'] < s['day']:
                continue
            c = i['culprit']
            v = s['chars'].get(c)
            if not v or not v['alive']:
                continue
            need = _C[c]['limit'] - v['par']
            if 0 < need <= i['day'] - s['day'] + 1 and any(x['target'] == c and x['card'] == 'PAR+' for x in mm):
                n += 1
        return n

    def _force_bonus(self, s, rel, mm):
        n = {'int': 0, 'par': 0, 'mv': 0}
        for x in mm:
            k = self._kind(x['card'])
            if k and (x['target'], k) in rel:
                n[k] += 1
        cap = {'int': 1, 'par': sum(1 for p in 'ABC' if 'PAR-' not in s['used'][p]),
               'mv': 1 + sum(1 for p in 'ABC' if 'MVX' not in s['used'][p])}
        # 暗躍禁止は1日に1か所しか効かない（2枚以上は全部無効）。暗躍の脅威を2か所に出すと、主人公は必ずどちらかを通す（ユーザーの観点）
        over = 1.5 * max(0, n['int'] - cap['int']) + max(0, n['par'] - cap['par']) + max(0, n['mv'] - cap['mv'])
        # タイムトラベラーの最終日の敗北: 主人公は最終日までに友好を積む札を使わなければならない（友好禁止は効かない）。
        # その分、止め手に使える札（1日3枚）が減る。残りの日数で積み切るのに今日から必要なら、今日の札の枠から引く
        need = self._tt_need(s)
        if need:
            over += max(0, sum(n.values()) - (3 - need))
        # 禁止エリアへの移動は必ず無効（主人公にも一目で無駄と分かる）。点検の試合で入院患者に3回置いた
        if self.v3:
            over -= 0.5 * self._forbidden_moves(s, mm)
        if self.stand:
            tot = sum(n.values()) + self._stand_now
            over += self.stand * (max(0, tot - 3) + 0.5 * (tot >= 3))
        mult = self.early if s['loop'] == 1 else 1.0
        return self.force * mult * (over + 0.2 * sum(n.values()))

    def _tt_need(self, s):
        """主人公がタイムトラベラーの敗北を防ぐために、今日友好を置かなければならない人数（0〜3。同じ対象には1日1枚）。
        主人公は誰がタイムトラベラーか知らないので、公開情報の推理でタイムトラベラーでありうる者全員が対象になる
        （Claude が主人公の点検の試合: 候補5人全員に友好を積んで守りきった）。"""
        if not any(ph.role(s, c) == 'TT' for c in s['chars']):
            return 0
        if not self.v3:  # 旧: 本物のタイムトラベラー1人分だけを見込む
            v = next(v for c, v in s['chars'].items() if ph.role(s, c) == 'TT')
            if not (v['alive'] and v.get('present', True)) or v['gw'] >= 3:
                return 0
            short = 3 - v['gw']
            gw2 = any('GW2' not in s['used'][p] for p in 'ABC')
            days_needed = 1 if short == 1 or (short == 2 and gw2) else 2 if (short == 2 or gw2) else 3
            return 1 if days_needed >= self.days - s['day'] + 1 else 0
        self.pub._ensure(s)
        m, tot = self.pub.ded.marginals()
        gw2 = any('GW2' not in s['used'][p] for p in 'ABC')
        need = 0
        for c, v in s['chars'].items():
            if not (v['alive'] and v.get('present', True)) or v['gw'] >= 3 or not tot or m.get(c, {}).get('TT', 0) / tot < 0.05:
                continue
            short = 3 - v['gw']
            days_needed = 1 if short == 1 or (short == 2 and gw2) else 2 if (short == 2 or gw2) else 3  # 友好+2 は1ループ1回
            if days_needed >= self.days - s['day'] + 1:  # 今日を含めた残りの日数で、もう余裕が無い
                need += 1
        return min(need, 3)

    @staticmethod
    def _forbidden_moves(s, mm):
        """移動先が禁止エリアで必ず無効になる移動札の数。盤面の並び GRID は [[病院, 神社], [都市, 学校]]。"""
        from engine.resolve import CHARS, GRID
        pos = {a: (r, c) for r, row in enumerate(GRID) for c, a in enumerate(row)}
        at = {rc: a for a, rc in pos.items()}
        n = 0
        for x in mm:
            t = x['target']
            if not x['card'].startswith('MV_') or t not in s['chars'] or s['chars'][t]['area'] not in pos:
                continue
            r, c = pos[s['chars'][t]['area']]
            dest = at[{'MV_V': (1 - r, c), 'MV_H': (r, 1 - c), 'MV_D': (1 - r, 1 - c)}[x['card']]]
            if dest in CHARS[t].get('forbidden', []) and t not in s.get('unbound', []):
                n += 1
        return n

    @staticmethod
    def _bluffs(s, rel, mm, real=()):
        """効くおとりの数: 近い勝ち筋が暗躍を必要とするボードに置いた、暗躍以外の札（主人公には暗躍に見える）。
        幻想がいるボードの札は幻想に効くので、おとりには数えない。
        本命と同じ場所のおとりは数えない（同じ日に暗躍を置くボード、このループで暗躍を置いたボード）。
        同じ場所を本命とおとりの両方で狙うと、その場所が要だと主人公に読まれる（Claude が主人公の点検の試合 gen_s11_012）。"""
        il = s['chars'].get('C20')
        today = {x['target'] for x in mm if x['card'] in ('INT1', 'INT2')}
        n = 0
        for x in mm:
            t = x['target']
            if not t.startswith('B:') or x['card'] in ('INT1', 'INT2') or (t, 'int') not in rel or t in today or t in real:
                continue
            if il and il['alive'] and il.get('present', True) and il['area'] == t[2:]:
                continue
            n += 1
        return min(n, 1)  # 1枚で十分（2枚以上のおとりは本命を削る）

    def _stuck_cost(self, s, mm):
        c = 0
        for x in mm:
            k = self._kind(x['card'])
            if k:
                c += max(0, self.blocked.get((s['loop'], x['target'], k), 0) - 1)
        if self.recall:
            # 同じ日に止められた回数＋日を問わず止められた回数の半分（d27 の searchrecall 戦: 都市ボードへの暗躍+1 を日を変えて置き続け、毎回消された）
            c2 = sum(self.blocked_day.get((s['day'], x['target'], self._kind(x['card'])), 0)
                     + 0.5 * self.blocked_day.get(('any', x['target'], self._kind(x['card'])), 0) for x in mm if self._kind(x['card']))
            return self.stuck * c + self.recall * c2
        return self.stuck * c

    def _urgent(self, s):
        return urgent_targets(s, self.days)

    def _urgency_bonus(self, urgent, mm):
        n = 0
        for x in mm:
            if (x['target'], 'par') in urgent and x['card'] == 'PAR+':
                n += 1
            elif (x['target'], 'int') in urgent and x['card'] in ('INT1', 'INT2'):
                n += 1
        return self.urgency * n

    def _oracle_reply(self, s, ts, order):
        """実際の主人公の写し（乱数の状態と履歴を写す。推理の本体は共有、既定の設定では pc_cards は推理を書き換えない）に応手を出させる。
        ループ1で主人公に守りきられたとき、脚本家が弱いのか脚本そのものが弱いのかを分けるための上限（ユーザーの観点）。"""
        import copy
        import random as _r
        real = self.oracle_pc
        p = copy.copy(real)
        p.rng = _r.Random()
        p.rng.setstate(real.rng.getstate())
        for a in ('history', 'targeted', 'blocked'):
            if isinstance(getattr(real, a, None), dict):
                setattr(p, a, dict(getattr(real, a)))
        p.debug = False
        return p.pc_cards(copy_state(s), order, list(ts))

    def _replies(self, s, ts, order):
        """主人公の応手の候補: 伏せ札の置き場所と勝ち筋の担い手に止める札を置く手を中心に、ダミー（何もしない）も含める。"""
        if getattr(self, 'oracle_pc', None) is not None:  # oracle: 実際の主人公にこの置き場所への応手を出させる（上限の測定用）
            return [self._oracle_reply(s, ts, order)]
        if self.shadow is not None:
            return [self._shadow_reply(s, ts, order)]
        focus = list(dict.fromkeys(ts + self._focus(s)))
        allt = self._targets(s)
        out = [DUMMY_PC]
        hands = {p: [c for c in CARDS['protagonist_hand'] if not (c in ONCE[p] and c in s['used'][p])] for p in order}
        if self.stop_replies:  # 伏せ札の対象に止める札を置く応手を必ず含める（点検の試合: 毎日止められる筋を攻め続けた）
            for t in ts:
                for card in STOP:
                    p = next((p for p in order if card in hands[p]), None)
                    if p is None:
                        continue
                    rest = [x for x in allt if x != t]
                    pc = [{'by': p, 'target': t, 'card': card}]
                    for q in order:
                        if q != p:
                            u = self.rng.choice([x for x in rest if x not in {y['target'] for y in pc}])
                            pc.append({'by': q, 'target': u, 'card': self.rng.choice(hands[q])})
                    out.append(pc)
        for k in range(self.nr):
            taken, pc = set(), []
            for p in order:
                hand = [c for c in CARDS['protagonist_hand'] if not (c in ONCE[p] and c in s['used'][p])]
                pool = [t for t in (focus if k % 3 else allt) if t not in taken] or [t for t in allt if t not in taken]
                t = self.rng.choice(pool)
                taken.add(t)
                stops = [c for c in STOP if c in hand]
                pc.append({'by': p, 'target': t, 'card': self.rng.choice(stops if k % 3 and stops else hand)})
            out.append(pc)
        return out

    def _lookahead(self, s, scored):
        """上位 look 手を、翌日の最善手の評価（gamma 倍）を足して並べ直す。翌日は事件・能力を飛ばした近似（行動の解決後の盤面で日だけ進める）。"""
        top, rest, out = scored[:self.look], scored[self.look:], []
        keep = (self.nt, self.nc, self._reply_of)
        for item in top:
            v, mm = item[0], item[1]
            pc = keep[2].get(id(mm), [])
            try:
                after, _ = resolve_actions(s, mm + pc)
            except IllegalPlacement:
                out.append(item)
                continue
            self.nt, self.nc, self._reply_of = 6, 4, {}
            try:
                nxt = self.rank_moves(dict(after, day=s['day'] + 1))
            finally:
                self.nt, self.nc, self._reply_of = keep
            v2 = nxt[0][0] if nxt else 0.0
            out.append((v + self.gamma * v2,) + tuple(item[1:]))
        out.sort(key=lambda x: -x[0])
        return out + rest

    def _deal(self, hand, ts):
        cards = self.rng.sample(hand, 3)
        for _ in range(20 if self.fit else 0):  # ponytail: 棄却抽出。暗躍の札が足りなければ最後の抽出のまま
            if all(not t.startswith('B:') or c in ('INT1', 'INT2') for t, c in zip(ts, cards)):
                break
            cards = self.rng.sample(hand, 3)
        return cards

    def _val(self, s, mm, pc):
        try:
            after, _ = resolve_actions(s, mm + pc)
            if self.lookday:
                from selfplay.pc_v2 import after_incidents
                after = after_incidents(after)
        except IllegalPlacement:
            return None
        base = self._plan_score(s, after) if (self.tempo or self.known != 1.0 or self.guide or self.cut_mm) else score(after)
        if self.persist:
            base += self.persist * self._persist(after)
        return base + (self.margin * surplus(after) if self.margin else 0.0)

    def _plan_score(self, s, after):
        try:
            rs = enumerate_routes(after)
        except Exception:
            return 0.0
        floor = 0
        if self.tempo:
            f = self.days if self.tempo == 'last' else max(2, (self.days + 1) // 2 + 1)
            floor = max(0, f - s['day'])
        known = self._known_chars(s) if self.known != 1.0 else set()
        from selfplay.players import _weight
        cut = 1 + self.cut_mm * (self.days - s['day'] + 1) / self.days if self.cut_mm else 1.0
        return sum(_weight(after, r) * (self.known if r['via'] in known else 1.0) * self._guide_w(s, r) * (1.0 if is_loop_end(r) else cut)
                   / (1 + max(r['total'], floor if r['total'] > 0 else 0)) for r in rs)

    def _guide_w(self, s, r):
        if not self.guide:
            return 1.0
        w = next((g.get('w', 1.0) for g in self.guide.get('routes', []) if g['id'] == r['id'] and g.get('via') == r['via']), 1.0)
        if r['via'] in self.guide.get('hide', []) and s['loop'] < self.loops:
            w *= 0.3
        return w

    def _persist(self, st):
        v = 0.0
        try:
            rs = enumerate_routes(st)
        except Exception:
            return 0.0
        for r in rs:
            ints = [c for c in r['conds'] if (c['kind'] == 'counter' and c['detail'][1] == 'int') or c['kind'] == 'board']
            if ints and all(c['deficit'] == 0 for c in ints):
                rest = sum(c['deficit'] for c in r['conds'] if c not in ints and c['kind'] != 'days')
                v += 1 / (1 + rest)
        return v

    def _gw_eve(self, s):
        """主人公が今日の友好+1（＋友好+2 が残っていれば+2）で閾値に届く、情報・守りの能力の持ち主 {人物: 閾値までの不足}。"""
        from selfplay.players import INFO_ABILITIES
        from selfplay.pc_v2 import DEFENSE_ABILITIES
        gw2 = any('GW2' not in s['used'][p] for p in 'ABC')
        out = {}
        for (cid, idx), need in {**INFO_ABILITIES, **DEFENSE_ABILITIES}.items():
            ch = s['chars'].get(cid)
            if ch and ch['alive'] and ch.get('present', True) and need - (2 if gw2 else 1) <= ch['gw'] < need:
                out[cid] = min(out.get(cid, 9), need - ch['gw'])
        return out

    def _joseki_moves(self, s, hand):
        """定石の手 {置き先の組: [手, ...]}。二枚押し: 暗躍の脅威になる2か所 a・b に暗躍+2・暗躍+1（両方の向き）、3枚目は置き先も札も無作為。"""
        out = {}
        if 'INT2' not in hand or 'INT1' not in hand:
            return out
        ints = {t for t, k in self._relevant(s, top=self.joseki_top) if k == 'int'}
        if self.shadow is not None:  # 主人公の推理で脅威に見える所（おとりになる）も対象に。d16 の L1: 神社への暗躍+1 は本当は意味がないが、主人公には封印されしモノに見える
            for sc in self.shadow._worlds(s)[:8]:
                try:
                    ints |= {t for t, k in self._relevant(dict(s, script={**s['script'], **sc})) if k == 'int'}
                except Exception:
                    continue
        ints = sorted(ints)[:8]
        allt = self._targets(s)
        rest_cards = [c for c in hand if c not in ('INT2', 'INT1')]
        for i, a in enumerate(ints):
            for b in ints[i + 1:]:
                for x, y in ((a, b), (b, a)):
                    third = self.rng.choice([u for u in allt if u not in (x, y)])
                    ts = (x, y, third)
                    out.setdefault(ts, []).append([{'by': 'M', 'target': x, 'card': 'INT2'}, {'by': 'M', 'target': y, 'card': 'INT1'},
                                                   {'by': 'M', 'target': third, 'card': self.rng.choice(rest_cards)}])
        return out

    def _guide_today(self, s):
        out = []
        for g in (self.guide or {}).get('cards', []):
            if s['loop'] in g.get('loop', [s['loop']]) and s['day'] in g.get('day', [s['day']]):
                out += [(g['target'], c) for c in g['card']]
        return out

    def _guide_deal(self, s, hand, ts):
        """置き先 ts に、指針の札を当てられるだけ当て、残りは無作為の札にした手。"""
        want = {}
        for t, c in self._guide_today(s):
            if t in ts and t not in want and c in hand:
                want[t] = c
        if not want:
            return None
        left = list(hand)
        for c in want.values():
            if c in left:
                left.remove(c)
            else:
                return None
        rest = [t for t in ts if t not in want]
        fill = self.rng.sample(left, len(rest)) if len(left) >= len(rest) else None
        if fill is None:
            return None
        fm = dict(zip(rest, fill))
        return [{'by': 'M', 'target': t, 'card': want.get(t, fm.get(t))} for t in ts]

    def _guide_cards(self, s, mm):
        v = 0.0
        for g in self.guide.get('cards', []):
            if s['loop'] in g.get('loop', [s['loop']]) and s['day'] in g.get('day', [s['day']]):
                if any(x['target'] == g['target'] and x['card'] in g['card'] for x in mm):
                    v += g.get('w', 0.5)
        return v

    def loop_setup(self, s, script):
        ch = super().loop_setup(s, script)
        setup = self.guide.get('setup') if self.guide else None
        if setup:  # 指針で決めたループの準備（学者の特性など）
            ch = {**ch, **{k: v for k, v in setup.items() if k != 'init'}, 'init': {**ch.get('init', {}), **setup.get('init', {})}}
        return ch

    def incident_choice(self, s, inc):
        pref = self.guide.get('incidents', {}).get(inc['id']) if self.guide else None
        if pref:
            import itertools
            keys = list(pref)
            for combo in itertools.product(*[pref[k] for k in keys]):
                ch = dict(zip(keys, combo))
                if len(set(combo)) < len(combo) and inc['id'] == 'SPREAD':
                    continue
                trial = copy_state(s)
                try:
                    ph.run_incident(trial, inc, ch)
                except ph.LoopEnd:
                    return ch
                except Exception:
                    continue
                return ch
        return super().incident_choice(s, inc)

    def _known_chars(self, s):
        if getattr(self, '_known_cache', (None,))[0] != (s['loop'], s['day']):
            self.pub._ensure(s)
            m, tot = self.pub.ded.marginals()
            ks = {c for c, d in m.items() if tot and max(d.values()) / tot > 0.9 and max(d, key=d.get) != 'PERSON'}
            self._known_cache = ((s['loop'], s['day']), ks)
        return self._known_cache[1]

    def plan_day(self, s, player, loop, day, k=4, n=2):
        """ロールアウト型（searchroll）: 日の始めに、上位 k 手をそれぞれ両陣営の写しでループの終わりまで n 回回し、
        ループを取れた率が最も高い手をその日の手に決める（上限の近似。ループ1で守られたのが脚本家の弱さか脚本の弱さかを分ける。ユーザーの観点）。"""
        import copy
        import random as _r
        from engine import phases as ph
        from selfplay.run import play_day
        scored = self.rank_moves(copy_state(s))
        best, best_key = None, None
        for rank, item in enumerate(scored[:k]):
            wins = 0
            for i in range(n):
                s2, pl = copy.deepcopy(s), copy.deepcopy(player)
                pl.mm._forced, pl.mm._roll = None, False
                pl.mm.rng = _r.Random(f'{loop}:{day}:{rank}:{i}:m')
                pl.pc.rng = pl.rng = _r.Random(f'{loop}:{day}:{rank}:{i}:p')
                box = {'s': s2}

                def log(kind, rec, _pl=pl, _box=box):
                    if kind == 'events' and 'events' in rec:
                        _pl.observe(_box['s'], rec.get('phase'), rec['events'])
                try:
                    s2 = play_day(s2, pl, loop, day, log, box, mm_override=item[1])
                    for d in range(day + 1, self.days + 1):
                        s2['day'] = d
                        s2 = play_day(s2, pl, loop, d, log, box)
                    wins += bool(ph.loop_end_loss(s2))
                except ph.LoopEnd as e:
                    wins += bool(e.loss) or bool(ph.loop_end_loss(box['s']))
            key = (wins, -rank)
            if best_key is None or key > best_key:
                best, best_key = item[1], key
        self._forced = best

    def mm_cards(self, s):
        if getattr(self, '_forced', None):
            mm, self._forced = self._forced, None
            self._last_mm = list(mm)
            return mm
        self._start_loop(s)
        self._reply_of = {}
        scored = self.rank_moves(s)
        if not scored:
            return super().mm_cards(s)
        if self.look and s['day'] < self.days:
            scored = self._lookahead(s, scored)
        k = 0
        if self.explore and self.rng.random() < self.explore:  # 段D: 上位8手から無作為に選ぶ（手の良し悪しを実際の結果で学ぶため）
            k = self.rng.randrange(min(8, len(scored)))
        v, mm, v_reply, v_mean = scored[k]
        if self.calc:
            fp = forced_plan(s, self.days, hidden=bool(self.gamble), watch=self._watch(s) if self.gamble else None)
            if fp['locked'] or self._locked(s):
                # 確定したループは押さずに隠す（メモリ mastermind-hide-after-locked-loop）: 閾値に一番近い情報・守りの能力の持ち主に友好禁止。
                # 残りの枠も押す札（不安+1・暗躍）は置かない: 犯人・負け筋を教えるだけ（問題 mm16c-l1d2-hide）
                eve = self._gw_eve(s)
                gwx = [(min(sorted(eve), key=eve.get), 'GWX')] if eve else []
                mm = self._calc_merge(s, gwx, scored, skip=('PAR+', 'INT1', 'INT2'))
            elif fp['forced'] and fp['move']:
                mm = self._calc_merge(s, fp['move'], scored)
            elif self.gamble and fp.get('p', 0) >= self.gamble and fp.get('mix'):
                # 今日の賭け（伏せ札の中身が見えない主人公への二択。route_calc.solve_hidden）: 混ぜ方から1つ引く。効果の無い札は友好禁止・不安−1・移動で
                # 見張られている所（_watch）を避けて暗躍+2 を置く。同じなら混ぜ方どおり引く
                w = self._watch(s)

                def watched(mv):
                    return sum(w.get(tg, 0) for tg, c in mv if c == 'INT2')
                low = min(watched(mv) for _, mv in fp['mix'])
                cand = [(pr, mv) for pr, mv in fp['mix'] if watched(mv) == low]
                r, acc = self.rng.random() * sum(pr for pr, _ in cand), 0.0
                for pr, mv in cand:
                    acc += pr
                    if r <= acc:
                        break
                mm = self._calc_merge(s, self._bluff_cards(s, mv), scored)
        self._last_mm = list(mm)  # 止められたかを行動解決の後に数える（stuck）
        for x in mm:
            self.tcount[(s['loop'], x['target'])] = self.tcount.get((s['loop'], x['target']), 0) + 1
        for x in mm:  # 本命の暗躍を置いたボード（bluff のおとりの場所から外す）
            if x['target'].startswith('B:') and x['card'] in ('INT1', 'INT2'):
                self.int_boards.setdefault(s['loop'], set()).add(x['target'])
        if self.move_log is not None:
            self._close_move(s)
            self.pub._ensure(s)
            self.move_log.append({'loop': s['loop'], 'day': s['day'], 'rank': k, 'val': v, 'val_best': scored[0][0],
                                  'val_reply': v_reply, 'val_mean': v_mean, 'score_before': score(s),
                                  'ent_before': self.pub.ded.role_entropy(), 'cards': [(x['target'], x['card']) for x in mm],
                                  'feat': self._move_features(s, mm), 'sfeat': state_features(s, self.days, self.loops)})
        return mm

    def _watch(self, s):
        """主人公に見張られている度合い {置き先: 点}: 暗躍禁止が置かれた回数＋前のループで暗躍が2に届いた（筋が割れた）ループ数×3。"""
        w = dict(self.intx_seen)
        for L, t in self.int_reached:
            if L < s['loop']:
                w[t] = w.get(t, 0) + 3
        return w

    @staticmethod
    def _bluff_cards(s, mv):
        """賭けの手の BLUFF を効果の無い札に置き換える（ボードには不安−1・友好禁止、人物には友好禁止・移動）。"""
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        for _, c in mv:
            if c != 'BLUFF':
                hand.remove(c)
        out = []
        for t, c in mv:
            if c == 'BLUFF':
                pref = ('PAR-', 'GWX') if t.startswith('B:') else ('GWX', 'MV_V', 'MV_H', 'MV_D')
                c = next((x for x in pref if x in hand), None)
                if c is None:
                    continue
                hand.remove(c)
            out.append((t, c))
        return out

    @staticmethod
    def _calc_merge(s, forced, scored, skip=()):
        """逆算の札を先に置き、残りの枠を評価の高い手の札で埋める（同じ対象に2枚・手札に無い札・skip の札は飛ばす）。"""
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        out = []
        for t, c in forced:
            hand.remove(c)
            out.append({'by': 'M', 'target': t, 'card': c})
        for _, mm, *_ in scored:
            for x in mm:
                if len(out) == 3:
                    return out
                if x['card'] in hand and x['card'] not in skip and x['target'] not in {o['target'] for o in out}:
                    hand.remove(x['card'])
                    out.append(x)
        return out if len(out) == 3 or not skip else SearchMastermind._calc_merge(s, [(o['target'], o['card']) for o in out], scored)

    def rank_moves(self, s):
        """候補の手を評価の高い順に [(評価, 手, 応手に対する評価, 平均の評価)]。"""
        plan_targets = self._plan_targets(s) if self.plan_w else set()
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        order = ['A', 'B', 'C']
        i = order.index(s['leader'])
        order = order[i:] + order[:i]
        last_day = s['day'] == self.days
        best, best_v = None, None
        scored = []  # (評価, 手)。探索（explore）と、手の記録（move_log）に使う
        urgent = self._urgent(s) if self.urgency else set()
        rel = self._relevant(s) if self.force else set()
        self._stand_now = self._standing(s) if self.stand else 0
        pnp = {}
        if self.consp:
            self.pub._ensure(s)
            m, tot = self.pub.ded.marginals()
            pnp = {c: 1 - d.get('PERSON', 0) / tot for c, d in m.items()} if tot else {}
        baitable = set()
        if self.bait and any('PAR-' not in s['used'][p] for p in 'ABC'):
            baitable = {t for t in reachable_targets(s, self.days) if t[1] == 'par'}
        forced = self._joseki_moves(s, hand) if self.joseki else {}
        eve = self._gw_eve(s) if self.gwx_eve else {}
        if eve and 'GWX' in hand:  # 閾値の前日に友好禁止を置く手を候補に必ず入れる（残り2枚は無作為）
            allt = self._targets(s)
            for c in sorted(eve):
                for _ in range(3):
                    others = self.rng.sample([u for u in allt if u != c], 2)
                    cards = self.rng.sample([x for x in hand if x != 'GWX'], 2)
                    ts = (c, *others)
                    forced.setdefault(ts, []).append([{'by': 'M', 'target': c, 'card': 'GWX'}] +
                                                      [{'by': 'M', 'target': u, 'card': k} for u, k in zip(others, cards)])
        locked = bool(self.hide_culprit) and self._locked(s)
        culprits_today = {i['culprit'] for i in s['script']['incidents'] if i['day'] >= s['day']} if locked else set()
        for ts in self._triples(s) + [list(k) for k in forced]:
            cands = list(forced.get(tuple(ts), []))
            for _ in range(self.nc):
                cards = self._deal(hand, ts)
                cands.append([{'by': 'M', 'target': t, 'card': c} for t, c in zip(ts, cards)])
            gd = self._guide_deal(s, hand, ts) if self.guide else None
            if gd:
                cands.append(gd)
            replies = self._replies(s, ts, order)
            table = [[self._val(s, mm, pc) for pc in replies] for mm in cands]
            # 主人公は札の中身を知らない: 中身の候補の平均で脚本家の評価が最小の応手を選ぶ
            def mean_over_mm(j):
                xs = [row[j] for row in table if row[j] is not None]
                return sum(xs) / len(xs) if xs else float('inf')
            jstar = min(range(len(replies)), key=mean_over_mm)
            for mm, row in zip(cands, table):
                xs = [x for x in row if x is not None]
                if not xs or row[jstar] is None:
                    continue
                v = self.worst * row[jstar] + (1 - self.worst) * sum(xs) / len(xs)
                if not last_day:
                    v -= self.hold * sum(1 for x in mm if x['card'] in ONCE['M'])
                if urgent:
                    v += self._urgency_bonus(urgent, mm)
                if any(x['card'] == 'GWX' and ph.role(s, x['target']) == 'TT' for x in mm if x['target'] in s['chars']):
                    v -= 1.0  # タイムトラベラーに友好禁止は効かず、無視したことが公開されて正体が割れる
                if self.force:
                    v += self._force_bonus(s, rel, mm)
                if self.enable:
                    v += self.enable * self._enable_bonus(s, mm)
                if self.plan_w:
                    v += self.plan_w * sum(1 for x in mm if x['target'] in plan_targets)
                if self.guide:
                    v += self._guide_cards(s, mm)
                if self.gwx_eve:
                    v += self.gwx_eve * sum(1 for x in mm if x['card'] == 'GWX' and x['target'] in eve)
                if self.hide_culprit and locked:
                    v -= self.hide_culprit * sum(1 for x in mm if x['card'] in ('PAR-', 'PARX') and x['target'] in culprits_today)
                if self.stuck:
                    v -= self._stuck_cost(s, mm)
                if self.conceal:
                    v -= self.conceal * sum(self.tcount.get((s['loop'], x['target']), 0) for x in mm)
                if self.consp:
                    v -= self.consp * sum(pnp.get(x['target'], 0) for x in mm)
                if self.bait and baitable:
                    v += self.bait * sum(1 for x in mm if (x['target'], 'par') in baitable and x['card'] in ('PARX', 'GWX', 'PAR-'))
                if self.bluff:
                    v += self.bluff * self._bluffs(s, rel if self.force else self._relevant(s), mm, self.int_boards.get(s['loop'], set()))
                scored.append((v, mm, row[jstar], sum(xs) / len(xs)))
                self._reply_of[id(mm)] = replies[jstar]
                if best_v is None or v > best_v:
                    best, best_v = mm, v
        scored.sort(key=lambda x: -x[0])
        return scored

    def _close_move(self, s):
        """直前の手の結果（次の日の始めの盤面）を書き込む。ループが変わっていれば、結果はループの終わりで決まる（league が埋める）。"""
        if self.move_log and 'score_after' not in self.move_log[-1] and self.move_log[-1]['loop'] == s['loop']:
            self.move_log[-1]['score_after'] = score(s)
            self.move_log[-1]['ent_after'] = self.pub.ded.role_entropy()

    def _move_features(self, s, mm):
        """手の特徴量: 札の種類の数、対象が勝ち筋に関わるか（features.card_relevance）、対象の役職の種類。"""
        from engine.features import card_relevance
        f = {f'n_{c}': 0 for c in ('PAR+', 'PAR-', 'PARX', 'GWX', 'INT1', 'INT2', 'MV_D', 'MV_V', 'MV_H')}
        rel = []
        for x in mm:
            f['n_' + x['card']] += 1
            rel.append(card_relevance(s, x['target']))
        rel = sorted(rel)
        f.update({'rel_min': rel[0], 'rel_mid': rel[1], 'rel_max': rel[2], 'n_board': sum(x['target'].startswith('B:') for x in mm),
                  'n_role_target': sum(1 for x in mm if not x['target'].startswith('B:') and ph.role(s, x['target']) != 'PERSON')})
        return f

    # ---- 脚本家能力 ----
    def _lam(self, s):
        # 最終ループはループを取らないと負けなので、進みを重く見る（λ を下げる）
        lam = self.lam * (0.5 if s['loop'] >= self.loops else 1.0)
        return lam * self.hide if self.hide and self._locked(s) else lam

    def _locked(self, s):
        """このループの勝ちが確定したか: ループ終了時の敗北条件が成立していて、主人公に崩す手がない。
        蝶の羽ばたきの発生とフレンドの死亡は戻らない。ボード・カウンターの条件は、1つ取られても残る余りがあるときだけ確定とみなす
        （神格[5] などで1つ取り除かれる。ponytail: 取り除く能力の有無は見ていない）。"""
        r = ph.loop_end_loss(copy.deepcopy(s))  # 写しで呼ぶ（フレンドの役職公開を書き込むため）
        return bool({'Y_FUTURE', 'FRIEND'} & set(r)) or (bool(r) and surplus(s) >= 1)

    def killer_pick(self, s):
        if self.hide and self._locked(s):
            return []  # 勝ちは確定済み。キラーの能力は役職を明かすだけ
        return super().killer_pick(s)

    def mm_ability(self, s, actor, ability=None):
        if self.hide and self._locked(s):
            return None
        a = s['chars'][actor]
        opts = [c for c in ph.alive_in(s, a['area'])]
        if (ability or ph.role(s, actor)) == 'KUROMAKU':
            opts.append('B:' + a['area'])
        self.pub._ensure(s)
        if self.gate is not None:
            from engine.routes import enumerate_routes as _er
            best, best_d = None, None
            for t in opts:
                trial = copy.deepcopy(s)
                try:
                    ph.mm_ability(trial, actor, t, ability)
                except IllegalPlacement:
                    continue
                rs = _er(trial)
                d = min((r['total'] for r in rs), default=99)
                if d <= self.gate and (best_d is None or d < best_d):
                    best, best_d = t, d
            return best
        best, best_v = None, score(s)
        for t in opts:
            trial = copy.deepcopy(s)
            try:
                ev = ph.mm_ability(trial, actor, t, ability)
            except IllegalPlacement:
                continue
            area = t[2:] if t.startswith('B:') else s['chars'][t]['area']
            obs = [('mm_effect', {'counter': 'int' if 'int' in ev[0] else 'par', 'present': ph.alive_in(s, area),
                                  'school_int': trial['boards']['SCH']})]
            lam = self._lam(s)
            if self.chain_ab != 1.0 and not t.startswith('B:') and any(
                    i['culprit'] == t and i['day'] >= s['day'] for i in s['script']['incidents']):
                lam *= self.chain_ab
            v = score(trial) - lam * self.pub.ded.trial(obs)
            if v > best_v:
                best, best_v = t, v
        return best

    def choose_rule_x(self, s, options):
        """情報屋で公開するルールXを選ぶ: 主人公の推理が進まない方（漏れの小さい方）。"""
        self.pub._ensure(s)
        return min(options, key=lambda x: self.pub.ded.trial([('rule_x_revealed', {'rule': x})]))

    def refuse(self, s, cid):
        """友好能力を拒否するか（B5）。拒否は「友好無視を持つ」と明かす（漏れ）。受け入れると能力が効く。
        役職を明かす能力を持つキャラクター（サラリーマン・巫女）は拒否する。それ以外は、拒否で漏れる量が小さければ拒否し、
        大きければ受け入れる（能力の効き目より、役職の候補を絞られる方が痛いと見る）。"""
        if ph.role(s, cid) not in ('KILLER', 'KUROMAKU', 'FACTOR'):
            return False
        if cid in ('C04', 'C06'):
            return True
        self.pub._ensure(s)
        return self.pub.ded.trial([('refused', {'char': cid})]) < 0.5

    def _leak_cost(self, s, o):
        """カルティストの任意能力は、そのエリアにカルティストがいると明かす。"""
        if not o:
            return 0.0
        self.pub._ensure(s)
        t = o['target']
        area = t[2:] if t.startswith('B:') else s['chars'][t]['area']
        return self._lam(s) * self.pub.ded.trial([('intx_ignored', {'present': ph.alive_in(s, area)})])

    def _leak_cost_ability(self, s, cid):
        """脚本家が医者[2] を使うと、医者が友好無視を持つと明かす（拒否と同じ情報）。"""
        self.pub._ensure(s)
        return self._lam(s) * self.pub.ded.trial([('refused', {'char': cid})])
