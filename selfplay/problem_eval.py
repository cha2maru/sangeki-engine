"""次の一手問題で、回答者の手をその場で AI に評価させる（ユーザー 2026-11-14「その場での評価をできるように」）。ブラウザ版は Pyodide の Worker から呼ぶ。

問題の書き出し（make_problems --export）の <id>.engine.json に、脚本の id・脚本家の型・その局面までの脚本家の観測（盤面つき）が入っている。
観測を流し直して脚本家（公開の推理と主人公の模型）を作り直し、回答者の手と AI の手を同じ物差しで評価する:
  - 主人公の模型が読む応手と、その応手の下の値・応手なし（主人公が止め損なう）の値
  - 人間が模型のように止める確率 q で混ぜた値（探索の評価 rank_moves と同じ混ぜ方。探索のほかの項は入れない）
  - 縮む負け筋（make_problems.effects）
ponytail: 脚本家の自分の手の記憶（止められた回数など）は作り直さない。値は探索の値と少しずれる。

    cd play && python -m selfplay.problem_eval web/problems/<id>.engine.json C14:MV_D,C21:GWX,C27:INT1 [AI の手]
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CACHE = {}


def build(eng):
    """観測を流し直して、局面での脚本家を作る（同じ問題は使い回す）。"""
    key = (eng['script_id'], eng['seed'], len(eng['obs']))
    if key in _CACHE:
        return _CACHE[key]
    from engine.scripts import by_id
    from selfplay.league import make
    script = eng.get('script_full') or by_id(eng['script_id'])  # 公開版のブラウザは脚本の束を持たないので、問題に入れた脚本を使う
    mm = make(eng['mm'], random.Random(eng['seed']), script)
    for st, phase, events in eng['obs']:
        mm.observe(dict(st, script=eng['state_script'], init=eng.get('init')), phase, events)
    s = dict(eng['state'], script=eng['state_script'], init=eng.get('init'))
    mm._start_loop(s)
    _CACHE[key] = (mm, s)
    return mm, s


def _cards(key):
    return [{'by': 'M', 'target': x.rsplit(':', 1)[0], 'card': x.rsplit(':', 1)[1]} for x in key.split(',') if x]


def evaluate_many(eng, keys):
    """札の組（'C14:MV_D,C21:GWX,C27:INT1' の形）を、探索（rank_moves）の候補に差し込んで評価する。値は探索と同じ物差しで、
    順位はそのときの候補全体の中での順位。読んだ主人公の応手と、縮む負け筋（主人公が何もしないとき・応手の後）も出す。"""
    from engine.resolve import IllegalPlacement, resolve_actions
    from selfplay.make_problems import _cards as ck, effects
    from selfplay.players import DUMMY_PC
    mm, s = build(eng)
    out, bonus = {}, {}
    for key in keys:
        cards = _cards(key)
        try:
            resolve_actions(s, cards + DUMMY_PC)
        except IllegalPlacement as e:
            out[key] = {'key': key, 'error': f'置けない手です: {e}'}
            continue
        bonus[tuple((x['target'], x['card']) for x in cards)] = cards
    mm._gbonus, mm._gp, mm._reply_of = dict(bonus), {}, {}  # 賭けの手と同じ入れ方で候補に入れる（missed.py と同じ）
    scored = sorted(mm.rank_moves(s), key=lambda x: -x[0])
    seen, uniq = set(), []
    for x in scored:
        k = ','.join(sorted(ck(x[1])))
        if k not in seen:
            seen.add(k)
            uniq.append((k, x))
    for rank, (k, x) in enumerate(uniq, 1):
        if k in keys and k not in out:
            reply = mm._reply_of.get(id(x[1]), [])
            out[k] = {'key': k, 'rank': rank, 'of': len(uniq), 'value': round(x[0], 3),
                      'reply': [{'by': y['by'], 'target': y['target'], 'card': y['card']} for y in reply],
                      **effects(s, sorted(k.split(',')), reply)}
    for key in keys:
        out.setdefault(key, {'key': key, 'error': '探索の候補に入らなかった'})
    return out


def evaluate_pc(eng, mine):
    """主人公の問題: 回答者の札（'A:C05:PAR-,B:B:SHR:INTX,C:C13:GW1' の形。主人公:置き先:札）を、脚本家の実際の札に当てた結果。"""
    from selfplay.make_problems import pc_outcome
    reply = []
    for x in mine.split(','):
        by, rest = x.split(':', 1)
        t, c = rest.rsplit(':', 1)
        reply.append({'by': by, 'target': t, 'card': c})
    s = dict(eng['state'], script=eng['state_script'], init=eng.get('init'))
    return pc_outcome(s, eng['mm_actual'], reply)


def compare(eng, mine, ai):
    """回答者の手と AI の手を同じ物差しで（同じ 1 回の探索の中で）。"""
    r = evaluate_many(eng, [mine, ai])
    return {'mine': r[mine], 'ai': r[ai]}


if __name__ == '__main__':
    eng = json.load(open(sys.argv[1], encoding='utf-8'))
    out = compare(eng, sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else evaluate_many(eng, [sys.argv[2]])
    print(json.dumps(out, ensure_ascii=False, indent=1))
