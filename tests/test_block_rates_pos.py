"""主人公の側の応手表（block_rates の pos・abil）: 脚本家に有利なこと（斜めの移動・クロマクの補給）を数える。"""
import json
import os

from engine import route_calc as rc
from engine.scripts import by_id

HERE = os.path.dirname(__file__)


def test_d29_l1d2_needs_intx_on_city():
    """d29 L1D2（自動同士、oread）: 斜めの移動でキラーをファクターの所へ＋都市に暗躍+1＋クロマクの補給で、翌日にキラーの筋が確定した。
    下限の表は「何もしなくても確定しない」、主人公の見積もりでは都市への暗躍禁止だけが最悪でも確定させない。"""
    c = json.load(open(os.path.join(HERE, 'cases', 'block_rates_d29_l1d2.json'), encoding='utf-8'))
    sc = by_id(c['script'])
    st = dict(c['state'], script=sc, loop=c['loop'], day=c['day'], leader='A', incident_log=[], ability_used_loop=[],
              ability_used_today=[], revealed_roles={})
    lo = rc.block_rates(st, sc['days'], c['mm_targets'])
    assert lo[(frozenset(), None)][0] == 0.0
    br = rc.block_rates(st, sc['days'], c['mm_targets'], pos=True, abil=True)
    assert br[(frozenset(), None)][0] == 1.0
    safe = {k for k, v in br.items() if v[0] == 0.0}
    assert safe and all(k[1] == 'B:CIT' for k in safe)
