"""脚本家の事件の対象選び（RouteMastermind.incident_choice）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.scripts import by_id  # noqa: E402
from selfplay.players import RouteMastermind  # noqa: E402
from selfplay.script_routes import start_state  # noqa: E402


def test_murder_kills_friend_over_person():
    """d12: 都市の殺人事件（犯人 C07）で、候補はパーソン C06・フレンド C14・ミスリーダー C15。
    フレンドの死はループ終了時の敗北なので C14 を選ぶ（以前は「キーパーソン、いなければ候補の先頭」で C06 を殺していた）。"""
    sc = by_id('designed/d12')
    s = start_state(sc)
    s['chars']['C07']['par'], s['day'] = 9, 3
    inc = next(i for i in sc['incidents'] if i['id'] == 'MURDER')
    assert RouteMastermind.incident_choice(None, s, inc) == {'target': 'C14'}


def test_cult_leader_remote_kills_lovers_not_main_lovers():
    """教祖が犯人の事件は効果を2回解決する。1回目の遠隔殺人でメインラバーズを殺すと、ラバーズの死→不安6→主人公死亡の筋が消える。
    評価で run_incident に1つの選択を渡すと、同じ対象を2回殺そうとして反則になり、候補の先頭（メインラバーズ）が選ばれていた
    （d09 L2D7、calcGBxhdK 対 read2 の seed 2 でループを落とした 2026-10-23）。"""
    import random
    import pytest
    from engine.scripts import by_id, find
    from selfplay.players import RouteMastermind
    from selfplay.script_routes import start_state
    if find('designed/d09.json') is None:
        pytest.skip('設計した脚本（sangeki-scripts）が無い')
    sc = by_id('designed/d09')
    s = start_state(sc)
    s['day'] = 7
    s['chars']['C29']['par'] = 3  # 教祖（犯人）が臨界
    s['chars']['C02']['int'] = 2  # 女子学生（メインラバーズ）
    s['chars']['C10']['int'] = 2  # 委員長（ラバーズ）
    inc = next(i for i in sc['incidents'] if i['id'] == 'REMOTE')
    ch = RouteMastermind(random.Random(0)).incident_choice(s, inc)
    assert ch == {'target': 'C10'}, ch
