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
import itertools
import os
import sys
import random

from engine import phases as ph
from engine import sets
from engine.resolve import CARDS, ONCE, IllegalPlacement, card_targets, copy_state, resolve_actions
from engine.features import state_features
from engine.route_calc import forced_plan
from engine.routes import enumerate_routes
from selfplay.players import DUMMY_PC, PublicObserver, RouteMastermind, cultist_options, score

STOP = ('MVX', 'PAR-', 'INTX', 'MV_V', 'MV_H')
_PAR = None  # 並列の計算（SearchMastermind._par_tables）に fork で渡す（関数, 組の一覧）


def _par_job(i):
    one, jobs = _PAR
    return one(jobs[i])


def urgent_targets(s, days, top=6):
    """近い勝ち筋のうち、締め切り（事件の日、なければループの最終日）までの日数と不足のカウンターが釣り合い、
    今日置かないと間に合わない (対象, カウンター) の集合。伏せ札は1日に同じ対象へ1枚なので、不足1につき1日かかるとみなす。
    脚本家は ここに置く札に加点し（searchurg）、主人公は ここを止める札に加点する（readurg）。"""
    out = set()
    for r in enumerate_routes(s)[:top]:
        dl = next((c['deficit'] for c in r['conds'] if c['kind'] == 'days' and c['detail'][0] != '登場'), days - s['day'])
        for c in r['conds']:
            if c['kind'] == 'counter' and c['detail'][1] in ('par', 'int') and 0 < c['deficit'] >= dl + 1:
                out.add((c['detail'][0], c['detail'][1]))
            elif c['kind'] == 'board' and 0 < c['deficit'] >= dl + 1:
                out.add(('B:' + c['detail'][0], 'int'))
    return out


# ループの終わりに負けが決まる筋（止められなくてもループは最後まで続き、主人公は情報を取り続けられる）。それ以外はループの途中で終わる（情報も断たれる）
LOOP_END_ROUTES = {'Y_SEAL', 'Y_CONTRACT', 'Y_FUTURE', 'Y_BOMB', 'FRIEND', 'TT_LOSS'} | {
    r for sid in sets.ids() for r, v in sets.load(sid)['rules'].items() if v.get('loop_end_loss')}  # ほかのセットのルールY（守るべき場所など）


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
        dl = next((c['deficit'] for c in r['conds'] if c['kind'] == 'days' and c['detail'][0] != '登場'), days - s['day'])
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


ALL_IN_CRACK = 0.5  # 全力モード: 本当の配役の重みがこれ以上なら、最後の戦いで外させる見込みが無いとみなす


def _wins_today(s, days):
    """今日のターン終了まで（最終日ならループ終了時も）に成り立つ負け筋があるか: 残り0で、待ちの日数（事件の日など）も0。"""
    for r in enumerate_routes(s):
        if r['total'] > 0:
            return False
        if any(c['kind'] == 'days' and c['deficit'] > 0 for c in r['conds']):
            continue
        if (r.get('end') or 'ループ終了時' in r['goal']) and s['day'] < days:
            continue  # ループ終了時に決まる筋は、最終日でなければまだ崩されうる
        return True
    return False


def par_compute(payload, idx, k):
    """補助の Web Worker（web/helper.js）が呼ぶ: 脚本家・盤面・置き場所の組（pickle）を受け、i % k == idx の組を計算して pickle で返す
    （SearchMastermind._par_browser の相手）。"""
    import pickle
    mm, s, order, jobs = pickle.loads(bytes(payload))
    return pickle.dumps({i: mm._par_one(s, order, jobs[i]) for i in range(idx, len(jobs), k)}, protocol=5)


class SearchMastermind(RouteMastermind):
    def __init__(self, rng, triples=16, cards=8, replies=16, lam=0.1, hold=0.25, days=8, loops=3, lam_choices=None,
                 explore=0.0, move_log=False, stop_replies=False, opp=0, urgency=1.0, force=1.0, stuck=0.3, v3=True, bluff=0.0, conceal=0.0, fit=True, bait=0.0, look=0, gamma=0.5, consp=0.0, stand=0.0, early=2.0, margin=0.0, hide=0.0, chain_ab=1.0, gate=None, enable=0.0, plan=0.0, plan_rank=0, recall=0.0, tempo=None, known=1.0, guide=None, worst=0.5, joseki=False, gwx_eve=0.0, hide_culprit=0.0, persist=0.0, joseki_top=6, cut_mm=0.0, lookday=False, calc=False, gamble=0.0, double=False, align=False, dg=0.0, surplus=1.0, fill=True):
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
        self.align = align  # 位置の筋を揃える移動（キラーをキーパーソンの所へ等）を候補に必ず入れる（d24: calcD が4日目の移動斜めを逃した）
        self.dg = dg  # calcD と calcG の合成: 賭けが閾値に届かない日も、賭けの手を候補に入れ、評価に dg×確率を足す
        self._gbonus = {}
        self.fill = fill  # 何も起きない札（予想した応手で）を、無作為のままにせず保険・妨害・ブラフに替える（_fill_idle）
        self.surplus = surplus  # 負け筋に要る数を満たし、主人公が取り除けない暗躍への上乗せの罰（バグの試合: 病院の暗躍 3 → 4）
        self.double = double  # 二重の脅威（同じ日に2か所を押す手）を候補に必ず入れる（d07: 暗躍禁止は1日1か所しか効かない）
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
        if getattr(self, 'reader', None) is not None:
            self.reader.observe(s, phase, events)

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
            k = getattr(self, 'shadow_k', 1) if s['day'] >= self.days else 1  # 最終日だけ（今日成り立つ筋を最優先にする日は、予想の揺れに引かれやすい）
            if k == 1:
                return [self._shadow_reply(s, ts, order)]
            # 模型の応手は内部の乱数で揺れる（gen_s11_018 L3D7: 同じ局面で病院の暗躍禁止を置いたり置かなかったりした）。
            # k 通り引いて並べ、主人公の最善の応手（rank_moves の jstar）で比べる
            sh, keep, out = self.shadow, self.shadow.rng.getstate(), []
            for i in range(k):
                sh.rng.seed(f"{s['loop']}:{s['day']}:{','.join(ts)}:{i}")
                out.append(self._shadow_reply(s, ts, order))
            sh.rng.setstate(keep)
            return out
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
        if getattr(self, 'need_plan', False):
            base = self._need_score(s, after)
        else:
            base = self._plan_score(s, after) if (self.tempo or self.known != 1.0 or self.guide or self.cut_mm) else score(after)
        if getattr(self, 'sk_leak', 0):
            base -= self.sk_leak * self._sk_leak(s, after)
        if getattr(self, 'last_day_now', False) and s['day'] >= self.days:
            # ループの最終日は、今日のうちに成り立つ筋（残り0）の数を最優先にし、同じなら普段の評価で比べる（×100 で辞書式の近似。
            # 目隠し対戦 gen_s11_018 L3D7: 教祖を学校へ動かせばウイルスのシリアルキラーと2人きりでフレンドが死ぬのに、不安を2か所に足す手を選んだ。
            # 残り0の筋だけで比べた版は、勝てない日に手が無作為になり悪化した。runs/blind_calcGB_2026-10-16）
            from selfplay.players import _weight
            try:
                base += 100 * sum(_weight(after, r) for r in enumerate_routes(after) if r['total'] == 0)
            except Exception:
                pass
        if self.persist:
            base += self.persist * self._persist(after)
        return base + (self.margin * surplus(after) if self.margin else 0.0)

    @staticmethod
    def _days_needed(r, int2):
        """筋を成り立たせるのに脚本家が要る日数の下限（主人公の妨害なし）。同じ対象には1日1枚・1日3枚・暗躍+2 は1ループ1回。
        位置の条件は2人を同じ日に動かせるとして半分。'days' は締め切りなので数えない。"""
        per, cards = {}, 0
        for c in r['conds']:
            d = c['deficit']
            if d <= 0 or c['kind'] in ('days', 'counter_max'):
                continue
            if d >= 99:
                return 99
            if c['kind'] == 'counter' or c['kind'] == 'incident_par':
                t = c['detail'][0]
                n = max(1, d - 1) if (int2 and c['kind'] == 'counter' and c['detail'][1] == 'int') else d
            elif c['kind'] == 'board':
                t, n = 'B:' + c['detail'][0], (max(1, d - 1) if int2 else d)
            else:  # same_area・alone_with・in_area
                t, n = ('pos',) + tuple(c['detail']), -(-d // 2)
            per[t] = per.get(t, 0) + n
            cards += d
        return max(max(per.values(), default=0), -(-cards // 3))

    def _need_score(self, s, after):
        """必要な手数から逆算した評価（ユーザー「必要な手数を先に出して、必要なタイミングからやれるようにしたい」2026-10-16）。
        筋ごとに、今日の行動の後の残りの日数（事件の筋は事件の日、他はループの最終日まで）と必要な日数を比べる。
        間に合わない筋は 0。余裕が need_margin 日より大きい筋は、今日の進みを数えない（朝の距離で数える＝早く押して漏らさない）。"""
        from selfplay.players import _weight
        key = (s['loop'], s['day'], id(s))
        if getattr(self, '_need_cache', (None,))[0] != key:
            try:
                before = {(r['id'], r['via'], r.get('reason')): r for r in enumerate_routes(s)}
            except Exception:
                before = {}
            self._need_cache = (key, before)
        before = self._need_cache[1]
        try:
            rs = enumerate_routes(after)
        except Exception:
            return 0.0
        int2 = 'INT2' not in after['used']['M']
        margin = getattr(self, 'need_margin', 1)
        v = 0.0
        for r in rs:
            dl = next((c['deficit'] for c in r['conds'] if c['kind'] == 'days'), self.days - s['day'])
            need = self._days_needed(r, int2)
            if need > dl:
                continue  # 間に合わない
            if dl - need > margin and r['total'] > 0:  # まだ始める日ではない: 今日の進みは数えない
                r = before.get((r['id'], r['via'], r.get('reason')), r)
            v += _weight(after, r) / (1 + r['total'])
        return v

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
        w = next((g.get('w', 1.0) for g in self.guide.get('routes', []) if g['id'] == r['id'] and g.get('via') == r['via']
                  and s['loop'] in g.get('loop', [s['loop']])), 1.0)
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
        abil = {**INFO_ABILITIES, **DEFENSE_ABILITIES}
        # 手先[3]（このループ中、自身が犯人の事件を発生させない）: 手先がこれから起きる事件の犯人なら、主人公の止め手になる（脚本家は犯人を知っている）。
        # 例: 封印されしモノで、邪気の汚染（神社に暗躍2）の犯人が手先（ユーザー指摘 2026-10-10）
        if any(i['culprit'] == 'C18' and i['day'] >= s['day'] for i in s['script']['incidents']):
            abil[('C18', 0)] = 3
        for (cid, idx), need in abil.items():
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
                third = self.rng.choice([u for u in allt if u not in (a, b)])  # 両方の向きで同じ3枚目（置き場所の集合が同じになり、応手を1回で読める）
                for x, y in ((a, b), (b, a)):
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
        # 評価で選ぶ対象でループが終わる・負け筋が成り立つなら、指針の好みより先にそれを選ぶ（d09 L2D7: 指針どおり遠隔殺人でメインラバーズを
        # 先に殺し、ラバーズの死→不安6→主人公死亡の筋を自分で消してループを落とした。calcGBxhdK の対戦表 2026-10-23）
        from selfplay.players import has_won_route
        base = super().incident_choice(s, inc)
        if base is not None:
            trial = copy_state(s)
            try:
                if ph.acting_culprit(s, inc) == 'C29':  # 教祖は2回解決（1回分で確かめる。players.RouteMastermind.incident_choice）
                    ph.incident_effect(trial, inc['id'], 'C29', base, [])
                else:
                    ph.run_incident(trial, inc, base)
                if has_won_route(trial):
                    return base
            except ph.LoopEnd as e:
                if e.loss:
                    return base
            except Exception:
                pass
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
        return base

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
        if getattr(self, 'lock_bluff', False):
            self._all_in(s)  # 全力モードはループの最初の日に決める（盤面が動く前）
        self._reply_of = {}
        self._prog(0.02, '盤面を読んでいます')
        fp = None
        self._gbonus, self._gp = {}, {}
        if self.calc and self.dg:  # 合成（dg）: 先に逆算と賭けを解き、閾値に届かない賭けの手も探索の候補に入れる
            fp = forced_plan(s, self.days, hidden=True, watch=self._watch(s),
                             pos=getattr(self, 'calc_pos', False), abil=getattr(self, 'calc_abil', False),
                             pcab=getattr(self, 'calc_pcab', False))
            if not fp['forced'] and fp.get('mix') and fp.get('p', 0) > 0:
                for pr, mv in fp['mix']:
                    mm_ = [{'by': 'M', 'target': tg, 'card': c} for tg, c in self._bluff_cards(s, mv)]
                    if len(mm_) == 3:
                        k_ = tuple((x['target'], x['card']) for x in mm_)
                        self._gbonus[k_] = mm_
                        self._gp[k_] = fp['p']
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
            self._prog(0.85, '負け筋を逆算しています（このループを取れるか' + ('・伏せ札の賭け' if self.gamble else '') + '）')
            fp = fp or forced_plan(s, self.days, hidden=bool(self.gamble), watch=self._watch(s) if self.gamble else None,
                                   pos=getattr(self, 'calc_pos', False), abil=getattr(self, 'calc_abil', False),
                             pcab=getattr(self, 'calc_pcab', False))
            g = self._guide_loop_routes(s)
            if g and fp['forced'] and not (fp['locked'] or self._locked(s)):
                # 指針がこのループに選んだ筋だけでも必ず取れるなら、そちらの手にする（ほかの筋を押して漏らさない。取る保証は変わらない）
                fg = forced_plan(s, self.days, hidden=bool(self.gamble), watch=self._watch(s) if self.gamble else None,
                                 pos=getattr(self, 'calc_pos', False), abil=getattr(self, 'calc_abil', False),
                                 pcab=getattr(self, 'calc_pcab', False), only=g)
                if fg['forced'] and fg['move']:
                    fp = fg
            if fp['locked'] or self._locked(s):
                # 確定したループは押さずに隠す（メモリ mastermind-hide-after-locked-loop）: 閾値に一番近い情報・守りの能力の持ち主に友好禁止。
                # 残りの枠も押す札（不安+1・暗躍）は置かない: 犯人・負け筋を教えるだけ（問題 mm16c-l1d2-hide）
                eve = self._gw_eve(s)
                gwx = [(min(sorted(eve), key=eve.get), 'GWX')] if eve else []
                if getattr(self, 'lock_bluff', False) and not self._all_in(s):  # 型1: 手を止めると「確定した」の合図になるので、偽の負け筋へは押す（メモリ mastermind-hide-after-locked-loop 改訂）
                    gwx += self._fake_pushes(s, 3 - len(gwx), {t for t, _ in gwx})
                # 本当の筋の人物への移動札も止める案は試して外した: gen_s11_034 で3試合ともループ3を落とした（calcG は3/3取る。
                # selfplay/runs/blind_calcGB_2026-10-15/analysis.md）
                mm = self._calc_merge(s, gwx, scored, skip=('PAR+', 'INT1', 'INT2'))
            elif fp['forced'] and fp['move']:
                self._fp_keep = set(fp['move'])  # 先回り（_key_preempt）で替えない札
                mv, drop = self._drop_harmful_pushes(s, fp['move']) if getattr(self, 'push_check', False) else (fp['move'], set())
                mm = self._calc_merge(s, mv, scored, skip=tuple(drop))
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
            elif getattr(self, 'setup_k', 0) and s['day'] < self.days:
                mm = self._setup_pick(s, scored, mm)
        if self.fill:
            mm = self._fill_idle(s, mm)
        if getattr(self, 'diag_gate', False) and s['day'] < self.days:
            mm = self._diag_gate(s, mm)
        if getattr(self, 'key_guard', False):
            mm = self._key_preempt(s, mm)
        self._fp_keep = set()
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

    # 鍵（主人公が必勝を崩す手）→ その能力の持ち主と要る友好（engine/abilities.ABILITIES）。kill はシリアルキラー（友好不要）を除く能力の殺害だけ
    KEY_HOLDERS = {'guard': {'C05': 5}, 'revive': {'C12': 5}, 'servant': {'C34': 4}, 'immortal': {'C25': 5}, 'suppress': {'C18': 3},
                   'kill': {'C12': 4, 'C22': 3}}

    def _key_holders(self, s):
        """必勝を崩す主人公の手（selfplay.editor_analysis.loop_keys の主人公の鍵）の持ち主と要る友好。脚本ごとに1回。
        ユーザー「エディタ向け機能で主人公脚本家のロジックに使えそうなもの」2026-10-23: calcGBxhd は d34 で刑事の護衛に6戦全敗、
        d08 で異世界人の蘇生に6戦4敗（巫女に友好禁止を置き続けた2戦は勝った）。"""
        if getattr(self, '_keys_cache', None) is None:
            from engine.route_calc import _pc_abilities, _abstract
            from engine.routes import INF, enumerate_routes
            from selfplay.editor_analysis import loop_keys
            from selfplay.script_routes import start_state
            k = loop_keys(self.key_script, mm_side=False)
            feats = (k.get('pc') or k.get('pc_any') or []) if k.get('kind') == 'mind' else []
            hold = {}
            for f in feats:
                for c, n in self.KEY_HOLDERS.get(f, {}).items():
                    hold[c] = min(hold.get(c, 99), n)
            if 'ability' in feats:  # 負け筋の対象のカウンターを減らせる友好能力の持ち主（友好を積めば・射程へ動かせば）
                s0 = start_state(self.key_script)
                rs = [r for r in enumerate_routes(s0) if r['total'] < INF]
                abst = _abstract(rs, s0, self.key_script['days'], positional=False, pos_mode=True, future=True, max_targets=8, max_routes=40)
                if abst:
                    for c, n in _pc_abilities(s0, abst[0], unlock=True, holders=True).items():
                        hold[c] = min(hold.get(c, 99), n)
            self._keys_cache = hold
        return self._keys_cache

    def _key_preempt(self, s, mm):
        """鍵の持ち主のうち、今日の主人公の友好の札で要る友好に届く者に友好禁止を置く。友好禁止・不安−1・不安禁止・移動・暗躍+1・不安+1 の順に
        替える（友好禁止は手札に1枚なので、別の所に置いた友好禁止は置き直す）。逆算の必勝の手順の札は、下限 forced_lower でも必勝なら替えない。"""
        hold = self._key_holders(s)
        # 友好禁止は守りの手: ずっと置き続けると押す札が減り、要の人物も教える。主人公が積んで、今日の友好+2（残っていなければ+1）で
        # 届く日だけ置く（ユーザー「多少つまれてから置いたほうがいい。あくまで友好禁止は防御の手」2026-10-23）
        step = 2 if any('GW2' not in s['used'][p] for p in 'ABC') else 1
        decisive = {c for f in ('guard', 'revive', 'servant', 'immortal') for c in self.KEY_HOLDERS[f]}
        # 届く者のうち、決め手（護衛・蘇生・身代わり・不死）の持ち主、友好の多い者の順
        cands = [(c in decisive, v['gw'], c) for c, n in hold.items() for v in [s['chars'].get(c)]
                 if v and v['alive'] and v.get('present', True) and c != 'C20' and v['gw'] < n <= v['gw'] + step]
        if not cands:
            return mm
        h = max(cands)[2]
        if any(x['target'] == h for x in mm):
            return mm
        keep = getattr(self, '_fp_keep', set())
        if keep and all((x['target'], x['card']) in keep for x in mm):
            # 逆算（主人公の鍵を数えない）の必勝の手順で3枚が埋まっている。主人公の鍵まで数えた下限 forced_lower でも必勝なら替えない。
            # 必勝でなければ、その主張は当てにならない（d34: 刑事の護衛で崩れた）ので、手順の札を1枚削ってでも先回りする（2026-10-23）
            from engine.route_calc import forced_lower
            if forced_lower(s, self.days):
                return mm
            keep = set()
        order = ('GWX', 'PAR-', 'PARX', 'MV_V', 'MV_H', 'MV_D', 'INT1', 'PAR+', 'INT2')
        for i, x in sorted(enumerate(mm), key=lambda ix: order.index(ix[1]['card']) if ix[1]['card'] in order else 9):
            if (x['target'], x['card']) in keep or x['card'] == 'INT2':
                continue  # 必勝の手順の札と、一度きりの暗躍+2 は替えない
            if x['card'] == 'GWX' or all(y['card'] != 'GWX' for y in mm):
                out = list(mm)
                out[i] = {'by': 'M', 'target': h, 'card': 'GWX'}
                return out
        return mm

    def _sk_leak(self, s, after):
        """行動の解決後の盤面で、シリアルキラー（【強制】）が負け筋にならない人物と2人きりなら、ターン終了の殺しで漏れる情報（ビット）×λ。
        相手が死なない役職（タイムトラベラー）なら「死亡しなかった」が公開され、両方が割れる（gen_b31_031 L1D1: 初期配置で神社に
        異世界人と妹の2人きり）。殺しのあったループのエントロピーの減りは約7（runs/select_2026-10-15/leak_by_end.py）。"""
        roles = {c: ph.base_role(after, c) for c in after['chars']}
        ml = any(r == 'MAIN_LOVERS' and after['chars'][c]['alive'] for c, r in roles.items())
        # ponytail: 負け筋になる被害者は engine.routes.enumerate_routes の victims を写したもの（ファクターの条件・従者は省略）
        loss = {c for c, r in roles.items() if r in ('KEY', 'FRIEND', 'FACTOR') or (r == 'LOVERS' and ml)}
        leak = 0.0
        for c, v in after['chars'].items():
            if not v['alive'] or not v.get('present', True) or ph.role(after, c) != 'SK':
                continue
            others = [x for x in ph.alive_in(after, v['area']) if x != c]
            if len(others) != 1 or others[0] in loss:
                continue
            o = others[0]
            immortal = sets.has_trait(after, roles[o], 'immortal')
            k = (s['loop'], s['day'], c, o, immortal, after['chars'][o]['int'], v['par'])
            cache = self.__dict__.setdefault('_skc', {})
            if k not in cache:
                self.pub._ensure(s)
                obs = [('no_death', dict(char=o))] if immortal else \
                    [('turn_end_death', dict(victim=o, others=[c], victim_int=k[5], par={c: k[6]}))]
                cache[k] = self._leak(s, obs)
            leak += cache[k]
        return self._lam(s) * leak

    def _feasible_score(self, s, after):
        """締め切り（事件の日、他はループの最終日）までに間に合う筋だけの評価（_days_needed）。"""
        from selfplay.players import _weight
        try:
            rs = enumerate_routes(after)
        except Exception:
            return 0.0
        int2 = 'INT2' not in after['used']['M']
        return sum(_weight(after, r) / (1 + r['total']) for r in rs
                   if self._days_needed(r, int2) <= next((c['deficit'] for c in r['conds'] if c['kind'] == 'days'), self.days - s['day']))

    def cultist_pick(self, s, placements):
        """カルティストの暗躍禁止の無視: cult_feasible なら、間に合う筋の評価で比べる（gen_s11_018 ではなく gen_b31_031 L2D3:
        遠隔殺人の犯人が臨界に届きようのない日に、フレンドの暗躍のために無視して、カルティストの場所を明かした）。"""
        if not getattr(self, 'cult_feasible', False):
            return super().cultist_pick(s, placements)
        best, best_sc = [], None
        for o in [None] + self.cultist_options(s, placements):
            opt = [o] if o else []
            try:
                after, _ = resolve_actions(s, placements, opt)
            except IllegalPlacement:
                continue
            if o and _wins_today(after, self.days):
                return opt  # 今日のうちにループが取れるなら、漏れと比べない（mm_ability と同じ）
            sc = self._feasible_score(s, after) - self._leak_cost(s, o)
            if best_sc is None or sc > best_sc:
                best, best_sc = opt, sc
        return best

    def _harmful_today(self, s):
        """今日の事件のうち、行動なしの盤面で「起きる」より「起きない」ほうが上のもの → [(犯人, 止める札)]。
        比べ方は優先順: ループが終わる ＞ 成り立った筋 ＞ score。"""
        from engine.resolve import CHARS as RC
        from selfplay.players import has_won_route
        from selfplay.pc_v2 import after_incidents

        def key(x):
            if '_loss' in x:
                return (1 if x['_loss'] else -1, False, 0.0)
            return (0, has_won_route(x), score(x))
        out = []
        for inc in s['script']['incidents']:
            cul = inc['culprit']
            if inc['day'] != s['day'] or cul not in s['chars'] or not s['chars'][cul]['alive']:
                continue
            par, lim = s['chars'][cul]['par'], RC[cul]['limit']
            if par >= lim + 1:
                continue  # 今日はもう止められない
            a = copy_state(s)
            a['chars'][cul]['par'] = max(par, lim)  # 起きる場合
            b = copy_state(s)
            b['suppressed_culprits'] = list(s.get('suppressed_culprits', [])) + [cul]
            if key(after_incidents(b)) > key(after_incidents(a)):
                out.append((cul, 'PAR-' if par >= lim else 'PARX'))
        return out

    def _drop_harmful_pushes(self, s, move):
        """逆算の札のうち、今日の事件の犯人への不安+1 で、予想した応手のもとで事件が起きると損になるものを外す。
        逆算は事件の副作用（主人公の移動で事件が別の相手や自分の駒に当たる）を見ない。害のあった事件の 30 件中 27 件が
        逆算の不安+1 だった（runs/inc_help_2026-10-16）。比べ方は優先順: ループが終わる ＞ 成り立った筋 ＞ score。"""
        today = {i['culprit'] for i in s['script']['incidents'] if i['day'] == s['day']}
        cand = [(t, c) for t, c in move if c == 'PAR+' and t in today]
        if not cand:
            return move, set()
        from selfplay.players import has_won_route
        from selfplay.pc_v2 import after_incidents
        mm = [{'by': 'M', 'target': t, 'card': c} for t, c in move]
        order = ['A', 'B', 'C']
        i = order.index(s['leader'])
        pc = self._shadow_reply(s, [t for t, _ in move], order[i:] + order[:i]) if self.shadow is not None else DUMMY_PC
        try:
            a = resolve_actions(s, mm + pc)[0]
        except IllegalPlacement:
            return move, set()

        def key(x):
            if '_loss' in x:
                return (1 if x['_loss'] else -1, False, 0.0)
            return (0, has_won_route(x), score(x))
        occ = key(after_incidents(a))
        drop = set()
        for t, c in cand:
            b = copy_state(a)
            b['suppressed_culprits'] = list(a.get('suppressed_culprits', [])) + [t]
            if key(after_incidents(b)) > occ:
                drop.add((t, c))
        # 朝から臨界の犯人は、不安+1 を外しても事件が起きる: 不安−1 で臨界の下に戻せるなら替える（不安−1 は回数制限なし）
        from engine.resolve import CHARS as RC
        out = []
        for t, c in move:
            if (t, c) in drop:
                if s['chars'][t]['par'] >= RC[t]['limit'] > s['chars'][t]['par'] - 1:
                    out.append((t, 'PAR-'))
                continue
            out.append((t, c))
        return out, drop

    def _diag_gate(self, s, mm):
        """移動斜め（1ループ1回）の門: 斜めを手札の他の札に替えた手と比べ、斜めが厳密に上のときだけ残す（同点なら温存）。
        比べ方は優先順（加点で混ぜない）: その日にループが終わる ＞ 今日成り立つ筋（残り0）の重み ＞ score。応手は模型の予想。
        斜めの値打ちは「対角へ1手で入れて、その日のうちに殺す」形にほぼ限られ、途中の日の 61 回中 46 回は他の札で同じ結果だった
        （runs/mvd_2026-10-16/report.md）。"""
        j = next((i for i, x in enumerate(mm) if x['card'] == 'MV_D'), None)
        if j is None:
            return mm
        if (mm[j]['target'], 'MV_D') in (getattr(self, '_locks', None) or ()) and getattr(self, 'lock_moves', False):
            return mm  # 決め手を仕込む斜め（効き目は翌朝。その日の結果だけで比べると他の札に替わる）
        from selfplay.players import _weight
        from selfplay.pc_v2 import after_incidents
        t = mm[j]['target']
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        for x in mm:
            if x['card'] in hand:
                hand.remove(x['card'])
        subs = [c for c in (('PAR-', 'GWX', 'PARX') if t.startswith('B:') else ('MV_V', 'MV_H', 'PARX', 'GWX', 'PAR-')) if c in hand]
        order = ['A', 'B', 'C']
        i = order.index(s['leader'])
        pc = self._shadow_reply(s, [x['target'] for x in mm], order[i:] + order[:i]) if self.shadow is not None else DUMMY_PC

        def key(m):
            try:
                a = after_incidents(resolve_actions(s, m + pc)[0])
            except IllegalPlacement:
                return None
            if '_loss' in a:
                return (1 if a['_loss'] else -1, 0.0, 0.0)
            return (0, sum(_weight(a, r) for r in enumerate_routes(a) if r['total'] == 0), score(a))
        kd = key(mm)
        best, bk = None, None
        for c in subs:
            m2 = mm[:j] + [dict(mm[j], card=c)] + mm[j + 1:]
            k2 = key(m2)
            if k2 is not None and (bk is None or k2 > bk):
                best, bk = m2, k2
        if best is None or (kd is not None and kd > bk):
            return mm
        return best

    def _setup_pick(self, s, scored, mm):
        """必勝が無い日の仕込み（ユーザー案 2026-10-16「必勝がないときはバレにくい複数の筋の仕込みをし、応手の状況によっては後日必勝になりえる状況も」）。
        評価の上位 setup_k 手に、模型の応手を当てて今日の事件まで解き、翌日の盤面で逆算（forced_plan）を解く。
        段で比べる: 翌日に必勝 ＞ 翌日の賭けの確率 ＞ 今日の評価（加点で混ぜない。メモリ mastermind-loop-win-first）。"""
        from selfplay.pc_v2 import after_incidents
        best, best_key = mm, None
        for v, cand, *_ in scored[:self.setup_k]:
            pc = self._reply_of.get(id(cand), [])
            try:
                after = after_incidents(resolve_actions(s, cand + pc)[0])
            except IllegalPlacement:
                continue
            if '_loss' in after:  # 今日の事件でループが終わる
                key = (2 if after['_loss'] else -1, 1.0, v)
            else:
                nxt = dict(after, day=s['day'] + 1)
                try:
                    f = forced_plan(nxt, self.days, hidden=bool(self.gamble), watch=self._watch(nxt) if self.gamble else None)
                except Exception:
                    continue
                key = (int(bool(f['forced'])), f.get('p') or 0.0, v)
            if best_key is None or key > best_key:
                best, best_key = cand, key
        self.setup_log = getattr(self, 'setup_log', []) + [(s['loop'], s['day'], best_key[:2] if best_key else None, best is not mm)]
        return best

    def _watch(self, s):
        """主人公に見張られている度合い {置き先: 点}: 暗躍禁止が置かれた回数＋前のループで暗躍が2に届いた（筋が割れた）ループ数×3。"""
        w = dict(self.intx_seen)
        for L, t in self.int_reached:
            if L < s['loop']:
                w[t] = w.get(t, 0) + 3
        return w

    def _prog(self, frac, text):
        """進み具合を知らせる（progress(割合 0〜1, 何をしているか)。対AIモードの盤面のローディングが使う。判断には影響しない）。"""
        cb = getattr(self, 'progress', None)
        if cb:
            cb(frac, text)

    @staticmethod
    def _bluff_cards(s, mv):
        """賭けの手の BLUFF を効果の無い札に置き換える（ボードには不安−1・友好禁止、人物には友好禁止・移動）。
        幻想のいるエリアのボードは、札が幻想にも効く（カードの特性、engine/resolve.py）ので、不安−1 は幻想の不安を下げて主人公を助け、
        移動は幻想を動かす。そこでは友好禁止・不安禁止（どちらも脚本家に損の無い効果）を先に使う。
        ponytail: 移動の解決で幻想が入ってくるエリアは見ていない（移動前のエリアだけ）"""
        il = s['chars'].get('C20')
        il_board = 'B:' + il['area'] if il and il['alive'] and il.get('present', True) and il.get('area') else None
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        for _, c in mv:
            if c != 'BLUFF':
                hand.remove(c)
        out = []
        for t, c in mv:
            if c == 'BLUFF':
                pref = ('GWX', 'PARX', 'MV_V', 'MV_H', 'MV_D', 'PAR-') if t == il_board else \
                    ('PAR-', 'GWX') if t.startswith('B:') else ('GWX', 'MV_V', 'MV_H', 'MV_D')
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
                if x['card'] in hand and x['card'] not in skip and (x['target'], x['card']) not in skip and x['target'] not in {o['target'] for o in out}:
                    hand.remove(x['card'])
                    out.append(x)
        pairs = tuple(x for x in skip if isinstance(x, tuple))  # 外した（対象, 札）の組は埋め直しでも置かない
        return out if len(out) == 3 or skip == pairs else SearchMastermind._calc_merge(s, [(o['target'], o['card']) for o in out], scored, skip=pairs)

    def _fill_idle(self, s, mm):
        """予想した主人公の応手で何も起きない（または上乗せの）札を、置き場所はそのままに役目のある札へ替える（ユーザー「無作為はよくない、
        隠す意味でも友好禁止などの妨害にあてるべき」2026-10-11）。人物には、不安が負け筋に要るなら不安禁止（不安−1 からの保険）、
        それ以外は友好禁止（閾値の前日なら妨害、友好無視の役職ならブラフ）。押す札（不安+1・暗躍・移動）には替えない＝賭けの混ぜ方を崩さない。"""
        from engine.resolve import CHARS as RCHARS, combine_moves, destination
        reply = self._reply_of.get(id(mm), []) if hasattr(self, '_reply_of') else []
        theirs = {}
        for p in reply:
            if p.get('by') != 'M':
                theirs.setdefault(p['target'], []).append(p['card'])
        wasted = self._wasted_int(s)
        il = s['chars'].get('C20')
        il_board = 'B:' + il['area'] if il and il['alive'] and il.get('present', True) and il.get('area') else None

        def use(x):  # 予想した応手のもとで、この札が何かを起こすか（league.card_use と同じ分け方を、行動解決なしで）
            t, c = x['target'], x['card']
            if t.startswith('B:') and c not in ('INT1', 'INT2'):
                return 'guard' if t == il_board else 'idle'  # 幻想のいるボードの札は幻想に効く
            if c in ('INT1', 'INT2'):
                return 'drew' if 'INTX' in theirs.get(t, []) else 'surplus' if t in wasted else 'effect'
            ch = s['chars'].get(t)
            if c == 'PAR+':
                return 'effect'
            if c == 'PAR-':
                return 'effect' if ch and ch['par'] > 0 else 'idle'
            if c.startswith('MV_'):
                if 'MVX' in theirs.get(t, []):
                    return 'drew'
                mv = [c] + [k for k in theirs.get(t, []) if k.startswith('MV_')]
                to = destination(ch['area'], combine_moves(mv)) if ch else None
                return 'idle' if not ch or (to in RCHARS[t]['forbidden'] and t not in s.get('unbound', [])) else 'effect'
            return 'guard'  # 友好禁止・不安禁止
        uses = [use(x) for x in mm]
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        for x in mm:
            if x['card'] in hand:
                hand.remove(x['card'])
        par_need = {d[0] for r_ in enumerate_routes(s) for c in r_['conds'] if c['kind'] == 'counter' and (d := c['detail'])[1] == 'par'}
        out = []
        for x, u in zip(mm, uses):
            t = x['target']
            if u in ('idle', 'surplus') and not t.startswith('B:') and t in s['chars']:
                for c in (('PARX', 'GWX') if t in par_need else ('GWX', 'PARX')):
                    if c in hand:
                        hand.append(x['card']) if x['card'] in CARDS['mastermind_hand'] else None
                        hand.remove(c)
                        x = dict(x, card=c)
                        break
            out.append(x)
        return out

    def _wasted_int(self, s):
        """暗躍を足しても意味の無い置き先: 負け筋に要る暗躍の数（筋ごとの最大）をもう満たし、主人公の友好能力でも取り除けないもの。
        負け筋に出てこない置き先（ブラフ）も、主人公から見てどの組み合わせでも要りうる最大（ボード2＝封印されしモノ・爆弾・病院の事件、
        人物4＝キラーの自身に暗躍4）に届いていれば、それ以上は脅威に見えないので意味が無い（ユーザー「ブラフでも必要以上に積まない」）。
        取り除けるなら上乗せは備えになるので含めない。"""
        from engine.route_calc import _pc_abilities
        need = {}
        for r_ in enumerate_routes(s):
            for c in r_['conds']:
                d = c['detail']
                if c['kind'] == 'counter' and d[1] == 'int':
                    need[d[0]] = max(need.get(d[0], 0), d[2])
                elif c['kind'] == 'board':
                    need['B:' + d[0]] = max(need.get('B:' + d[0], 0), d[1])
        cur = lambda t: s['boards'][t[2:]] if t.startswith('B:') else s['chars'][t]['int']  # noqa: E731
        for a in s['boards']:  # ブラフの上限
            need.setdefault('B:' + a, 2)
        for c, v in s['chars'].items():
            if v.get('alive') and v.get('present', True):
                need.setdefault(c, 4)
        full = [t for t, n in need.items() if (t.startswith('B:') or t in s['chars']) and cur(t) >= n]
        if not full:
            return set()
        targets = [(t, 'int') for t in full]
        removable = {targets[i][0] for _, idx in _pc_abilities(s, targets) for i in idx}
        return set(full) - removable

    def _align_moves(self, s, hand, xr, k=8):
        """位置を揃える候補 [(置き先の組, 手)]: 位置の条件の不足が1で、ほかの条件（カウンター）は満たされている筋を、今日の移動1枚で成立させる。
        残り2枚は近い負け筋の対象に押す札（無ければ無作為）。"""
        from engine.route_calc import _pos_move
        out = []
        rel = sorted(t for t in self._relevant(s) if t[1] in ('int', 'par'))
        for r_ in enumerate_routes(s)[:10]:
            pos = [c for c in r_['conds'] if c['kind'] in ('same_area', 'alone_with', 'in_area')]
            if len(pos) != 1 or pos[0]['deficit'] != 1 or any(c['deficit'] > 0 for c in r_['conds'] if c not in pos and c['kind'] != 'days'):
                continue
            pm = _pos_move(s, 'P:%s:%s:%s' % (pos[0]['kind'], *pos[0]['detail'][:2]))
            if not pm or pm[1] not in hand:
                continue
            h = [c for c in hand if c != pm[1]]
            mm = [{'by': 'M', 'target': pm[0], 'card': pm[1]}]
            for t, kind in rel:
                if len(mm) == 3 or t in {x['target'] for x in mm}:
                    continue
                c = next((c for c in (('INT2', 'INT1') if kind == 'int' else ('PAR+',)) if c in h), None)
                if c:
                    h.remove(c)
                    mm.append({'by': 'M', 'target': t, 'card': c})
            rest = [u for u in self._targets(s) if u not in {x['target'] for x in mm}]
            while len(mm) < 3 and rest and h:
                u = xr.choice(rest)
                rest.remove(u)
                c = xr.choice(h)
                h.remove(c)
                mm.append({'by': 'M', 'target': u, 'card': c})
            if len(mm) == 3:
                out.append((tuple(x['target'] for x in mm), mm))
        return out[:k]

    def _double_moves(self, s, hand, xr=None, k=12):
        """二重の脅威の候補 [(置き先の組, 手)]: 近い負け筋が要るカウンター（_relevant）の2か所を同じ日に押す。
        暗躍は暗躍+1・暗躍+2 で2か所（暗躍禁止は1日1か所しか効かない＝2枚出すと両方無効）、不安は不安+1（不安−1 は各主人公ループ1回）。
        3枚目は近い負け筋の別の対象か、無作為の置き先に残りの札。"""
        xr = xr or self.rng
        rel = sorted(t for t in self._relevant(s) if t[1] in ('int', 'par'))
        ok = lambda t: t.startswith('B:') or (t in s['chars'] and s['chars'][t]['alive'] and s['chars'][t].get('present', True) and t != 'C20')  # noqa: E731
        rel = [t for t in rel if ok(t[0])]
        out = []
        for i, a in enumerate(rel):
            for b in rel[i + 1:]:
                if a[0] == b[0]:
                    continue
                pairs = []
                for ca in (('INT2', 'INT1') if a[1] == 'int' else ('PAR+',)):
                    for cb in (('INT1', 'INT2') if b[1] == 'int' else ('PAR+',)):
                        h = list(hand)
                        if ca in h:
                            h.remove(ca)
                            if cb in h:
                                h.remove(cb)
                                pairs.append((ca, cb, h))
                for ca, cb, h in pairs[:2]:
                    rest = [t for t in self._targets(s) if t not in (a[0], b[0])]
                    if not rest or not h:
                        continue
                    third = next((t[0] for t in rel if t[0] not in (a[0], b[0])), None) or xr.choice(rest)
                    c3 = next((c for c in h if (c in ('INT1', 'INT2')) == (third.startswith('B:'))), None) or xr.choice(h)
                    mm = [{'by': 'M', 'target': a[0], 'card': ca}, {'by': 'M', 'target': b[0], 'card': cb}, {'by': 'M', 'target': third, 'card': c3}]
                    out.append(((a[0], b[0], third), mm))
        xr.shuffle(out)
        return out[:k]

    def _decoy_moves(self, s, hand, xr, k=12, ratio=0.7):
        """見せ札の二重の脅威 [(置き先の組, 手)]: 本当に押す1か所（_relevant）に加え、主人公から同じくらい危なく見える置き場所
        （そこに押す札が1枚あったときの負け筋の近づき方 card_read._near が、本命の ratio 倍以上）に筋を進めない札を置く。
        読む主人公は危なく見える所を止めに来るので、止める札が見せ札に割かれれば本命が通る。評価は探索がする（加点はしない）。"""
        from selfplay.card_read import _after, _near
        push = [c for c in ('PAR+', 'INT1', 'INT2') if c in hand]
        weak = [c for c in ('PARX', 'GWX', 'PAR-') if c in hand]
        if not push or not weak:
            return []
        base = _near(s)
        allt = self._targets(s)

        def one(u, c):  # 脚本家は3枚置くので、残り2か所に筋を進めない札を添えて測る（card_read.candidates の1枚の効果と同じ）
            for o1, o2 in itertools.combinations([x for x in allt if x != u], 2):
                a = _after(s, [(u, c), (o1, 'PARX'), (o2, 'GWX')]) or _after(s, [(u, c), (o1, 'GWX'), (o2, 'PARX')])
                if a:
                    return _near(a) - base
            return None
        look = {}
        for u in allt:
            vs = [v for c in push for v in [one(u, c)] if v is not None]
            if vs:
                look[u] = max(vs)
        out = []
        for a, kind in sorted(self._relevant(s)):
            ca = next((c for c in (('INT1', 'INT2') if kind == 'int' else ('PAR+',)) if c in hand), None)
            if ca is None or a not in look or look[a] <= 0:
                continue
            ds = sorted((u for u in look if u != a and look[u] >= ratio * look[a]), key=lambda u: -look[u])[:2]
            if not ds:
                continue
            third = ds[1] if len(ds) > 1 else xr.choice([u for u in self._targets(s) if u not in (a, ds[0])])
            for w1 in weak:
                rest = [c for c in hand if c not in (ca, w1)]
                w2 = next((c for c in weak if c in rest), None) or xr.choice(rest)
                mm = [{'by': 'M', 'target': a, 'card': ca}, {'by': 'M', 'target': ds[0], 'card': w1}, {'by': 'M', 'target': third, 'card': w2}]
                if _after(s, [(x['target'], x['card']) for x in mm]):
                    out.append(((a, ds[0], third), mm))
        xr.shuffle(out)
        return out[:k]

    def _lock_moves(self, s, hand, xr, k=12):
        """決め手を仕込む候補 [(置き先の組, 手)]: その1枚が止められずに通ると、翌朝に必ず取れる形（forced_plan）になる札
        （card_read の lock 型と同じ判定。d18 で Claude の脚本家が、大物を斜めの移動で動かせないカルティストの神社へ運んで2ループ取った。
        2026-10-24）。今朝もう必ず取れるなら足さない。残り2枚は無作為（評価は探索がする。加点はしない）。"""
        from selfplay.card_read import all_targets, lock_cards, lock_of, route_targets
        self._locks = set()
        if forced_plan(s, self.days, pos=True, pcab=True)['forced']:
            return []
        allt = self._targets(s)
        out = []
        rt = route_targets(s)
        for t in [u for u in allt if u in rt]:
            for c in sorted(set(hand) & lock_cards(rt[t])):
                rest = [u for u in allt if u != t]
                if not lock_of(s, t, c, all_targets(s)):
                    continue
                self._locks.add((t, c))
                for _ in range(2):
                    others = xr.sample(rest, 2)
                    cards = xr.sample([x for x in hand if x != c], 2)
                    out.append(((t, *others), [{'by': 'M', 'target': t, 'card': c}] +
                                [{'by': 'M', 'target': u, 'card': x} for u, x in zip(others, cards)]))
        xr.shuffle(out)
        return out[:k]

    def _once_waste(self, s, mm, reply):
        """一度きりの札（暗躍+2・移動斜め）が、応手 reply の下で無効になる（暗躍禁止・移動禁止・禁止エリア）枚数。
        gen_b31_005 L1D1: 主人公が毎日守る学校のボードに暗躍+2 を使い、二重の脅威に使えなくなった（目隠し対戦2、2026-10-25）。"""
        once = [x for x in mm if x['card'] in ONCE['M']]
        if not once:
            return 0
        try:
            _, ev = resolve_actions(s, mm + reply, cultist_options(s, mm + reply))
        except (IllegalPlacement, ph.LoopEnd):
            return 0
        n = 0
        for x in once:
            t, c = x['target'], x['card']
            n += any((e.get('kind') == 'nullified' and e.get('target') == t and c in e.get('cards', [])) or
                     (e.get('kind') in ('move_blocked', 'move_forbidden') and e.get('char') == t and c in e.get('cards', [])) for e in ev)
        return n

    def _lock_bonus(self, s, mm, reply, none_w=0.5):
        """決め手の札を含む手: 予想した応手のあと（重み 1−none_w）と応手なしのあと（none_w）に、翌朝に必ず取れる形が残る見込み
        （カルティストの無視も入れて解決）。読む主人公の写しの応手で数えるときは none_w=0（写しは止め損なわない）。"""
        from selfplay.card_read import _locked
        from selfplay.pc_solve import end_of_day
        from selfplay.players import cultist_options
        if not any((x['target'], x['card']) in self._locks for x in mm):
            return 0.0
        hit = 0.0
        for pc, w in ((reply, 1 - none_w), (DUMMY_PC, none_w)):
            if not w:
                continue
            try:
                after, _ = resolve_actions(s, mm + pc, cultist_options(s, mm + pc))
            except IllegalPlacement:
                continue
            except ph.LoopEnd as e:
                hit += w * bool(e.loss)
                continue
            hit += w * _locked(after, self.days, end_of_day, forced_plan)
        return hit

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
        # 足す候補は別の乱数で作る（本体の乱数の流れを変えない＝無作為の候補が入れ替わって良い手を逃さない。d24 の調べ）
        xr = random.Random(f"{s['loop']}:{s['day']}:{len(s['used']['M'])}")
        if getattr(self, 'lock_bluff', False) and 'PAR+' in hand:
            # 型6 の見せ札: 主人公が暗躍禁止を置いてきた人物（intx_seen）が、本当の筋で不安を要する人物なら、不安+1 を置く手を候補に足す。
            # 暗躍禁止を置かれても不安は通る（同じ人物に暗躍禁止と不安−1 は同じ日に置けない）。評価は探索がする（加点はしない）
            allt = self._targets(s)
            for c in sorted({t for t, k in self._relevant(s) if k == 'par' and self.intx_seen.get(t, 0) > 0}):
                for _ in range(3):
                    others = xr.sample([u for u in allt if u != c], 2)
                    cards = xr.sample([x for x in hand if x != 'PAR+'], 2)
                    forced.setdefault((c, *others), []).append([{'by': 'M', 'target': c, 'card': 'PAR+'}] +
                                                               [{'by': 'M', 'target': u, 'card': k} for u, k in zip(others, cards)])
        if getattr(self, 'sk_leak', 0) and self._sk_leak(s, s) > 0:
            # シリアルキラーが筋に要らない相手と2人きりなら、どちらかを動かす手を候補に足す（評価は探索。gen_b31_031 L1D1）
            allt = self._targets(s)
            pairs = [(c, o) for c, v in s['chars'].items() if v['alive'] and v.get('present', True) and ph.role(s, c) == 'SK'
                     for o in [x for x in ph.alive_in(s, v['area']) if x != c] if len(ph.alive_in(s, v['area'])) == 2]
            for c, o in pairs:
                for t in (c, o):
                    for mv in [x for x in ('MV_V', 'MV_H') if x in hand]:
                        others = xr.sample([u for u in allt if u not in (c, o)], 2)
                        cards = xr.sample([x for x in hand if x != mv], 2)
                        forced.setdefault((t, *others), []).append([{'by': 'M', 'target': t, 'card': mv}] +
                                                                   [{'by': 'M', 'target': u, 'card': k} for u, k in zip(others, cards)])
        if getattr(self, 'inc_guard', False):
            # 事件の門番: 今日の事件が起きると損（起きない盤面のほうが上）なら、犯人を止める手を候補に足す（評価は探索。加点はしない）。
            # 臨界未満なら不安禁止、臨界ちょうどなら不安−1（runs/inc_help_2026-10-16 の 4-1）
            allt = self._targets(s)
            for cul, c in self._harmful_today(s):
                if c not in hand:
                    continue
                for _ in range(3):
                    others = xr.sample([u for u in allt if u != cul], 2)
                    cards = xr.sample([x for x in hand if x != c], 2)
                    forced.setdefault((cul, *others), []).append([{'by': 'M', 'target': cul, 'card': c}] +
                                                                 [{'by': 'M', 'target': u, 'card': k} for u, k in zip(others, cards)])
        self._doubles = set()
        if self.double:
            for ts, mm in self._double_moves(s, hand, xr):
                forced.setdefault(ts, []).append(mm)
                self._doubles.add(frozenset(ts))
        if getattr(self, 'lock_moves', False):
            for ts, mm in self._lock_moves(s, hand, xr):
                forced.setdefault(ts, []).append(mm)
        if getattr(self, 'decoy', False):
            for ts, mm in self._decoy_moves(s, hand, xr):
                forced.setdefault(ts, []).append(mm)
        if self.align:
            for ts, mm in self._align_moves(s, hand, xr):
                forced.setdefault(ts, []).append(mm)
        for key_, mm in self._gbonus.items():  # 賭けの手（dg）
            forced.setdefault(tuple(x['target'] for x in mm), []).append(mm)
        wasted = self._wasted_int(s) if self.surplus else set()
        locked = bool(self.hide_culprit) and self._locked(s)
        culprits_today = {i['culprit'] for i in s['script']['incidents'] if i['day'] >= s['day']} if locked else set()
        tss = self._triples(s) + [list(k) for k in forced]
        jobs = []  # 置き場所の組ごとの候補（乱数を引く順は並列にしても変えない）
        # 主人公の模型が見るのは置き場所だけ（中身は見ない）。置き場所の集合が同じ組は1つにまとめ、応手を1回だけ読む
        # （1日目は定石の候補で最大56組に増え、最初の1手が後の手の3〜4倍かかっていた。2026-10-17 高速化）
        seen_ts = {}
        for ts in tss:
            key = frozenset(ts)
            if key in seen_ts:
                seen_ts[key][1].extend(forced.get(tuple(ts), []))
                continue
            cands = list(forced.get(tuple(ts), []))
            seen_ts[key] = (ts, cands)
            for _ in range(self.nc):
                cards = self._deal(hand, ts)
                cands.append([{'by': 'M', 'target': t, 'card': c} for t, c in zip(ts, cards)])
            gd = self._guide_deal(s, hand, ts) if self.guide else None
            if gd:
                cands.append(gd)
            jobs.append((ts, cands))
        hstate = self._block_state(s) if getattr(self, 'human_block', None) else None
        results = self._par_tables(s, order, jobs)
        for i_ts, ((ts, cands), (replies, table)) in enumerate(zip(jobs, results)):
            self._prog(0.05 + 0.75 * i_ts / max(1, len(tss)), '伏せ札の置き場所を比べています')  # 候補の組の数（N）は出さない（盤面の読みの手がかりになりうる。設計資料 4.12）。進み具合は割合のバーで
            # 主人公は札の中身を知らない: 中身の候補の平均で脚本家の評価が最小の応手を選ぶ
            nw = getattr(self, 'naive_w', 0)
            hb = getattr(self, 'human_block', None)
            nrep = len(replies) - (1 if (nw or hb) else 0)  # 最後の列（応手なし）は jstar・平均に入れない
            def mean_over_mm(j):
                xs = [row[j] for row in table if row[j] is not None]
                return sum(xs) / len(xs) if xs else float('inf')
            jstar = min(range(nrep), key=mean_over_mm)
            for mm, row in zip(cands, table):
                xs = [x for x in row[:nrep] if x is not None]
                if not xs or row[jstar] is None:
                    continue
                v = self.worst * row[jstar] + (1 - self.worst) * sum(xs) / len(xs)
                if hb and row[-1] is not None:
                    # 人間の止め方の模型（ユーザー「なぜ止めなかったかを数値化」2026-10-16、selfplay/threat_table）:
                    # この手が近づける脅威のうち一番見えやすいもの（公開の推理で要る役職がそろう割合 vis）を、
                    # 止める確率 q = hb[0] + hb[1]·vis で止められ、1−q で止められない（応手なしの値）とみなす
                    q = self._block_prob(s, mm, hb, hstate)
                    if q is not None:
                        v = q * v + (1 - q) * row[-1]
                if nw and row[-1] is not None:
                    # 課題 D: 模型は慎重な主人公の応手を返す。人間は止め損なうことがあるので、応手なしの値を重み nw で混ぜる
                    # （gen_s11_009 L3D6: 止める応手が1つあるだけで最終日の決め手を捨てた）。ponytail: 重みは一定、主人公の推理の確信度で変えるのは後
                    v = (1 - nw) * v + nw * row[-1]
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
                if self._gp:
                    v += self.dg * self._gp.get(tuple((x['target'], x['card']) for x in mm), 0)
                if wasted:
                    v -= self.surplus * sum(1 for x in mm if x['card'] in ('INT1', 'INT2') and x['target'] in wasted)
                if self.stuck:
                    v -= self._stuck_cost(s, mm)
                if getattr(self, 'lock_moves', False) and getattr(self, '_locks', None):
                    v += self.lock_w * self._lock_bonus(s, mm, replies[jstar], none_w=0)  # 応手で止められる決め手には付けない
                if getattr(self, 'once_w', 0):
                    v -= self.once_w * self._once_waste(s, mm, replies[jstar])
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
        if getattr(self, 'reader', None) is not None and scored:
            self._read_rerank(s, order, scored)
        scored.sort(key=lambda x: -x[0])
        return scored

    def _reader_reply(self, s, order, ts, keep_state=False):
        """読む主人公の写し（selfplay/card_read.reader_pc。公開の出来事を毎日見せて型の重みを主人公と同じに保つ）が、置き場所 ts に打つ応手。
        keep_state=False なら写しの記憶と乱数を元に戻す（候補を比べる用）。True は実際に置いた札で写しを進める（毎日1回）。"""
        import types
        rd = self.reader
        keep = None if keep_state else {k: copy.deepcopy(v) for k, v in rd.__dict__.items() if not isinstance(v, types.FunctionType)}
        try:
            return rd.pc_cards(copy_state(s), order, list(ts))
        finally:
            if keep:
                rd.__dict__.update(keep)

    def _read_rerank(self, s, order, scored, k=6):
        """評価の1位の置き場所に、読む主人公の写しの応手を出させ、その応手に対する値で評価を直す（模型の応手 row[jstar] を置き換える。重みは worst）。
        1位が確かめ済みの置き場所になるまで繰り返す（最大 k 組）。上位だけ直すと、写しの応手は模型より上手に止めるので直した組が軒並み下がり、
        直していない組が楽観的な値のまま1位に上がった（d18 seed1 game2 L1D1）。読みを外す中身（危なく見える所に弱い札）が上がる。"""
        key = lambda x: frozenset(m['target'] for m in x[1])  # noqa: E731
        done = set()
        locks = getattr(self, '_locks', None) if getattr(self, 'lock_moves', False) else None
        # 決め手の札を含む置き場所は、模型の応手では低く出ても写しでは通ることがある（読まれない強い札）ので、先に全部写しで見直す
        ranked = sorted(scored, key=lambda x: -x[0])
        queue = list(dict.fromkeys(key(x) for x in ranked
                                   if locks and any((m['target'], m['card']) in locks for m in x[1])))[:self.lock_k]  # 評価の上位 lock_k 組だけ（写しは1日目で1回約5秒）
        # 2本の筋を同時に押す置き場所（_double_moves）も先に写しで見直す。写しは本当に危ない1か所を止めに来るので、二重の脅威は写しで初めて上がる。
        # 決め手の枠だけにすると、ループ1を守られる試合が 9→14 に増えた（ユーザーの見立て「2本の筋を同時に追うのをやめている」、2026-10-25）
        doubles = getattr(self, '_doubles', set())
        queue += [k_ for k_ in dict.fromkeys(key(x) for x in ranked if key(x) in doubles) if k_ not in queue][:getattr(self, 'double_k', 0)]
        while len(done) < k:
            top = queue.pop(0) if queue else key(max(scored, key=lambda x: x[0]))
            if top in done:
                if queue:
                    continue
                break
            done.add(top)
            rep = None
            for i, x in enumerate(scored):
                if key(x) != top:
                    continue
                rep = rep or self._reader_reply(s, order, [m['target'] for m in x[1]])
                val = self._val(s, x[1], rep)
                if val is not None:
                    v = x[0] + self.worst * (val - x[2])
                    old_rep = self._reply_of.get(id(x[1]), rep)
                    if getattr(self, 'lock_moves', False) and getattr(self, '_locks', None):  # 決め手の加点も写しの応手で数え直す
                        v += self.lock_w * (self._lock_bonus(s, x[1], rep, none_w=0) - self._lock_bonus(s, x[1], old_rep, none_w=0))
                    if getattr(self, 'once_w', 0):  # 一度きりの札の空振りも写しの応手で数え直す
                        v -= self.once_w * (self._once_waste(s, x[1], rep) - self._once_waste(s, x[1], old_rep))
                    scored[i] = (v, x[1], val, x[3])
                    self._reply_of[id(x[1])] = rep

    def _block_state(self, s):
        from selfplay.threat_table import Snap
        key = lambda x: (x['id'], x['via'], x['goal'])  # noqa: E731
        return Snap(self.pub, s), {key(x): x['total'] for x in enumerate_routes(s)}

    def _block_prob(self, s, mm, hb, hstate):
        """人間が模型の応手のように動く確率（threat_table の脅威の定義・見えやすさ）。応手なしと比べて混ぜる重み。"""
        from selfplay.threat_table import NEAR, visibility
        snap, start_d = hstate
        try:
            dummy, _ = resolve_actions(s, mm + DUMMY_PC)
        except IllegalPlacement:
            return None
        th = [x for x in enumerate_routes(dummy)
              if x['total'] <= NEAR and start_d.get((x['id'], x['via'], x['goal']), 99) > x['total'] and '登場前' not in x.get('note', '')]
        near = min((x['total'] for x in th), default=None)  # 一番近い脅威（決め手）の見えやすさで決める。脇の脅威で上げない
        vis = [v for v in (visibility(snap, x, s) for x in th if x['total'] == near) if v is not None]
        # 近づける脅威が無い手: 模型の応手どおりに主人公が動く（移動の合成などで筋が成り立つ）とは限らないので、基準の hb[0] で混ぜる
        # （目隠し6戦目 L3D6: 模型の応手との合成に賭けた手が、模型の下だけで勝ちになっていた）
        return min(1.0, max(0.0, hb[0] + hb[1] * max(vis))) if vis else hb[0]

    def _par_tables(self, s, order, jobs):
        """組ごとの（応手, 評価の表）。主人公の模型（shadow）の応手は自分の乱数を元に戻すので、組ごとに独立に計算できる。
        環境変数 SANGEKI_WORKERS（既定 1）が 2 以上なら fork したプロセスで並べる（常駐モードの待ち時間向け。対戦表は試合ごとに
        プロセスが分かれ、メモリが足りなくなるので使わない）。模型が無い脚本家は応手に self.rng を使うので並べない。"""
        def one(job):
            return self._par_one(s, order, job)
        if sys.platform == 'emscripten' and self.shadow is not None and len(jobs) >= 2:
            r = self._par_browser(s, order, jobs)
            if r is not None:
                return r
        workers = int(os.environ.get('SANGEKI_WORKERS', '1'))
        import multiprocessing
        if workers < 2 or self.shadow is None or len(jobs) < 2 or 'fork' not in multiprocessing.get_all_start_methods():
            return [one(j) for j in jobs]  # fork の無い環境（Windows）は並べない
        global _PAR
        _PAR = (one, jobs)
        try:
            with multiprocessing.get_context('fork').Pool(min(workers, len(jobs))) as pool:
                return pool.map(_par_job, range(len(jobs)))
        finally:
            _PAR = None

    def _par_one(self, s, order, job):
        """置き場所の組1つ分の（応手, 評価の表）。並列のときは別のプロセス・Worker でも同じ結果になる（模型の乱数は呼ぶたびに戻す）。"""
        ts, cands = job
        replies = self._replies(s, ts, order)
        if getattr(self, 'naive_w', 0) or getattr(self, 'human_block', None):  # 最後の列は「応手なし」（人間が止め損なう場合）
            replies = replies + [DUMMY_PC]
        return replies, [[self._val(s, mm, pc) for pc in replies] for mm in cands]

    def _picklable(self):
        """自分の写しから、あとで差し込まれた関数（進行役の「考えています」の表示の包みなど）を除いたもの。pickle で補助に渡す用。"""
        import types
        c = object.__new__(type(self))
        c.__dict__ = {k: v for k, v in self.__dict__.items() if not isinstance(v, (types.FunctionType, types.MethodType))}
        return c

    def _par_browser(self, s, order, jobs):
        """ブラウザ版（Pyodide）: 補助の Web Worker（web/helper.js）に組を分けて計算する。補助が読み込み中・無いときは None（1つで計算）。
        脚本家と盤面を pickle で渡し、組の番号 i を i % k で分ける（0 は自分、1.. は補助）。自分の分を計算してから補助の結果を待つ。"""
        try:
            import js
            from pyodide.ffi import run_sync, to_js
            n = int(js.par_helpers_ready())
        except Exception:
            return None
        if n < 1:
            return None
        import pickle
        k = n + 1
        js.par_start(to_js(pickle.dumps((self._picklable(), s, order, jobs), protocol=5)), k)
        out = {i: self._par_one(s, order, jobs[i]) for i in range(0, len(jobs), k)}
        try:
            for b in run_sync(js.par_wait()):
                out.update(pickle.loads(bytes(b.to_py())))
        except Exception:  # 補助が落ちた: 受け取れなかった組はここで計算する（止まらない）
            pass
        for i in range(len(jobs)):
            if i not in out:
                out[i] = self._par_one(s, order, jobs[i])
        return [out[i] for i in range(len(jobs))]

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
    def _leak(self, s, obs):
        """観測 obs の漏れ（ビット）: 配役のエントロピーの減り（最後の戦い）＋ rule_leak × 残りのループ × ルールの組のエントロピーの減り。
        ルールが割れると主人公はこの先のループで負け筋を読んで止めやすくなる。残りのループが減るほど許せる（ユーザー 2026-10-16）。
        残りのループ = 残りのループ数 + 今のループの残りの日の割合（最終ループの最終日で 0）。"""
        self.pub._ensure(s)
        v = self.pub.ded.trial(obs)
        w = getattr(self, 'rule_leak', 0)
        if w:
            rem = (self.loops - s['loop']) + (self.days - s['day']) / self.days
            v += w * rem * self.pub.ded.trial_rules(obs)
        return v

    def _lam(self, s):
        # 最終ループはループを取らないと負けなので、進みを重く見る（λ を下げる）
        lam = self.lam * (0.5 if s['loop'] >= self.loops else 1.0)
        return lam * self.hide if self._hiding(s) else lam

    def _fake_pushes(self, s, n, taken):
        """確定したループで置く偽の筋への暗躍（最大 n 枚）。主人公の推理で生きている仮説を引き、本当の近い筋が要する所でない置き先に押す。
        型2: 偽のループ終了時の敗北条件（神社・ウィッチの初期エリア・キーパーソンの暗躍2）を先に選び、ループ終了時に成立させきる。
        成立していれば敗北の原因が絞れず、届かずに終わると原因の候補から消えるだけで札の無駄（p006 の分析）。
        - すでに2以上の所には置かない。残りの日数（暗躍+2 が使えれば +1）で2に届かない所にも置かない。
        - 仮説のループ終了時の条件が無ければ、近い筋が暗躍を要する所（型1の初版）に押す。
        ponytail: 不安+1 は使わない（事件の日に限界以上にすると「犯人ではない」が割れる。型4 の安全条件の判定が要る）。
        仮説の事件の犯人は本当の犯人のまま（偽の筋は役職とルールの側だけ）。主人公の暗躍禁止の読みは見ていない。"""
        rel0 = self._relevant(s)
        real = {t for t, k in rel0 if k == 'int'}  # 本当の筋が暗躍を要する所（ここは押さない）
        # 型6 の仕込み: 本当の筋が不安を要する人物（事件の犯人・メインラバーズなど）に暗躍を置き、「暗躍の人」に見せておく。
        # 次のループで主人公がそこへ暗躍禁止を置けば、不安+1 が通る（暗躍禁止と不安−1 は同じ人物に同じ日に置けない）
        bait = {t for t, k in rel0 if k == 'par' and t not in real and not t.startswith('B:')}
        # 置き先の順はループの最初に確定したときに一度だけ決め、そのループの間は同じ所へ押す（日ごとに変えると偽の話にならない。H2。p006 の分析）
        fix = getattr(self, 'fake_fix', True)  # False: H2 の前（呼ぶたびに引き直し、置き先の数も絞らない。比較用）
        fr = getattr(self, '_fake_rank', None)
        if not fix or not fr or fr[0] != s['loop']:
            self.pub._ensure(s)
            cnt, end = {}, set()
            for combo, roles in self.pub.ded.sample(random.Random(self.rng.random()), 8) or []:
                sc = {'rules': list(combo), 'roles': dict(roles), 'incidents': s['script']['incidents']}
                for t in self._loop_end_targets(s, sc):
                    if t not in real:
                        cnt[t] = cnt.get(t, 0) + 3  # ループ終了時の条件を優先（型2）
                        end.add(t)
                try:
                    rel = self._relevant(dict(s, script={**s['script'], **sc}))
                except Exception:
                    continue
                for t, k in rel:
                    if k == 'int' and t not in real:
                        cnt[t] = cnt.get(t, 0) + 1
            order = sorted(cnt, key=lambda t: (-cnt[t], t))
            if fix and bait and s['loop'] < self.loops:  # 次のループがあるときは、2か所のうち1か所を仕込みに充てる
                b = sorted(bait)[0]
                order = [t for t in order if t != b][:1] + [b]
            fr = self._fake_rank = (s['loop'], [(t, t in end) for t in order[:2 if fix else None]])  # 2か所まで
        hand = [c for c in ('INT2', 'INT1') if not (c in ONCE['M'] and c in s['used']['M'])]
        out = []
        for t, is_end in fr[1]:
            if len(out) >= n or not hand or t in real or t in taken:
                continue
            if t not in card_targets(s):
                continue  # 死亡・登場前の人物・幻想には札を置けない
            if is_end:
                need = 2 - (s['boards'].get(t[2:], 0) if t.startswith('B:') else s['chars'][t]['int'])
                if need <= 0 or need > self.days - s['day'] + 1 + ('INT2' in hand):
                    continue
                c = 'INT2' if need >= 2 and 'INT2' in hand else 'INT1' if 'INT1' in hand else hand[0]
            else:
                c = hand[0]
            hand.remove(c)
            out.append((t, c))
        return out

    @staticmethod
    def _loop_end_targets(s, sc):
        """仮説の脚本 sc のループ終了時の暗躍の敗北条件の置き先（データ loop_end_loss: board_int・role_counter・start_area_int）。"""
        out = []
        for rid in sc['rules']:
            c = sets.rule(s, rid).get('loop_end_loss') or {}
            if c.get('do') == 'board_int':
                out.append('B:' + c['board'])
            elif c.get('do') == 'role_counter' and c.get('counter') == 'int':
                out += [x for x, r in sc['roles'].items() if r == c['role'] and x in s['chars'] and s['chars'][x]['alive']
                        and s['chars'][x].get('present', True)]  # 登場前の人物には札を置けない
            elif c.get('do') == 'start_area_int':
                out += ['B:' + (s.get('init') or {})[x] for x, r in sc['roles'].items() if r == c['role'] and (s.get('init') or {}).get(x)]
        return out

    def _all_in(self, s):
        """全力モードの理由（無ければ None）。ループの最初に1回決め、そのループの間は変えない（ユーザー案 2026-10-15
        「残ループと残っている筋から、情報が割れても全力で勝ちに行くべきタイミングはある」）。全力なら、確定したループでも
        おとり（型1・型2）と隠す（任意能力を使わない）をやめる。最終ループは今まで通り（確定後は隠す）。
        - 最後の戦いで外させる見込みが無い（公開情報の推理で、本当の配役の重みが ALL_IN_CRACK 以上）なら全力。
        「使える筋の数 < 残りのループ数」の条件は試して外した: 筋を1本ずつ逆算すると、ほとんどの筋は主人公に止められる（見込み 0）。
        全部の筋を同時に押して必ず取れる脚本（p002・p006 など）は隠しても取る確実さが下がらず、同時でも取れない脚本（p001・gen_s11_034 など）は
        主人公の読み違いで取っているので、どちらも筋の数で全力に切り替える理由にならない（plan の「全力モード」）。"""
        m = getattr(self, '_allin', None)
        if m and m[0] == s['loop']:
            return m[1]
        why = None
        if s['loop'] < self.loops:
            import numpy as np
            from engine.deduce import ROLES
            self.pub._ensure(s)
            ded = self.pub.ded
            R, C = ded._sync()
            truth = np.array([ROLES.index(s['script']['roles'].get(c, 'PERSON')) for c in ded.chars])
            w = ded._w(C)
            crack = float(w[(R == truth).all(axis=1)].sum() / w.sum()) if w.sum() else 1.0
            if crack >= ALL_IN_CRACK:
                why = f'最後の戦いで外させる見込みが無い（本当の配役の重み {crack:.2f}）'
        self._allin = (s['loop'], why)
        return why

    def _locked(self, s):
        """このループの勝ちが確定したか: ループ終了時の敗北条件が成立していて、主人公に崩す手がない。
        蝶の羽ばたきの発生とフレンドの死亡は戻らない。ボード・カウンターの条件は、1つ取られても残る余りがあるときだけ確定とみなす
        （神格[5] などで1つ取り除かれる。ponytail: 取り除く能力の有無は見ていない）。"""
        r = ph.loop_end_loss(copy.deepcopy(s))  # 写しで呼ぶ（フレンドの役職公開を書き込むため）
        return bool({'Y_FUTURE', 'FRIEND'} & set(r)) or (bool(r) and surplus(s) >= 1)

    def _guide_loop_routes(self, s):
        """指針（guide の routes に loop があるもの）がこのループに選んだ筋 {(id, via)}。無ければ空。"""
        return {(g['id'], g.get('via')) for g in (self.guide or {}).get('routes', []) if s['loop'] in g.get('loop', ())}

    def _hiding(self, s):
        """確定したループで隠すか（hide があり、確定していて、全力モードでない。全力モードは lock_bluff を持つ脚本家だけ）。"""
        return bool(self.hide) and self._locked(s) and not (getattr(self, 'lock_bluff', False) and self._all_in(s))

    def killer_pick(self, s):
        if self._hiding(s):
            return []  # 勝ちは確定済み。キラーの能力は役職を明かすだけ
        opts = super().killer_pick(s)
        g = self._guide_loop_routes(s)
        if opts and g and s['day'] < self.days:
            # 指針の筋が明日以降も必ず取れるなら、指針に無い筋の任意の能力（メインラバーズ・キラー）は使わない（その役職を明かすだけ）
            # ponytail: 明日の朝の盤面の代わりに日だけ進めた写しで逆算する（ターン終了後の事件・能力は見ていない）
            nxt = dict(copy_state(s), day=s['day'] + 1)
            if forced_plan(nxt, self.days, pcab=getattr(self, 'calc_pcab', False), only=g)['forced']:
                rid = {'MAIN_LOVERS': 'ML_PROT'}
                opts = [o for o in opts if (rid.get(o['ability'], o['ability']), o['char']) in g]
        return opts

    def mm_ability(self, s, actor, ability=None):
        if self._hiding(s):
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
        # 優先順（メモリ mastermind-loop-win-first）: 今日ループが取れる ＞ このループが必ず取れる（逆算の必勝） ＞ 評価−漏れ。
        # 使わないと必勝でないのに、使うと必勝になる選び方があれば、漏れと比べずにそれから選ぶ（ユーザー「ループ勝てなくなるのは意味がない」）
        from engine.route_calc import forced_plan
        need_forced = self.calc and not forced_plan(s, self.days)['forced']
        forced_opts = []
        for t in opts:
            trial = copy.deepcopy(s)
            try:
                ev = ph.mm_ability(trial, actor, t, ability)
            except IllegalPlacement:
                continue
            if need_forced and forced_plan(trial, self.days)['forced']:
                forced_opts.append((score(trial), t))
            if _wins_today(trial, self.days):
                # 今日のうちにループが取れるなら、漏れと比べずに使う（ループを取るのが最優先。メモリ mastermind-loop-win-first）。
                # 目隠し7戦目 gen_s11_027 L1D5: クロマクで手先（メインラバーズ、不安3）に暗躍1を置けば主人公死亡だったのに、漏れの λ で使わなかった
                return t
            area = t[2:] if t.startswith('B:') else s['chars'][t]['area']
            obs = [('mm_effect', {'counter': 'int' if 'int' in ev[0] else 'par', 'present': ph.alive_in(s, area),
                                  'school_int': trial['boards']['SCH']})]
            lam = self._lam(s)
            if self.chain_ab != 1.0 and not t.startswith('B:') and any(
                    i['culprit'] == t and i['day'] >= s['day'] for i in s['script']['incidents']):
                lam *= self.chain_ab
            v = score(trial) - lam * self._leak(s, obs)
            if v > best_v:
                best, best_v = t, v
        if forced_opts:
            return max(forced_opts)[1]
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
        return self._lam(s) * self._leak(s, [('intx_ignored', {'present': ph.alive_in(s, area)})])

    def _leak_cost_ability(self, s, cid):
        """脚本家が医者[2] を使うと、医者が友好無視を持つと明かす（拒否と同じ情報）。"""
        self.pub._ensure(s)
        return self._lam(s) * self._leak(s, [('refused', {'char': cid})])
