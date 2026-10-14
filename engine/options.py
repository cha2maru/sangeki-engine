"""いま選べる手の一覧（plan/engine-api.md 3章「合法な手はエンジンだけが出す」）。窓口（env.py）と対AIモードの進行役（ai_gm.py）が使う。

判断の種類ごとに、エンジンの規則（resolve・phases・abilities）から候補を作る。規則を書き写さずに済むものは、写しの盤面でエンジンに解かせて通るものだけ残す。
"""
import json
import os

from . import abilities as ab
from . import phases as ph
from .resolve import CARDS, CHARS, ONCE, IllegalPlacement, copy_state

BOARDS = ['B:' + a for a in ph.AREAS]
ROLES = list(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'roles.json'), encoding='utf-8'))['roles'])


def targets(s):
    """行動カードを置ける所: 盤上の生存キャラクター（幻想を除く＝カードの特性）と4つのボード。"""
    return [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True) and c != 'C20'] + BOARDS


def pc_card_options(s, p, chosen):
    """主人公 p の札1枚。chosen: その日に先に選んだ主人公の札（同じ対象に2人は置けない）。"""
    hand = [c for c in CARDS['protagonist_hand'] if not (c in ONCE[p] and c in s['used'][p])]
    taken = {x['target'] for x in chosen}
    return [{'by': p, 'target': t, 'card': c} for t in targets(s) if t not in taken for c in dict.fromkeys(hand)]


def pc_placement_error(s, pc):
    """主人公の3枚の置き方の反則（主人公の書 印刷p21 4-3・p22）。None なら合法。"""
    if sorted(x['by'] for x in pc) != ['A', 'B', 'C']:
        return '主人公A・B・Cが1枚ずつ置く（主人公の書 印刷p21 4-3）'
    if len({x['target'] for x in pc}) < 3:
        return '主人公どうしで同じ場所には置けない'
    ok = set(targets(s))
    for x in pc:
        if x['card'] not in CARDS['protagonist_hand']:
            return f'{x["card"]} は主人公の札ではない'
        if x['card'] in ONCE[x['by']] and x['card'] in s['used'][x['by']]:
            return f'{x["by"]} の札 {x["card"]} はこのループで使用済み（1ループ1回）'
        if x['target'] not in ok:
            return f'{x["target"]} には置けない（盤上の生存キャラクターかボードだけ。幻想には置けない）'
    return None


def mm_card_options(s, chosen):
    """脚本家の札1枚。chosen: その日に先に選んだ札（同じ対象に2枚は置けない、手札の枚数）。"""
    hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
    for x in chosen:
        hand.remove(x['card'])
    taken = {x['target'] for x in chosen}
    return [{'by': 'M', 'target': t, 'card': c} for t in targets(s) if t not in taken for c in dict.fromkeys(hand)]


def declarations(s):
    """主人公能力フェイズの宣言 (人物, 番号, 引数)。"""
    return [list(x) for x in ab.declaration_options(s)]


def final_guess_options(s, chosen):
    """最後の戦いの指摘1件（人物, 役職）。chosen: 先に指摘した分。"""
    left = [c for c in s['chars'] if c not in {g['char'] for g in chosen}]
    return [{'char': c, 'role': r} for c in left for r in ROLES]


def mm_ability_options(s, actor, ability=None):
    """脚本家能力（クロマク・ミスリーダーなど）の対象。None は使わない。"""
    out = [None]
    for t in [c for a in ph.mm_areas(s, actor) for c in ph.alive_in(s, a)] + ['B:' + a for a in ph.mm_areas(s, actor)]:
        try:
            ph.mm_ability(copy_state(s), actor, t, ability)
            out.append(t)
        except (IllegalPlacement, KeyError):
            pass
    return list(dict.fromkeys(out))


def incident_choice_options(s, inc):
    """事件の効果の対象（選ぶことの無い事件・候補の無い事件は None）。"""
    from selfplay.run import incident_options
    opts = incident_options(s, inc)
    if opts is None:
        cul = inc['culprit']
        alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
        if inc['id'] == 'MURDER':
            opts = [{'target': c} for c in ph.alive_in(s, s['chars'][cul]['area']) if c != cul]
        elif inc['id'] == 'SPREAD':
            opts = [{'par_target': p, 'int_target': i} for p in alive for i in alive if p != i]
        else:
            return None
    return opts or None


def killer_pick_options(s):
    o = ph.killer_options(s)
    return [[]] + [[x] for x in o] + ([o] if len(o) > 1 else [])


def cultist_pick_options(s, placements):
    from selfplay.players import RouteMastermind
    return [[]] + [[x] for x in RouteMastermind.cultist_options(None, s, placements)]


def loop_setup_options(s, script):
    inits = [{'C18': a} for a in ph.AREAS if a not in CHARS['C18'].get('forbidden', [])] if 'C18' in script['init'] else [{}]
    sch = ['gw', 'par', 'int'] if 'C19' in script['init'] else [None]
    return [{'init': i, **({'scholar': k} if k else {})} for i in inits for k in sch]


def rumor_options(s):
    return [None] + BOARDS


def extra_use_options(s):
    """医者[友好2] を脚本家が使う・ご神木の強制の特性。合法かは脚本家能力フェイズを写しで解いて決める。"""
    doc = []
    d = s['chars'].get('C08')
    if d and d['alive'] and d.get('present', True) and ph.role(s, 'C08') in ph.REFUSERS and d['gw'] >= 2:
        doc = [{'char': 'C08', 'ability': 'DOCTOR', 'target': t, 'mode': m} for t in ph.alive_in(s, d['area']) for m in ('place', 'remove')]
    tree = []
    if ph.tree_forced(s):
        tr = s['chars']['C30']
        tree = [{'char': 'C30', 'ability': 'SHINBOKU', 'target': t, 'counter': k}
                for t in ph.alive_in(s, tr['area']) if t != 'C30' for k in ('par', 'gw', 'int') if tr.get(k, 0) >= 1]
    opts = ([[t] for t in tree] + [[u, t] for u in doc for t in tree]) if tree else ([[]] + [[u] for u in doc])

    def ok(o):
        try:
            ph.mm_phase(copy_state(s), o)
            return True
        except (IllegalPlacement, KeyError):
            return False
    return [o for o in opts if ok(o)] or [[]]
