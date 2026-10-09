"""犯人の推理の健全性: 自己対戦で集めた公開の事件の記録から絞った候補（deduce.incident_candidates）に、本当の犯人が必ず残る。
事件の効果の場所による絞り込み（PublicObserver._culprit_where）を含めて、9本の試験用の脚本で確かめる。"""
import os
import random
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine.deduce import incident_candidates  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402

SCRIPTS = ['toukou_001', 's01_seal', 's02_contract', 's03_bomb', 's04_circle', 's05_future', 's06_school', 's07_shrine', 's08_misfits', 'blind_001']
from engine.scripts import find  # noqa: E402
SCRIPTS = [s for s in SCRIPTS if find(f'{s}.json')]  # 公開版に無い脚本（toukou_001）は外す


def test_true_culprits_survive():
    for sid in SCRIPTS:
        sc = by_id(sid)
        for g in range(1):
            pc = make('read', random.Random(f'{sid}:{g}:pc'), sc)
            play_game(sc, Combo(make('route', random.Random(f'{sid}:{g}:mm'), sc), pc), lambda *a: None)
            cand = incident_candidates(list(pc.ded.chars), pc.inc_history, pc.culprits)
            for inc in sc['incidents']:
                key = f"{inc['id']}@{inc['day']}"
                if key in cand:
                    assert inc['culprit'] in cand[key], (sid, g, inc, cand[key])
