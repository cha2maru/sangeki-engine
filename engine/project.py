"""完全な状態 → 公開用 state.json（play/board.html が読む形）への射影。

完全な状態には役職・ルール・犯人・脚本家の伏せ札の中身が含まれる。公開側に出してよいのは
盤上の事実（位置・カウンター・生死）、使用済みの1ループ1回の札、公開シートの事件予定だけ。
（計画 plan/engine-and-selfplay.md「レビューを受けた修正」未決2の結論）
"""
from .resolve import CHARS

AREA_JA = {'HOS': '病院', 'SHR': '神社', 'CIT': '都市', 'SCH': '学校'}
INC_JA = {'MURDER': '殺人事件', 'SPREAD': '不安拡大', 'CORRUPT': '邪気の汚染', 'SUICIDE': '自殺',
          'HOSPITAL': '病院の事件', 'REMOTE': '遠隔殺人', 'MISSING': '行方不明', 'RUMOR_SPREAD': '流布', 'BUTTERFLY': '蝶の羽ばたき'}
# board.html の札名（PCARDS / MCARDS の表記）
CARD_UI = {'PAR+': '不安+1', 'PAR-': '不安-1', 'GW1': '友好+1', 'GW2': '友好+2', 'INTX': '暗躍禁止', 'MV_V': '移動↑↓',
           'MV_H': '移動←→', 'MVX': '移動禁止', 'PARX': '不安禁止', 'GWX': '友好禁止', 'INT1': '暗躍+1', 'INT2': '暗躍+2', 'MV_D': '移動斜め'}


def _target_ui(t):
    return 'board:' + AREA_JA[t[2:]] if t.startswith('B:') else CHARS[t]['name']


def project(full, placed=None, revealed=False):
    """placed: 盤上の札（行動解決の前後）。revealed=False なら脚本家の札の中身を伏せる。"""
    return {
        'loop': full['loop'], 'day': full['day'], 'leader': full['leader'],
        'characters': [{
            'name': CHARS[c]['name'], 'card': c[1:], 'limit': CHARS[c]['limit'], 'area': AREA_JA[v['area']],
            'alive': v['alive'], 'paranoia': v['par'], 'goodwill': v['gw'], 'intrigue': v['int'], 'guard': v['guard'],
        } for c, v in full['chars'].items() if v.get('present', True) and v.get('area')],  # 登場前（神格・転校生など）は盤面に出さない
        'boards': {AREA_JA[a]: {'intrigue': n} for a, n in full['boards'].items()},
        'used': {p: [CARD_UI[c] for c in full['used'][p]] for p in 'MABC'},
        # 公開シートに載る事件予定（名前と日付）だけ。犯人は出さない
        'incidents': [{'day': i['day'], 'name': INC_JA[i['id']]} for i in full['script']['incidents']],
        'cards': [{'owner': p['by'], 'target': _target_ui(p['target']),
                   'card': CARD_UI[p['card']] if (revealed or p['by'] != 'M' or p.get('open')) else None} for p in (placed or [])],
    }
