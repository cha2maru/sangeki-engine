"""Claude が片方を担当して自動プレイヤーと対戦するための道具（ユーザー指示: 自動の側の弱点を調べる）。

対戦は「乱数の種＋それまでの Claude の選択の一覧」から毎回最初から再生する。Claude の番で止まり、
盤面と選べる手を表示して終了する。Claude は選択を decisions ファイルに1行（JSON）足して再実行する。

    cd play && /workspaces/sangeki/.venv/bin/python -m selfplay.play_cli --side mm --seed 5 --file selfplay/runs/claude_mm.jsonl

--side mm: Claude が脚本家（相手は DeductiveBlocker＝公開情報だけの主人公）
--side pc: Claude が主人公（相手は --opp route|hidden の脚本家）。表示は公開情報だけ（役職は出さない）
  {"kind": "pc_cards", "choice": [{"by": "A", "target": "C03", "card": "INTX"}, ...リーダーから3枚]}
  {"kind": "abilities", "choice": [["C06", 0, null], ["C04", 1, {"target": "C02"}]]}   （その日の友好能力の宣言。無ければ []）
  {"kind": "final", "choice": [{"char": "C03", "role": "KEY"}, ...全員、宣言順]}
  友好能力の引数の例: 情報屋 {"declare": "X_..."}、医者[2] {"target": ..., "mode": "remove"}、委員長 {"card": "PAR-"}、刑事[4] {"incident": "SUICIDE", "day": 2}
選択の書式（1行1つ、決定の順）:
  {"kind": "loop_setup", "choice": {"init": {"C18": "HOS"}}}   （手先・学者がいる脚本だけ。各ループの始め）
  {"kind": "mm_cards", "choice": [{"target": "C03", "card": "INT1"}, ...3枚]}
  {"kind": "mm_ability", "choice": "C03" | "B:SCH" | null}
  {"kind": "incident", "choice": {"target": ...} | {"par_target": ..., "int_target": ...} | null}
  {"kind": "killer", "choice": [{"char": "C08", "ability": "KILLER_KEY"}] | []}
  {"kind": "refuse", "choice": true | false}
  {"kind": "rumor", "choice": "B:SHR" | null}
--script で engine/scripts/<id>.json を使う。--opp は相手の型（脚本家: route|hidden|search、主人公: blind|read）
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import phases as ph  # noqa: E402
from engine.routes import enumerate_routes  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402


class NeedDecision(Exception):
    pass


def show(s, title, options):
    print(f'=== Claude の番: {title}（L{s["loop"]}D{s["day"]}、リーダー {s["leader"]}）')
    for c, v in s['chars'].items():
        r = ph.role(s, c)
        print(f"  {c} {v['area']} {'生' if v['alive'] else '死'} 不{v['par']} 友{v['gw']} 暗{v['int']} 護{v['guard']} [{r}]")
    print('  ボードの暗躍', s['boards'], ' 使用済み', s['used'])
    print('  勝ち筋（距離の近い順）:')
    for r in enumerate_routes(s)[:5]:
        print(f"    {r['total']:>2} {r['id']} via {r['via']} → {r['goal']}")
    print('  選べる手:', json.dumps(options, ensure_ascii=False)[:600])


class ClaudeMastermind:
    def __init__(self, decisions):
        self.d = list(decisions)

    def _next(self, kind, s, title, options):
        if not self.d:
            show(s, title, options)
            raise NeedDecision(kind)
        x = self.d.pop(0)
        assert x['kind'] == kind, f'決定の順がずれている: 期待 {kind} / ファイル {x["kind"]}'
        return x['choice']

    def loop_setup(self, s, script):
        if 'C18' not in script['init'] and 'C19' not in script['init']:
            return {}
        return self._next('loop_setup', s, 'ループの準備（{"init": {"C18": "HOS"}} 手先の初期エリア・{"scholar": "gw"} 学者）',
                          {'C18': 'C18' in script['init'], 'C19': 'C19' in script['init']})

    def mm_cards(self, s):
        ch = self._next('mm_cards', s, '伏せ札を3枚', {'hand_used': s['used']['M']})
        return [{'by': 'M', **c} for c in ch]

    def mm_ability(self, s, actor, ability=None):
        return self._next('mm_ability', s, f'脚本家能力（{actor}={ph.role(s, actor)}、{ability or ph.role(s, actor)} の能力）',
                          {'actor': actor, 'area': s['chars'][actor]['area']})

    def mm_rumor(self, s):
        return self._next('rumor', s, '不穏な噂（任意のボード1つに暗躍1。使わなければ null）', {})

    def incident_choice(self, s, inc):
        return self._next('incident', s, f"事件 {inc['id']}（犯人 {inc['culprit']}）", inc)

    def killer_pick(self, s):
        opts = ph.killer_options(s)
        return self._next('killer', s, 'キラーの任意能力', opts) if opts else []

    def refuse(self, s, cid):
        return self._next('refuse', s, f'{cid} の友好能力を拒否するか', {'char': cid, 'role': ph.role(s, cid)})


class BlockingMastermind(ClaudeMastermind):
    """対AIモードで Claude が脚本家を務める（ユーザー「ちょっと私と遊んでほしい」）。判断のたびに問いを qpath に書き、
    dpath（JSON の1行＝1決定）に答えが足されるまで待つ。Claude は qpath を Monitor で見て、dpath に1行足して答える。
    Claude の型が持たない判断（ルールXの公開の選び方など）は fallback（自動の脚本家）に任せる。"""

    def __init__(self, qpath, dpath, fallback):
        super().__init__([])
        self.qpath, self.dpath, self.fallback, self.n = qpath, dpath, fallback, 0
        open(dpath, 'a').close()

    def _next(self, kind, s, title, options):
        import contextlib
        import io
        import time
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            show(s, title, options)
        self.n += 1
        with open(self.qpath, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'n': self.n, 'kind': kind}, ensure_ascii=False) + '\n' + buf.getvalue())
        while True:
            lines = [l for l in open(self.dpath, encoding='utf-8') if l.strip()]
            if len(lines) >= self.n:
                x = json.loads(lines[self.n - 1])
                assert x['kind'] == kind, f'決定の順がずれている: 期待 {kind} / ファイル {x["kind"]}'
                return x['choice']
            time.sleep(1)

    def __getattr__(self, name):
        if name.startswith('__') or name in ('fallback', 'd'):
            raise AttributeError(name)
        return getattr(self.fallback, name)


CARD_CODES = ('札のコード: PAR+ 不安+1 / PAR- 不安-1（ループ1回）/ GW1 友好+1 / GW2 友好+2（ループ1回）/ INTX 暗躍禁止 / '
              'MV_V 移動↑↓ / MV_H 移動←→ / MVX 移動禁止（ループ1回）。ボードは B:HOS・B:SHR・B:CIT・B:SCH')


def show_public(s, title, extra, script=None):
    """主人公に見える盤面（公開シート・キャラクター名・事件名は日本語。Claude が主人公の点検: d07 で SPREAD を流布と読み違えた）。"""
    from engine.project import AREA_JA, INC_JA
    from engine.resolve import CHARS
    sc = dict(s['script'], **{k: v for k, v in (script or {}).items() if k in ('loops', 'set')})
    print(f'=== Claude（主人公）の番: {title}（ループ {s["loop"]}/{sc.get("loops", "?")}・{s["day"]}日目/{sc.get("days")}日、リーダー {s["leader"]}）')
    from ai_gm import SET_JA
    print(f"  公開シート: {SET_JA.get(sc.get('set', 'BTX'), sc.get('set'))}（ループ {sc.get('loops', '?')} 回 × {sc.get('days')} 日）")
    for c, v in s['chars'].items():
        if not v.get('present', True) or not v.get('area'):
            continue
        print(f"  {c} {CHARS[c]['name']}（臨界{CHARS[c]['limit']}） {AREA_JA.get(v['area'], v['area'])} {'生' if v['alive'] else '死'} "
              f"不{v['par']} 友{v['gw']} 暗{v['int']} 護{v['guard']}")
    print('  ボードの暗躍', {AREA_JA[k]: n for k, n in s['boards'].items()}, ' 使用済み', s['used'])
    print('  事件予定', [(i['day'], INC_JA.get(i['id'], i['id'])) for i in sc['incidents']], ' 公開された役職', s.get('revealed_roles', {}))
    print('  ' + CARD_CODES)
    print('  ', json.dumps(extra, ensure_ascii=False)[:500])


class ClaudeProtagonist:
    def __init__(self, decisions, rng, script=None):
        self.d, self.rng, self.script = list(decisions), rng, script

    def _next(self, kind, s, title, extra):
        if not self.d:
            show_public(s, title, extra, self.script)
            raise NeedDecision(kind)
        x = self.d.pop(0)
        assert x['kind'] == kind, f'決定の順がずれている: 期待 {kind} / ファイル {x["kind"]}'
        return x['choice']

    def pc_cards(self, s, order, mm_targets=None):
        return self._next('pc_cards', s, f'行動カード（{"→".join(order)}）', {'脚本家の伏せ札の置き場所': mm_targets})

    def abilities_for_day(self, s):
        return [tuple(x) for x in self._next('abilities', s, '友好能力の宣言（その日の分をまとめて）', {'1ループ1回で使用済みの能力': [x for x in s.get('ability_used_loop', []) if '#' in x]})]  # 主人公の能力だけ（不穏な噂 X_RUMOR などは非公開）

    def ability_candidates(self, s):
        return []

    def final_guesses(self, s):
        return self._next('final', s, '最後の戦い（全員の役職を宣言順に）', {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--side', default='mm', choices=['mm', 'pc', 'both'])
    # --side both: Claude 同士の対戦。--mm-file・--pc-file にそれぞれの決定、--as で自分の側。相手の番なら盤面は出さず「相手の番」とだけ出す
    ap.add_argument('--mm-file')
    ap.add_argument('--pc-file')
    ap.add_argument('--as', dest='as_side', choices=['mm', 'pc'])
    ap.add_argument('--opp', default=None)  # 相手の型（league の make が受け付ける名前）
    ap.add_argument('--script', default='s03_bomb')
    ap.add_argument('--seed', type=int, default=5)
    ap.add_argument('--file')
    a = ap.parse_args()
    def _load(f):
        return [json.loads(l) for l in open(f, encoding='utf-8') if l.strip()] if f and os.path.exists(f) else []
    decs = _load(a.file)
    rng = random.Random(a.seed)
    log = []
    from engine.scripts import by_id
    from selfplay.league import make
    script = by_id(a.script)
    if a.side == 'both':
        return main_both(a, script, rng, _load)
    if a.side == 'pc':
        pc = ClaudeProtagonist(decs, rng, script)
        player = Combo(make(a.opp or 'route', rng, script), pc)
    else:
        pc = make(a.opp or 'blind', rng, script)
        player = Combo(ClaudeMastermind(decs), pc)
    try:
        r = play_game(script, player, lambda k, rec: log.append((k, rec)))
        print('=== 対戦終了', r)
    except NeedDecision:
        pass
    # 直近の出来事。主人公側では、役職や理由を含む項目を伏せる（公開ログと同じ範囲）
    for k, rec in log[-8:]:
        if k == 'events' and rec.get('phase') != 'pc_knowledge':
            evs = rec.get('events') or []
            if a.side == 'pc':  # 対AIモードの公開ログと同じ文（ai_gm.describe）。死亡など起きた事実とフェイズは出し、理由は伏せる
                from ai_gm import describe
                lines = [x for x in (describe(e) for e in evs if isinstance(e, dict)) if x]
                if rec.get('phase') == 'actions':  # 行動解決で脚本家の札も表向きになる（公開）
                    from ai_gm import name
                    from engine.project import CARD_UI
                    mmc = next((r_['choice'] for k_, r_ in log if k_ == 'decisions' and r_.get('kind') == 'place_cards' and r_.get('who') == 'M'
                                and (r_.get('loop'), r_.get('day')) == (rec.get('loop'), rec.get('day'))), None)
                    if mmc:
                        lines.insert(0, '脚本家の札: ' + '、'.join(f"{name(x['target'])}={CARD_UI.get(x['card'], x['card'])}" for x in mmc))
                print(f"  出来事（L{rec.get('loop')}D{rec.get('day')} {rec.get('phase')}）: " + ' ／ '.join(lines)[:1500])
            else:
                print('  出来事', rec.get('phase'), json.dumps(evs, ensure_ascii=False)[:1500])
    if getattr(pc, 'ded', None) is not None:
        m, tot = pc.ded.marginals()
        top = {c: max(d.items(), key=lambda x: x[1]) for c, d in m.items() if d}
        print('  主人公の推理（各人の最有力の役職と確率）:', {c: (r, round(w / tot, 2)) for c, (r, w) in top.items()})


PC_KINDS = {'pc_cards', 'abilities', 'final'}


def main_both(a, script, rng, _load):
    """Claude 同士の対戦（脚本家・主人公とも Claude のサブエージェント）。毎回最初から再生し、決定の足りない側の番で止まる。
    --as の側の番なら、その側の盤面（主人公は公開情報だけ）を出す。相手の番なら「相手の番」とだけ出す（脚本家の盤面を主人公に見せない）。"""
    import contextlib
    import io
    log = []
    player = Combo(ClaudeMastermind(_load(a.mm_file)), ClaudeProtagonist(_load(a.pc_file), rng, script))
    buf = io.StringIO()
    turn, result = None, None
    try:
        with contextlib.redirect_stdout(buf):
            result = play_game(script, player, lambda k, rec: log.append((k, rec)))
    except NeedDecision as e:
        turn = 'pc' if e.args[0] in PC_KINDS else 'mm'
    if result is not None:
        print('TURN: end')  # 待つ側は1行目で判定するので、終了も1行目に出す
        print('=== 対戦終了', result)
        return
    print(f'TURN: {turn}')
    if turn != a.as_side:
        print('  相手の番。相手が決定を足すまで待つ。')
        return
    print(buf.getvalue()[-6000:])
    for k, rec in log[-8:]:
        if k == 'events' and rec.get('phase') != 'pc_knowledge':
            evs = rec.get('events') or []
            if a.as_side == 'pc':
                evs = [{kk: vv for kk, vv in e.items() if kk not in ('role', 'actor', 'cause', 'by', 'reason')} for e in evs if isinstance(e, dict)]
            print('  出来事', rec.get('phase'), json.dumps(evs, ensure_ascii=False)[:1500])


if __name__ == '__main__':
    main()
