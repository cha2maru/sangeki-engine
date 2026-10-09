"""次の一手問題（ユーザーの案 2026-10-07）: 対戦の記録の特定の局面で、新しいロジックが良い手を選べるかを短時間で測る。

問題は positions/*.json（1ファイルに複数）。1問の形:
  {"id": "d16-l2d1-mm", "script": "designed/d16", "seed": 64,
   "mm": "selfplay/runs/cvc_d16_s64_mm.jsonl", "pc": "selfplay/runs/cvc_d16_s64_pc.jsonl",
   "at": {"loop": 2, "day": 1, "kind": "mm_cards"},          # mm_cards・incident（脚本家）／pc_cards（主人公）
   "good": [[{"card": "INT2", "target": "C34"}, {"card": "INT1", "target": "B:CIT"}]],   # どれか1つの組を全部含めば正解
   "bad": [[{"card": "INT2", "target": "B:CIT"}]],          # どれか1つの組を全部含めば悪手
   "note": "なぜそれが良い・悪いか", "split": "tune"}        # tune（調整用）・hold（確かめ用）
片側だけ Claude の対戦は、相手側のファイルの代わりに "mm_opp": "search" / "pc_opp": "readlook" など（記録した時の --opp の型と seed）。
自動のプレイヤーのコードが変わると再生がずれるので、問題を足すときは記録どおりに進むことを確かめる。
記録をその局面の直前まで再生する。試すロジックは再生の最初から観察者として一緒に見るので、推理や記憶は実戦と同じに育つ。

    cd play && python -m selfplay.positions positions/*.json --mm search searchS8 --pc readlook --samples 3
"""
import argparse
import pickle
import copy
import glob
import json
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.scripts import by_id  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.play_cli import ClaudeMastermind, ClaudeProtagonist  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402


class Reached(Exception):
    def __init__(self, s, args):
        super().__init__('reached')
        self.s, self.args = s, args


def _tolerant(cls):
    """記録の後でエンジンが聞かなくなった問い（拒否を選べない役職への拒否の確認）の答えを読み飛ばす。"""
    class T(cls):
        def _next(self, kind, s, title, options):
            while self.d and self.d[0]['kind'] != kind and self.d[0]['kind'] == 'refuse':
                self.d.pop(0)
            return super()._next(kind, s, title, options)
    return T


TRACK = set(Combo.MM) | {'pc_cards', 'abilities_for_day', 'ability_one', 'ability_candidates', 'final_guesses'}


class Recorder:
    """自動のプレイヤーを包み、判断の結果を順に記録する（問題の相手側を凍結するため）。"""
    def __init__(self, inner):
        self._inner, self.log = inner, []
        self.has = sorted(n for n in TRACK if hasattr(inner, n))

    def __getattr__(self, n):
        a = getattr(self._inner, n)
        if n in TRACK and callable(a):
            def w(*args, **kw):
                r = a(*args, **kw)
                self.log.append((n, copy.deepcopy(r)))
                return r
            return w
        return a


class Replayer:
    """記録した判断を順に返す。ロジックのコードが変わっても、問題の相手側の手は変わらない。"""
    def __init__(self, data, rng):
        self.log, self.has, self.rng = list(data['log']), set(data['has']), rng

    def __getattr__(self, n):
        if n in self.__dict__.get('has', ()):
            def f(*a, **k):
                name, r = self.log.pop(0)
                assert name == n, f'凍結した判断の順がずれた: 期待 {n} / 記録 {name}'
                return copy.deepcopy(r)
            return f
        raise AttributeError(n)


def _at(s, at):
    return s['loop'] == at['loop'] and s['day'] == at['day']


def replay_to(prob):
    """局面の直前まで再生し、(盤面, 呼び出しの引数) を返す。observers に試すロジックを渡しておく。"""
    script = by_id(prob['script'])
    load = lambda f: [json.loads(x) for x in open(f, encoding='utf-8') if x.strip()]  # noqa: E731
    at = prob['at']

    rng = random.Random(prob.get('seed', 0))
    # 片側だけ Claude の対戦: mm_opp / pc_opp に相手の自動のプレイヤーの型（league.make の名前）。play_cli と同じく rng を共有する
    # 相手側の自動のプレイヤーは、凍結した記録（*_rec、freeze で作る）があればそれを読み返す
    side = lambda k, rec, mk: Replayer(pickle.load(open(rec, 'rb')), rng) if rec else mk  # noqa: E731
    mm = (side('mm', prob.get('mm_rec'), None) or make(prob['mm_opp'], rng, script)) if prob.get('mm_opp') else _tolerant(ClaudeMastermind)(load(prob['mm']))
    pc = (side('pc', prob.get('pc_rec'), None) or make(prob['pc_opp'], rng, script)) if prob.get('pc_opp') else _tolerant(ClaudeProtagonist)(load(prob['pc']), rng)

    def hook(p, name, kind):  # 局面に来たら Reached で止める（自動のプレイヤーの手も問題にできるよう、インスタンスの関数を包む）
        f = getattr(p, name)

        def g(s, *a):
            if at['kind'] == kind and _at(s, at) and (kind != 'incident' or a[0]['id'] == at.get('incident', a[0]['id'])):
                raise Reached(s, a)
            return f(s, *a)
        setattr(p, name, g)
    hook(mm, 'mm_cards', 'mm_cards')
    hook(mm, 'incident_choice', 'incident')
    hook(pc, 'pc_cards', 'pc_cards')
    return script, mm, pc


def ask(prob, kind, rng_seed):
    """ロジック kind に、この局面の手を聞く。"""
    script, mm, pc = replay_to(prob)
    side = 'pc' if prob['at']['kind'] == 'pc_cards' else 'mm'
    logic = make(kind, random.Random(rng_seed), script)
    try:
        play_game(script, Combo(mm, pc), lambda *a: None, observers=[logic])
    except Reached as r:
        s = copy.deepcopy(r.s)
        if prob['at']['kind'] == 'mm_cards':
            return [{'target': x['target'], 'card': x['card']} for x in logic.mm_cards(s)]
        if prob['at']['kind'] == 'incident':
            ch = logic.incident_choice(s, r.args[0])
            return [ch] if ch else []
        order, targets = (list(r.args) + [None])[:2]
        return [{'target': x['target'], 'card': x['card']} for x in logic.pc_cards(s, order, targets)]
    raise RuntimeError(f"{prob['id']}: 局面に届かなかった（{side}）")


def freeze(path):
    """問題ファイルの片側だけ Claude の問題について、相手の自動のプレイヤーの判断を最後まで記録して positions/games/*.pkl に保存し、*_rec を書き込む。"""
    probs = json.load(open(path, encoding='utf-8'))
    done = {}
    for p in probs:
        for k in ('mm', 'pc'):
            if not p.get(f'{k}_opp') or p.get(f'{k}_rec'):
                continue
            key = (p[k if k == 'pc' else 'mm'] if False else p.get('pc' if k == 'mm' else 'mm'), p[f'{k}_opp'], p.get('seed', 0), p['script'])
            if key not in done:
                q = {kk: vv for kk, vv in p.items() if kk != 'at'}
                q['at'] = {'loop': -1, 'day': -1, 'kind': 'none'}
                script, mm, pc = replay_to(q)
                rec = Recorder(mm if k == 'mm' else pc)
                play_game(script, Combo(rec, pc) if k == 'mm' else Combo(mm, rec), lambda *a: None)
                out = f"positions/games/frozen_{os.path.basename(key[0]).replace('.jsonl', '')}_{k}_{key[1]}_s{key[2]}.pkl"
                pickle.dump({'log': rec.log, 'has': rec.has}, open(out, 'wb'))
                done[key] = out
                print('凍結', out, len(rec.log))
            p[f'{k}_rec'] = done[key]
    json.dump(probs, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def match(move, pats):
    """move（札の並び）が pats のどれか1組を全部含むか。"""
    def has(p):
        return any(all(m.get(k) == v for k, v in p.items()) for m in move)
    return any(all(has(p) for p in grp) for grp in pats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--freeze', action='store_true', help='片側だけ Claude の問題の相手側を記録して凍結する（ロジックを変える前に1度）')
    ap.add_argument('--mm', nargs='*', default=['search'])
    ap.add_argument('--pc', nargs='*', default=['readlook'])
    ap.add_argument('--samples', type=int, default=1, help='乱数を変えて何回聞くか')
    ap.add_argument('--split', default=None, help='tune か hold だけを解く')
    a = ap.parse_args()
    if a.freeze:
        for f in a.files:
            freeze(f)
        return
    probs = [p for f in a.files for p in json.load(open(f, encoding='utf-8'))]
    if a.split:
        probs = [p for p in probs if p.get('split', 'tune') == a.split]
    total = {}
    for p in probs:
        kinds = a.pc if p['at']['kind'] == 'pc_cards' else a.mm
        for k in kinds:
            ok = 0
            for i in range(a.samples):
                mv = ask(p, k, f"{p['id']}:{i}")
                good = match(mv, p.get('good', [])) if p.get('good') else True
                bad = match(mv, p.get('bad', []))
                ok += good and not bad
                print(f"{p['id']:22} {k:14} {'○' if good and not bad else '×'} {mv}")
            t = total.setdefault(k, [0, 0])
            t[0] += ok
            t[1] += a.samples
    print('---')
    for k, (o, n) in total.items():
        print(f'{k:14} {o}/{n}')


if __name__ == '__main__':
    main()
