"""手動モードの形式（play/game/state.json・inbox.jsonl・private/cards_*.txt）とエンジンの形式の相互変換。
手動モードはこのモジュールに依存しない。gm.py が関数の中でだけ import する。"""
from .project import AREA_JA, CARD_UI
from .resolve import CHARS

NAME2C = {v['name']: c for c, v in CHARS.items()}
JA2AREA = {v: k for k, v in AREA_JA.items()}
UI2CARD = {v: k for k, v in CARD_UI.items()}
UI2CARD['不安−1'] = 'PAR-'  # 表記ゆれ
USED2CARD = dict(UI2CARD)


def full_from_public(pub, script):
    """公開用 state.json と非公開シート（script: rules・roles・incidents）から、エンジンの完全な状態を作る。"""
    chars = {NAME2C[c['name']]: {'area': JA2AREA[c['area']], 'alive': c.get('alive', True), 'par': c.get('paranoia', 0),
                                 'gw': c.get('goodwill', 0), 'int': c.get('intrigue', 0), 'guard': c.get('guard', 0)}
             for c in pub['characters']}
    return {'loop': pub['loop'], 'day': pub['day'], 'leader': pub['leader'], 'chars': chars,
            'boards': {JA2AREA[a]: b.get('intrigue', 0) for a, b in pub['boards'].items()},
            'used': {p: [USED2CARD[x] for x in pub.get('used', {}).get(p, [])] for p in 'MABC'},
            'script': script}


def target_from_ui(t):
    return 'B:' + JA2AREA[t[6:]] if t.startswith('board:') else NAME2C[t]


def placements_from_inbox(rec):
    """inbox の type=cards を主人公の placements に。"""
    return [{'by': p['actor'], 'target': target_from_ui(p['target']), 'card': UI2CARD[p['card']]} for p in rec['placements']]


def placements_from_cards_file(text):
    """private/cards_L?D?.txt（'L1D1 T=C03 K=INT1'、nonce 行あり）を脚本家の placements に。"""
    out = []
    for line in text.splitlines():
        if not line.startswith('L'):
            continue
        t = line.split('T=')[1].split()[0]
        k = line.split('K=')[1].split()[0]
        out.append({'by': 'M', 'target': t, 'card': k})
    return out
