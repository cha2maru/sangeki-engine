"""よい脚本の定義（wiki/concepts/good-script.md）のうち、脚本の JSON とエンジンだけで測れる項目の採点（0〜2）。
対戦データが要る項目（3 CS・5 報酬論理・7 測定）はここでは測らない（script_difficulty・対戦表の by_loop を使う）。

  1b タブーの②: 役職・犯人・ルールXを知る友好能力の持ち主が登場し、友好無視の役職でない人数（2人以上で2点）
  2  パワープレイ: 勝ち筋の厚み spread と要の数 n_via（script_routes）
  6  要素の価値: 効かない事件・ルールYの割合（下の表で判定）
  8  ギミック: 登場の時期（神格はループ2以降・転校生は2日目以降）と、特性を持つ人物の特性が効いているか
  loops: 脚本家の書 p48 の目安とループ数の差（1を超えると主人公に甘い・厳しい）

評価の案はサブエージェントのレビュー（2026-09-27、plan/engine-smarter.md）から。 cd play && python -m selfplay.script_eval [脚本id ...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.scripts import by_id, loops_estimate  # noqa: E402

# 情報を返す友好能力（engine/abilities.py の reveal_role・reveal_culprit・reveal_rule_x）の持ち主
INFO = {'C04', 'C05', 'C06', 'C07', 'C11', 'C13', 'C16', 'C21', 'C23', 'C29'}
IGNORE_GW = {'KILLER', 'KUROMAKU', 'FACTOR', 'CULTIST', 'WITCH'}  # 友好無視・絶対友好無視（role-abilities.md）
VICTIM_ROLES = {'KEY', 'FRIEND', 'LOVERS', 'MAIN_LOVERS'}


def _dead_incidents(sc):
    """負け筋にも推理の足場にもならない事件（ponytail: 表で判定。流布・行方不明・不安拡大は補給役として生かす）。"""
    roles = set(sc['roles'].values())
    # ファクターは都市に暗躍2以上でキーパーソンの能力を得る＝死ねば負ける人物になる（d13 の設計者の指摘）
    victims = bool(roles & VICTIM_ROLES) or 'Y_MURDER' in sc['rules'] or 'Y_CONTRACT' in sc['rules'] or 'X_FACTOR' in sc['rules']
    # 手先（C18）の初期エリアは脚本家が毎ループ選ぶ（神社にも置ける）ので、手先のウィッチは神社を狙いうる（d21 の設計者の指摘）
    witch_shr = any(r == 'WITCH' and (sc.get('init', {}).get(c) == 'SHR' or c == 'C18') for c, r in sc['roles'].items())
    dead = []
    for i in sc['incidents']:
        iid = i['id']
        if i['culprit'] == 'C26':  # 黒猫が犯人の事件は「何も起きない」（見せかけとしての価値は測らない）
            dead.append(iid)
        elif iid == 'CORRUPT' and not ('Y_SEAL' in sc['rules'] or witch_shr):
            dead.append(iid)
        elif iid == 'BUTTERFLY' and 'Y_FUTURE' not in sc['rules'] and 'MAIN_LOVERS' not in roles and 'Y_CONTRACT' not in sc['rules']:
            dead.append(iid)
        elif iid in ('MURDER', 'SUICIDE', 'REMOTE') and not victims:
            dead.append(iid)
    return dead


def _trait_live(sc):
    """特性を持つ人物ごとに、特性が効いているか（good-script.md 8 の表）。"""
    ch, roles = sc['characters'], sc['roles']
    culprits = {i['culprit'] for i in sc['incidents']}
    witch_shr = any(r == 'WITCH' and (sc.get('init', {}).get(c) == 'SHR' or c == 'C18') for c, r in roles.items())
    live = {
        'C26': 'Y_SEAL' in sc['rules'] or witch_shr,  # 黒猫: ループ開始時に神社に暗躍1
        'C29': 'C29' in culprits,  # 教祖: 犯人の事件を2回解決
        'C34': 'C03' in ch or 'C16' in ch,  # 従者: お嬢様か大物に付いて動く
        'C16': any(sc.get('init', {}).get(c) == sc.get('territory') for c in roles),  # 大物: テリトリーに役職持ち
        'C18': 'C18' in roles or 'C18' in culprits,  # 手先: 初期エリアを脚本家が選ぶ
        'C19': roles.get('C19') in ('KEY', 'KILLER', 'MAIN_LOVERS', 'LOVERS', 'TT') or 'C19' in culprits,  # 学者
        'C22': 'C22' in culprits or 'C22' in roles,  # A.I.
        'C13': sc.get('appear', {}).get('C13', {}).get('loop', 1) >= 2 and ('C13' in roles or 'C13' in culprits or True),
        'C24': sc.get('appear', {}).get('C24', {}).get('day', 1) >= 2 and ('C24' in roles or 'C24' in culprits),
        'C32': 'C32' in culprits or 'C33' in ch,  # アルバイト: 配役を無視、死ぬとアルバイト？
        'C20': 'C20' in roles or 'C20' in culprits,  # 幻想: 札を置けず、同じエリアのボードの札が効く
        'C30': 'C30' in roles or 'C30' in culprits,  # ご神木: カウンターを移せる（友好無視なら脚本家も強制で使う）
        'C31': 'C31' in culprits or any(r in ('KEY', 'FRIEND', 'LOVERS', 'MAIN_LOVERS', 'TT') for r in roles.values()),  # 妹: 大人の能力の代理
        'C27': 'C27' in roles or 'C27' in culprits,  # 女の子: 学校から出られない（友好1で外せる）
    }
    return {c: v for c, v in live.items() if c in ch}


def evaluate(sid):
    from selfplay.script_routes import route_richness
    sc = by_id(sid)
    sc['characters'] = list(sc['init'])  # to_game の形は登場人物を init（初期エリア）で持つ
    out = {'id': sid}
    info = [c for c in sc['characters'] if c in INFO and sc['roles'].get(c) not in IGNORE_GW]
    out['1b'] = 2 if len(info) >= 2 else len(info)
    try:
        rr = route_richness(sc)
    except Exception:
        rr = {'spread': 0, 'n_via': 0}
    out['spread'] = rr['spread']
    out['2'] = 2 if rr['spread'] >= 0.8 and rr['n_via'] >= 3 else 1 if rr['spread'] >= 0.6 else 0
    dead = _dead_incidents(sc)
    y_dead = 'Y_FUTURE' in sc['rules'] and not any(i['id'] == 'BUTTERFLY' for i in sc['incidents'])
    frac = (len(dead) + y_dead) / (len(sc['incidents']) + 1)
    out['dead'] = dead + (['Y_FUTURE'] if y_dead else [])
    out['6'] = 2 if frac <= 0.15 else 1 if frac <= 0.35 else 0
    tl = _trait_live(sc)
    out['traits'] = tl
    out['8'] = 1 if not tl else 2 if sum(tl.values()) / len(tl) >= 0.75 else 1 if any(tl.values()) else 0
    out['loops_gap'] = round(sc['loops'] - loops_estimate(sc), 2)
    out['total'] = out['1b'] + out['2'] + out['6'] + out['8']
    return out


if __name__ == '__main__':
    ids = sys.argv[1:] or ['designed/d01_seal', 'designed/d02_traits', 'designed/d03_future']
    for sid in ids:
        r = evaluate(sid)
        print(f"{sid:24} 計{r['total']}/8  1b={r['1b']} 2={r['2']}(spread {r['spread']}) 6={r['6']} 8={r['8']}  ループ差{r['loops_gap']:+}  "
              f"効かない:{','.join(r['dead']) or 'なし'}  特性:{' '.join(c + ('○' if v else '×') for c, v in r['traits'].items())}")
