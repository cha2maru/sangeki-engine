"""自己対戦の進行役（段5の土台）。エンジンがルールを処理し、判断はプレイヤー関数が行う。

    /workspaces/sangeki/.venv/bin/python -m selfplay.run --games 200 --seed 1   （play/ で実行）

出力は play/selfplay/runs/<名前>/ に書く（play/game/ には書かない）:
  decisions.jsonl（決定点の記録。README の形式）、events.jsonl（エンジンの出来事）、summary.json
最後の戦いはまだ無い。
"""
import argparse
import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.resolve import CARDS, card_targets, ONCE, resolve_actions  # noqa: E402
from engine import phases as ph  # noqa: E402
from engine import abilities as ab  # noqa: E402
from engine.scripts import by_id  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _toukou001():
    """公式配布の投稿シナリオ「歪んだ独占欲」（engine/scripts/toukou_001.json）。他の方の作品なので公開版には入れない＝無ければ None。"""
    try:
        return by_id('toukou_001')
    except FileNotFoundError:
        return None


TOUKOU001 = _toukou001()


class RandomPlayer:
    """合法手から一様に選ぶ。誤解している箇所を突く手も出るよう、戦略を持たせない。"""
    def __init__(self, rng):
        self.rng = rng

    def targets(self, s):
        return card_targets(s)

    def mm_cards(self, s):
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        ts = self.rng.sample(self.targets(s), 3)
        cards = self.rng.sample(hand, 3)
        return [{'by': 'M', 'target': t, 'card': c} for t, c in zip(ts, cards)]

    def pc_cards(self, s, order):
        taken, out = set(), []
        for p in order:
            t = self.rng.choice([t for t in self.targets(s) if t not in taken])
            taken.add(t)
            hand = [c for c in CARDS['protagonist_hand'] if not (c in ONCE[p] and c in s['used'][p])]
            out.append({'by': p, 'target': t, 'card': self.rng.choice(hand)})
        return out

    def mm_ability(self, s, actor, ability=None):
        a = s['chars'][actor]
        opts = [c for c in ph.alive_in(s, a['area'])]
        if (ability or ph.role(s, actor)) == 'KUROMAKU':
            opts.append('B:' + a['area'])
        return self.rng.choice(opts + [None])  # None=使わない

    def ability_candidates(self, s):
        """いま合法な宣言の候補（公開情報だけで判定できる範囲。engine/abilities.declaration_options）。"""
        return ab.declaration_options(s)

    def refuse(self, s, cid):
        return ab.REFUSAL.get(ph.role(s, cid)) == 'optional' and self.rng.random() < 0.5

    def loop_setup(self, s, script):
        ch = {}
        if 'C18' in script['init']:
            ch['init'] = {'C18': self.rng.choice(ph.AREAS)}
        if 'C19' in script['init']:
            ch['scholar'] = self.rng.choice(['gw', 'par', 'int'])
        return ch

    def incident_choice(self, s, inc):
        cul = inc['culprit']
        alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
        if inc['id'] == 'MURDER':
            cand = [c for c in ph.alive_in(s, s['chars'][cul]['area']) if c != cul]
            return {'target': self.rng.choice(cand)} if cand else None
        if inc['id'] == 'SPREAD':
            p = self.rng.choice(alive)
            i = self.rng.choice([c for c in alive if c != p]) if len(alive) > 1 else p
            return {'par_target': p, 'int_target': i}
        return incident_options(s, inc, self.rng)


def incident_options(s, inc, rng=None):
    """遠隔殺人・行方不明・流布・蝶の羽ばたきの合法な選択の一覧（rng があれば1つ引いて返す）。"""
    cul = inc['culprit']
    alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
    area = s['chars'][cul]['area']
    if inc['id'] == 'REMOTE':
        opts = [{'target': c} for c in alive if s['chars'][c]['int'] >= 2]
    elif inc['id'] == 'MISSING':
        from engine.resolve import CHARS
        opts = [{'board': a} for a in ph.AREAS if a not in CHARS[cul].get('forbidden', []) or cul in s.get('unbound', [])]
    elif inc['id'] == 'RUMOR_SPREAD':
        opts = [{'from': f, 'to': t} for f in alive for t in alive if f != t]
    elif inc['id'] == 'BUTTERFLY':
        opts = [{'target': t, 'counter': k} for t in ph.alive_in(s, area) for k in ('par', 'int', 'gw')]
    else:
        return None
    if rng is None:
        return opts
    return rng.choice(opts) if opts else None


class Combo:
    """脚本家の判断は mm に、主人公の判断は pc に振り分ける。"""
    MM = ('mm_cards', 'mm_ability', 'incident_choice', 'refuse', 'killer_pick', 'choose_rule_x', 'mm_rumor', 'loop_setup',
          'cultist_pick', 'mm_extra_uses')

    def __init__(self, mm, pc):
        self.mm, self.pc, self.rng = mm, pc, pc.rng
        if hasattr(pc, 'abilities_for_day'):
            self.abilities_for_day = pc.abilities_for_day

    def observe(self, s, phase, events):
        for side in (self.mm, self.pc):  # 脚本家も公開情報の推理を持つことがある（自分の漏れを測る）
            if hasattr(side, 'observe'):
                side.observe(s, phase, events)

    def __getattr__(self, name):
        if name.startswith('__') or name in ('mm', 'pc'):  # 複製（copy.deepcopy）の途中で無限に辿らない
            raise AttributeError(name)
        return getattr(self.mm if name in self.MM else self.pc, name)


def _ask_refuse(player, s, cid, idx):
    """拒否を選べるとき（友好無視の役職で、拒否できない能力でない）だけ脚本家に聞く。絶対友好無視は必ず拒否（エンジンが強制）、
    友好無視の無い役職は拒否できない。以前は拒否できない能力だけを除いていて、d05 の対戦表が止まり、Claude 同士の対戦（d16）でも余分に聞いていた。"""
    if (cid, idx) in ab.UNREFUSABLE or ab.REFUSAL.get(ph.role(s, cid)) != 'optional':
        return False
    return player.refuse(s, cid)


def play_game(script, player, log0, observers=(), on_day=None):
    """observers: 公開の出来事を player と同じく受け取る観測者（評価用の理想の観測者など）。
    loop_done(s, loop) があれば各ループの終わりと最後の戦いの直前（loop='final'）に呼ぶ。
    on_day(s, player, loop, day, box): 各日の始めに呼ぶ（段D の分岐のデータ集め）。状態は変えないこと。"""
    order = ['A', 'B', 'C']
    box = {}
    watchers = ([player] if hasattr(player, 'observe') else []) + list(observers)

    def log(kind, rec):
        if rec.get('phase') and rec.get('phase') not in ('loop_end', 'loop_end_check', 'pc_knowledge'):
            box['phase'] = rec['phase']  # いまのフェイズ（ループが途中で終わったとき、どのフェイズかは公開情報）
        log0(kind, rec)
        if kind == 'events' and 'events' in rec and box.get('s') is not None:
            for w in watchers:
                w.observe(box['s'], rec.get('phase'), rec['events'])

    def loop_done(loop):
        for o in observers:
            if hasattr(o, 'loop_done'):
                o.loop_done(s, loop)
    # 第1ループも「ループの準備」を通す（手先の初期エリア・学者・黒猫・登場前のキャラクター）。第0ループの空の盤面から始める
    s = {'loop': 0, 'day': 0, 'leader': 'A',
         'script': {**{k: script[k] for k in ('rules', 'roles', 'incidents')}, 'days': script['days'], 'appear': script.get('appear', {}),
                    'territory': script.get('territory'), 'start': script.get('start', {})},
         'chars': {c: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0} for c, a in script['init'].items()},
         'boards': {a: 0 for a in ph.AREAS}, 'used': {p: [] for p in 'MABC'}, 'ability_used_loop': [], 'incident_log': []}
    for loop in range(1, script['loops'] + 1):
        setup = player.loop_setup(s, script) if hasattr(player, 'loop_setup') else {}
        s, ev = ph.loop_start(s, script['init'], setup)
        # 初期エリアは公開（手先は脚本家がこのループに決めた場所。登場前のキャラクターはカードの初期エリア）
        s['init'] = {c: v['area'] or script['init'].get(c) for c, v in s['chars'].items()}
        box['s'] = s  # ループ1の準備の出来事（黒猫・学者など）も観測者に渡す（以前は box が空で、ログにも推理にも届かなかった）
        if loop > 1 or ev:
            log('events', {'loop': loop, 'day': 0, 'phase': 'loop_start', 'events': ev})
        loss = False
        try:
            for day in range(1, script['days'] + 1):
                if on_day:
                    s['day'] = day
                    on_day(s, player, loop, day, box)
                s = play_day(s, player, loop, day, log, box)
        except ph.LoopEnd as e:
            # 1日の途中で終わったときは、その日の変化を含む最新の状態を使う（play_day は複製して進めるので s は前日の終わりのまま）。
            # 以前は古い s でループ終了時の判定と次のループの準備（因果の糸の不安など）をしていた
            s = box['s']
            loss = e.loss
            log('events', {'loop': loop, 'day': s['day'], 'phase': 'loop_end',
                           # エンジンの出来事に loop_end があれば、フェイズを付けた1つにまとめる（ログに2回出ていた）
                           'events': [x for x in e.events if x.get('kind') != 'loop_end'] + [{'kind': 'loop_end', 'reason': e.reason, 'at_phase': box.get('phase')}]})
        # ループ終了時の判定は、規定日数前にループが終わっても行う（FAQ Ch07・Ch20）。どの条件が成立したかは告げないが、
        # 敗北したこと（wiki/concepts/script.md）と、死亡したフレンドの正体（早見表 フレンド、FAQ Ru02）は公開される
        before = set(s.get('revealed_roles', {}))
        reasons = ph.loop_end_loss(s)
        ev = [{'kind': 'role_public', 'char': c, 'role': r} for c, r in s.get('revealed_roles', {}).items() if c not in before]
        if not loss:
            ev.append({'kind': 'loop_end_check', 'loss': bool(reasons)})
        if ev:
            log('events', {'loop': loop, 'day': s['day'], 'phase': 'loop_end_check', 'events': ev})
        loop_done(loop)
        if not loss and not reasons:
            return {'winner': 'protagonists', 'loop': loop}
    # 全ループで敗北 → 最後の戦い（主人公の書 印刷p25 §6）。盤面を初期状態に戻して、1人ずつ役職を宣言する
    setup = player.loop_setup(s, script) if hasattr(player, 'loop_setup') else {}
    s, _ = ph.loop_start(s, script['init'], setup)
    loop_done('final')
    guesses = player.final_guesses(s) if hasattr(player, 'final_guesses') else [{'char': c, 'role': 'PERSON'} for c in s['chars']]
    from engine.abilities import final_battle
    fb = final_battle(s, guesses)
    log('events', {'loop': None, 'day': None, 'phase': 'final_battle', 'guesses': guesses, 'result': fb})
    return {'winner': fb['winner'], 'loop': 'final', 'final_stopped_at': fb['stopped_at']}



def play_day(s, player, loop, day, log, box, mm_override=None):
    """1日分（ターン開始〜ターン終了）。ループが終われば ph.LoopEnd を投げる。
    mm_override: 脚本家の伏せ札を外から与える（段D の分岐: 同じ局面で候補の手ごとに1日を回す）。"""
    order = ['A', 'B', 'C']
    s['day'] = day
    box['s'] = s
    ev = ph.turn_start(s)  # 転校生の登場
    if ev:
        log('events', {'loop': loop, 'day': day, 'phase': 'turn_start', 'events': ev})
    lead = order.index(s['leader'])
    porder = order[lead:] + order[:lead]
    mm = mm_override if mm_override is not None else player.mm_cards(s)
    try:
        pc = player.pc_cards(s, porder, [x['target'] for x in mm])  # 主人公は伏せ札の置き場所だけを見て置く
    except TypeError:
        pc = player.pc_cards(s, porder)
    log('decisions', {'loop': loop, 'day': day, 'phase': 'actions', 'who': 'M', 'kind': 'place_cards', 'choice': mm,
                      'state': {'chars': copy.deepcopy(s['chars']), 'boards': dict(s['boards']), 'used': copy.deepcopy(s['used'])}})
    log('decisions', {'loop': loop, 'day': day, 'phase': 'actions', 'who': 'P', 'kind': 'place_cards', 'choice': pc})
    opt = player.cultist_pick(s, mm + pc) if hasattr(player, 'cultist_pick') else []  # カルティストの任意能力
    s, ev = _resolve(s, mm + pc, opt)
    box['s'] = s
    log('events', {'loop': loop, 'day': day, 'phase': 'actions', 'events': ev})
    # 能力を持つ者（ファクターは学校に暗躍2以上でミスリーダーの能力を得る＝早見表）
    for actor, abl in [(c, a) for c in s['chars'] for a in ('KUROMAKU', 'MISLEADER')
                       if s['chars'][c]['alive'] and ph.has_ability(s, c, a)]:
        try:
            t = player.mm_ability(s, actor, abl)
        except TypeError:  # 能力を受け取らない古いプレイヤー（Claude の play_cli など）
            t = player.mm_ability(s, actor)
        log('decisions', {'loop': loop, 'day': day, 'phase': 'mm_ability', 'who': 'M', 'kind': 'mm_ability', 'actor': actor, 'choice': t})
        if t:
            log('events', {'loop': loop, 'day': day, 'phase': 'mm_ability', 'events': ph.mm_ability(s, actor, t, abl)})
    if hasattr(player, 'mm_extra_uses'):  # 医者[友好2] を脚本家が使う・ご神木の強制の特性
        uses = player.mm_extra_uses(s)
        if uses or ph.tree_forced(s):
            log('decisions', {'loop': loop, 'day': day, 'phase': 'mm_ability', 'who': 'M', 'kind': 'mm_extra', 'choice': uses})
            log('events', {'loop': loop, 'day': day, 'phase': 'mm_ability', 'events': ph.mm_phase(s, uses)})
    if 'X_RUMOR' in s['script']['rules'] and 'X_RUMOR' not in s['ability_used_loop'] and hasattr(player, 'mm_rumor'):
        t = player.mm_rumor(s)  # 不穏な噂（1ループ1回、任意のボード）
        log('decisions', {'loop': loop, 'day': day, 'phase': 'mm_ability', 'who': 'M', 'kind': 'rumor', 'choice': t})
        if t:
            log('events', {'loop': loop, 'day': day, 'phase': 'mm_ability', 'events': ph.rumor(s, t)})
    s['ability_used_today'] = []
    if hasattr(player, 'ability_one'):  # 1件ずつ宣言し、その場で解決する（人間。前の結果を見て次を選べる。ユーザー指摘）
        while True:
            dec = player.ability_one(s)
            if dec is None:
                break
            cid, idx, arg = dec
            ref = _ask_refuse(player, s, cid, idx)
            log('decisions', {'loop': loop, 'day': day, 'phase': 'ability', 'who': s['leader'], 'kind': 'ability',
                              'choice': {'char': cid, 'ability': idx, 'arg': arg}, 'refused': ref})
            log('events', {'loop': loop, 'day': day, 'phase': 'ability', 'events': _use(s, player, cid, idx, arg, ref)})
    elif hasattr(player, 'abilities_for_day'):  # 宣言の一覧を返すプレイヤー（Claude など）
        for cid, idx, arg in player.abilities_for_day(s):
            try:  # 先の宣言で状況が変わり、合法でなくなった宣言は行わない
                ab.check_declaration(copy.deepcopy(s), cid, idx, arg)
            except ph.IllegalPlacement:
                continue
            ref = _ask_refuse(player, s, cid, idx)
            log('decisions', {'loop': loop, 'day': day, 'phase': 'ability', 'who': s['leader'], 'kind': 'ability',
                              'choice': {'char': cid, 'ability': idx, 'arg': arg}, 'refused': ref})
            log('events', {'loop': loop, 'day': day, 'phase': 'ability', 'events': _use(s, player, cid, idx, arg, ref)})
    for _ in range(0 if hasattr(player, 'abilities_for_day') or hasattr(player, 'ability_one') else 3):  # リーダーは合法な宣言を1件ずつ、最大3件まで（ランダム）
        cands = player.ability_candidates(s)
        if not cands or player.rng.random() < 0.4:
            break
        cid, idx, arg = player.rng.choice(cands)
        ref = _ask_refuse(player, s, cid, idx)
        log('decisions', {'loop': loop, 'day': day, 'phase': 'ability', 'who': s['leader'], 'kind': 'ability',
                          'options': len(cands), 'choice': {'char': cid, 'ability': idx, 'arg': arg}, 'refused': ref})
        log('events', {'loop': loop, 'day': day, 'phase': 'ability', 'events': _use(s, player, cid, idx, arg, ref)})
    for inc in ph.incident_today(s):
        ch = player.incident_choice(s, inc) if ph.incident_occurs(s, inc) else None
        if inc['culprit'] == 'C29' and ph.incident_occurs(s, inc):
            # 教祖: 事件効果を2回解決する。2回目の選択は1回目を解決した後の盤面で決める
            after = copy.deepcopy(s)
            try:
                ph.incident_effect(after, inc['id'], 'C29', ch, [])
                ch = [ch, player.incident_choice(after, inc)]
            except ph.LoopEnd:
                ch = [ch, None]
        log('decisions', {'loop': loop, 'day': day, 'phase': 'incident', 'who': 'M', 'kind': 'incident_target', 'id': inc['id'], 'choice': ch})
        log('events', {'loop': loop, 'day': day, 'phase': 'incident', 'events': ph.run_incident(s, inc, ch)})
    s['leader'] = order[(order.index(s['leader']) + 1) % 3]
    # 【強制】（シリアルキラー・アルバイト）を先に解き、その後の盤面で【任意】の候補を決める
    # （ユーザー裁定 2026-09-27: 処理順は脚本家の自由。シリアルキラーの殺害でメインラバーズに不安6→同じフェイズに主人公死亡、ができる）
    ev = ph.turn_end_mandatory(s)
    opts = ph.killer_options(s)
    pick = player.killer_pick(s) if hasattr(player, 'killer_pick') else [o for o in opts if player.rng.random() < 0.5]
    log('decisions', {'loop': loop, 'day': day, 'phase': 'turn_end', 'who': 'M', 'kind': 'killer', 'options': opts, 'choice': pick})
    snap = copy.deepcopy(s)
    try:
        ev += ph.turn_end_optional(s, pick)
    except ph.IllegalPlacement:
        s = snap
        box['s'] = s
    log('events', {'loop': loop, 'day': day, 'phase': 'turn_end', 'events': ev})
    _check(s)
    ded = getattr(getattr(player, 'pc', player), 'ded', None)
    if ded is not None:
        m, tot = ded.marginals()
        know = {r: round(max((d.get(r, 0) for d in m.values()), default=0) / tot, 3) if tot else 0
                for r in ('KEY', 'KILLER', 'KUROMAKU', 'MISLEADER')}
        log('events', {'loop': loop, 'day': day, 'phase': 'pc_knowledge', 'knowledge': know, 'n_hyp': ded.n_hyp()})
    return s

def _resolve(s, placements, optional=None):
    """行動解決。従者が付いていく相手が2人以上で行き先が違えばリーダーが選ぶ（ponytail: お嬢様→大物→対象の順で固定）。"""
    try:
        return resolve_actions(s, placements, optional)
    except ph.IllegalPlacement as e:
        if '従者' not in str(e):
            raise
        for c in ['C03', 'C16', *s.get('servant_targets', [])]:
            try:
                return resolve_actions(s, placements, optional, follow={'C34': c})
            except ph.IllegalPlacement:
                continue
        raise


def _use(s, player, cid, idx, arg, ref):
    """友好能力を解決する。情報屋で公開するルールXが2つ残った場合は、脚本家がどちらを公開するか選ぶ（カードの文面）。"""
    ev = ab.use_ability(s, cid, idx, arg, refuse=ref)
    for e in ev:
        opts = e.get('reveal_rule_x')
        if isinstance(opts, dict):
            pick = getattr(player, 'choose_rule_x', None)
            e['reveal_rule_x'] = pick(s, opts['choose_from']) if pick else opts['choose_from'][0]
    return ev


def _check(s):
    """自己整合の確認だけ（ルールの正しさは境界例と記録の再生で見る）。"""
    for c, v in s['chars'].items():
        assert v['par'] >= 0 and v['gw'] >= 0 and v['int'] >= 0 and v['guard'] >= 0, c
        assert v['area'] in ph.AREAS or not v.get('present', True), c
    for a, n in s['boards'].items():
        assert n >= 0, a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--games', type=int, default=100)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--name', default=None)
    ap.add_argument('--mm', default='random', choices=['random', 'route', 'hidden'])
    ap.add_argument('--pc', default='random', choices=['random', 'oracle', 'deduce', 'blind'])
    a = ap.parse_args()
    name = a.name or f'{a.mm}_vs_{a.pc}_s{a.seed}_n{a.games}'
    out = os.path.join(HERE, 'runs', name)
    os.makedirs(out, exist_ok=True)
    files = {k: open(os.path.join(out, f'{k}.jsonl'), 'w', encoding='utf-8') for k in ('decisions', 'events')}
    rng = random.Random(a.seed)
    results = []
    for g in range(a.games):
        def log(kind, rec, g=g):
            files[kind].write(json.dumps({'game': g, **rec}, ensure_ascii=False) + '\n')
        if a.pc == 'blind':
            from selfplay.players import DeductiveBlocker
            pc = DeductiveBlocker(rng)
        elif a.pc == 'deduce':
            from selfplay.players import DeductiveProtagonist
            pc = DeductiveProtagonist(rng)
        elif a.pc == 'oracle':
            from selfplay.players import OracleBlocker
            pc = OracleBlocker(rng)
        else:
            pc = RandomPlayer(rng)
        if a.mm in ('route', 'hidden'):
            from selfplay.players import HiddenMastermind, RouteMastermind
            player = Combo((HiddenMastermind if a.mm == 'hidden' else RouteMastermind)(rng), pc)
        else:
            player = Combo(RandomPlayer(rng), pc) if a.pc != 'random' else pc
        r = play_game(TOUKOU001 or by_id('s03_bomb'), player, log)
        log('events', {'loop': None, 'day': None, 'phase': 'game_end', 'result': r})
        results.append(r)
    summ = {'games': a.games, 'seed': a.seed,
            'protagonist_wins': sum(r['winner'] == 'protagonists' for r in results),
            'by_loop': {str(l): sum(r['winner'] == 'protagonists' and r['loop'] == l for r in results) for l in (1, 2, 3, 'final')},
            'final_battles': sum(r['loop'] == 'final' for r in results)}
    json.dump(summ, open(os.path.join(out, 'summary.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False))


if __name__ == '__main__':
    main()
