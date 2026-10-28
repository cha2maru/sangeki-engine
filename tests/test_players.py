"""名前の台帳（engine/data/players.json、規則は plan/naming.md）と、画面の選択肢・league の種類コードが合っているか。"""
import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_registry_matches_board_and_server():
    reg = json.load(open(os.path.join(HERE, 'engine', 'data', 'players.json'), encoding='utf-8'))
    mm = {x['code']: x for x in reg['mastermind']}
    for side in ('mastermind', 'protagonist'):
        names = [x['name'] for x in reg[side]]
        assert len(names) == len(set(names)), side  # 陣営の中で通称は重複させない
        for x in reg[side]:
            major = int(x['official'].split('.')[2])
            assert major == 0 or x['name'][0] == chr(64 + major), x  # 頭文字＝メジャー版
    board = open(os.path.join(HERE, 'board.html'), encoding='utf-8').read()
    server = open(os.path.join(HERE, 'server.mjs'), encoding='utf-8').read()
    mms = re.search(r"const MMS = \[(.*?)\]", server).group(1).replace("'", '').replace(' ', '').split(',')
    for code in mms:  # 開始画面で選べる脚本家は、台帳にあり、画面の表記が通称になっている
        assert code in mm, code
        assert f"{code}: '{mm[code]['name']}（" in board, code
