"""対戦エンジンの窓口（env.Env、plan/engine-api.md 段A）。"""
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from env import Env  # noqa: E402
from engine.scripts import glob_scripts  # noqa: E402

SECRET = {'KEY', 'KILLER', 'KUROMAKU', 'CULTIST', 'TT', 'WITCH', 'FRIEND', 'MISLEADER', 'LOVERS', 'MAIN_LOVERS', 'SK', 'FACTOR'}


def play(env, seed, pick=None):
    rnd = random.Random(seed)
    d, kinds = env.reset(seed=seed), set()
    while d['kind'] not in ('end', 'error'):
        kinds.add((d['side'], d['kind']))
        if d['side'] == 'pc':  # 主人公に見える盤面に、役職の名前（役職のコード）が出ない
            assert not SECRET & set(json.dumps(d['view']).replace('"', ' ').split()), d['kind']
        d = env.step((pick or rnd.choice)(d['options']))
    return d, kinds


def test_env_random_games_end():
    for sid in ('s01_seal', 's03_bomb'):
        d, kinds = play(Env(sid), 1)
        assert d['kind'] == 'end', d
        assert {('pc', 'pc_card'), ('mm', 'mm_card')} <= kinds


@pytest.mark.skipif(not glob_scripts('designed/*.json'), reason='sangeki-scripts が無い')
def test_env_all_designed_scripts():
    for p in glob_scripts('designed/*.json'):
        sid = 'designed/' + os.path.basename(p)[:-5]
        d, _ = play(Env(sid), 3)
        assert d['kind'] == 'end', (sid, d)


def test_env_one_side_automatic():
    """脚本家を自動（route）にして、主人公だけを外から。最後の戦いの判断も通る。"""
    d, kinds = play(Env('s03_bomb', mm='route'), 2, pick=lambda o: o[-1] if o[0] is None else o[0])
    assert d['kind'] == 'end' and all(side == 'pc' for side, _ in kinds)


def test_env_replay_gives_the_same_game():
    """記録（env.record）を流し込むと、自動の側も考えずに同じ答えを返し、同じ試合になる。途中で切った記録なら、そこから先は普通に続く。"""
    rnd = random.Random(4)
    env = Env('s03_bomb', mm='route')
    d = env.reset(seed=4)
    while d['kind'] != 'end':
        d = env.step(rnd.choice(d['options']))
    rec, result = list(env.record), d['result']
    env2 = Env('s03_bomb', mm='route', replay=rec)
    d2 = env2.reset(seed=4)
    assert d2['kind'] == 'end' and d2['result'] == result and env2.record == rec
    env3 = Env('s03_bomb', mm='route', replay=rec[:len(rec) // 2])
    d3 = env3.reset(seed=4)
    assert d3['kind'] not in ('end', 'error') and d3['side'] == 'pc'


def test_env_stdio_protocol():
    """--stdio: 1行1判断の JSON。番号でも手そのものでも答えられ、最後に end が来る。"""
    import subprocess
    p = subprocess.Popen([sys.executable, 'env.py', '--stdio', '--script', 's03_bomb', '--mm', 'route'], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    n = 0
    for line in p.stdout:
        d = json.loads(line)
        if d['kind'] in ('end', 'error'):
            break
        assert d['side'] == 'pc' and 'sheet' in d['view']
        ans = None if d['kind'] == 'final_guess' else (n % len(d['options']) if n % 2 else d['options'][-1])
        p.stdin.write(json.dumps(ans) + '\n')
        p.stdin.flush()
        n += 1
    p.stdin.close()
    p.wait(timeout=60)
    assert d['kind'] == 'end', d
