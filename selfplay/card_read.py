"""脚本家の伏せ札の中身を、思考の型で読む（ユーザー方針 2026-10-23: 自動プレイヤーの癖ではなく思考の癖で、裏をかく可能性も入れる）。

置き場所3つ（主人公に見える）に、手札から中身を割り当てる組み合わせを全部作り、エンジンで盤面を進めて、型ごとの「選びたさ」を数える。
型（どれもルールと脚本から計算する。学習しない）:
- push    押す: 負け筋の近さの和（players.score と同じ量）を増やす
- urgent  締め切りで押す: 残りの日数で届く筋だけの近さ
- feint   見せ札: 筋を進めない（主人公の止め札を吸わせる）
- preempt 先回り: 主人公が今日〜明日に届く友好能力の持ち主に友好禁止
- level1  裏をかく: 主人公が一番危ないと見る置き場所（その場所に置ける札だけで最も筋が進む所）の札は囮で、本命は残りの2か所
- guard   防ぐ: 主人公が置きそうな札を打ち消す（不安禁止＝主人公が不安−1 を置きたい人物＝不安が1減ると筋が最も遠のく人物、友好禁止＝先回りと同じ）
- pushguard 押しつつ防ぐ: push と guard の標準化した値の和（強い脚本家は1日の3枚に両方を混ぜる）
- lock    決め手を仕込む: 主人公が止めなければ、翌朝に脚本家が必ず取れる形になる札（1枚だけ通したとき。移動でカルティストのエリアへ運ぶなど）を含む。
          読みの上位（押す札）の裏をかく強い札。d18 で Claude の脚本家が、暗躍と読まれる置き場所に斜めの移動を置いて2ループ取った（2026-10-24）
- random  無作為（比べる基準）
型ごとの確率は exp(λ·標準化した値)（λ は事前に 3 と決める）。型の重みは、日ごとに公開された中身でベイズ更新する。
未: 乗り換える（前の日に止められた筋を捨てる）・隠す（勝ちが決まったら本当の筋に触らない）。

    python -m selfplay.card_read collect --out DIR [--mms route search calcG calcGBxhdK] [--scripts d01_seal ...] [--seeds 1 2]
    python -m selfplay.card_read eval DIR
"""
import argparse
import itertools
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.resolve import CARDS, ONCE, IllegalPlacement, resolve_actions  # noqa: E402
from engine.routes import enumerate_routes  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.players import DUMMY_PC, _weight, cultist_options  # noqa: E402

TYPES = ['push', 'urgent', 'feint', 'preempt', 'level1', 'guard', 'pushguard', 'lock', 'random']
LAM = 3.0
NEUTRAL = ('PARX', 'GWX')  # 脚本家の筋を進めない札（1枚だけの効果を測るとき、残りの置き場所に置く）
_CH = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engine', 'data', 'characters_public.json')))
_CH = _CH.get('characters', _CH)


def hand(s):
    return [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]


def _after(s, mm):
    try:
        return resolve_actions(s, [{'by': 'M', 'target': t, 'card': c} for t, c in mm] + DUMMY_PC)[0]
    except IllegalPlacement:
        return None


def _near(state, days_left=None):
    try:
        rs = enumerate_routes(state)
    except Exception:
        return 0.0
    return sum(_weight(state, r) / (1 + r['total']) for r in rs if days_left is None or r['total'] <= days_left)


def route_targets(s):
    """負け筋の条件に出てくる人物とボード（決め手を探す置き場所。ここに無い所の1枚で翌朝に必ず取れる形にはならないとみなす）。
    ponytail: 筋に出ない人物を動かして位置の筋を作る手は拾わない。拾えずに落としたら、位置の条件の相手も入れる"""
    out = {}  # 置き場所 → その所で筋に要るもの（'par' 'int' 'pos'）
    for r in enumerate_routes(s):
        for c in r['conds']:
            d = c.get('detail') or ()
            if c['kind'] == 'board':
                out.setdefault('B:' + d[0], set()).add('int')
            elif c['kind'] == 'counter' and d[1] in ('par', 'int'):
                out.setdefault(d[0], set()).add(d[1])
            elif c['kind'] == 'incident_par' and len(d) > 1 and d[1] in s['chars']:
                out.setdefault(d[1], set()).add('par')  # 事件の犯人に要る不安（遠隔殺人の教師など。d14 L3 で犯人の準備の手が候補に出なかった）
            elif c['kind'] in ('counter_max', 'same_area', 'alone_with', 'in_area'):
                for x in d[:2]:
                    if isinstance(x, str) and x in s['chars']:
                        out.setdefault(x, set()).add('pos')
    return out


def lock_cards(need):
    """置き場所で筋に要るもの need に効く決め手の札（暗躍の札は暗躍、不安+1 は不安、移動は人物の位置と、カルティストのエリアへ運ぶ暗躍）。"""
    out = set()
    if 'int' in need:
        out |= {'INT1', 'INT2', 'MV_V', 'MV_H', 'MV_D'}
    if 'par' in need:
        out |= {'PAR+', 'MV_V', 'MV_H', 'MV_D'}
    if 'pos' in need:
        out |= {'MV_V', 'MV_H', 'MV_D'}
    return out


_LOCK = {}  # lock_of の答え（朝の盤面・置き場所・札ごと）


def lock_of(s, t, c, targets):
    """札 c を置き場所 t に1枚だけ通すと（残り2枚は筋を進めない札を、決まった別の置き場所に）、翌朝に脚本家が必ず取れる形になるか。
    朝の盤面・t・c だけで決まるようにして覚える（読む主人公の写しが同じ朝に置き場所の組を変えて何度も呼び、脚本家の決め手探しとも重なる）。"""
    from engine.route_calc import forced_plan
    from selfplay.pc_solve import end_of_day
    key = (json.dumps({k: v for k, v in s.items() if k != 'script'}, sort_keys=True, default=str), s['script'].get('id'), t, c)
    if key not in _LOCK:
        if len(_LOCK) > 20000:
            _LOCK.clear()
        rest = [u for u in sorted(targets) if u != t]
        a = next((a for o1, o2 in zip(rest, rest[1:]) for a in [_after(s, [(t, c), (o1, 'PARX'), (o2, 'GWX')])] if a), None)
        _LOCK[key] = bool(a) and _locked(a, s['script']['days'], end_of_day, forced_plan)
    return _LOCK[key]


def all_targets(s):
    """札を置ける所（盤上の生きているキャラクターとボード）。lock_of の「別の置き場所」をいつも同じにするため"""
    return [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)] + ['B:' + b for b in s['boards']]


def _locked(after, days, end_of_day, forced_plan):
    """行動解決のあとの盤面 after から、日の終わりまで進めて、負けか、翌朝に脚本家が必ず取れる形か。"""
    nxt, lost = end_of_day(after, days)
    return bool(lost) or (nxt is not None and forced_plan(nxt, days, pos=True, pcab=True)['forced'])


def _unlock(s, target):
    """先回りの値: 主人公が今日〜明日に届く友好能力（要る友好が今の友好+1〜+2）の数。"""
    c = s['chars'].get(target)
    if not c or not c['alive']:
        return 0
    return sum(1 for a in _CH.get(target, {}).get('abilities', []) if c['gw'] < a['need'] <= c['gw'] + 2)


def candidates(s, targets):
    """置き場所 targets に手札を割り当てる組み合わせ（重複なし）と、型ごとの値。"""
    h = hand(s)
    days_left = s['script']['days'] - s['day']
    base = _near(s)
    one = {}  # 1枚だけの効果（残りの置き場所に筋を進めない札）
    lock = {}  # その1枚だけが通ると、翌朝に脚本家が必ず取れる形になるか（lock 型）
    from engine.route_calc import forced_plan
    from selfplay.pc_solve import end_of_day
    days = s['script']['days']
    base_lock = forced_plan(s, days, pos=True, pcab=True)['forced']  # 今朝もう決まっているなら、どの札でも同じで型に効かない
    rt = route_targets(s)
    alls = all_targets(s)
    for t in targets:
        for c in set(h):
            rest = [x for x in NEUTRAL if x != c]
            others = [u for u in targets if u != t]
            if len(rest) < 2:
                one[t, c], lock[t, c] = 0.0, False
                continue
            a = _after(s, [(t, c)] + list(zip(others, rest)))
            one[t, c] = (_near(a) - base) if a else 0.0
            lock[t, c] = bool(a) and not base_lock and c in lock_cards(rt.get(t, ())) and lock_of(s, t, c, alls)
    relief = {}  # 主人公の不安−1 の値: その人物の不安が1減ると、筋の近さがどれだけ下がるか
    for t in targets:
        c = s['chars'].get(t)
        if c and c['alive'] and c['par'] > 0:
            relief[t] = max(0.0, base - _near(dict(s, chars=dict(s['chars'], **{t: dict(c, par=c['par'] - 1)}))))
    rmax = max(relief.values(), default=0) or 1.0
    obvious = max(targets, key=lambda t: max(one[t, c] for c in set(h)))  # 主人公から一番危なく見える置き場所
    out = []
    for cards in set(itertools.permutations(h, 3)):
        mm = list(zip(targets, cards))
        a = _after(s, mm)
        if a is None:
            continue
        push = _near(a)
        out.append({'cards': cards, 'push': push, 'urgent': _near(a, days_left), 'feint': -abs(push - base),
                    'preempt': sum(_unlock(s, t) for t, c in mm if c == 'GWX'),
                    'level1': push - one[obvious, cards[targets.index(obvious)]],
                    'guard': sum(relief.get(t, 0) / rmax for t, c in mm if c == 'PARX') + sum(min(1, _unlock(s, t)) for t, c in mm if c == 'GWX'),
                    'filler': sum(1 for t, c in mm if (c == 'PARX' and (t.startswith('B:') or s['chars'][t]['par'] == 0))
                                  or (c == 'GWX' and not _unlock(s, t))),
                    'lock': float(any(lock.get((t, c)) for t, c in mm)),
                    'random': 0.0})
    if out:  # 置けない置き場所の組（脚本家の候補にはありうる）では空
        _z(out, 'push', 'guard', 'pushguard')
    return out


def _z(out, a, b, key):
    for k in (a, b):
        v = [x[k] for x in out]
        m = sum(v) / len(v)
        sd = (sum((y - m) ** 2 for y in v) / len(v)) ** .5 or 1.0
        for x in out:
            x.setdefault('_z', {})[k] = (x[k] - m) / sd
    for x in out:
        x[key] = x['_z'][a] + x['_z'][b]


def dists(cands):
    """型ごとの確率（候補の並びに対応）。"""
    res = {}
    for k in TYPES:
        v = [x[k] for x in cands]
        m = sum(v) / len(v)
        sd = (sum((y - m) ** 2 for y in v) / len(v)) ** .5
        e = [math.exp(LAM * (y - max(v)) / sd) if sd > 1e-12 else 1.0 for y in v]
        z = sum(e)
        res[k] = [y / z for y in e]
    return res


def collect(a):
    from selfplay.league import make
    from selfplay.run import Combo, play_game
    os.makedirs(a.out, exist_ok=True)
    for sid in a.scripts:
        script = by_id('designed/' + sid)
        for mm in a.mms:
            for seed in a.seeds:
                path = os.path.join(a.out, f'{sid}_{mm}_{seed}.jsonl')
                if os.path.exists(path):
                    continue
                rows = []

                def log(kind, rec):
                    if kind == 'decisions' and rec.get('kind') == 'place_cards' and rec.get('who') == 'M' and rec.get('state'):
                        rows.append({'loop': rec['loop'], 'day': rec['day'], 'state': rec['state'], 'choice': rec['choice']})
                mmp = make(mm, random.Random(f'{seed}:mm'), script)
                pcp = make(a.pc, random.Random(f'{seed}:pc'), script)
                play_game(script, Combo(mmp, pcp), log)
                with open(path, 'w', encoding='utf-8') as f:
                    for r in rows:
                        f.write(json.dumps({'script': sid, 'mm': mm, **r}, ensure_ascii=False, default=list) + '\n')
                print(path, len(rows), flush=True)


def state_of(row, script):
    """記録の盤面に、league と同じ補い（脚本・日・ループなど）を足す。"""
    return dict(row['state'], script=script, loop=row['loop'], day=row['day'],
                ability_used_loop=[], ability_used_today=[], incident_log=[], leader='A')


FEATS = ['push', 'urgent', 'preempt', 'guard', 'filler', 'level1', 'feint']  # 度合いの型（対数線形）


def _zmat(cands):
    cols = []
    for k in FEATS:
        v = [x[k] for x in cands]
        m = sum(v) / len(v)
        sd = (sum((y - m) ** 2 for y in v) / len(v)) ** .5 or 1.0
        cols.append([(y - m) / sd for y in v])
    return [list(r) for r in zip(*cols)]


def _probs(X, th):
    e = [sum(a * b for a, b in zip(r, th)) for r in X]
    mx = max(e)
    e = [math.exp(y - mx) for y in e]
    z = sum(e)
    return [y / z for y in e]


def _fit(hist, th, sigma=1.0, steps=40, lr=0.5):
    """公開された日々（X, 実際の行）から、型の度合い th を当て直す（事前分布 N(0, sigma²) の最大事後）。"""
    th = list(th)
    for _ in range(steps):
        g = [-x / sigma ** 2 for x in th]
        for X, i in hist:
            p = _probs(X, th)
            for k in range(len(th)):
                g[k] += X[i][k] - sum(pj * X[j][k] for j, pj in enumerate(p))
        th = [x + lr * gk / max(1, len(hist)) for x, gk in zip(th, g)]
    return th


def evaluate_ll(a):
    """対数線形: 1つの手の選びたさ = Σ 型の度合い × その型の値（標準化）。度合いは試合の中で公開された中身から当て直す。"""
    import glob
    from collections import defaultdict
    tot = defaultdict(lambda: defaultdict(float))
    ths = defaultdict(list)
    scripts = {}
    for path in sorted(glob.glob(os.path.join(a.dir, '*.jsonl'))):
        th, hist = [0.0] * len(FEATS), []
        mm = None
        for line in open(path, encoding='utf-8'):
            row = json.loads(line)
            mm = row['mm']
            sc = scripts.setdefault(row['script'], by_id('designed/' + row['script']))
            s = state_of(row, sc)
            targets = [p['target'] for p in row['choice']]
            actual = tuple(p['card'] for p in row['choice'])
            cands = candidates(s, targets)
            idx = next((i for i, x in enumerate(cands) if x['cards'] == actual), None)
            if idx is None:
                continue
            X = _zmat(cands)
            p = _probs(X, th)
            t = tot[mm]
            t['n'] += 1
            t['ll'] += -math.log(max(p[idx], 1e-12))
            t['uniform'] += math.log(len(cands))
            for j in range(3):
                marg = defaultdict(float)
                for i, x in enumerate(cands):
                    marg[x['cards'][j]] += p[i]
                t['top1'] += max(marg, key=marg.get) == actual[j]
                t['slots'] += 1
            hist.append((X, idx))
            th = _fit(hist, th)
        if mm:
            ths[mm].append(th)
    for mm, t in tot.items():
        n = t['n']
        avg = [sum(x[k] for x in ths[mm]) / len(ths[mm]) for k in range(len(FEATS))]
        print(f"{mm}: {int(n)}日  対数損失 {t['ll'] / n:.2f} / 無作為 {t['uniform'] / n:.2f}  置き場所ごとの当たり {t['top1'] / t['slots']:.0%}")
        print('   試合の終わりの型の度合い（平均）: ' + ' '.join(f'{k} {v:+.2f}' for k, v in zip(FEATS, avg)))


def evaluate(a):
    if getattr(a, 'model', '') == 'loglin':
        return evaluate_ll(a)
    import glob
    from collections import defaultdict
    tot = defaultdict(lambda: defaultdict(float))
    post = defaultdict(lambda: defaultdict(float))
    scripts = {}
    for path in sorted(glob.glob(os.path.join(a.dir, '*.jsonl'))):
        w = {k: 1 / len(TYPES) for k in TYPES}
        for line in open(path, encoding='utf-8'):
            row = json.loads(line)
            sc = scripts.setdefault(row['script'], by_id('designed/' + row['script']))
            s = state_of(row, sc)
            if a.reset and row['day'] == 1:
                w = {k: 1 / len(TYPES) for k in TYPES}
            targets = [p['target'] for p in row['choice']]
            actual = tuple(p['card'] for p in row['choice'])
            cands = candidates(s, targets)
            idx = next((i for i, x in enumerate(cands) if x['cards'] == actual), None)
            if idx is None:
                continue
            d = dists(cands)
            mix = sum(w[k] * d[k][idx] for k in TYPES)
            t = tot[row['mm']]
            t['n'] += 1
            t['mix'] += -math.log(max(mix, 1e-12))
            t['uniform'] += math.log(len(cands))
            for k in TYPES:
                t[k] += -math.log(max(d[k][idx], 1e-12))
            # 置き場所ごとの当たり（混ぜた確率の周辺で一番の札）
            for j in range(3):
                marg = defaultdict(float)
                for i, x in enumerate(cands):
                    marg[x['cards'][j]] += sum(w[k] * d[k][i] for k in TYPES)
                t['top1'] += max(marg, key=marg.get) == actual[j]
                t['slots'] += 1
            z = sum(w[k] * d[k][idx] for k in TYPES)
            w = {k: w[k] * d[k][idx] / z for k in TYPES}
            w = {k: 0.02 / len(TYPES) + 0.98 * v for k, v in w.items()}  # 型の重みが0に張り付かないよう少し混ぜる
            for k in TYPES:
                post[row['mm']][k] += w[k]
            post[row['mm']]['n'] += 1
    for mm, t in tot.items():
        n = t['n']
        print(f"{mm}: {int(n)}日  1日の対数損失（小さいほど当たる） 混ぜ {t['mix'] / n:.2f} / 無作為 {t['uniform'] / n:.2f}  "
              + ' '.join(f"{k} {t[k] / n:.2f}" for k in TYPES) + f"  置き場所ごとの当たり {t['top1'] / t['slots']:.0%}")
        print('   型の重み（日ごとの平均）: ' + ' '.join(f"{k} {post[mm][k] / post[mm]['n']:.2f}" for k in TYPES))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    c = sub.add_parser('collect')
    c.add_argument('--out', required=True)
    c.add_argument('--mms', nargs='+', default=['route', 'search', 'calcG', 'calcGBxhdK'])
    c.add_argument('--pc', default='read2')
    c.add_argument('--scripts', nargs='+', default=['d01_seal', 'd13', 'd18', 'd35', 'd03_future', 'd09', 'd12', 'd34'])
    c.add_argument('--seeds', nargs='+', type=int, default=[1, 2])
    e = sub.add_parser('eval')
    e.add_argument('dir')
    e.add_argument('--reset', action='store_true')
    e.add_argument('--model', default='mix', choices=['mix', 'loglin'])  # ループの始めに型の重みを戻す
    a = ap.parse_args()
    collect(a) if a.cmd == 'collect' else evaluate(a)


def reader_pc(rng, cover=0.9, top=30, safety=0.1, robust=False, graded=True, stage2=6):
    """伏せ札を読む主人公（league の oreadc）。脚本を知る osolve と同じく、中身がどれでも守れる応手があればそれを打つ。
    無い日は、型で読んだ中身の確率（上位 cover・最大 top 通り）で重みづけた「翌朝に脚本家が必ず取れる形になっていない」見込みが
    最も高い応手を打つ（＋ safety × 最悪の場合。読まれて裏をかかれても崩れにくいように）。型の重みは公開された中身で日ごとに更新する。
    robust=False（既定）: 中身がどれでも守れる応手の探索（osolve、脚本によって1試合数十分）を飛ばし、毎日読む。読めないときは oreadKQL の手。"""
    from engine import phases as ph
    from engine.route_calc import forced_plan
    from selfplay.pc_solve import Solver, end_of_day, relevant, solver_pc
    from selfplay.players import score
    p = solver_pc(rng)
    p.read_w = {k: 1 / len(TYPES) for k in TYPES}
    p.read_log = []
    p._read_pending = None
    orig_cards, orig_observe = p.pc_cards, p.observe

    def value(s, mm, R, days, s0=1.0, use_forced=True):
        try:
            after, _ = resolve_actions(s, mm + R, cultist_options(s, mm + R))  # 脚本家はカルティストで暗躍禁止を無視しうる（d18 L2D6 で落とした）
        except IllegalPlacement:
            return None
        except ph.LoopEnd as e:
            return 0.0 if e.loss else 1.0
        nxt, lost = end_of_day(after, days)
        if lost:
            return 0.0
        if nxt is None:
            return 1.0
        forced = use_forced and forced_plan(nxt, days, pos=True, pcab=True)['forced']
        if not graded:
            return 0.0 if forced else 1.0 - 0.01 * min(score(nxt), 50)
        # 今朝の負け筋の近さ s0 に比べて、翌朝どれだけ遠のいたか。下限の表の「必ず取れる」は主人公の移動の一部を数えず、
        # 2日目から全部の応手が 0 になって差がつかなかった（d35、2026-10-23）ので、0 にせず割り引くだけにする
        return (0.3 + 0.7 * math.exp(-score(nxt) / max(s0, 1e-6))) * (0.3 if forced else 1.0)

    def pc_cards(s, order, mm_targets=None):
        T = tuple(mm_targets or [])
        R0 = orig_cards(s, order, mm_targets) if robust else type(p).__mro__[1].pc_cards(p, s, order, mm_targets)
        if len(T) != 3:
            return R0
        cands = candidates(s, list(T))
        p._read_pending = (s, list(T), cands)
        if robust and p.solve_log and p.solve_log[-1][:2] == (s['loop'], s['day']) and p.solve_log[-1][2]:
            return R0  # どの中身でも守れる応手がある
        d = dists(cands)
        P = [sum(p.read_w[k] * d[k][i] for k in TYPES) for i in range(len(cands))]
        idx = sorted(range(len(cands)), key=lambda i: -P[i])
        pick, cum = [], 0.0
        for i in idx[:top]:
            pick.append(i)
            cum += P[i]
            if cum >= cover:
                break
        days = s['script']['days']
        sv = Solver(days)
        rel, other = relevant(s)
        s0 = score(s)

        def ev_of(R, use_forced):
            vs = []
            for i in pick:
                v = value(s, [{'by': 'M', 'target': t, 'card': c} for t, c in zip(T, cands[i]['cards'])], R, days, s0, use_forced)
                if v is not None:
                    vs.append((P[i], v))
            if not vs:
                return None
            return sum(a * b for a, b in vs) / sum(a for a, _ in vs) + safety * min(b for _, b in vs) + (1e-6 if R is R0 else 0)  # 同じ見込みなら計画の手
        # 読まない主人公の手（友好を積むなど、先の計画の手）も候補に入れる。d13 でタイムトラベラーへの友好を積まず悪くなった（2026-10-23）
        Rs = [R0] + list(sv.responses(s, T, rel, other))
        # 2段階: 翌朝の表（forced_plan、1回数ミリ〜数十ミリ秒）を引かずに全部の応手を比べ、上位 stage2 個（＋計画の手）だけ表で比べ直す
        # （KRL の写しが1日目に1回20秒かかった。2026-10-25）
        rough = [(e, j) for j, R in enumerate(Rs) for e in [ev_of(R, False)] if e is not None]
        keep = {j for _, j in sorted(rough, reverse=True)[:stage2]} | {0}
        best, best_v = None, -1.0
        for j in sorted(keep):
            ev = ev_of(Rs[j], True)
            if ev is not None and ev > best_v:
                best, best_v = Rs[j], ev
        p.read_log.append((s['loop'], s['day'], round(best_v, 3)))
        return best or R0

    def observe(s, phase, events):
        orig_observe(s, phase, events)
        for e in events:
            if e.get('kind') == 'revealed' and p._read_pending:
                st, T, cands = p._read_pending
                got = {x['target']: x['card'] for x in e['cards'] if x['by'] == 'M'}
                actual = tuple(got.get(t) for t in T)
                i = next((j for j, x in enumerate(cands) if x['cards'] == actual), None)
                if i is not None:
                    d = dists(cands)
                    z = sum(p.read_w[k] * d[k][i] for k in TYPES)
                    p.read_w = {k: 0.02 / len(TYPES) + 0.98 * p.read_w[k] * d[k][i] / z for k in TYPES}
                p._read_pending = None

    p.pc_cards, p.observe = pc_cards, observe
    return p
