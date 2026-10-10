"""推理（主人公が公開情報だけから、ルールと配役を絞る）。

仮説 = (ルールY, ルールXの組, 配役)。惨劇セットのルールと役職の対応（早見表、data/rules.json）から、
各ルールの組み合わせが追加する役職を、登場人物に割り当てる方法を列挙する（役職の上限も守る。ミスリーダー1・フレンド2）。
観測（Observation）と矛盾する仮説を捨て、残った仮説からキャラクターごとの役職の分布を出す。

扱う観測（どれも公開される情報）:
- role_revealed(char, role): 友好能力で明かされた役職（妄想拡大ウイルスで変化した後の役職の場合がある）
- rule_x_revealed(rule): 情報屋で明かされたルールX
- refused(char) / not_refused(char): 友好能力の拒否の有無（友好無視・絶対友好無視）
- mm_effect(area, kind): 脚本家能力フェイズに、そのエリアのキャラクターかボードに暗躍/不安が置かれた
  （クロマク＝暗躍、ミスリーダー＝不安。どちらも同一エリアのみ。ファクターは学校に暗躍2以上でミスリーダーの能力を得る）
- key_death(chars): その死亡でループが終わった。死んだ者のうち誰かがキーパーソン（または都市に暗躍2以上のファクター）
- turn_end_death(victim, others): ターン終了フェイズの死亡。同一エリアにキラーかシリアルキラーがいた
- loop_start_par: ループ開始時に不安が置かれた（因果の糸がある）
犯人の推理は別（事件の発生・不発から候補を絞る。incident_candidates）。
"""
import itertools
import json
import math
import os

import numpy as np

from .resolve import CHARS

RULES = json.load(open(os.path.join(os.path.dirname(__file__), 'data', 'rules.json'), encoding='utf-8'))['rules']
LIMIT = {'MISLEADER': 1, 'FRIEND': 2}
REFUSERS = {'KILLER', 'KUROMAKU', 'FACTOR', 'CULTIST', 'WITCH'}
ABSOLUTE = {'CULTIST', 'WITCH'}


def rule_combos(set_rules=None):
    ys = [r for r, v in RULES.items() if v['type'] == 'Y']
    xs = [r for r, v in RULES.items() if v['type'] == 'X']
    for y in ys:
        for x1, x2 in itertools.combinations(xs, 2):
            yield (y, x1, x2)


def roles_for(combo):
    need = {}
    for r in combo:
        for role, n in RULES[r]['roles'].items():
            need[role] = need.get(role, 0) + n
    for role, cap in LIMIT.items():
        if role in need:
            need[role] = min(need[role], cap)
    return need


ROLES = ['PERSON', 'KEY', 'KILLER', 'KUROMAKU', 'CULTIST', 'TT', 'WITCH', 'FRIEND', 'MISLEADER', 'LOVERS', 'MAIN_LOVERS', 'SK', 'FACTOR']
RC = {r: i for i, r in enumerate(ROLES)}
COMBOS = list(rule_combos())
_BASE = {}  # 登場人物の数 → 観測前の全仮説（配役の行列, ルールの組の番号）。観測者どうしで共有する


class TooManyHypotheses(Exception):
    """仮説の数が上限を超えた。黙って切り捨てると推理が壊れる（敵対的レビュー1）ので止める。"""


SPECIAL = ('C11', 'C28', 'C32', 'C33')  # 役職の決まり方が通常と違うキャラクター（カードの特性）


class _Rows:
    """仮説の行を少しずつ int8 の配列にまとめて貯める（Python のリストのまま数百万行を持つと、10人の脚本で 2GB を超えた。
    Docker が止まった原因。2026-10-02）。"""
    def __init__(self, width):
        self.width, self.buf, self.ci, self.chunks, self.cchunks, self.n = width, [], [], [], [], 0

    def append(self, row, ci):
        self.buf.append(row)
        self.ci.append(ci)
        self.n += 1
        if len(self.buf) >= 200_000:
            self._flush()

    def _flush(self):
        if self.buf:
            self.chunks.append(np.array(self.buf, dtype=np.int8).reshape(-1, self.width))
            self.cchunks.append(np.array(self.ci, dtype=np.int16))
            self.buf, self.ci = [], []

    def arrays(self):
        self._flush()
        if not self.chunks:
            return np.zeros((0, self.width), dtype=np.int8), np.zeros(0, dtype=np.int16)
        return np.concatenate(self.chunks), np.concatenate(self.cchunks)


def _base_for(chars, cap):
    """登場人物に合わせた観測前の全仮説。特別なキャラクターがいなければ人数だけで決まる（共有）。
    - イレギュラー C11: ルールの役職の枠を埋めず、選ばれたルールが追加しない役職（パーソン以外）になる
    - コピーキャット C28: ルールの役職の枠を埋めず、他のキャラクター1人と同じ役職（パーソン以外）になる
    - アルバイト C32: 配役を無視してパーソン。アルバイト？ C33 はアルバイトの配役と同じ"""
    if not any(c in chars for c in SPECIAL):
        return _base(len(chars), cap)
    key = tuple(chars)
    if key in _BASE:
        return _BASE[key]
    ix = {c: i for i, c in enumerate(chars)}
    free = [c for c in chars if c not in ('C11', 'C28', 'C33')]  # ルールの枠を埋めうる者
    R0, C0 = _base(len(free), cap)
    acc = _Rows(len(chars))
    for row, ci in zip(R0.tolist(), C0.tolist()):
        full = [0] * len(chars)
        for c, x in zip(free, row):
            full[ix[c]] = x
        if 'C32' in ix:
            cast = full[ix['C32']]
            full[ix['C32']] = 0  # 配役を無視してパーソン
            if 'C33' in ix:
                full[ix['C33']] = cast
        opts11 = [None]
        if 'C11' in ix:
            added = set(roles_for(COMBOS[ci]))
            opts11 = [RC[r] for r in ROLES if r != 'PERSON' and r not in added]
        for r11 in opts11:
            f2 = list(full)
            if r11 is not None:
                f2[ix['C11']] = r11
            opts28 = [None]
            if 'C28' in ix:
                opts28 = sorted({x for j, x in enumerate(f2) if x and j != ix['C28']}) or [None]
            for r28 in opts28:
                f3 = list(f2)
                if r28 is not None:
                    f3[ix['C28']] = r28
                acc.append(f3, ci)
        if acc.n > cap:
            raise TooManyHypotheses(f'{len(chars)}人で {acc.n} 件を超えた（上限 {cap}）')
    _BASE[key] = acc.arrays()
    return _BASE[key]


def _base(n, cap):
    if n in _BASE:
        return _BASE[n]
    acc = _Rows(n)
    for ci, combo in enumerate(COMBOS):
        need = [(r, k) for r, k in roles_for(combo).items() if k]
        if sum(k for _, k in need) > n:
            continue

        def rec(i, avail, cur):
            if i == len(need):
                acc.append(cur.copy(), ci)
                return
            r, k = need[i]
            for pos in itertools.combinations(avail, k):
                for p in pos:
                    cur[p] = RC[r]
                rec(i + 1, [a for a in avail if a not in pos], cur)
                for p in pos:
                    cur[p] = 0
        rec(0, list(range(n)), [0] * n)
        if acc.n > cap:
            raise TooManyHypotheses(f'{n}人で {acc.n} 件を超えた（上限 {cap}）')
    _BASE[n] = acc.arrays()
    return _BASE[n]


def _combo_has(rule):
    return np.array([rule in cb for cb in COMBOS])


HAS = {r: _combo_has(r) for r in RULES}


class Deduction:
    """仮説は numpy の行列で持つ: R[仮説, キャラクター] = 役職の番号、C[仮説] = ルールの組の番号。
    観測はそれぞれ仮説ごとの真偽の配列（マスク）に直して絞る。試しの観測（trial）は絞らずに結果だけ返す。"""
    # 試験・対戦表用: 真の脚本 {'rules', 'roles'} を置くと、以後に作る推理はそれが消された観測を記録する（truth_lost_by）
    WATCH = None

    def __init__(self, chars, cap=30_000_000):
        self.chars = list(chars)
        self.ix = {c: i for i, c in enumerate(self.chars)}
        self.cap = cap
        self.obs = []
        self._R = self._C = None
        self._seen_obs = 0
        # 仮説が全部消えた最初の観測（推理の矛盾。ふつうは起きない＝推理かエンジンの不具合）と、真の脚本を消した最初の観測
        self.empty_by = None
        self.truth_lost_by = None
        self._watch = Deduction.WATCH
        self._truth = None
        # カードの特性による配役の制約（公開情報）。A.I.【特性】パーソンにできない、妹【特性】友好無視を持つ役職にできない。
        # 脚本の検査（scripts.check）と生成（generate）は守っていたが、推理が残していた（対AIモードの点検で発見）
        if 'C22' in self.ix:
            self.obs.append(('not_role', {'char': 'C22', 'roles': ('PERSON',)}))
        if 'C31' in self.ix:
            self.obs.append(('not_role', {'char': 'C31', 'roles': tuple(sorted(REFUSERS))}))
        # 僕と契約しようよ！【強制：脚本作成時】必ず少女がキーパーソンとなる（早見表、wiki/concepts/rule-role-matrix.md）
        # コピーキャットは役職をルールの枠でなく特性で得る（他の1人と同じ役職）ので除く。脚本の検査（scripts.check: 少女のキーパーソンが
        # 1人いればよい）と合わせる（除かないと、コピーキャットがキーパーソンの脚本 d05 で真の脚本を最初から消していた）
        self.obs.append(('contract_girl', {'nongirls': [c for c in self.chars if '少女' not in CHARS[c].get('tags', []) and c != 'C28']}))

    def observe(self, kind, **kw):
        self.obs.append((kind, kw))

    # ---- 観測 → 仮説ごとの真偽 ----
    def _col(self, R, c):
        return R[:, self.ix[c]]

    def _is(self, R, c, *roles):
        return np.isin(self._col(R, c), [RC[r] for r in roles])

    def _mask(self, R, C, kind, kw):
        virus = HAS['X_VIRUS'][C]
        if kind == 'rule_x_revealed':
            return HAS[kw['rule']][C]
        if kind == 'role_revealed':
            r, c = kw['role'], kw['char']
            m = self._is(R, c, r)
            if r == 'SK':  # 妄想拡大ウイルスでパーソンがシリアルキラーとして明かされることがある
                m |= self._is(R, c, 'PERSON') & virus
            return m
        if kind == 'contract_girl':
            m = np.ones(len(C), bool)
            for c in kw['nongirls']:
                m &= ~(HAS['Y_CONTRACT'][C] & self._is(R, c, 'KEY'))
            return m
        if kind == 'not_role':
            return ~self._is(R, kw['char'], *kw['roles'])
        if kind == 'refused':
            return self._is(R, kw['char'], *REFUSERS)
        if kind == 'not_refused':
            return ~self._is(R, kw['char'], *ABSOLUTE)
        if kind == 'mm_effect':
            present = kw['present']  # そのとき、そのエリアにいた生存キャラクター
            want = {'int': ('KUROMAKU',), 'par': ('MISLEADER',)}[kw['counter']]
            m = np.zeros(len(C), bool)
            for c in present:
                m |= self._is(R, c, *want)
            if kw['counter'] == 'par' and kw.get('school_int', 0) >= 2:
                for c in present:
                    m |= self._is(R, c, 'FACTOR')
            if kw['counter'] == 'int' and kw.get('rumor_possible'):
                m |= HAS['X_RUMOR'][C]  # 不穏な噂（1ループ1回、任意のボード）
            return m
        if kind == 'loop_start_par':
            return HAS['X_THREAD'][C]  # ループ開始時に不安が置かれるのは因果の糸だけ（早見表 ルールX）
        if kind == 'turn_end_death':
            # ターン終了フェイズの死亡: 同一エリアにキラー（被害者がキーパーソンで暗躍2以上）か、
            # シリアルキラー（被害者と2人きり。配役、または妄想拡大ウイルスで不安3以上のパーソン）がいた
            v, others = kw['victim'], kw['others']
            m = np.zeros(len(C), bool)
            for c in others:
                if kw['victim_int'] >= 2:
                    m |= self._is(R, c, 'KILLER') & self._is(R, v, 'KEY', 'FACTOR')
                if len(others) == 1:
                    m |= self._is(R, c, 'SK')
                    if kw['par'].get(c, 0) >= 3:
                        m |= self._is(R, c, 'PERSON') & virus
            return m
        if kind == 'no_sk':
            # シリアルキラーの能力は【強制】。ターン終了時に2人きりで、どちらも死なず護衛も減らなければ、どちらもシリアルキラーではない
            m = np.ones(len(C), bool)
            for c in kw['chars']:
                m &= ~self._is(R, c, 'SK')
                if kw['par'].get(c, 0) >= 3:
                    m &= ~(self._is(R, c, 'PERSON') & virus)
            return m
        if kind == 'prot_death_turn_end':
            # ターン終了フェイズの主人公死亡: 暗躍4以上のキラーか、暗躍1以上・不安3以上のメインラバーズがいた（早見表 役職）
            m = np.zeros(len(C), bool)
            for c, (i, p) in kw['counters'].items():
                if i >= 4:
                    m |= self._is(R, c, 'KILLER')
                if i >= 1 and p >= 3:
                    m |= self._is(R, c, 'MAIN_LOVERS')
            return m
        if kind == 'intx_ignored':
            # 暗躍禁止があったのに暗躍が置かれた → そのエリアにカルティストがいた（早見表 カルティスト【任意：行動解決フェイズ】）
            m = np.zeros(len(C), bool)
            for c in kw['present']:
                m |= self._is(R, c, 'CULTIST')
            return m
        if kind == 'no_death':
            # 死亡させる効果を受けて死ななかった（護衛の減少は別に公開される）→ タイムトラベラー（早見表 役職）
            return self._is(R, kw['char'], 'TT')
        if kind == 'lovers_par':
            # 誰かの死亡と同時に、どこからともなく不安6 → 死者と受け手がラバーズとメインラバーズの組（早見表 役職）
            c, d = kw['char'], kw['dead']
            return (self._is(R, c, 'MAIN_LOVERS') & self._is(R, d, 'LOVERS')) | (self._is(R, c, 'LOVERS') & self._is(R, d, 'MAIN_LOVERS'))
        if kind == 'tt_loss':
            # 最終日のターン終了フェイズに、死亡も主人公死亡も無く敗北 → 友好2以下のタイムトラベラーがいた
            m = np.zeros(len(C), bool)
            for c, gw in kw['gw'].items():
                if gw <= 2:
                    m |= self._is(R, c, 'TT')
            return m
        if kind == 'loop_end_check':
            # ループ終了時の敗北条件（rule-role-matrix.md「追加ルールの文面」、role-abilities.md フレンド）のどれかが成り立つか
            b = kw['boards']
            cond = HAS['Y_SEAL'][C] & (b['SHR'] >= 2)
            if kw.get('butterfly'):
                cond = cond | HAS['Y_FUTURE'][C]
            for c, i in kw['ints'].items():
                if i >= 2:
                    cond = cond | (HAS['Y_CONTRACT'][C] & self._is(R, c, 'KEY'))
                a = kw.get('init', {}).get(c)
                if a and b.get(a, 0) >= 2:
                    cond = cond | (HAS['Y_BOMB'][C] & self._is(R, c, 'WITCH'))
            for c in kw['dead']:
                cond = cond | self._is(R, c, 'FRIEND')
            m = cond if kw['loss'] else ~cond
            # 死亡したフレンドはループ終了時に正体が公開される（早見表 フレンド、FAQ Ru02）。公開されなかった死者はフレンドではない
            for c in kw['dead']:
                if c not in kw.get('revealed', ()):
                    m = m & ~self._is(R, c, 'FRIEND')
            return m
        if kind == 'key_death':
            # 同時に複数が死んだとき、どれがキーパーソンかは公開情報では分からない。死んだ者の誰かがキー（またはキーの能力を持つファクター）
            m = np.zeros(len(C), bool)
            for c in kw.get('chars') or [kw['char']]:
                m |= self._is(R, c, 'KEY')
                if kw.get('city_int', 0) >= 2:
                    m |= self._is(R, c, 'FACTOR')
            return m
        raise ValueError(f'未知の観測 {kind}')

    def _sync(self):
        if self._R is None:
            self._R, self._C = _base_for(self.chars, self.cap)
            self._lw = None  # 仮説ごとの対数の重み（soft_target）。None なら全て0
            self._seen_obs = 0
            if self._watch is not None:
                self._truth = self._truth_mask(self._R, self._C, self._watch)
                if not self._truth.any():
                    self.truth_lost_by = ('（仮説の空間に真の脚本が無い）', {})
        for kind, kw in self.obs[self._seen_obs:]:
            keep = self._mask(self._R, self._C, kind, kw)
            self._R, self._C = self._R[keep], self._C[keep]
            if self._lw is not None:
                self._lw = self._lw[keep]
            if self._truth is not None:
                had = self._truth.any()
                self._truth = self._truth[keep]
                if had and not self._truth.any() and self.truth_lost_by is None:
                    self.truth_lost_by = (kind, kw)
            if not len(self._C) and self.empty_by is None:
                self.empty_by = (kind, kw)
                import sys
                print(f'[推理の矛盾] 仮説が全部消えた: 観測 {kind} {kw}', file=sys.stderr)
        self._seen_obs = len(self.obs)
        return self._R, self._C

    def _truth_mask(self, R, C, script):
        """真の脚本（ルールの組＋配役）に当たる仮説の印。アルバイトはパーソン、アルバイト？はアルバイトの配役（truth_metrics と同じ）。"""
        def true_role(c):
            if c == 'C32':
                return 'PERSON'
            return script['roles'].get('C32' if c == 'C33' else c, 'PERSON')
        true = np.array([RC[true_role(c)] for c in self.chars], dtype=R.dtype)
        ci = [i for i, cb in enumerate(COMBOS) if set(cb) == set(script['rules'])]
        return (R == true).all(axis=1) & np.isin(C, ci)

    def soft_target(self, char, gamma):
        """脚本家がこのキャラクターに伏せ札を置いた。役職のある者ほど狙われやすい、という弱い読みで重みを掛ける（仮説は消さない）。
        主人公の推理だけで使い、理想の観測者（強さの指標）には使わない。"""
        R, C = self._sync()
        if char not in self.ix or not len(C):
            return
        if self._lw is None:
            self._lw = np.zeros(len(C), dtype=np.float32)
        self._lw += gamma * (R[:, self.ix[char]] != 0)
        self._lw -= self._lw.max()

    INT_ROLES = ('KEY', 'FRIEND', 'KILLER', 'MAIN_LOVERS')
    # 自分の不安が負け筋になる役職（メインラバーズ: 暗躍1・不安3 で主人公の死）。事件の犯人も不安で動くが、犯人は仮説の外なので数えない
    PAR_ROLES = ('MAIN_LOVERS',)  # 自分の暗躍が負け筋になる役職（契約・遠隔殺人の標的・キラー・メインラバーズ）

    def card_evidence(self, target, card, init, lr):
        """公開された脚本家の暗躍・不安+1 の札（行動解決で6枚公開。学者の特性で脚本家が選んだカウンターも同じ扱い）から、その札が負け筋を進める仮説を lr 倍に重くする（仮説は消さない）。
        人物への暗躍: その人物の役職が INT_ROLES。ボードへの暗躍: 封印されしモノ（神社）・巨大時限爆弾X（ウィッチの初期エリア）・
        不定因子χ（都市、ファクターがキーパーソンの能力）。脚本家の意図の読み（Claude の主人公「暗躍+2 を使った所が要」）。
        ponytail: 不安+1 は事件の犯人（仮説の外）に効くので使わない。おとりの札も同じ重みで数える（lr を小さめに）。"""
        if card not in ('INT1', 'INT2', 'PAR+'):
            return
        R, C = self._sync()
        if not len(C):
            return
        if target.startswith('B:'):
            if card == 'PAR+':
                return
            a = target[2:]
            rules = lambda r: np.array([r in cb for cb in COMBOS])[C]  # noqa: E731
            m = np.zeros(len(C), bool)
            if a == 'SHR':
                m |= rules('Y_SEAL')
            if a == 'CIT':
                m |= rules('X_FACTOR')
            ws = [c for c in self.chars if (init or {}).get(c) == a]
            if ws:
                m |= rules('Y_BOMB') & np.logical_or.reduce([self._is(R, c, 'WITCH') for c in ws])
        elif target in self.ix:
            m = self._is(R, target, *(self.PAR_ROLES if card == 'PAR+' else self.INT_ROLES))
        else:
            return
        if self._lw is None:
            self._lw = np.zeros(len(C), dtype=np.float32)
        self._lw += math.log(lr) * m
        self._lw -= self._lw.max()

    # ---- 読み出し ----
    def n_hyp(self):
        return len(self._sync()[1])

    def hypotheses(self, cap=None):
        """[(ルールの組, {キャラクター: 役職（パーソン以外）})]。数が多いときは重いので、試験と少数の場合に使う。"""
        R, C = self._sync()
        return [(COMBOS[ci], {self.chars[j]: ROLES[x] for j, x in enumerate(row) if x}) for row, ci in zip(R.tolist(), C.tolist())]

    def weight(self, combo):
        """事前の重み: ルールの組み合わせは等確率、その中の配役も等確率とする。
        役職の多い組み合わせは配役の並べ方が多いので、1/(並べ方の数) で割って偏りを消す。"""
        need = roles_for(combo)
        k = sum(need.values())
        n = len(self.chars)
        if k > n:
            return 0.0
        ways = math.perm(n, k)
        for c in need.values():
            ways //= math.factorial(c)
        return 1.0 / ways

    def _w(self, C):
        if not hasattr(self, '_cw'):
            self._cw = np.array([self.weight(cb) for cb in COMBOS])
        w = self._cw[C]
        lw = getattr(self, '_lw', None)
        if lw is not None and len(lw) == len(C) and C is self._C:  # 全仮説のときだけ soft_target の重みを掛ける
            w = w * np.exp(lw)
        return w

    def _marg(self, R, C):
        w = self._w(C)
        return np.stack([np.bincount(R[:, j], weights=w, minlength=len(ROLES)) for j in range(len(self.chars))]), w.sum()

    def marginals(self, hyps=None):
        M, tot = self._marg(*self._sync())
        return {c: {ROLES[k]: M[j, k] for k in np.nonzero(M[j])[0]} for j, c in enumerate(self.chars)}, tot

    def role_entropy(self, R=None, C=None):
        """配役の周辺分布のエントロピーの和（ビット）。最後の戦いで当てるのは配役なので、ルールの組の不確かさは数えない。"""
        if R is None:
            R, C = self._sync()
        M, tot = self._marg(R, C)
        if not tot:
            return 0.0
        P = M / tot
        with np.errstate(divide='ignore', invalid='ignore'):
            return float(-np.nansum(np.where(P > 0, P * np.log2(P), 0.0)))

    def trial(self, obs):
        """観測の列 [(kind, kw)] を足したと仮定したときの、配役のエントロピーの減り（漏れ、ビット）。絞り込みはしない。"""
        R, C = self._sync()
        keep = np.ones(len(C), bool)
        for kind, kw in obs:
            keep &= self._mask(R, C, kind, kw)
        if keep.all():
            return 0.0
        return self.role_entropy() - self.role_entropy(R[keep], C[keep])

    def sample(self, rng, k):
        """重みつきで k 個の仮説を引く（ルールの組, 配役）。"""
        R, C = self._sync()
        if not len(C):
            return []
        w = self._w(C)
        idx = rng.choices(range(len(C)), weights=w.tolist(), k=k) if len(C) < 200000 else \
            [int(i) for i in np.searchsorted(np.cumsum(w), [rng.random() * w.sum() for _ in range(k)])]
        return [(COMBOS[C[i]], {self.chars[j]: ROLES[x] for j, x in enumerate(R[i].tolist()) if x}) for i in idx]

    def final_guesses(self):
        """最後の戦いの宣言。キャラクターごとに別々に最多の役職を選ぶと、役職の上限（ミスリーダー1など）や
        ルールの組み合わせに反する宣言になりうる。そこで、残った仮説のうち最も多く現れる配役の組（ルールの組を問わず
        同じ配役なら合算）を採る。宣言の順は、その役職の周辺確率が高い（確信のある）キャラクターから。当てるのは配役（FAQ XY02）。"""
        R, C = self._sync()
        if not len(C):
            return [{'char': c, 'role': 'PERSON'} for c in self.chars]
        uniq, inv = np.unique(R, axis=0, return_inverse=True)
        score = np.bincount(inv.ravel(), weights=self._w(C))
        best = uniq[int(np.argmax(score))]
        M, n = self._marg(R, C)
        guess = [(M[j, best[j]] / n, {'char': c, 'role': ROLES[best[j]]}) for j, c in enumerate(self.chars)]
        if 'C32' in self.ix and 'C33' in self.ix:  # アルバイトとアルバイト？は1回の宣言で、正解はアルバイトの配役＝C33 の列（裁定）
            guess = [x for x in guess if x[1]['char'] != 'C32']
        return [g for _, g in sorted(guess, key=lambda x: -x[0])]


def incident_candidates(chars, history, revealed=None):
    """犯人の候補。history: [{'id','occurred','critical':[判定時に不安臨界以上だった生存者]}]。revealed: {事件ID: 犯人}（刑事[友好4]）。
    - 発生 → 犯人は critical の中、不発 → critical の中の生存者は犯人ではない（犯人が死亡していれば不発もありうる）
    - 事件の犯人は互いに異なる（wiki/concepts/script.md「決着済み」。特殊ルールで外す脚本では使わない）ので、
      犯人が1人に決まった事件があれば、そのキャラクターを他の事件の候補から外し、決まるものが無くなるまで繰り返す"""
    out = {}
    for h in history:
        # 同じ名前の事件が複数の日にある脚本があるので、名前と日付で区別する（日付の無い古い記録は名前だけ）
        key = f"{h['id']}@{h['day']}" if h.get('day') is not None else h['id']
        h = dict(h, id=key)
        cand = set(out.get(h['id'], chars))
        if h['occurred']:
            cand &= set(h['critical'])
            if 'culprit_in' in h:  # 効果の現れた場所から絞った候補（PublicObserver._culprit_where）
                cand &= set(h['culprit_in'])
        else:
            cand -= set(h['critical'])
        out[h['id']] = cand
    for inc, c in (revealed or {}).items():  # 刑事[友好4] などで明かされた犯人（名前で指定。同名の事件すべてに当てはめる）
        for k in [k for k in out if k == inc or k.startswith(inc + '@')] or [inc]:
            out[k] = {c}
    changed = True
    while changed:
        changed = False
        fixed = {inc: next(iter(c)) for inc, c in out.items() if len(c) == 1}
        for inc, cand in out.items():
            others = {c for i, c in fixed.items() if i != inc}
            if cand & others and len(cand - others) >= 1:
                out[inc] = cand - others
                changed = True
    return out


KEY_ROLES = ('KEY', 'KILLER', 'KUROMAKU', 'MISLEADER')


def truth_metrics(ded, script, inc_history=(), culprits=None):
    """公開情報がどこまで真相を証明したか（plan/engine-smarter.md「強さの評価」の1層目）。
    ded は公開の出来事だけを入れた Deduction。script は真の脚本（rules・roles・incidents）。
    - confirmed: 真の役職の周辺確率が 1 のキャラクター（KEY_ROLES は別に数える）
    - p_truth: 真の配役の事後確率（ルールの組は問わない）。truth_alive=False は推理の健全性の破れ
    - mean_true: キャラクターごとの、真の役職の周辺確率の平均
    - role_entropy: 配役の周辺分布のエントロピーの和（ビット）
    - combos: 残ったルールの組の数、p_rules: 真のルールの組の事後確率
    - culprits_fixed: 犯人が1人に決まった事件の数"""
    R, C = ded._sync()
    M, tot = ded._marg(R, C)
    w = ded._w(C)
    def true_role(c):  # ゲーム中の役職（アルバイトはパーソン、アルバイト？はアルバイトの配役）
        if c == 'C32':
            return 'PERSON'
        return script['roles'].get('C32' if c == 'C33' else c, 'PERSON')
    true = np.array([RC[true_role(c)] for c in ded.chars], dtype=np.int8)
    p_truth = float(w[(R == true).all(axis=1)].sum() / tot) if tot else 0.0
    true_ci = [i for i, cb in enumerate(COMBOS) if set(cb) == set(script['rules'])]
    p_rules = float(w[np.isin(C, true_ci)].sum() / tot) if tot else 0.0
    pt = M[np.arange(len(ded.chars)), true] / tot if tot else np.zeros(len(ded.chars))
    conf = pt >= 1 - 1e-9
    cand = incident_candidates(ded.chars, list(inc_history), culprits)
    return {
        'n_hyp': int(len(C)),
        'truth_alive': p_truth > 0,
        'p_truth': round(p_truth, 6),
        'nlog_p_truth': round(-math.log2(p_truth), 3) if p_truth > 0 else None,
        'mean_true': round(float(pt.mean()), 4),
        'role_entropy': round(ded.role_entropy(R, C), 3),
        'confirmed': int(conf.sum()),
        'confirmed_key_roles': int(sum(conf[j] and ROLES[true[j]] in KEY_ROLES for j in range(len(true)))),
        'combos': int(len(np.unique(C))),
        'p_rules': round(p_rules, 4),
        'culprits_fixed': sum(len(v) == 1 for v in cand.values()),
    }
