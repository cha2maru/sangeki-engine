"""見本エージェント（Python、同じプロセスで Env を使う）。主人公を外から操作し、選べる手から無作為に選ぶ。docs/env.md

    python agents/random_agent.py [--script s03_bomb] [--mm calcD] [--seed 1]
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from env import Env  # noqa: E402


def choose(d, rng):
    """判断 d に答える。ここを書き換えて自分のエージェントにする。"""
    if d['kind'] == 'final_guess':
        return None  # 指摘しない（残りは全員「パーソン」と指摘したことになる）
    return rng.choice(d['options'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--script', default='s03_bomb')
    ap.add_argument('--mm', default='calcD')
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    env = Env(a.script, mm=a.mm)
    d = env.reset(seed=a.seed)
    while d['kind'] not in ('end', 'error'):
        for line in d['view']['log']:
            print(line)
        d = env.step(choose(d, rng))
    print(d['kind'], d.get('result') or d.get('error'))
    env.close()


if __name__ == '__main__':
    main()
