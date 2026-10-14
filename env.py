"""対戦エンジンの窓口（plan/engine-api.md 段A）。判断を1件ずつ外に渡し、選んだ手で先へ進める。

    from env import Env
    env = Env('designed/d16', mm='calcD')        # 脚本家は自動（calcD）、主人公は外から
    d = env.reset(seed=1)
    while d['kind'] != 'end':
        d = env.step(d['options'][0])           # 選べる手から1つ（options の要素をそのまま返す）

判断（Decision、JSON にできる）:
  {'seq', 'side': 'pc'|'mm', 'kind', 'view', 'options': [...], 'context': {...}}
  kind（主人公）: 'pc_card'（1人1枚ずつ、リーダーから3回）・'ability'（1件ずつ。None で宣言を終える）・'final_guess'（1人ずつ。None で終える）
  kind（脚本家）: 'mm_card'（1枚ずつ3回）・'mm_ability'・'incident_choice'・'refuse'・'killer_pick'・'cultist_pick'・'loop_setup'・
                  'mm_rumor'・'mm_extra_uses'・'choose_rule_x'
  view: その側に見える盤面と、前の判断から後の公開ログ。主人公には役職・犯人・伏せ札の中身を入れない（engine/project.py の公開の範囲）。
  試合が終わると {'kind': 'end', 'result': …, 'reveal': 非公開シート}。

選べる手（options）はエンジンの関数からだけ作る（合法な手は1か所＝plan 3章）。進行は selfplay/run.play_game をそのまま別スレッドで動かす
（ルールの処理は書き直さない）。env.record は判断の記録（再生・再開の元）。
"""
import copy
import json
import os
import queue
import random
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import options as opt  # noqa: E402
from engine.project import project  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402



class _Stop(Exception):
    """窓口が閉じられた（試合を途中でやめる）。"""


class _Bridge:
    """外から操作する側。判断のメソッドが呼ばれるたびに、Decision を窓口へ渡して答えを待つ。選べる手は engine/options.py（エンジン）から。"""

    def __init__(self, env, side):
        self.env, self.side, self.rng = env, side, random.Random(0)

    def ask(self, kind, s, options, context=None):
        return self.env._ask(self.side, kind, s, options, context or {})

    # ---- 主人公 ----
    def pc_cards(self, s, order, mm_targets=None):
        mm_targets = list(mm_targets or [])
        while True:
            out = []
            for p in order:
                out.append(self.ask('pc_card', s, opt.pc_card_options(s, p, out), {'by': p, 'order': order, 'chosen': list(out), 'mm_targets': mm_targets}))
            err = opt.pc_placement_error(s, out)  # 3枚そろってから、置き方の反則を確かめる（反則なら置き直し。理由は env.notes）
            if err is None:
                return out
            self.env._note(f'置き方が合法でない: {err}。置き直し')

    def ability_one(self, s):
        r = self.ask('ability', s, [None] + opt.declarations(s))
        return tuple(r) if r else None

    def ability_candidates(self, s):
        return []

    def final_guesses(self, s):
        out = []
        while True:
            o = opt.final_guess_options(s, out)
            if not o:
                return out
            g = self.ask('final_guess', s, [None] + o, {'chosen': list(out)})
            if g is None:
                return out + [{'char': c, 'role': 'PERSON'} for c in s['chars'] if c not in {x['char'] for x in out}]
            out.append(g)

    # ---- 脚本家 ----
    def mm_cards(self, s):
        out = []
        for _ in range(3):
            out.append(self.ask('mm_card', s, opt.mm_card_options(s, out), {'chosen': list(out)}))
        return out

    def mm_ability(self, s, actor, ability=None):
        return self.ask('mm_ability', s, opt.mm_ability_options(s, actor, ability), {'actor': actor, 'ability': ability})

    def incident_choice(self, s, inc):
        o = opt.incident_choice_options(s, inc)
        return self.ask('incident_choice', s, o, {'incident': inc}) if o else None

    def refuse(self, s, cid):
        return self.ask('refuse', s, [False, True], {'char': cid})

    def killer_pick(self, s):
        o = opt.killer_pick_options(s)
        return self.ask('killer_pick', s, o) if len(o) > 1 else []

    def cultist_pick(self, s, placements):
        o = opt.cultist_pick_options(s, placements)
        return self.ask('cultist_pick', s, o) if len(o) > 1 else []

    def loop_setup(self, s, script):
        o = opt.loop_setup_options(s, script)
        return self.ask('loop_setup', s, o) if len(o) > 1 else o[0]

    def mm_rumor(self, s):
        return self.ask('mm_rumor', s, opt.rumor_options(s))

    def mm_extra_uses(self, s):
        o = opt.extra_use_options(s)
        return self.ask('mm_extra_uses', s, o) if len(o) > 1 else o[0]

    def choose_rule_x(self, s, options):
        return self.ask('choose_rule_x', s, list(options))


_NONE = object()


class _Auto:
    """自動の側。判断のメソッドの答えも記録する（kind はメソッド名）。記録があれば考えずに返す。"""
    DECISIONS = set(Combo.MM) | {'pc_cards', 'ability_one', 'abilities_for_day', 'final_guesses'}

    def __init__(self, env, side, inner):
        self.__dict__.update(_env=env, _side=side, _inner=inner)

    def __getattr__(self, n):
        v = getattr(self._inner, n)
        if n not in self.DECISIONS or not callable(v):
            return v

        def f(*a, **k):
            rc = self._env._replayed(self._side, n)
            if rc is not _NONE:
                return _tuplify(n, rc)
            r = v(*a, **k)
            self._env.seq += 1
            self._env.record.append({'seq': self._env.seq, 'side': self._side, 'kind': n, 'choice': _plain(r)})
            return r
        return f


def _tuplify(kind, v):
    """記録（JSON）から戻すとき、タプルだった答えを戻す（能力の宣言 (人物, 番号, 引数)）。"""
    if kind == 'ability_one' and v:
        return tuple(v)
    if kind == 'abilities_for_day':
        return [tuple(x) for x in v]
    return v


class _Log:
    """公開の出来事を、対AIモードの公開ログと同じ文に（ai_gm.describe）。"""

    def __init__(self):
        self.lines = []

    def observe(self, s, phase, events):
        import ai_gm
        for e in events:
            if isinstance(e, dict):
                try:
                    t = ai_gm.describe(e)
                except Exception:
                    t = None
                if t:
                    self.lines.append(t)


class Env:
    def __init__(self, script, mm=None, pc=None, script_obj=None, replay=None):
        """script: 脚本 ID（'designed/d16' など）。mm・pc: 自動にする側の型（selfplay.league.make の名前）。None なら外から操作する。
        replay: 前の env.record。先頭から順に、その判断を答えとして使う（自動の側も考えずに返す＝作り直しが速い）。尽きたら普通に進む。"""
        self.script = script_obj or by_id(script)
        self.kinds = {'mm': mm, 'pc': pc}
        self.replay = list(replay or [])
        self.record, self.seq, self.notes = [], 0, []
        self._t = None

    # ---- 窓口 ----
    def reset(self, seed=1):
        from selfplay.league import make
        self.close()
        self._q_out, self._q_in = queue.Queue(), queue.Queue()
        self.record, self.seq, self.notes, self._seen = [], 0, [], 0
        self._log = _Log()
        self._rq = list(self.replay)
        side = {k: (_Auto(self, k, make(v, random.Random(f'{seed}:{k}'), self.script)) if v else _Bridge(self, k)) for k, v in self.kinds.items()}
        player = Combo(side['mm'], side['pc'])

        def run():
            try:
                r = play_game(self.script, player, lambda k, rec: None, observers=(self._log,))
                self._q_out.put({'kind': 'end', 'result': r, 'reveal': {k: self.script[k] for k in ('rules', 'roles', 'incidents')},
                                 'view': {'log': self._log.lines[self._seen:]}})
            except _Stop:
                pass
            except Exception as e:  # 試合の途中の不具合は、窓口に知らせる（スレッドの中で消えないように）
                self._q_out.put({'kind': 'error', 'error': f'{type(e).__name__}: {e}'})
        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()
        return self._q_out.get()

    def step(self, choice):
        """いまの判断に答える。choice は options の要素（または同じ形の値）。"""
        if self._t is None:
            raise RuntimeError('reset() を先に呼ぶ')
        self._q_in.put(choice)
        return self._q_out.get()

    def close(self):
        if self._t is not None and self._t.is_alive():
            self._q_in.put(_Stop)
            self._t.join(timeout=5)
        self._t = None

    # ---- スレッドの側 ----
    def _note(self, text):
        self.notes.append(text)

    def _replayed(self, side, kind):
        """記録の次がこの判断なら、その答え（無ければ _NONE）。"""
        if self._rq and self._rq[0]['side'] == side and self._rq[0]['kind'] == kind:
            x = self._rq.pop(0)
            self.seq += 1
            self.record.append(dict(x, seq=self.seq))
            return x['choice']
        self._rq = []  # 食い違ったら、そこから先の記録は使わない
        return _NONE

    def _ask(self, side, kind, s, options, context):
        rc = self._replayed(side, kind)
        if rc is not _NONE and _plain(rc) in _plain(options):
            self._seen = len(self._log.lines)
            return copy.deepcopy(rc)
        if rc is not _NONE:
            self.record.pop()
            self.seq -= 1
        self.seq += 1
        d = {'seq': self.seq, 'side': side, 'kind': kind, 'view': self._view(side, s, context), 'options': _plain(options),
             'context': _plain(context)}
        self._q_out.put(d)
        choice = self._q_in.get()
        if choice is _Stop:
            raise _Stop()
        if _plain(choice) not in d['options']:
            # 選べる手に無い答え: 同じ判断を聞き直す（notes に理由）
            self._note(f'{kind}: 選べる手に無い {choice!r}')
            self.seq -= 1
            return self._ask(side, kind, s, options, context)
        self.record.append({'seq': self.seq, 'side': side, 'kind': kind, 'choice': _plain(choice)})
        return copy.deepcopy(choice)

    def _view(self, side, s, context):
        new = self._log.lines[self._seen:]
        self._seen = len(self._log.lines)
        placed = [{'by': 'M', 'target': t, 'card': 'INT1'} for t in context.get('mm_targets', [])]
        if side == 'mm':  # 脚本家には全部（脚本＝非公開シートも）
            return {'state': _plain({k: v for k, v in s.items() if k != 'script'}), 'script': _plain(self.script), 'log': new}
        sheet = {k: self.script.get(k) for k in ('set', 'loops', 'days')}  # 公開シートのうち盤面に無いもの
        return {'board': _plain(project(s, placed)), 'sheet': sheet, 'log': new}  # 主人公には公開の範囲だけ（伏せ札は置き場所だけ）


def _plain(x):
    """JSON の形に（タプル→リスト、集合→並べたリスト）。"""
    return json.loads(json.dumps(x, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))


def _show(d):
    """判断を人（や LLM）が読める文に。"""
    from engine.resolve import CHARS as C
    if d['kind'] in ('end', 'error'):
        print('=== 試合の終わり' if d['kind'] == 'end' else '=== エラー', json.dumps(d.get('result') or d.get('error'), ensure_ascii=False))
        for x in d.get('view', {}).get('log', []):
            print('  ', x)
        return
    v = d['view']
    print(f"=== 判断 #{d['seq']}  側: {'主人公' if d['side'] == 'pc' else '脚本家'}  種類: {d['kind']}")
    for x in v.get('log', [])[-40:]:
        print('  ログ:', x)
    b = v.get('board') or {}
    if b:
        print(f"  ループ {b.get('loop')} {b.get('day')}日目 リーダー {b.get('leader')}  ボードの暗躍 {b.get('boards')}")
        for c in b.get('characters', []):
            print(f"  {c['name']}（{c['area']}） 不{c['paranoia']} 友{c['goodwill']} 暗{c['intrigue']}{' 死亡' if not c['alive'] else ''}")
    if v.get('state'):
        s = v['state']
        print(f"  ループ {s.get('loop')} {s.get('day')}日目  ボードの暗躍 {s.get('boards')}")
        for cid, c in s['chars'].items():
            if c.get('present', True) and c.get('area'):
                print(f"  {cid} {C[cid]['name']}（{c['area']}） 不{c['par']} 友{c['gw']} 暗{c['int']}{' 死亡' if not c['alive'] else ''} [{v['script']['roles'].get(cid, 'PERSON')}]")
    if d['context']:
        print('  状況:', json.dumps(d['context'], ensure_ascii=False)[:400])
    print('  選べる手（--answer 番号）:')
    for i, o in enumerate(d['options']):
        print(f'   {i}: {json.dumps(o, ensure_ascii=False)}')


def stdio(a):
    """1行1判断の JSON でやりとりする（docs/env.md）。答えが選べる手に無ければ、同じ判断が notes つきでもう一度出る。"""
    env = Env(a.script, mm=a.mm, pc=a.pc)
    d = env.reset(seed=a.seed)
    while True:
        d['notes'], env.notes = env.notes, []
        print(json.dumps(d, ensure_ascii=False), flush=True)
        if d['kind'] in ('end', 'error'):
            break
        line = sys.stdin.readline()
        if not line:
            break
        x = json.loads(line)
        d = env.step(d['options'][x] if isinstance(x, int) and not isinstance(x, bool) else x)
    env.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description='窓口でコマンドラインから対戦する。毎回、記録（--file）を作り直して次の判断を出す（自動の側は記録があれば考えない）')
    ap.add_argument('--script', default='s03_bomb')
    ap.add_argument('--mm', default=None, help='自動の脚本家（省くと脚本家も外から）')
    ap.add_argument('--pc', default=None, help='自動の主人公（省くと主人公も外から）')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--file', help='判断の記録（JSON Lines）。無ければ新しく作る')
    ap.add_argument('--stdio', action='store_true', help='判断を1行1つの JSON で標準出力に出し、標準入力の1行（選べる手の番号か、手そのものの JSON）で答える。どの言語のエージェントでも遊べる')
    ap.add_argument('--answer', type=int, default=None, help='いまの判断に、選べる手の番号で答える')
    a = ap.parse_args()
    if a.stdio:
        return stdio(a)
    if not a.file:
        ap.error('--file か --stdio が要る')
    rec = [json.loads(x) for x in open(a.file, encoding='utf-8') if x.strip()] if os.path.exists(a.file) else []
    env = Env(a.script, mm=a.mm, pc=a.pc, replay=rec)
    d = env.reset(seed=a.seed)
    if a.answer is not None and d['kind'] not in ('end', 'error'):
        d = env.step(d['options'][a.answer])
    with open(a.file, 'w', encoding='utf-8') as f:
        for x in env.record:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    for n in env.notes:
        print('  注意:', n)
    _show(d)
    env.close()


if __name__ == '__main__':
    main()
