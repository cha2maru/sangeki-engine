"""次の一手問題の局面を、自動どうしの対戦から拾う（問題サイト P2、plan/problem_site_plan.md）。まず脚本家の問題だけ。

脚本家が札を決めるたびに、盤面・探索の上位の候補と値・実際の手を記録し、「AI が迷った」局面（1 位と 2 位の値の差が小さい）に印を付ける。
問題の JSON は盤面をそのまま持つ（再生しない）。ページは盤面を描くだけで計算しない。

    cd play && python -m selfplay.make_problems generated/gen_s51_003 --mm calcGBxhdKNi --pc plan_read2 --seeds 1-2 --out selfplay/runs/problems_2026-11-14/raw
"""
import argparse
import copy
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.scripts import by_id  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.players import PublicObserver  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402

GAP = 0.3  # 1 位と 2 位の値の差がこれ未満なら「迷った」


def _cards(mm):
    return sorted(f"{x['target']}:{x['card']}" for x in mm)


def known_of(obs, s, top_rules=6):
    """主人公から割れていること: 公開の推理（engine.deduce）の、人物ごとの役職の見込みとルールの組の見込み。"""
    try:
        obs._ensure(s)
        marg, tot = obs.ded.marginals()
        R, C = obs.ded._sync()
        w = obs.ded._w(C)
    except Exception as e:  # 推理が作れない盤面（仮説が多すぎるなど）は空で出す
        return {'error': repr(e)[:120]}
    import numpy as np
    roles = {c: {r: round(float(v) / float(tot), 3) for r, v in m.items() if tot and v / tot >= 0.01} for c, m in marg.items()}
    pr = np.bincount(C, weights=w) if len(C) else np.zeros(0)
    combos = sorted(((obs.ded.T.combos[i], float(pr[i]) / float(pr.sum())) for i in np.nonzero(pr)[0]), key=lambda x: -x[1])[:top_rules]
    return {'roles': roles, 'rules': [[list(cb), round(p, 3)] for cb, p in combos], 'n_hyp': int(len(C))}


def record(script, mm_kind, pc_kind, seed, top=4):
    """1 試合を進め、脚本家の各日の局面を返す。"""
    rm, rp = random.Random(f'{seed}:mm'), random.Random(f'{seed}:pc')
    mm, pc = make(mm_kind, rm, script), make(pc_kind, rp, script)
    out, public = [], []
    obs = PublicObserver()  # 主人公側の公開の推理（その日の朝までの公開の出来事だけを見る）
    rank0, cards0 = mm.rank_moves, mm.mm_cards
    box = {}

    def rank_moves(s, *a, **k):
        r = rank0(s, *a, **k)
        if s is not box.get('s'):  # 先読み（look）の中の盤面の呼び出しは記録しない
            return r
        seen, uniq = set(), []
        for x in sorted(r or [], key=lambda x: -x[0]):  # 同じ札の組は 1 つに（探索は同じ手を別の経路で何度も出す）
            k_ = tuple(_cards(x[1]))
            if k_ not in seen:
                seen.add(k_)
                uniq.append(x)
        box['ranked'] = uniq[:top]
        return r

    def mm_cards(s):
        box['ranked'], box['s'] = [], s
        s0 = copy.deepcopy(s)
        chose = cards0(s)
        ranked = box.get('ranked') or []
        vals = [round(x[0], 3) for x in ranked]
        out.append({'loop': s0['loop'], 'day': s0['day'], 'state': s0, 'public_log': list(public), 'known': known_of(obs, s0),
                    'ai': _cards(chose), 'candidates': [{'cards': _cards(x[1]), 'value': round(x[0], 3)} for x in ranked],
                    'gap': round(vals[0] - vals[1], 3) if len(vals) >= 2 else None})
        return chose
    mm.rank_moves, mm.mm_cards = rank_moves, mm_cards

    def log(kind, rec):
        if kind == 'events' and rec.get('events'):
            public.append({'loop': rec.get('loop'), 'day': rec.get('day'), 'phase': rec.get('phase'), 'events': rec['events']})
    try:
        play_game(script, Combo(mm, pc), log, observers=[obs])
    except Exception as e:  # 1 試合の失敗で全体を止めない
        print('試合が止まった', script['id'], seed, repr(e)[:120], file=sys.stderr)
    for x in out:
        top1 = x['candidates'][0]['cards'] if x['candidates'] else None
        x['rewritten'] = top1 is not None and x['ai'] != top1  # 後処理（_finalize 系）が探索の 1 位を書き換えた
        x['troubled'] = (x['gap'] is not None and x['gap'] < GAP) or x['rewritten']  # 広い印（記録用）。問題の選び方は pick()
    return out


def _fork(mm, mm_orig, pc, pc_orig):
    """記録用の差し替え（フック）を外した脚本家・主人公を固める（cloudpickle＋gzip。主人公 plan_read2 は関数の中の関数で作られるので pickle では固まらない）。
    戻した複製で同じ局面の 1 日を 2 回進め、同じ札になることを確かめた（2026-11-15）。"""
    import cloudpickle
    import gzip
    cur = [(o, {k: o.__dict__.get(k) for k in og}) for o, og in ((mm, mm_orig), (pc, pc_orig))]
    try:
        for o, og in ((mm, mm_orig), (pc, pc_orig)):
            for k, v in og.items():
                if v is None:
                    o.__dict__.pop(k, None)
                else:
                    o.__dict__[k] = v
        return gzip.compress(cloudpickle.dumps((mm, pc)))
    finally:
        for o, c in cur:
            for k, v in c.items():
                if v is not None:
                    o.__dict__[k] = v


def rollout(fork, s0, mm_cards):
    """分けておいた試合（_fork）を、その日の脚本家の札を mm_cards に固定して、ループの終わりまで進める。その後の日の判断はどちらもエンジン（脚本家・主人公の型）。"""
    import cloudpickle
    import gzip
    mm, pc = cloudpickle.loads(gzip.decompress(open(fork, 'rb').read()))
    return play_through(Combo(mm, pc), s0, mm_cards, s0['script']['days'])


def play_through(player, s0, mm_cards, last_day):
    """s0 の日に脚本家の札を mm_cards に固定し、last_day のターン終了まで（ループが先に終われば そこまで）進める。
    返り値: lines（日ごとの出来事の文。非公開の理由も含む）・end（ループが途中で終わった日・フェイズ・理由）・loop_end_loss（最終日まで進んだときのループ終了時の判定で負けた条件）。"""
    from ai_gm import CARD_UI, PHASE_JA, describe, name
    from engine import phases as ph
    from selfplay.run import play_day
    s, box, lines = copy.deepcopy(s0), {}, []

    def log(kind, rec):
        if rec.get('phase') and rec['phase'] not in ('loop_end', 'loop_end_check', 'pc_knowledge'):
            box['phase'] = rec['phase']
        d = rec.get('day')
        if kind == 'decisions' and rec.get('kind') == 'place_cards':
            lines.append({'day': d, 'text': ('脚本家' if rec['who'] == 'M' else '主人公') + 'の札: '
                          + '、'.join((f"{x['by']} " if x.get('by') != 'M' else '') + f"{name(x['target'])}={CARD_UI.get(x['card'], x['card'])}" for x in rec['choice'])})
        if kind == 'events' and 'events' in rec:
            for e in rec['events']:
                t = describe(e) if e.get('kind') not in ('revealed', 'loop_end') else None
                if t and e.get('kind') == 'mm_ability' and e.get('actor'):
                    t += f"（{name(e['actor'])}の能力）"
                if t:
                    lines.append({'day': d, 'text': t, 'phase': PHASE_JA.get(rec.get('phase'), '')})
            if box.get('s') is not None and hasattr(player, 'observe'):
                player.observe(box['s'], rec.get('phase'), rec['events'])
    end = None
    try:
        for day in range(s0['day'], last_day + 1):
            s = play_day(s, player, s0['loop'], day, log, box, mm_override=copy.deepcopy(mm_cards) if day == s0['day'] else None)
    except ph.LoopEnd as e:
        s = box['s']
        end = {'day': s['day'], 'phase': PHASE_JA.get(box.get('phase'), box.get('phase')), 'reason': e.reason, 'loss': e.loss}
        for x in e.events:  # ループを終わらせた出来事（事件の死亡など）は log を通らない
            t = describe(x) if x.get('kind') not in ('revealed', 'loop_end') else None
            if t:
                lines.append({'day': s['day'], 'text': t, 'phase': end['phase']})
        lines.append({'day': s['day'], 'text': f"{end['phase']}にループが終わった（{e.reason}）", 'phase': end['phase']})
    full = end is not None or last_day >= s0['script']['days']
    return {'lines': lines, 'end': end, 'loop_end_loss': ph.loop_end_loss(s) if full else [], 'to_loop_end': full}


def record_gm(sid, mm_kind, pc_kind, seed, gmdir, top=4):
    """record() と同じ局面を、対戦画面の進行役（ai_gm の自動同士の観戦モード）の上で拾う。盤面の記録（snapshots.jsonl・log.jsonl）が
    gmdir に残るので、問題の局面までの試合の流れを board.html のリプレイでそのまま見られる（ユーザー 2026-11-14）。
    各局面に snap（その時点の盤面の記録の数）を付ける。"""
    import ai_gm
    from types import SimpleNamespace
    os.makedirs(gmdir, exist_ok=True)
    out, obs, box, pcs = [], PublicObserver(), {}, []
    make0, pg0 = ai_gm.make, ai_gm.play_game

    def mk(kind, rng, script):
        p = make0(kind, rng, script)
        if kind == pc_kind and not box.get('pc'):  # 主人公の問題: 主人公の手番の局面（脚本家の実際の札つき）を残す
            box['pc'] = p
            box['pc_orig'] = {'pc_cards': p.__dict__.get('pc_cards')}
            pcc0 = p.pc_cards

            def pc_cards(s, order, mm_targets=None):
                s0 = copy.deepcopy(s)
                snap = sum(1 for _ in open(os.path.join(gmdir, 'snapshots.jsonl'), encoding='utf-8')) if os.path.exists(os.path.join(gmdir, 'snapshots.jsonl')) else 0
                out_ = pcc0(s, order, mm_targets)
                pcs.append({'side': 'pc', 'pc': pc_kind, 'loop': s0['loop'], 'day': s0['day'], 'state': s0, 'order': list(order), 'targets': list(mm_targets or []),
                            'known': known_of(obs, s0), 'snap': snap, 'mm_actual': box.get('last_mm', []),
                            'ai': [{'by': x['by'], 'target': x['target'], 'card': x['card']} for x in out_]})
                return out_
            p.pc_cards = pc_cards
            return p
        if kind != mm_kind or box.get('mm'):
            return p
        box['mm'] = p
        box['mm_orig'] = {k: p.__dict__.get(k) for k in ('rank_moves', 'mm_cards', 'observe', 'progress')}
        rank0, cards0, obs0 = p.rank_moves, p.mm_cards, p.observe
        box['obs'] = []

        def observe(s, phase, events):
            # その場の評価（selfplay.problem_eval）で脚本家の推理と主人公の模型を作り直すため、観測の呼び出しを盤面ごと残す（脚本・初期配置は除く）
            box['obs'].append([{k: v for k, v in s.items() if k not in ('script', 'init')}, phase, events])
            return obs0(s, phase, events)
        p.observe = observe

        def rank_moves(s, *a, **k):
            r = rank0(s, *a, **k)
            if s is box.get('s'):
                seen, uniq = set(), []
                for x in sorted(r or [], key=lambda x: -x[0]):
                    k_ = tuple(_cards(x[1]))
                    if k_ not in seen:
                        seen.add(k_)
                        uniq.append(x)
                box['ranked'] = uniq[:top]
                rep = getattr(p, '_reply_of', {}) or {}
                box['evals'] = [{'cards': _cards(x[1]), 'value': round(x[0], 3),
                                 'reply': [{'by': y['by'], 'target': y['target'], 'card': y['card']} for y in rep.get(id(x[1]), [])]}
                                for x in uniq[:300]]  # 回答者の手が AI と違うとき、その手の AI の評価を出す（ユーザー 2026-11-14）
            return r

        def mm_cards(s):
            box['ranked'], box['s'], box['evals'] = [], s, []
            s0 = copy.deepcopy(s)
            snap = sum(1 for _ in open(os.path.join(gmdir, 'snapshots.jsonl'), encoding='utf-8')) if os.path.exists(os.path.join(gmdir, 'snapshots.jsonl')) else 0
            blob = _fork(p, box['mm_orig'], box['pc'], box['pc_orig']) if box.get('pc') else None  # 考える前の両者（各回答を進めるため）
            chose = cards0(s)
            box['last_mm'] = [{'by': 'M', 'target': x['target'], 'card': x['card']} for x in chose]
            ranked = box.get('ranked') or []
            vals = [round(x[0], 3) for x in ranked]
            fork = None
            if blob and len(vals) >= 2 and (vals[0] - vals[1] < GAP or _cards(chose) != _cards(ranked[0][1])):  # 問題の候補になる局面だけ残す
                fork = os.path.join(gmdir, 'forks', f"L{s0['loop']}D{s0['day']}.pkl.gz")
                os.makedirs(os.path.dirname(fork), exist_ok=True)
                open(fork, 'wb').write(blob)
            out.append({'fork': fork, 'loop': s0['loop'], 'day': s0['day'], 'state': s0, 'known': known_of(obs, s0), 'snap': snap, 'evals': box.get('evals', []),
                        'obs_n': len(box['obs']),
                        'ai': _cards(chose), 'candidates': [{'cards': _cards(x[1]), 'value': round(x[0], 3)} for x in ranked],
                        'gap': round(vals[0] - vals[1], 3) if len(vals) >= 2 else None})
            return chose
        p.rank_moves, p.mm_cards = rank_moves, mm_cards
        return p
    ai_gm.make = mk
    ai_gm.play_game = lambda script, player, log0, observers=(), **kw: pg0(script, player, log0, observers=tuple(observers) + (obs,), **kw)
    try:
        ai_gm.run(SimpleNamespace(script=sid, code='', mm=mm_kind, pc=pc_kind, seed=seed, dir=gmdir, blind=False,
                                  blind_pool='recommended', replay=None, resume=False))
    except Exception as e:
        print('試合が止まった', sid, seed, repr(e)[:120], file=sys.stderr)
    finally:
        ai_gm.make, ai_gm.play_game = make0, pg0
    json.dump(json.loads(json.dumps(box.get('obs', []), default=str)), open(os.path.join(gmdir, 'mm_observe.json'), 'w', encoding='utf-8'))
    for x in pcs:
        x['gmdir'] = gmdir
    with open(os.path.join(gmdir, 'pc_positions.jsonl'), 'w', encoding='utf-8') as f:  # 主人公の問題の局面（pick_pc が読む）
        for x in pcs:
            f.write(json.dumps(x, ensure_ascii=False, default=str) + '\n')
    for x in out:
        top1 = x['candidates'][0]['cards'] if x['candidates'] else None
        x['rewritten'] = top1 is not None and x['ai'] != top1
        x['troubled'] = (x['gap'] is not None and x['gap'] < GAP) or x['rewritten']
        x['gmdir'] = gmdir
    return out


def gm_replay(r, title):
    """問題の局面までの試合の流れ（ai_gm の盤面の記録の先頭 snap 個と、そこまでの公開ログ）を sangeki-replay にする。"""
    snaps = [json.loads(x) for x in open(os.path.join(r['gmdir'], 'snapshots.jsonl'), encoding='utf-8')][:r['snap']]
    last = snaps[-1]['log_n'] if snaps else 0
    log = [json.loads(x) for x in open(os.path.join(r['gmdir'], 'log.jsonl'), encoding='utf-8')]
    return {'format': 'sangeki-replay', 'version': 1, 'complete': False, 'title': title,
            'log': [x for x in log if x['n'] <= last], 'snapshots': snaps}


STAGES = ('初手', '中盤', '終盤')


def stage_of(r):
    """問題の段階（ユーザー 2026-11-15）: 初手＝ループ1の1日目、終盤＝最終ループ、中盤＝それ以外。"""
    if (r['loop'], r['day']) == (1, 1):
        return '初手'
    return '終盤' if r['loop'] >= by_id(r['script'])['loops'] else '中盤'


def auto_text(r, side):
    """題と問いの文を局面の事実（ルールY・ループ・日）から作る（Claude が考えて書かない。ユーザー 2026-11-15）。"""
    from ai_gm import RULE_JA
    sc = by_id(r['script'])
    y = '・'.join(RULE_JA.get(x, x) for x in sc['rules'] if x.startswith('Y_'))
    last = r['loop'] >= sc['loops']
    when = f"ループ{r['loop']}{'（最終）' if last else ''}・{r['day']}日目{'（最終日）' if r['day'] >= sc['days'] else ''}"
    q = ('脚本家として、3枚の札をどこに置きますか。' if side == 'mm' else '脚本家の伏せ札の置き場所を見て、主人公として3人の札をどこに置きますか。')
    return {'title': f"{y}（{r['script'].split('_', 1)[-1]}）、{when}", 'question': f"{when}。{q}"}  # 同じルール・同じ日の問題を見分けるため脚本の番号も


def pick(raw_dir, n=10, gap=0.1, stage=None):
    """拾った局面から問題の候補を選ぶ: AI が探索の 1 位をそのまま指し（後処理の書き換えはよくあるので外す）、1 位と 2 位の値の差が gap 未満で、
    2 つの手が 2 枚以上違う局面。脚本・ループ・日が偏らないよう、脚本ごとに 1 つずつ順に取る。"""
    import glob
    recs = [json.loads(line) for f in sorted(glob.glob(os.path.join(raw_dir, '*.jsonl'))) if not os.path.basename(f).startswith('picked') for line in open(f, encoding='utf-8')]
    ok, seen = [], set()
    for r in recs:
        c = r['candidates']
        if len(c) < 2 or r['rewritten'] or r['gap'] is None or r['gap'] >= gap or (stage and stage_of(r) != stage):
            continue
        # 2 枚以上違う（対称差 4 以上）。初手は上位 2 手が 1 枚違いのことが多い（50/86 局面）ので 1 枚違いも採る（ユーザー 2026-11-15「もう少し初手問題増やしたい」）
        if len(set(c[0]['cards']) ^ set(c[1]['cards'])) < (2 if stage == '初手' else 4):
            continue
        tg = lambda cs: {x.rsplit(':', 1)[0] for x in cs}  # noqa: E731
        if tg(c[0]['cards']) == tg(c[1]['cards']):  # 同じ置き先で札を入れ替えただけ（暗躍+1 と +2 の入れ替えなど）は外す
            continue
        key = (r['script'], r['loop'], r['day'], tuple(r['ai']))
        if key in seen:  # seed 違いで同じ局面
            continue
        seen.add(key)
        ok.append(r)
    by = {}
    for r in ok:
        by.setdefault(r['script'], []).append(r)
    out = []
    while len(out) < n and any(by.values()):
        for k in sorted(by):
            if by[k] and len(out) < n:
                out.append(by[k].pop(len(by[k]) // 2))
    return out


def replay_of(r):
    """問題の局面を、盤面の 1 枚だけのリプレイ（board.html の ?replay= で開ける sangeki-replay）にする。
    盤面の表示は ai_gm.show と同じ project() から作る。脚本家の問題なので非公開シート（mmSheet）も付ける。
    ponytail: 公開ログ・過去のループの結果・事件の結果は入れない（必要なら ai_gm の上で局面を拾う形に替える）"""
    from ai_gm import INC_JA, ROLE_JA, RULE_JA, SET_JA, name
    from engine.project import AREA_JA, project
    s, sc = r['state'], r['state']['script']
    full = by_id(r['script'])  # 局面の state の script には loops が無いことがある
    loops, days = full['loops'], full['days']
    pub = project(s, [])
    pub['publicSheet'] = {'set': SET_JA.get(sc.get('set', 'BTX'), sc.get('set')), 'loops': loops, 'days': days,
                          'territory': AREA_JA.get(sc.get('territory')) if sc.get('territory') else None, 'special': sc.get('special') or ''}
    pub['mmSheet'] = {'rules': [RULE_JA.get(x, x) for x in sc['rules']],
                      'roles': [{'char': name(c), 'id': c, 'role': ROLE_JA.get(v, v)} for c, v in sc['roles'].items()],
                      'incidents': [{'day': i['day'], 'name': INC_JA.get(i['id'], i['id']), 'culprit': name(i['culprit']), 'cid': i['culprit']} for i in sc['incidents']],
                      'used': list(s.get('used', {}).get('M', [])), 'ruleIds': list(sc['rules']), 'routes': []}
    pub.update({'mode': 'ai', 'side': 'mm', 'watch': False, 'loops': loops, 'days': days, 'phaseName': '行動カード（脚本家）',
                'waiting': {'kind': 'none', 'text': '問題: 脚本家の 3 枚'}, 'loopHistory': [], 'seenChars': [], 'abilityUsedToday': [], 'abilityUsedLoop': []})
    pub['loop'] = min(pub['loop'], loops)
    return {'format': 'sangeki-replay', 'version': 1, 'complete': False, 'title': f"問題 {r['script'].split('/')[-1]} L{r['loop']}D{r['day']}",
            'log': [], 'snapshots': [{'prev_n': 0, 'log_n': 0, 'state': pub}]}


def label(cards):
    """札の組を画面の文に（例「神格に暗躍+1・大物に移動←→」）。"""
    from engine.project import AREA_JA, CARD_UI
    from engine.resolve import CHARS
    def tg(t):
        return AREA_JA[t[2:]] + 'のボード' if t.startswith('B:') else CHARS[t]['name']
    return '・'.join(f"{tg(x.rsplit(':', 1)[0])}に{CARD_UI[x.rsplit(':', 1)[1]]}" for x in cards)


def route_ja(r):
    """負け筋を画面の文に（例「シリアルキラー（教祖）が大物を殺す（フレンド）」）。"""
    from ai_gm import INC_JA, RULE_JA
    from engine.resolve import CHARS
    nm = lambda c: CHARS[c]['name'] if c in CHARS else c  # noqa: E731
    goal = re.sub(r'C\d\d', lambda m: nm(m.group(0)), r.get('goal') or '')
    via = nm(r['via']) if r.get('via') else ''
    head = {'SK_KILL': f'シリアルキラー（{via}）', 'KILLER_KEY': f'キラー（{via}）', 'KILLER_PROT': f'キラー（{via}）の暗躍',
            'ML_PROT': f'メインラバーズ（{via}）', 'FRIEND': 'フレンド', 'TT_LOSS': 'タイムトラベラー'}.get(r['id'])
    if head is None:
        rid = {'HOSPITAL_PROT': 'HOSPITAL', 'HOSPITAL_KILL': 'HOSPITAL'}.get(r['id'], r['id'])  # 病院の事件の筋（主人公死亡・殺害）
        head = INC_JA.get(rid, RULE_JA.get(rid, rid))
        head = f'{head}（{via}）' if via else head
    if goal and ' 死亡' in goal:  # 「刑事 死亡（フレンド）」→「シリアルキラー（女子学生）による 刑事の死亡（フレンド）」（死んだと読まれないように）
        return f"{head}による {goal.replace(' 死亡', 'の死亡')}"
    return f'{head}: {goal}' if goal else head


def after_routes(s, placements):
    """札を解決した後の負け筋。カルティストが暗躍禁止を無視する選び方（脚本家の任意）は、筋ごとにいちばん近いものを採る。
    以前は無視を入れずに数え、主人公の問題 023 L2D4 で「この日に成り立つ負け筋はない」と出たのに、実際は病院の暗躍禁止が無視されて主人公が死亡した（2026-11-15）。"""
    from engine.resolve import resolve_actions
    from engine.routes import enumerate_routes
    from selfplay.players import cultist_options
    best = {}
    for o in [None] + cultist_options(s, placements):
        for r in enumerate_routes(resolve_actions(s, placements, [o] if o else [])[0]):
            k = (r['id'], r['via'], r['goal'])
            if k not in best or r['total'] < best[k]['total']:
                best[k] = r
    return list(best.values())


def effects(s, cards, reply):
    """札の組 cards の効果: 主人公が何もしないときと、AI が読んだ応手 reply のときに、朝より縮む負け筋（残りの手数つき。0 はその日のうちに成り立つ）。"""
    from engine.resolve import IllegalPlacement, resolve_actions
    from engine.routes import enumerate_routes
    from selfplay.players import DUMMY_PC
    mm = [{'by': 'M', 'target': x.rsplit(':', 1)[0], 'card': x.rsplit(':', 1)[1]} for x in cards]
    key = lambda r: (r['id'], r['via'], r['goal'])  # noqa: E731
    before = {key(r): r['total'] for r in enumerate_routes(s)}
    def near(pc):
        try:
            after = after_routes(s, mm + pc)
        except (IllegalPlacement, Exception):
            return None
        left_days = (s['script'].get('days') or s.get('days') or 99) - s['day']  # 今日の後の日数。それより多くの手が要る筋はこのループで間に合わない
        out = sorted(((r['total'], route_ja(r)) for r in after if r['total'] <= min(2, left_days) and r['total'] < before.get(key(r), 99)), key=lambda x: x[0])
        return [{'left': t, 'route': g} for t, g in out[:4]]
    return {'none': near(DUMMY_PC), 'after_reply': near(reply) if reply and len(reply) == 3 else None}


def pc_outcome(s, mm_actual, reply):
    """主人公の札 reply を、脚本家の実際の札 mm_actual に当てて行動を解決したとき、朝より縮む負け筋（残り。0 はその日のうちに成り立つ）。"""
    from engine.resolve import IllegalPlacement, resolve_actions
    from engine.routes import enumerate_routes
    key = lambda r: (r['id'], r['via'], r['goal'])  # noqa: E731
    before = {key(r): r['total'] for r in enumerate_routes(s)}
    try:
        after = after_routes(s, list(mm_actual) + list(reply))
    except IllegalPlacement as e:
        return {'error': f'置けない手です: {e}'}
    left_days = (s['script'].get('days') or s.get('days') or 99) - s['day']
    # 朝からすでに成り立っている筋（残り 0）も、主人公の札で崩せなければ残る（以前は縮んだ筋だけを見て、朝から 0 の病院の事件の筋を落としていた）
    rs = [r for r in after if r['total'] <= min(2, left_days) and (r['total'] < before.get(key(r), 99) or r['total'] == 0)]
    out = sorted(((r['total'], route_ja(r)) for r in rs), key=lambda x: x[0])
    # その日のうちに負けが決まる: ターン終了の殺害・今日の事件の筋が残り 0、または最終日のループ終了時の条件が残り 0
    today = {'KILLER_KEY', 'KILLER_PROT', 'SK_KILL', 'ML_PROT', 'HOSPITAL_PROT', 'HOSPITAL_KILL', 'MURDER', 'SUICIDE', 'REMOTE'}
    last = s['day'] >= (s['script'].get('days') or s.get('days') or 99)
    return {'routes': [{'left': t, 'route': g} for t, g in out[:5]],
            'lethal': any(r['total'] == 0 and (r['id'] in today or last) for r in rs)}


def pick_pc(raw_dir, n=5, stage=None):
    """主人公の問題の候補: 自動の主人公（AI）の札では、脚本家の実際の札による負け筋がその日のうちに成り立った（残り 0）局面＝AI が止めきれなかった局面。
    脚本・ループが偏らないよう脚本ごとに 1 つずつ。"""
    import glob
    by = {}
    for f in sorted(glob.glob(os.path.join(raw_dir, 'gm', '*', 'pc_positions.jsonl'))):
        sid = 'generated/' + os.path.basename(os.path.dirname(f)).rsplit('_s', 1)[0]
        seed = int(os.path.basename(os.path.dirname(f)).rsplit('_s', 1)[1])
        for line in open(f, encoding='utf-8'):
            r = json.loads(line)
            if not r['mm_actual'] or len(r['ai']) != 3:
                continue
            r = dict(r, script=sid)
            if stage and stage_of(r) != stage:
                continue
            o = pc_outcome(r['state'], r['mm_actual'], r['ai'])
            if o.get('lethal') or stage == '初手':  # 初手はその日に負けることがほぼ無いので、ループ1の1日目をそのまま問題にする
                by.setdefault(sid, []).append(dict(r, script=sid, seed=seed, ai_outcome=o))
    for k in by:  # ループ 1 の 1 日目は主人公に手がかりが無く運の要素が強いので外し、後のループを先に
        by[k] = sorted((r for r in by[k] if stage == '初手' or (r['loop'], r['day']) != (1, 1)), key=lambda r: (-r['loop'], r['day']))
    out = []
    while len(out) < n and any(by.values()):
        for k in sorted(by):
            if by[k] and len(out) < n:
                out.append(by[k].pop(0))
    return out


def export_pc(picked, texts, outdir, index):
    """主人公の問題を書き出す。真相と伏せ札の中身は、回答の後まで見せない（reveal に入れる）。"""
    from engine.project import AREA_JA, CARD_UI
    from engine.resolve import CARDS, CHARS, ONCE, card_targets
    from ai_gm import ROLE_JA
    tname = lambda t: AREA_JA[t[2:]] + 'のボード' if t.startswith('B:') else CHARS[t]['name']  # noqa: E731
    for r in picked:
        key = f"{r['script'].split('/')[-1]}:{r['seed']}:L{r['loop']}D{r['day']}:pc"
        t = texts.get(key) or auto_text(r, 'pc' if key.endswith(':pc') else 'mm')
        pid = key.replace(':', '_')
        s = r['state']
        rp = gm_replay(r, f"問題 {r['script'].split('/')[-1]} L{r['loop']}D{r['day']}")
        if rp['snapshots']:
            # 主人公の手番の盤面は記録に無い（次の記録は行動の解決の後で、伏せ札の中身まで見える）。前の場面に、その日の伏せ札（中身は伏せる）を載せた 1 枚を足す
            last = copy.deepcopy(rp['snapshots'][-1])
            st = last['state']
            st.update({'loop': r['loop'], 'day': r['day'], 'phaseName': f"L{r['loop']} {r['day']}日目 行動カード（主人公）",
                       'cards': [{'owner': 'M', 'target': ('board:' + AREA_JA[t[2:]]) if t.startswith('B:') else CHARS[t]['name'], 'card': None} for t in r['targets']]})
            rp['snapshots'].append({'prev_n': last['log_n'], 'log_n': last['log_n'], 'state': st})
        json.dump(rp, open(os.path.join(outdir, pid + '.replay.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        hands = {p: [c for c in dict.fromkeys(CARDS['protagonist_hand']) if not (c in ONCE[p] and c in s['used'][p])] for p in 'ABC'}
        k = r.get('known') or {}
        guess = [{'id': c, 'name': CHARS[c]['name'], 'guess': [{'role': ROLE_JA.get(x, x), 'p': round(v, 2)} for x, v in sorted(m.items(), key=lambda kv: -kv[1])[:3]]}
                 for c, m in (k.get('roles') or {}).items() if c in s['chars'] and s['chars'][c].get('present', True)]
        from ai_gm import RULE_JA
        known = {'chars': guess, 'rules': [{'rules': [RULE_JA.get(x, x) for x in cb], 'p': p} for cb, p in k.get('rules', [])], 'n_hyp': k.get('n_hyp')}
        lbl = lambda xs: '・'.join(f"主人公{x['by']}が{tname(x['target'])}に{CARD_UI[x['card']]}" for x in xs)  # noqa: E731
        p = {'id': pid, 'side': 'pc', 'title': t['title'], 'question': t['question'], 'notes': t.get('notes', []),
             'replay': pid + '.replay.json', 'order': r['order'], 'hands': hands, 'mm_targets': [{'id': x, 'name': tname(x)} for x in r['targets']],
             'targets': [{'id': x, 'name': tname(x)} for x in card_targets(s)], 'known': known, 'losses': [],
             'choices': [], 'ai': {'cards': sorted(f"{x['target']}:{x['card']}" for x in r['ai']), 'label': lbl(r['ai']),
                                   'outcome': r['ai_outcome'], 'after': game_after(r['gmdir'], r['snap'], r['loop'])},
             'reveal': {'mm': '・'.join(f"{tname(x['target'])}に{CARD_UI[x['card']]}" for x in r['mm_actual'])}}
        # 理由はエンジンの出力から（AI の札・脚本家の実際の札・実際の試合のその後）。Claude が考えて書かない（ユーザー 2026-11-15）
        end = [x['text'] for x in p['ai']['after'] if 'ループが終わった' in x['text'] or x['text'].startswith('ループ終了時の判定')]
        p['ai']['reason'] = (f"{player_name(r.get('pc', 'plan_read2'))} は {p['ai']['label']} を置いた。脚本家の伏せ札は {p['reveal']['mm']}。"
                             + (f"実際の試合では、この後 {end[-1]}。" if end else ''))
        snaps = [json.loads(x) for x in open(os.path.join(r['gmdir'], 'snapshots.jsonl'), encoding='utf-8')][:r['snap']]
        last = snaps[-1]['log_n'] if snaps else 0
        lines = [x for x in (json.loads(y) for y in open(os.path.join(r['gmdir'], 'log.jsonl'), encoding='utf-8')) if x['n'] <= last]
        for i, x in enumerate(lines):
            if (x.get('ev') or {}).get('kind') == 'loop_end' or x['text'].startswith('ループ終了時の判定: 主人公の敗北'):  # ループ終了時の判定の負けは出来事の印が無い
                dead = [y['text'] for y in lines[max(0, i - 4):i] if (y.get('ev') or {}).get('kind') == 'death' and y['loop'] == x['loop'] and y['day'] == x['day']]
                p['losses'].append({'loop': x['loop'], 'day': x['day'], 'text': '／'.join(dead + [x['text']])})
        json.dump(p, open(os.path.join(outdir, pid + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        json.dump({'state': {k_: v for k_, v in s.items() if k_ not in ('script', 'init')}, 'state_script': s['script'], 'init': s.get('init'),
                   'mm_actual': r['mm_actual'], 'script_full': by_id(r['script']), 'mm': r.get('mm', 'calcGBxhdKNi'), 'seed': r['seed']}, open(os.path.join(outdir, pid + '.engine.json'), 'w', encoding='utf-8'), ensure_ascii=False, default=str)
        index.append({'id': pid, 'side': 'pc', 'title': t['title'], 'stage': stage_of(r)})
    return index


def player_name(code):
    """種類コードの通称（台帳 play/engine/data/players.json）。台帳に無ければ種類コード。"""
    reg = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engine', 'data', 'players.json'), encoding='utf-8'))
    for side in ('mastermind', 'protagonist'):
        for x in reg.get(side, []):
            if x.get('code') == code:
                return f"{x['name']}（{code}）"
    return code


def after_text(ro):
    """rollout の結末を 1 行に。"""
    if ro.get('error'):
        return '進められなかった: ' + ro['error']
    e = ro['end']
    if e and e['loss']:
        return f"{e['day']}日目の{e['phase']}に主人公の敗北（{e['reason']}）"
    if ro['loop_end_loss']:
        from ai_gm import ROLE_JA, RULE_JA
        return 'ループ終了時の判定で主人公の敗北（' + '・'.join(RULE_JA.get(x, ROLE_JA.get(x, str(x))) for x in ro['loop_end_loss']) + '）'
    if not ro.get('to_loop_end', True):
        return 'この日のターン終了まで、主人公の敗北は起きない'
    return '主人公がループを守りきった（主人公の勝ち）'


def game_after(gmdir, snap, loop):
    """実際の試合で、局面（盤面の記録 snap 件目）の後、そのループの終わりまでの公開ログ（主人公の問題の AI の手の「その後」）。"""
    snaps = [json.loads(x) for x in open(os.path.join(gmdir, 'snapshots.jsonl'), encoding='utf-8')]
    n0 = snaps[snap - 1]['log_n'] if 0 < snap <= len(snaps) else 0
    out = []
    for x in (json.loads(y) for y in open(os.path.join(gmdir, 'log.jsonl'), encoding='utf-8')):
        if x['n'] > n0 and x.get('loop') == loop:
            if x['text'].startswith('──'):  # フェイズの見出しは飛ばし、次のループの見出しで止める
                if 'ループの準備' in x['text']:
                    break
                continue
            if x['text'].startswith(('ゲーム終了', '非公開シート')):
                break
            out.append({'day': x.get('day'), 'text': x['text']})
            if x['text'].startswith('ループ終了時の判定'):  # ループの終わり（この後はゲーム終了・非公開シート）
                break
    return out


def ai_reason(r, evals, tname, card_ui):
    """AI の手の説明を、探索の出力（値・順位・読んだ応手）と、各選択肢を進めた結末から作る（Claude が理由を考えて書かない。ユーザー 2026-11-15）。"""
    ev = {e['key']: e for e in evals}
    ai = ev.get(','.join(sorted(r['ai'])), {})
    c = r.get('candidates') or []
    out = [f"{player_name(r.get('mm', 'calcGBxhdKNi'))} は {label(r['ai'])} を選んだ（探索の値 {ai.get('value')}、{ai.get('rank')} 位）。"]
    if ai.get('reply'):
        out.append(f"読んだ主人公の応手は「{ai['reply']}」。")
    if len(c) >= 2:
        nxt = c[1] if c[0]['cards'] == r['ai'] else c[0]
        out.append(f"いちばん近い候補は {label(nxt['cards'])}（値 {nxt['value']}、差 {round(abs(c[0]['value'] - nxt['value']), 3)}）。")
    if ai.get('after'):
        out.append(f"この手のままエンジン同士でループの終わりまで進めると: {ai['after']['summary']}。")
    return ''.join(out)


def export(picked, texts, outdir):
    """選んだ局面と、Claude が書いた問いの文・理由（texts: {局面の鍵: {...}}）から、問題ページ（problems.html）の JSON を書く。
    局面の鍵は「脚本の id:seed:L ループ D 日」（例 gen_s51_027:1:L3D5）。texts に無い局面は書かない（文を確かめていない問題は出さない）。"""
    from engine.project import AREA_JA
    from engine.resolve import CHARS, CARDS, ONCE
    os.makedirs(outdir, exist_ok=True)
    index = []
    for r in picked:
        key = f"{r['script'].split('/')[-1]}:{r['seed']}:L{r['loop']}D{r['day']}"
        t = texts.get(key) or auto_text(r, 'pc' if key.endswith(':pc') else 'mm')
        pid = key.replace(':', '_')
        s = r['state']
        rp = gm_replay(r, f"問題 {r['script'].split('/')[-1]} L{r['loop']}D{r['day']}") if r.get('gmdir') else replay_of(r)
        if r.get('gmdir') and rp['snapshots']:  # 盤面の帯を、前の日の終わりではなく問題の日の行動カード（脚本家）に
            last = copy.deepcopy(rp['snapshots'][-1])
            last['state'].update({'loop': r['loop'], 'day': r['day'], 'phaseName': f"L{r['loop']} {r['day']}日目 行動カード（脚本家）", 'cards': []})
            rp['snapshots'].append({'prev_n': last['log_n'], 'log_n': last['log_n'], 'state': last['state']})
        json.dump(rp, open(os.path.join(outdir, pid + '.replay.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
        from engine.resolve import card_targets  # 札を置ける対象（エンジンの決まり。幻想を除く生存キャラクターと4つのボード）
        targets = [{'id': t, 'name': AREA_JA[t[2:]] + 'のボード' if t.startswith('B:') else CHARS[t]['name']} for t in card_targets(s)]
        choices = [{'cards': c['cards'], 'label': label(c['cards'])} for c in r['candidates'][:3]]
        if r['ai'] not in [c['cards'] for c in choices]:
            choices.insert(0, {'cards': r['ai'], 'label': label(r['ai'])})
        random.Random(pid).shuffle(choices)  # AI の手を先頭に固定しない
        from ai_gm import INC_JA, ROLE_JA, RULE_JA
        sc = s['script']
        secret = {'rules': [RULE_JA.get(x, x) for x in sc['rules']],
                  'roles': [{'id': c, 'name': CHARS[c]['name'], 'role': ROLE_JA.get(v, v)} for c, v in sc['roles'].items()],
                  'incidents': [{'day': i['day'], 'name': INC_JA.get(i['id'], i['id']), 'culprit': CHARS[i['culprit']]['name']} for i in sc['incidents']]}
        k = r.get('known') or {}
        known = None
        if k.get('roles'):
            true = lambda c: sc['roles'].get(c, 'PERSON')  # noqa: E731
            rows = []
            for c, m in k['roles'].items():
                if c not in s['chars'] or not s['chars'][c].get('present', True):
                    continue
                top = sorted(m.items(), key=lambda x: -x[1])[:3]
                rows.append({'id': c, 'name': CHARS[c]['name'], 'true': ROLE_JA.get(true(c), true(c)), 'p_true': round(m.get(true(c), 0.0), 2),
                             'guess': [{'role': ROLE_JA.get(x, x), 'p': round(v, 2)} for x, v in top]})
            known = {'chars': sorted(rows, key=lambda x: -x['p_true']),
                     'rules': [{'rules': [RULE_JA.get(x, x) for x in cb], 'p': p, 'true': sorted(cb) == sorted(sc['rules'])} for cb, p in k.get('rules', [])],
                     'n_hyp': k.get('n_hyp')}
        losses = []  # これまでのループの負け方（公開）
        for blk in r.get('public_log', []):
            for e in blk['events']:
                if e.get('kind') == 'loop_end':
                    losses.append({'loop': blk.get('loop'), 'day': blk.get('day'), 'reason': e.get('reason'), 'phase': e.get('at_phase')})
        if r.get('gmdir'):  # 観戦モードで拾った局面: 盤面ログ（公開の文）の、ループの終わりとその日の死亡の行
            snaps = [json.loads(x) for x in open(os.path.join(r['gmdir'], 'snapshots.jsonl'), encoding='utf-8')][:r['snap']]
            last = snaps[-1]['log_n'] if snaps else 0
            lines = [json.loads(x) for x in open(os.path.join(r['gmdir'], 'log.jsonl'), encoding='utf-8')]
            lines = [x for x in lines if x['n'] <= last]
            for i, x in enumerate(lines):
                if (x.get('ev') or {}).get('kind') == 'loop_end' or x['text'].startswith('ループ終了時の判定: 主人公の敗北'):  # ループ終了時の判定の負けは出来事の印が無い
                    dead = [y['text'] for y in lines[max(0, i - 4):i] if (y.get('ev') or {}).get('kind') == 'death' and y['loop'] == x['loop'] and y['day'] == x['day']]
                    losses.append({'loop': x['loop'], 'day': x['day'], 'text': '／'.join(dead + [x['text']])})
        from engine.resolve import CHARS as _CH
        tname = lambda t: AREA_JA[t[2:]] + 'のボード' if t.startswith('B:') else _CH[t]['name']  # noqa: E731
        from engine.project import CARD_UI
        if r.get('gmdir') and os.path.exists(os.path.join(r['gmdir'], 'mm_observe.json')):
            obs_all = json.load(open(os.path.join(r['gmdir'], 'mm_observe.json'), encoding='utf-8'))
            json.dump({'script_id': r['script'], 'script_full': by_id(r['script']), 'mm': r.get('mm', 'calcGBxhdKNi'), 'seed': r['seed'], 'init': s.get('init'), 'state_script': s['script'],
                       'state': {k: v for k, v in s.items() if k not in ('script', 'init')}, 'obs': obs_all[:r['obs_n']]},
                      open(os.path.join(outdir, pid + '.engine.json'), 'w', encoding='utf-8'), ensure_ascii=False, default=str)
        # 選択肢（探索の上位と AI の手）は、分けておいた試合をエンジン同士でループの終わりまで進める（ユーザー 2026-11-15「その後どうなるかまでちゃんとエンジンを使って確かめて」）
        mmc = lambda cards: [{'by': 'M', 'target': x.rsplit(':', 1)[0], 'card': x.rsplit(':', 1)[1]} for x in cards]  # noqa: E731
        after = {}
        for c in choices:
            k_ = ','.join(sorted(c['cards']))
            if not r.get('fork'):
                after[k_] = {'error': '試合を分けた記録が無い'}
                continue
            try:
                after[k_] = rollout(r['fork'], s, mmc(c['cards']))
            except Exception as ex:
                after[k_] = {'error': repr(ex)[:80]}
            after[k_]['summary'] = after_text(after[k_])
            print(pid, label(c['cards']), '→', after[k_]['summary'], file=sys.stderr)
        evals = []
        for i, e in enumerate(r.get('evals', [])):
            k_ = ','.join(sorted(e['cards']))
            evals.append({'key': k_, 'after': after.get(k_), 'rank': i + 1, 'value': e['value'],
                          'reply': '・'.join(f"主人公{y['by']}が{tname(y['target'])}に{CARD_UI[y['card']]}" for y in e['reply']),
                          **effects(s, e['cards'], e['reply'])})
        json.dump({'id': pid, 'side': 'mm', 'title': t['title'], 'question': t['question'], 'notes': t.get('notes', []),
                   'evals': evals, 'ai_value': r['candidates'][0]['value'] if r.get('candidates') else None,
                   'secret': secret, 'known': known, 'losses': losses,
                   'replay': pid + '.replay.json', 'choices': choices, 'hand': hand, 'targets': targets,
                   'ai': {'cards': r['ai'], 'label': label(r['ai']), 'reason': ai_reason(r, evals, tname, CARD_UI)}},
                  open(os.path.join(outdir, pid + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        index.append({'id': pid, 'side': 'mm', 'title': t['title'], 'stage': stage_of(r)})
    json.dump(index, open(os.path.join(outdir, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('scripts', nargs='+')
    ap.add_argument('--mm', default='calcGBxhdKNi')
    ap.add_argument('--pc', default='plan_read2')
    ap.add_argument('--seeds', default='1-2')
    ap.add_argument('--out', required=True)
    ap.add_argument('--gm', action='store_true', help='対戦画面の進行役（ai_gm）の上で拾い、試合の流れのリプレイを残す（<out>/gm/<脚本>_s<seed>）')
    ap.add_argument('--export', default='', help='--out の picked.jsonl と --texts の文から、問題ページの JSON をこのフォルダに書く')
    ap.add_argument('--texts', default='', help='問いの文・理由（{局面の鍵: {title, question, notes, reason}}）')
    ap.add_argument('--pick-pc', type=int, default=0, help='主人公の問題の候補を n 個選び、<out>/picked_pc.jsonl に書く')
    ap.add_argument('--texts-pc', default='', help='--export のとき、主人公の問題の文（鍵は 脚本:seed:LxDy:pc）')
    ap.add_argument('--gap', type=float, default=0.3, help='--pick: 1 位と 2 位の値の差がこれ未満の局面')
    ap.add_argument('--pick', type=int, default=0, help='scripts の代わりに --out の拾った局面から問題の候補を n 個選び、<out>/picked.jsonl に書く')
    a = ap.parse_args()
    if a.pick_pc:
        ps = [r for st in STAGES for r in pick_pc(a.out, a.pick_pc, stage=st)]  # 段階ごとに n 問ずつ
        with open(os.path.join(a.out, 'picked_pc.jsonl'), 'w', encoding='utf-8') as f:
            for r in ps:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + '\n')
        for r in ps:
            print(r['script'], r['seed'], f"L{r['loop']}D{r['day']}", 'AI', [(x['by'], x['target'], x['card']) for x in r['ai']],
                  '脚本家', [(x['target'], x['card']) for x in r['mm_actual']], '結果', [(x['left'], x['route']) for x in r['ai_outcome']['routes']][:2])
        return
    if a.export:
        picked = [json.loads(x) for x in open(os.path.join(a.out, 'picked.jsonl'), encoding='utf-8')]
        idx = export(picked, json.load(open(a.texts, encoding='utf-8')) if a.texts else {}, a.export)
        pp = os.path.join(a.out, 'picked_pc.jsonl')
        if os.path.exists(pp):
            idx = export_pc([json.loads(x) for x in open(pp, encoding='utf-8')], json.load(open(a.texts_pc, encoding='utf-8')) if a.texts_pc else {}, a.export, idx)
            json.dump(idx, open(os.path.join(a.export, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('書き出した', len(idx), '問 →', a.export)
        return
    if a.pick:
        ps = [r for st in STAGES for r in pick(a.out, a.pick, gap=a.gap, stage=st)]  # 段階ごとに n 問ずつ
        with open(os.path.join(a.out, 'picked.jsonl'), 'w', encoding='utf-8') as f:
            for r in ps:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        for r in ps:
            print(r['script'], r['seed'], f"L{r['loop']}D{r['day']}", 'gap', r['gap'], 'AI', r['ai'], '2位', r['candidates'][1]['cards'])
        return
    lo, hi = (int(x) for x in a.seeds.split('-'))
    os.makedirs(a.out, exist_ok=True)
    for sid in a.scripts:
        script = by_id(sid)
        for seed in range(lo, hi + 1):
            recs = record_gm(sid, a.mm, a.pc, seed, os.path.join(a.out, 'gm', f"{script['id']}_s{seed}")) if a.gm else record(script, a.mm, a.pc, seed)
            path = os.path.join(a.out, f"{script['id']}_s{seed}.jsonl")
            with open(path, 'w', encoding='utf-8') as f:
                for r in recs:
                    f.write(json.dumps(dict(r, script=sid, seed=seed, mm=a.mm, pc=a.pc), ensure_ascii=False, default=str) + '\n')
            print(sid, seed, '局面', len(recs), '迷った', sum(r['troubled'] for r in recs))


if __name__ == '__main__':
    main()
