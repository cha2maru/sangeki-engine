"""優秀な脚本（おすすめ）の選定の道具。plan/mastermind_algorithm_evolution_plan.md「優秀な脚本の選定」（2026-10-16）の手順をまとめたもの。

    cd play
    python -m selfplay.select_scripts static 'designed/*' 'generated/*' > OUT/static.jsonl     # 静的な判定（数秒〜数分）
    python -m selfplay.select_scripts games 'designed/*' --mm calcGBxhd --seeds 1-3 -P 6 --out OUT  # 試合（重い。裏で回す）
    python -m selfplay.select_scripts pick --static OUT/static.jsonl --games OUT                # 条件で選ぶ（表と、通った id）

脚本の指定は engine/scripts からの相対（'designed/*'・'generated/gen_b31_009'・'bgw/b071'）。
- static: 要素の意味（役職・事件のうち、近い筋＝距離10以下に使われる割合）、主人公が情報を取る手立ての数、逆算（ループ1が必勝か・賭けの確率）、
  タブー（必勝かつ手立て0）、ループ数・日数の警告、難易度（脚本の notes）、性格（ループ1開始の近い筋が要る条件で一番多いもの: 位置・事件・暗躍）
- games: 1試合ずつ別のプロセス（nice）。OUT/<id>_<脚本家>_s<seed>.res（結果の JSON）と .jsonl（記録。ループごとの推理のエントロピー）。
  済んだ試合は飛ばす（止めても続きから回せる）
- pick の条件（引数で変えられる。既定は 2026-10-16 の選定）: タブーでない／要素 0.5 以上（役職・事件とも）／日数 5〜7（投稿シナリオは8まで）／
  手立て 2 以上／脚本家がループを取りきる試合（最後の戦いに届く）が過半／最後のループ終了時のエントロピーの平均が 3〜12
- 目隠し対戦の候補（generated）を選定にかけるときは、この道具の数値の表だけを見て、脚本の中身は読まない（Claude の目隠し対戦に残すため）
"""
import argparse
import collections
import glob
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

PLAY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLAY)
INFO_WORDS = ('役職を知る', '犯人を知る', 'ルールXを', '役職を公開')


def expand(pats):
    from engine.scripts import glob_scripts, script_dirs
    out = []
    for pat in pats:
        for f in glob_scripts(pat if pat.endswith('.json') else pat + '.json'):
            rel = next(os.path.relpath(f, d) for d in script_dirs() if f.startswith(d))
            sid = rel[:-5].replace(os.sep, '/')
            if sid not in out:
                out.append(sid)
    return out


def static_row(sid):
    from engine.route_calc import forced_plan
    from engine.routes import enumerate_routes
    from engine.scripts import by_id, find, load, report
    from selfplay.script_routes import start_state
    pub = json.load(open(os.path.join(PLAY, 'engine/data/characters_public.json'), encoding='utf-8'))
    pub = pub.get('characters', pub)
    raw, sc = load(find(sid + '.json')), by_id(sid)
    s = start_state(sc)
    rs = [r for r in enumerate_routes(s) if r['total'] <= 10]
    used_c, used_i = set(), set()
    kinds = collections.Counter()
    for r in rs:
        if r['via']:
            used_c.add(r['via'])
        ks = set()
        for c in r['conds']:
            for x in c['detail'] if isinstance(c['detail'], (list, tuple)) else [c['detail']]:
                if isinstance(x, str) and x.startswith('C'):
                    used_c.add(x)
            if c['kind'] == 'days' and c['detail'][0] == '事件':
                used_i.add(c['detail'][1])
                ks.add('inc')
            elif c['kind'] == 'counter':
                ks.add(c['detail'][1])
            elif c['kind'] == 'board':
                ks.add('int')
            elif c['kind'] in ('same_area', 'alone_with', 'in_area'):
                ks.add('pos')
        if r['total'] <= 8:
            kinds.update(ks)
    used_c, used_i = _supporters(s, rs, sc, used_c, used_i)
    roles = [c for c, r in sc['roles'].items() if r != 'PERSON']
    role_ok = sum(1 for c in roles if c in used_c) / max(1, len(roles))
    inc_ok = sum(1 for i in sc['incidents'] if i['id'] in used_i or i['culprit'] in used_c) / max(1, len(sc['incidents']))
    info = sum(1 for c in raw['characters'] for a in (pub.get(c, {}).get('abilities') or []) if any(k in a['text'] for k in INFO_WORDS))
    try:
        f = forced_plan(s, sc['days'], hidden=True, pcab=True)
        l1 = '必' if f['forced'] else f"{f.get('p') or 0:.2f}"
    except Exception:  # ponytail: 逆算が扱えない脚本は 'err'（条件には使わない値）
        l1 = 'err'
    notes = raw.get('notes') if isinstance(raw.get('notes'), dict) else {}
    return {'id': sid, 'loops': sc['loops'], 'days': sc['days'], 'routes': len(rs), 'role_ok': round(role_ok, 2), 'inc_ok': round(inc_ok, 2),
            'info': info, 'L1': l1, 'taboo': l1 == '必' and info == 0,
            'warn': '／'.join(w for w in report(raw)['warnings'] if 'ループ' in w or '日数' in w),
            'diff': notes.get('difficulty', ''), 'kind': max(('pos', 'inc', 'int'), key=lambda x: kinds[x]) if kinds else ''}


def _supporters(s, rs, sc, used_c, used_i):
    """筋の条件に名前が出なくても、筋を補う役職・事件を「使われる」に足す（2026-10-17）。
    クロマク・ミスリーダー（ファクターは学校の暗躍2以上で）: 能力の届くエリアに筋の暗躍・不安の対象がある。カルティスト: 暗躍の要る筋がある
    （暗躍禁止を無視して通す）。事件: routes._unstoppable_supply が筋の対象に数える事件（邪気の汚染・行方不明・流布・蝶の羽ばたき）。"""
    from engine.phases import base_role, mm_areas
    from engine.routes import _unstoppable_supply
    used_c, used_i = set(used_c), set(used_i)
    tgt = set()
    for r in rs:
        for c in r['conds']:
            if c['kind'] == 'counter' and c['detail'][1] in ('par', 'int') and c['detail'][0] in s['chars']:
                tgt.add((c['detail'][0], c['detail'][1]))
            elif c['kind'] == 'board':
                tgt.add(('B:' + c['detail'][0], 'int'))
    alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
    for c in alive:
        r = base_role(s, c)
        kind = {'KUROMAKU': 'int', 'MISLEADER': 'par', 'FACTOR': 'par' if s['boards'].get('SCH', 0) >= 2 else None}.get(r)
        if kind and any(k == kind and (t[2:] if t.startswith('B:') else s['chars'][t]['area']) in mm_areas(s, c) for t, k in tgt):
            used_c.add(c)
        if r == 'CULTIST' and any(k == 'int' for _, k in tgt):
            used_c.add(c)
    for t, k in tgt:
        _, incs = _unstoppable_supply(s, t, k, sc['days'], sc['days'])
        for _, _, iid, cul in incs:
            used_i.add(iid)
            used_c.add(cul)
    return used_c, used_i


def one_game(sid, mm, pc, seed, prefix):
    """1試合を回し、prefix.res（結果）と prefix.jsonl（記録とループごとの推理）を書く。"""
    import random
    from engine.scripts import by_id
    from selfplay.league import make
    from selfplay.players import PublicObserver
    from selfplay.run import Combo, play_game
    sc, rng = by_id(sid), random.Random(int(seed))
    recs = []

    mm_p = make(mm, rng, sc)  # 脚本家を先に作る（乱数の使い方を前と同じ順に）
    pc_p = make(pc, rng, sc)

    class Obs(PublicObserver):
        def loop_done(self, s, loop):
            self._ensure(s)
            rec = {'loop': loop, 'entropy': round(self.ded.role_entropy(), 1), 'n_hyp': self.ded.n_hyp()}
            ded = getattr(pc_p, 'ded', None)
            if ded is not None:  # 主人公自身の推理で、本当の役職に乗っている確率の平均（意図の読みなどの効き目を測る。2026-10-17）
                m, tot = ded.marginals()
                if tot:
                    ps = [m.get(c, {}).get(s['script']['roles'].get(c, 'PERSON'), 0) / tot for c in s['chars']]
                    rec['pc_ptrue'] = round(sum(ps) / len(ps), 3)
            recs.append(('belief', rec))
    r = play_game(sc, Combo(mm_p, pc_p), lambda k, rec: recs.append((k, rec)), observers=(Obs(),))
    with open(prefix + '.jsonl', 'w', encoding='utf-8') as f:
        for k, rec in recs:
            f.write(json.dumps({'k': k, **rec}, ensure_ascii=False, default=str) + '\n')
    with open(prefix + '.res', 'w', encoding='utf-8') as f:
        json.dump(r, f, ensure_ascii=False, default=str)


def seeds_of(s):
    a, _, b = s.partition('-')
    return list(range(int(a), int(b or a) + 1))


def run_games(ids, mm, pc, seeds, par, out, timeout):
    os.makedirs(out, exist_ok=True)
    jobs = [(sid, sd, os.path.join(out, f"{sid.replace('/', '_')}_{mm}_s{sd}")) for sid in ids for sd in seeds]
    jobs = [j for j in jobs if not os.path.exists(j[2] + '.res')]
    print(f'試合 {len(jobs)}（済みは飛ばす）、並列 {par}', flush=True)

    def go(j):
        sid, sd, prefix = j
        cmd = ['nice', '-n', '10', sys.executable, '-m', 'selfplay.select_scripts', 'one', sid, mm, pc, str(sd), prefix]
        try:
            p = subprocess.run(cmd, cwd=PLAY, capture_output=True, text=True, timeout=timeout)
            if p.returncode:
                open(prefix + '.err', 'w').write(p.stderr[-4000:])
        except subprocess.TimeoutExpired:
            open(prefix + '.err', 'w').write('timeout')
        return sid
    with ThreadPoolExecutor(par) as ex:
        for i, sid in enumerate(ex.map(go, jobs), 1):
            if i % 10 == 0 or i == len(jobs):
                print(f'{i}/{len(jobs)}', flush=True)


def load_games(out):
    """{脚本: [(脚本家, 勝者, 最後の戦いに届いたか, 最後のループ終了時のエントロピー)]}"""
    G = collections.defaultdict(list)
    for res in glob.glob(os.path.join(out, '*.res')):
        base = os.path.basename(res)[:-4]
        stem, _, _ = base.rpartition('_s')
        sid_, _, mm = stem.rpartition('_')
        sid = sid_.replace('_', '/', 1)
        r = json.load(open(res, encoding='utf-8'))
        ent = None
        jl = res[:-4] + '.jsonl'
        if os.path.exists(jl):
            es = [json.loads(x)['entropy'] for x in open(jl, encoding='utf-8') if '"k": "belief"' in x]
            ent = es[-1] if es else None
        G[sid].append((mm, r.get('winner'), r.get('loop') == 'final', ent))
    return G


def pick(static, G, a):
    rows, ok = [], []
    for s in static:
        sid, g, why = s['id'], G.get(s['id'], []), []
        dmax = 8 if sid.startswith('posted/') else a.days_max
        if s['taboo']:
            why.append('タブー')
        if s['role_ok'] < a.elem or s['inc_ok'] < a.elem:
            why.append(f"要素 役{s['role_ok']}/事{s['inc_ok']}")
        if not a.days_min <= s['days'] <= dmax:
            why.append(f"日数{s['days']}")
        if s['info'] < a.info:
            why.append(f"情報{s['info']}")
        reach = ent = None
        if not g:
            why.append('試合なし')
        else:
            reach = sum(x[2] for x in g) / len(g)
            es = [x[3] for x in g if x[3] is not None]
            ent = sum(es) / len(es) if es else None
            if reach * 2 <= 1:
                why.append(f'到達{sum(x[2] for x in g)}/{len(g)}')
            if ent is None or not a.ent_min <= ent <= a.ent_max:
                why.append(f'ent{ent if ent is None else round(ent, 1)}')
        rows.append((not why, sid, s.get('kind', ''), s['diff'], len(g), reach, ent, why))
        if not why:
            ok.append(sid)
    for good, sid, kind, diff, n, reach, ent, why in rows:
        r = '-' if reach is None else f'{reach:.0%}'
        e = '-' if ent is None else f'{ent:.1f}'
        print(f"{'◎' if good else '×'} {sid:24} {kind:3} {diff or '':8} 試合{n:2} 到達{r:>4} ent{e:>5}  {'／'.join(why)}")
    print(f'\n通った {len(ok)}/{len(rows)}')
    for sid in ok:
        print(sid)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('static')
    p.add_argument('pats', nargs='+')
    p = sub.add_parser('games')
    p.add_argument('pats', nargs='+')
    p.add_argument('--mm', default='calcGBxhd')
    p.add_argument('--pc', default='read2')
    p.add_argument('--seeds', default='1-3')
    p.add_argument('-P', type=int, default=4, help='並列の数（合計のプロセスはコア数16以内、メモリに注意）')
    p.add_argument('--out', required=True)
    p.add_argument('--timeout', type=int, default=2400)
    p = sub.add_parser('one')
    p.add_argument('sid')
    p.add_argument('mm')
    p.add_argument('pc')
    p.add_argument('seed')
    p.add_argument('prefix')
    p = sub.add_parser('pick')
    p.add_argument('--static', required=True)
    p.add_argument('--games', required=True)
    p.add_argument('--elem', type=float, default=0.5)
    p.add_argument('--info', type=int, default=2)
    p.add_argument('--days-min', type=int, default=5)
    p.add_argument('--days-max', type=int, default=7)
    p.add_argument('--ent-min', type=float, default=3)
    p.add_argument('--ent-max', type=float, default=12)
    a = ap.parse_args(argv)
    if a.cmd == 'static':
        for sid in expand(a.pats):
            print(json.dumps(static_row(sid), ensure_ascii=False), flush=True)
    elif a.cmd == 'games':
        run_games(expand(a.pats), a.mm, a.pc, seeds_of(a.seeds), a.P, a.out, a.timeout)
    elif a.cmd == 'one':
        one_game(a.sid, a.mm, a.pc, a.seed, a.prefix)
    else:
        pick([json.loads(x) for x in open(a.static, encoding='utf-8') if x.strip()], load_games(a.games), a)


if __name__ == '__main__':
    main()
