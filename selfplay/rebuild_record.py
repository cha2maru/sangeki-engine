"""判断の記録（record.json）の無いリプレイ（以前に書き出した JSON）から、記録を組み立て直す（2026-10-10、ユーザー
「大物の問題の主人公能力フェイズから再開できる URL」）。公開ログから両陣営の判断の候補を作り、エンジンで再生して公開ログが元と
一致する候補だけを残す（合わなければ前の判断に戻って別の候補を試す）。脚本家の判断で公開ログに現れないもの（手先の初期エリアなど）は、
後の行（移動の元のエリアなど）で絞られる。

  python -m selfplay.rebuild_record replay.json --script generated/gen_s11_027 --mm calcG --upto 245 --out record.json

--upto N: 公開ログの N 行目の手前まで（その時点で盤面からの操作を待つ記録）。省くと最後まで。
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace

import ai_gm
from engine import abilities as ab
from engine import phases as ph
from engine.adapter import NAME2C, UI2CARD
from selfplay.players import RouteMastermind
from selfplay.run import incident_options

COUNTER_JA = {'par': '不安', 'gw': '友好', 'int': '暗躍', 'guard': '護衛'}
AREA_FROM_JA = {v: k for k, v in ai_gm.AREA_JA.items()}


class NeedMM(Exception):
    def __init__(self, name, args):
        self.name, self.args = name, args


class NeedHuman(Exception):
    def __init__(self, types, s):
        self.types, self.s = types, s


class Diverged(Exception):
    pass


def _target(nm):
    """公開ログの置き先の名前（人物名・エリア名）→ エンジンの置き先。"""
    return 'B:' + AREA_FROM_JA[nm] if nm in AREA_FROM_JA else NAME2C[nm]


def _cards(line):
    """「脚本家の札が公開された: お嬢様=暗躍+2、…」→ [(置き先, 札)]。"""
    out = []
    for part in line.split(': ', 1)[1].split('、'):
        nm, card = part.rsplit('=', 1)
        out.append((_target(nm.split(' ', 1)[-1]), UI2CARD[card]))
    return out


def _decl(line):
    """「宣言を受け付けた: 巫女 [3] …」→ (人物, 友好, ★)。能力の文面は版で違うことがある（盤面の表から公開のキャラクター情報に変えた）ので比べない。"""
    m = re.match(r'宣言を受け付けた: (\S+) \[(\d+)\](★?)', line)
    return m.groups() if m else None


def _same(a, b):
    return a == b or (_decl(a) is not None and _decl(a) == _decl(b))


def _next(log, n, prefix):
    """公開ログの n 行目より後で prefix で始まる最初の行。"""
    return next((x for x in log[n:] if x.startswith(prefix)), None)


class Rebuilder:
    def __init__(self, replay, script, mm, upto=None):
        self.log = [x['text'] for x in replay['log']]
        self.upto = upto or len(self.log) + 1
        self.script, self.mm = script, mm
        self.tries = 0

    def attempt(self, h, m):
        """記録 (h, m) で再生し、(出た公開ログ, 次に要る判断の例外 or None)。出た公開ログが元と食い違えば Diverged。"""
        self.tries += 1
        d = tempfile.mkdtemp(prefix='rebuild_')
        outer = self

        class T(ai_gm.Table):
            def take(self, types):
                if self.replay and self.replay['h']:
                    return ai_gm.Table.take(self, types)
                raise NeedHuman(types, self.s)

            def log(self, text):
                ai_gm.Table.log(self, text)
                if self.n > len(outer.log) or not _same(outer.log[self.n - 1], text):
                    raise Diverged(f'{self.n}: {text}')

        def need(self, n):
            if n.startswith('_') or n not in self._has:
                raise AttributeError(n)
            if n == 'observe':
                return lambda *a, **k: None

            def f(*a, **k):
                raise NeedMM(n, a)
            return f
        orig_rmm, orig_ded = ai_gm.ReplayMM.__getattr__, ai_gm.Narrator.deduce
        ai_gm.ReplayMM.__getattr__ = need
        ai_gm.Narrator.deduce = lambda self, s: None  # 推理の表は記録に要らない（速くする）
        rec = {'v': 2, 's': self.script, 'c': '', 'mm': self.mm, 'h': list(h), 'm': [[0, v] for v in m], 'n': 0, 'crc': 0}
        try:
            ai_gm.run(SimpleNamespace(script='', mm='', seed=0, blind=False, dir=d, code='', replay=rec), T)
            return d, None
        except (NeedMM, NeedHuman) as e:
            return d, e
        finally:
            ai_gm.ReplayMM.__getattr__, ai_gm.Narrator.deduce = orig_rmm, orig_ded

    def n_of(self, d):
        p = os.path.join(d, 'log.jsonl')
        return sum(1 for _ in open(p, encoding='utf-8')) if os.path.exists(p) else 0

    # ---- 候補 ----
    def mm_candidates(self, e, n):
        name, a = e.name, e.args
        s = a[0] if a else None
        if name == 'mm_cards':
            line = _next(self.log, n, '脚本家の札が公開された:')
            return [[{'by': 'M', 'target': t, 'card': c} for t, c in _cards(line)]] if line else []
        if name == 'loop_setup':
            script = a[1]
            if 'C18' in script['init']:
                return [{'init': {'C18': x}} for x in ph.AREAS]
            return [{}]
        if name == 'mm_ability':
            actor = a[1]
            areas = ph.mm_areas(s, actor)
            opts = [c for ar in areas for c in ph.alive_in(s, ar)] + ['B:' + ar for ar in areas]
            return [None] + list(dict.fromkeys(opts))
        if name == 'refuse':
            return [False, True]
        if name == 'incident_choice':
            opts = incident_options(s, a[1])
            if opts is None:  # 殺人事件・不安拡大など、選択のある事件は対象の組
                cul = a[1]['culprit']
                alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
                if a[1]['id'] == 'MURDER':
                    opts = [{'target': c} for c in ph.alive_in(s, s['chars'][cul]['area']) if c != cul]
                elif a[1]['id'] == 'SPREAD':
                    opts = [{'par_target': p, 'int_target': i} for p in alive for i in alive if p != i]
                else:
                    opts = []
            return [None] + opts
        if name == 'cultist_pick':
            return [[]] + [[o] for o in RouteMastermind.cultist_options(None, s, a[1])]
        if name == 'killer_pick':
            return [[]] + [[o] for o in ph.killer_options(s)]
        if name in ('mm_extra_uses',):
            return [[]]
        if name == 'mm_rumor':
            return [None] + ['B:' + x for x in ph.AREAS]
        raise SystemExit(f'未対応の判断 {name}')

    def human_candidates(self, e, n):
        if 'cards' in e.types:
            line = _next(self.log, n, '主人公の札:')
            if not line:
                return []
            pl = []
            for part in line.split(': ', 1)[1].split('、'):
                actor, rest = part.split(' ', 1)
                nm, card = rest.rsplit('=', 1)
                pl.append({'actor': actor, 'target': ('board:' + nm) if nm in AREA_FROM_JA else nm, 'card': card})
            return [{'type': 'cards', 'placements': pl}]
        if 'ability' in e.types:
            out = []
            nxt = self.log[n] if n < len(self.log) else ''
            dc = _decl(nxt)
            if dc and dc[0] in NAME2C:
                cid = NAME2C[dc[0]]
                for c, i, a in ab.declaration_options(e.s):
                    if c != cid or _decl('宣言を受け付けた: %s %s' % (dc[0], ai_gm.ability_label(c, i))) != dc:
                        continue
                    rec = {'type': 'ability', 'char': dc[0], 'ability': i}
                    for k, v in (a or {}).items():
                        if k in ('target', 'from', 'to'):
                            rec[k] = ai_gm.name(v) if v in NAME2C.values() else v
                        elif k == 'counter':
                            rec[k] = COUNTER_JA.get(v, v)
                        elif k == 'mode':
                            rec[k] = {'remove': '取り除く', 'place': '置く'}.get(v, v)
                        elif k == 'declare':
                            rec[k] = ai_gm.RULE_JA.get(v, v)
                        elif k == 'incident':
                            rec[k] = ai_gm.INC_JA.get(v, v)
                        else:
                            rec[k] = v
                    out.append(rec)
            out.append({'type': 'option', 'option': ai_gm.END_ABILITY})
            return out
        if 'final' in e.types:
            return [{'type': 'option', 'option': ai_gm.END_FINAL}]
        return []

    def solve(self, h=(), m=(), depth=0):
        """記録を深さ優先で組み立てる。返り値は (h, m) か None。"""
        try:
            d, e = self.attempt(h, m)
        except Diverged:
            return None
        n = self.n_of(d)
        shutil.rmtree(d, ignore_errors=True)
        if e is None or (isinstance(e, NeedHuman) and n >= self.upto - 1):  # 試合の終わりか、目当ての場面で主人公の操作を待つところ
            return list(h), list(m), n
        if isinstance(e, NeedMM):
            for c in self.mm_candidates(e, n):
                r = self.solve(h, list(m) + [c], depth + 1)
                if r:
                    return r
            return None
        for c in self.human_candidates(e, n):
            r = self.solve(list(h) + [c], m, depth + 1)
            if r:
                return r
        return None


BASE = 'https://cha2maru.github.io/sangeki-engine/'


def _b64(raw):
    import base64
    import zlib
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.urlsafe_b64encode(c.compress(raw) + c.flush()).decode('ascii').rstrip('=')


def long_url(rp, base=BASE):
    """盤面の記録をそのまま入れる URL（#r=。board.html の RP.pack と同じ形）。"""
    prev = {}

    def diff(st):
        nonlocal prev
        o = {k: v for k, v in st.items() if prev.get(k, object()) != v}
        prev = st
        return o
    o = {'f': 'r1', 't': rp.get('title'), 'c': rp.get('complete'), 'l': [[x['n'], x.get('loop'), x.get('day'), x['text']] for x in rp['log']],
         's': [[x['prev_n'], x['log_n'], diff(x['state'])] for x in rp['snapshots']], 'x': rp.get('rejected') or []}
    o['z'] = diff(rp.get('final') or {})
    return base + '#r=' + _b64(json.dumps(o, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def record_url(rec, base=BASE, resume=False):
    """判断の記録の URL（?p= ＝作り直して見る、?resume= ＝作り直してから続きを遊ぶ）。"""
    return base + ('?resume=' if resume else '?p=') + _b64(json.dumps(rec, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def guess(rp):
    """リプレイから (脚本, 脚本家の型)。脚本は終了時の非公開シート、脚本家は開始のログの行から。"""
    sid = ((rp.get('final') or {}).get('reveal') or {}).get('id')
    m = re.search(r'脚本家は自動（(\w+)）', rp['log'][0]['text']) if rp.get('log') else None
    return sid, (m.group(1) if m else 'calcG')


def main():
    ap = argparse.ArgumentParser(description='リプレイの JSON から共有・再開の URL を作る（判断の記録が無ければ公開ログから組み立て直す）')
    ap.add_argument('replay', help='盤面で書き出したリプレイの JSON')
    ap.add_argument('--resume-at', type=int, default=0, help='公開ログのこの行の場面から再開する URL を作る（その行の手前まで作り直し、次の主人公の操作を待つ）')
    ap.add_argument('--long', action='store_true', help='盤面の記録ごとの長い URL（#r=）も出す')
    ap.add_argument('--script', help='脚本 ID（省くと終了時の非公開シートから）')
    ap.add_argument('--mm', help='脚本家の型（省くと開始のログから）')
    ap.add_argument('--base', default=BASE, help='盤面の URL（既定は GitHub Pages）')
    ap.add_argument('--out', help='組み立てた記録（record.json の形）の書き出し先')
    a = ap.parse_args()
    sys.setrecursionlimit(10000)
    rp = json.load(open(a.replay, encoding='utf-8'))
    sid, mm = guess(rp)
    sid, mm = a.script or sid, a.mm or mm
    rec = rp.get('record')
    if rec and rec.get('v') == 2 and a.resume_at:  # 記録があれば、その場面の手前で切るだけ
        part = dict(rec, h=[x for x in rec['h'] if x['n'] < a.resume_at], m=[x for x in rec['m'] if x[0] < a.resume_at])
    else:
        if not sid:
            raise SystemExit('脚本 ID が分からない（--script で指定する。途中までのリプレイには非公開シートが無い）')
        rb = Rebuilder(rp, sid, mm, a.resume_at or None)
        r = rb.solve()
        if not r:
            raise SystemExit(f'組み立てられなかった（試行 {rb.tries}）')
        h, m, n = r
        part = {'v': 2, 's': sid, 'c': '', 'mm': mm, 'h': [dict(x, n=0) for x in h], 'm': [[0, v] for v in m], 'n': 0, 'crc': 0}
        print(f'組み立てた: 主人公の操作 {len(h)}・脚本家の判断 {len(m)}、公開ログ {n} 行まで一致（試行 {rb.tries}）', file=sys.stderr)
        if not a.resume_at:
            rec = part
    if a.out:
        json.dump(part, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False)
    if a.resume_at:
        print('再開:', record_url(part, a.base, resume=True))
    elif rec:
        print('短い:', record_url(rec, a.base))
    if a.long:
        print('長い:', long_url(rp, a.base))


if __name__ == '__main__':
    main()
