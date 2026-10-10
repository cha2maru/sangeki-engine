"""自動の主人公で対AIモード（ai_gm.run）の1試合を最後まで進め、盤面のリプレイ JSON（board.html の RP.collect と同じ形）を書く。
リプレイの見本（sangeki-scripts の replays/）を作るのに使う。
  python -m selfplay.auto_replay --script designed/d16 --mm calcG --pc readlook --seed 1 --out replays/sample_d16_calcG.json"""
import argparse
import json
import os
import tempfile
import time
from types import SimpleNamespace

import ai_gm
from engine.scripts import by_id
from selfplay.league import make


def auto_protagonist(kind, script):
    """ai_gm.HumanProtagonist の代わり。判断は自動の主人公に任せ、人が遊ぶときと同じフェイズの区切り・指摘の行をログに出す。"""
    class AutoPC:
        def __init__(self, t, rng):
            self.t, self.p = t, make(kind, rng, script)
            self.rng = self.p.rng

        def __getattr__(self, k):
            if k == 'p':  # 複製の途中で無限に辿らない
                raise AttributeError(k)
            return getattr(self.p, k)

        def pc_cards(self, s, order, mm_targets=None):
            self.t.s = s
            self.t.phase(s['loop'], s['day'], 'mm_cards', '伏せ札: ' + '・'.join(ai_gm.name(x) for x in mm_targets or []))
            self.t.phase(s['loop'], s['day'], 'pc_cards', f'リーダー {order[0]}')
            # 脚本家の伏せ札が置かれた盤面（人が遊ぶときと同じ場面。リプレイで伏せ札が見える）
            self.t.show(s, f'L{s["loop"]} {s["day"]}日目 主人公行動フェイズ', {'kind': 'none', 'text': '自動の主人公が札を置く', 'options': []},
                        placed=[{'by': 'M', 'target': x, 'card': 'INT1'} for x in mm_targets or []])
            return self.p.pc_cards(s, order, mm_targets)

        def abilities_for_day(self, s, *a, **k):
            self.t.phase(s['loop'], s['day'], 'mm_ability')
            self.t.phase(s['loop'], s['day'], 'ability')
            self.t.show(s, f'L{s["loop"]} {s["day"]}日目 主人公能力フェイズ', bump=False)
            return self.p.abilities_for_day(s, *a, **k)

        def final_guesses(self, s):
            self.t.show(s, '最後の戦い', {'kind': 'none', 'text': '自動の主人公が役職を指摘する', 'options': []}, bump=False)
            self.guesses = self.p.final_guesses(s)
            for g in self.guesses:
                self.t.log(f'指摘: {ai_gm.name(g["char"])} は {ai_gm.ROLE_JA.get(g["role"], g["role"])}')
            return self.guesses
    return AutoPC


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--script', default='designed/d16')
    ap.add_argument('--mm', default='calcG')
    ap.add_argument('--pc', default='readlook')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    ai_gm.HumanProtagonist = auto_protagonist(a.pc, by_id(a.script))
    d = tempfile.mkdtemp(prefix='auto_replay_')
    ai_gm.run(SimpleNamespace(script=a.script, mm=a.mm, seed=a.seed, blind=False, dir=d))
    rd = lambda f: [json.loads(x) for x in open(os.path.join(d, f), encoding='utf-8') if x.strip()]
    st = json.load(open(os.path.join(d, 'state.json'), encoding='utf-8'))
    rp = {'format': 'sangeki-replay', 'version': 1, 'exportedAt': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
          'complete': bool(st.get('reveal')), 'title': (st.get('reveal') or {}).get('title'),
          'log': rd('log.jsonl'), 'snapshots': rd('snapshots.jsonl'), 'final': st}
    json.dump(rp, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'{a.out}: {st["reveal"]["winner"]}の勝ち（{st["reveal"]["how"]}）、場面 {len(rp["snapshots"])}')


if __name__ == '__main__':
    main()
