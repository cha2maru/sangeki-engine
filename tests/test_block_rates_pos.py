"""主人公の側の応手表（block_rates の pos・abil）: 脚本家に有利なこと（斜めの移動・クロマクの補給）を数える。"""
import json
import os

from engine import route_calc as rc
from engine.scripts import by_id

HERE = os.path.dirname(__file__)


def test_d29_l1d2_killer_cannot_target_factor():
    """d29 L1D2（自動同士、oread）。以前は「斜めの移動でキラーをファクターの所へ＋都市に暗躍+1 で、キラーの筋が確定」とみていたが、
    キラーが殺せるのは役職がキーパーソンの人物だけ（ファクターはキーパーソンの能力を得ても役職は変わらない。wiki role-abilities、進行の
    phases._keys_in_reach）。負け筋の列挙はファクターをキラーの的に数えない（2026-11-15、問題 gen_s51_059 で発見）。"""
    from engine.routes import enumerate_routes
    c = json.load(open(os.path.join(HERE, 'cases', 'block_rates_d29_l1d2.json'), encoding='utf-8'))
    sc = by_id(c['script'])
    st = dict(c['state'], script=sc, loop=c['loop'], day=c['day'], leader='A', incident_log=[], ability_used_loop=[],
              ability_used_today=[], revealed_roles={})
    fac = [x for x, r in sc['roles'].items() if r == 'FACTOR']
    assert fac and not any(r['id'] == 'KILLER_KEY' and fac[0] in r['goal'] for r in enumerate_routes(st))
    assert any(r['id'] == 'KILLER_KEY' for r in enumerate_routes(st))  # 本物のキーパーソンを殺す筋は残る
