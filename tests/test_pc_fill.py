"""伏せ札の中身の見積もり（ReadingBlocker._fill）が評価に基づいて引いていること。
以前は1枚ずつ解決して毎回反則（脚本家の札は3枚）になり、中身が常に無作為だった。"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.scripts import by_id  # noqa: E402
from selfplay.pc_v2 import OracleReader  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402
from selfplay.league import make  # noqa: E402


class _Stop(Exception):
    pass


def test_fill_prefers_intrigue_on_witch_area():
    sc = by_id('s03_bomb')
    box = {}
    pc = make('blind', random.Random(1), sc)

    def grab(s, order, t=None):
        box['s'] = s
        raise _Stop
    pc.pc_cards = grab
    try:
        play_game(sc, Combo(make('route', random.Random(1), sc), pc), lambda *a: None)
    except _Stop:
        pass
    s = box['s']
    area = s['chars']['C04']['area']  # 1日目の始めなのでウィッチ C04 は初期エリアにいる
    r = OracleReader(random.Random(0))
    hand = ['PAR+', 'PAR-', 'PARX', 'GWX', 'INT1', 'INT2', 'MV_V', 'MV_H', 'MV_D']
    others = [c for c in ('C01', 'C05', 'C07') if s['chars'][c]['alive']][:2]
    hits = sum(any(x['target'] == f'B:{area}' and x['card'].startswith('INT')
                   for x in r._fill(s, s['script'], others + [f'B:{area}'], hand)) for _ in range(20))
    assert hits >= 8  # 無作為なら 2/9 程度（約4回）。修正後は 10 回
