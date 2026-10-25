"""脚本を知る主人公が、伏せ札の中身が見えなくてもこのループを守りきれるかを、本物のエンジンの前方探索で読む（試作 2026-10-22）。

ユーザー「伏せ札の中身を判断できない状態においてもループを止める手段があるかを探索する高速な方法」「前方探索と逆算を同時に」。
毎日「脚本家の置き先がどれでも → 主人公の応手があって → 中身がどれでも」翌朝に進め、ループの終わりまで負けなければ守りきれる。
中身は解決で公開されるので、翌朝からはすべて見えた盤面で読める（＝脚本家が主人公の札を見てから中身を選ぶ完全情報ゲームと同じ）。

翌朝の盤面は、まず逆算の表を引いて打ち切る（チェッカーの終盤の表＋前方の証明探索の形）:
- 上限 route_calc.pc_holds（脚本家に有利なことを数える）が守れる、かつ数えなかった筋が無い → 守れる
- 下限 route_calc.forced_plan（pos・pcab）が必ず取れる → 守れないとみる（下限は主人公の移動の一部を数えないので、守れる筋を見落としうる）
どちらでもなければ1日進める（事件・死亡・ターン終了の役職は本物のエンジン）。

数えていないもの（ponytail）:
- 主人公の友好能力（使わない＝主人公に不利。守れると出れば、使わなくても守れる）
- 脚本家能力・事件の選び方は after_incidents の1通り（脚本家に良い選び方の目安。別の選び方は読まない）
- 主人公の応手は守りの札（不安−1・暗躍禁止・移動禁止・移動）を筋に関わる対象に置く組だけ。脚本家の置き先・中身は、筋に関わらない対象を
  代表3つにまとめ、そこへの中身は効果の小さい1枚とみなす

    cd play && python -m selfplay.pc_solve generated/gen_b31_003 [--no-upper] [--no-lower]
"""
import argparse
import time
from itertools import combinations, permutations

from engine import phases as ph
from engine.resolve import CARDS, ONCE, IllegalPlacement, card_targets, copy_state, resolve_actions
from engine.route_calc import forced_plan, pc_holds
from engine.routes import INF, enumerate_routes
from selfplay.pc_v2 import after_incidents
from selfplay.players import cultist_options

PCS = 'ABC'
FILLER = ('GWX', 'PAR-', 'PARX', 'GW1')  # 筋に関わらない対象に置く札（効果の小さい順）


def end_of_day(after, days):
    """行動解決の後の盤面から今日の残り（脚本家能力・事件・ターン終了・最終日ならループ終了）を進める（selfplay/pc_game.py と同じ）。
    返り値 (翌朝の盤面 or None, 負けたか)。"""
    s = after_incidents(after)
    if s.get('_loss') is not None:
        return None, bool(s['_loss'])
    try:
        ph.turn_end_mandatory(s)
    except ph.LoopEnd as e:
        return None, bool(e.loss)
    for o in ph.killer_options(s):  # 【任意】のどれか1つでループが主人公の負けで終わるなら負け
        t = copy_state(s)
        try:
            ph.turn_end_optional(t, [o])
        except ph.LoopEnd as e:
            if e.loss:
                return None, True
        except IllegalPlacement:
            continue
    if s['day'] >= days:
        return None, bool(ph.loop_end_loss(copy_state(s)))
    s['day'] += 1
    try:
        ph.turn_start(s)
    except ph.LoopEnd as e:
        return None, bool(e.loss)
    return s, False


def _key(s):
    return repr({k: v for k, v in s.items() if k not in ('script', 'init')})


def relevant(s):
    """筋に関わる対象（筋の条件に出る人物・ボード、事件の犯人、2人きりの筋のエリアにいる人物）と、その他。"""
    rel = set()
    for r in enumerate_routes(s):
        if r['total'] >= INF:
            continue
        if r['via']:
            rel.add(r['via'])
        for c in r['conds']:
            d = c['detail'] if isinstance(c['detail'], (list, tuple)) else [c['detail']]
            if c['kind'] == 'board':
                rel.add('B:' + d[0])
            for x in d:
                if isinstance(x, str) and x in s['chars']:
                    rel.add(x)
            if c['kind'] == 'alone_with':
                areas = {s['chars'][w].get('area') for w in d[:2] if w in s['chars']}
                rel |= {cid for cid, v in s['chars'].items() if v.get('area') in areas}
    rel |= {i['culprit'] for i in s['script']['incidents'] if i['day'] >= s['day']}
    ts = card_targets(s)
    return [t for t in ts if t in rel], [t for t in ts if t not in rel]


class Solver:
    def __init__(self, days, upper=True, lower=True, hold_budget=0.5, max_resp=60, upper_from=1):
        """upper_from: 上限・下限の表を引き始める日（それより前は本物のエンジンで読む＝表の「守れる」をその日数ぶん確かめる）。"""
        self.days, self.upper, self.lower, self.hold_budget, self.max_resp = days, upper, lower, hold_budget, max_resp
        self.upper_from = upper_from
        self.memo, self.stats = {}, {'nodes': 0, 'steps': 0, 'cut_upper': 0, 'cut_lower': 0, 'unknown': 0}

    def holds(self, s):
        k = _key(s)
        if k not in self.memo:
            self.memo[k] = self._holds(s)
        return self.memo[k]

    def _holds(self, s):
        self.stats['nodes'] += 1
        if all(r['total'] >= INF for r in enumerate_routes(s)):
            return True
        if self.upper and s['day'] >= self.upper_from:
            h = pc_holds(s, self.days, budget=self.hold_budget)
            if h['holds'] and not h['skipped']:
                self.stats['cut_upper'] += 1
                return True
        if self.lower and s['day'] >= self.upper_from and forced_plan(s, self.days, pos=True, pcab=True)['forced']:
            self.stats['cut_lower'] += 1
            return False
        rel, other = relevant(s)
        for T in self.placements(rel, other):
            if not any(self.survives(s, T, R) for R in self.responses(s, T, rel, other)):
                return False
        return True

    def placements(self, rel, other):
        ts = rel + other[:3]  # ponytail: 筋に関わらない対象は代表3つ（移動で位置の筋に関わる人物は rel に入れてある）
        return combinations(ts, 3)

    def contents(self, s, T, rel):
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        out = set()
        for a in permutations(hand, 3):
            mv = []
            for t, c in zip(T, a):
                if t not in rel:
                    c = '_'
                elif t.startswith('B:') and c not in ('INT1', 'INT2'):
                    c = '_'  # ボードに効くのは暗躍だけ。他の札は効果なし
                mv.append((t, c))
            out.add(tuple(mv))
        for mv in out:
            left = list(hand)
            for _, c in mv:
                if c != '_':
                    left.remove(c)
            res = []
            for t, c in mv:
                if c == '_':
                    c = next(f for f in FILLER + tuple(left) if f in left)
                    left.remove(c)
                res.append({'by': 'M', 'target': t, 'card': c})
            yield res

    def responses(self, s, T, rel, other):
        """主人公の守りの札の組（置き先の違う3枚。足りない分は友好+1）。脚本家の置いた対象への札を先に試す。"""
        opts = []
        for t in rel:
            if t.startswith('B:'):
                opts.append((t, 'INTX'))
                continue
            opts += [(t, 'PAR-'), (t, 'MVX'), (t, 'MV_V'), (t, 'MV_H'), (t, 'INTX')]
        opts.sort(key=lambda o: (o[0] not in T, o[1] not in ('PAR-', 'INTX')))
        ts = card_targets(s)
        seen, n = set(), 0
        for k in (1, 2, 3, 0):
            for combo in combinations(opts, k):
                if len({t for t, _ in combo}) < k or sum(c == 'INTX' for _, c in combo) > 1:
                    continue  # 暗躍禁止は2枚出すと両方無効
                ps = self._assign(s, combo, ts)
                if ps is None:
                    continue
                key = tuple(sorted((p['target'], p['card']) for p in ps))
                if key in seen:
                    continue
                seen.add(key)
                yield ps
                n += 1
                if n >= self.max_resp:
                    return

    def _assign(self, s, combo, ts):
        free = [p for p in PCS]
        out = []
        for t, c in sorted(combo, key=lambda x: x[1] not in ONCE['A']):
            who = next((p for p in free if not (c in ONCE[p] and c in s['used'][p])), None)
            if who is None:
                return None
            free.remove(who)
            out.append({'by': who, 'target': t, 'card': c})
        used_t = {t for t, _ in combo}
        for p in free:
            t = next((x for x in ts if x not in used_t), None)
            if t is None:
                return None
            used_t.add(t)
            out.append({'by': p, 'target': t, 'card': 'GW1'})
        return out

    def survives(self, s, T, R):
        rel, _ = relevant(s)
        for mm in self.contents(s, T, rel):
            try:
                after, _ = resolve_actions(s, mm + R, cultist_options(s, mm + R))  # 脚本家はカルティストで暗躍禁止を無視しうる
            except IllegalPlacement:
                continue
            except ph.LoopEnd as e:
                if e.loss:
                    return False
                continue
            self.stats['steps'] += 1
            nxt, lost = end_of_day(after, self.days)
            if lost or (nxt is not None and not self.holds(nxt)):
                return False
        return True


def solver_pc(rng, **kw):
    """読み切りの応手を打つ脚本を知る主人公（league の osolve）。見えている伏せ札の置き先に対し、中身がどれでも守れる応手があれば打つ。
    無ければ oreadKQL と同じ手。self.solve_log に日ごとの判定（守れる応手があったか）を残す。"""
    from selfplay.pc_v2 import OracleReader

    class SolverPC(OracleReader):
        def pc_cards(self, s, order, mm_targets=None):
            T = tuple(mm_targets or [])
            if len(T) == 3:
                sv = Solver(s['script']['days'], upper_from=s['day'] + 1)
                rel, other = relevant(s)
                for R in sv.responses(s, T, rel, other):
                    if sv.survives(s, T, R):
                        self.solve_log.append((s['loop'], s['day'], True))
                        return R
                self.solve_log.append((s['loop'], s['day'], False))
            return super().pc_cards(s, order, mm_targets)

    p = SolverPC(rng, null_cost=0.1, model_mm=True, calc_block=True, info_weight=0.0, info_adapt=0.0, reveal=False, prio=0.02, lookday=True, **kw)
    p.solve_log = []
    return p


def main():
    from engine.scripts import by_id
    from selfplay.script_routes import start_state
    ap = argparse.ArgumentParser()
    ap.add_argument('scripts', nargs='+')
    ap.add_argument('--no-upper', action='store_true')
    ap.add_argument('--no-lower', action='store_true')
    ap.add_argument('--max-resp', type=int, default=60)
    ap.add_argument('--upper-from', type=int, default=1, help='上限・下限の表を引き始める日')
    a = ap.parse_args()
    for sid in a.scripts:
        sc = by_id(sid)
        s = start_state(sc)
        sv = Solver(sc['days'], upper=not a.no_upper, lower=not a.no_lower, max_resp=a.max_resp, upper_from=a.upper_from)
        t = time.time()
        r = sv.holds(s)
        print(sid, '守りきれる' if r else '見つからない', f'{time.time() - t:.1f}s', sv.stats, flush=True)


if __name__ == '__main__':
    main()
