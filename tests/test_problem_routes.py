"""次の一手問題の負け筋の数え（make_problems.pc_outcome）が、1 日を進めた結果と食い違わないこと。"""
import json
import os

from selfplay.problem_eval import evaluate_pc

HERE = os.path.dirname(os.path.abspath(__file__))


def test_cultist_ignore_and_morning_zero_route():
    # gen_s51_023 L2D4: 病院の事件（巫女・不安2）・病院の暗躍1。主人公が病院に暗躍禁止を置いても、カルティストが無視して暗躍2 → 主人公死亡
    eng = json.load(open(os.path.join(HERE, 'data', 'problem_023_L2D4_pc.engine.json'), encoding='utf-8'))
    o = evaluate_pc(eng, 'A:B:HOS:INTX,B:B:SHR:GW1,C:B:CIT:GW1')
    assert o['lethal']
    assert any(r['left'] == 0 and '主人公死亡' in r['route'] for r in o['routes'])
    assert '主人公死亡' in o['today']['summary']


def test_route_mastermind_ability_skips_absent_actor():
    # 登場前（エリアなし）の人物には脚本家の能力を使わせない（gen_s51_017 で pc_v2 の先読みが TypeError で落ちた）
    from selfplay.players import RouteMastermind
    s = {'chars': {'C24': {'area': None, 'present': False, 'alive': True, 'par': 0, 'gw': 0, 'int': 0}}, 'boards': {}}
    assert RouteMastermind.mm_ability(None, s, 'C24', 'KUROMAKU') is None
