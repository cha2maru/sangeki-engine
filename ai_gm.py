"""対AIモード: 人間がブラウザの盤面（board.html）で主人公3人を操作し、エンジンと自動の脚本家が進行する。

    # 盤面サーバー（手動モードの game/ とは別の置き場所・ポート）
    SANGEKI_GAME=play/game_ai PORT=8766 node play/server.mjs
    # 進行役
    cd play && python ai_gm.py --script s03_bomb [--mm search] [--seed 1]

- 盤面（state.json）と公開ログ（log.jsonl）には公開情報だけを書く（engine/project.py の射影と、出来事の秘匿項目の除去）。
- 主人公の番では waiting に入力待ちを書き、inbox.jsonl に届いた操作を読む（手動モードと同じ形式）。
- 公開の出来事は推理エンジン（selfplay.players.PublicObserver）にも通し、候補の割合を deduce.json に書く（推理シートの印）。
"""
import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import abilities as ab  # noqa: E402
from engine import phases as ph  # noqa: E402
from engine.adapter import NAME2C, UI2CARD, placements_from_inbox  # noqa: E402
from engine.project import AREA_JA, CARD_UI, INC_JA, project  # noqa: E402
from engine.resolve import CARDS, CHARS, ONCE  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.players import PublicObserver  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402
from engine import sets  # noqa: E402

ROLES = json.load(open(os.path.join(HERE, 'engine', 'data', 'roles.json'), encoding='utf-8'))['roles']
RULES = json.load(open(os.path.join(HERE, 'engine', 'data', 'rules.json'), encoding='utf-8'))['rules']
ROLE_JA = {k: (v['name'] if isinstance(v, dict) else v) for k, v in ROLES.items()}
RULE_JA = {k: (v['name'] if isinstance(v, dict) else v) for k, v in RULES.items()}
for _sid in sets.ids():  # Basic Tragedy X 以外のセットの役職・ルール（First Steps のマイナスなど）
    for _k, _v in sets.load(_sid)['roles'].items():
        ROLE_JA.setdefault(_k, _v['name'])
    for _k, _v in sets.load(_sid)['rules'].items():
        RULE_JA.setdefault(_k, _v['name'])
JA2ROLE = {v: k for k, v in ROLE_JA.items()}
JA2RULE = {v: k for k, v in RULE_JA.items()}
JA2INC = {v: k for k, v in INC_JA.items()}
INC_EFFECT = {v['name']: v['effect'] for v in json.load(open(os.path.join(HERE, 'engine', 'data', 'incidents.json'), encoding='utf-8'))['incidents'].values()}
SET_JA = {'BTX': 'Basic Tragedy X'}
SECRET = ('role', 'actor', 'cause', 'by', 'culprit', 'reason')  # 公開ログに出さない項目（役職や理由を含みうる）
END_ABILITY = '能力の宣言を終える'
END_FINAL = '指摘を終える'


COUNTER_JA = {'par': '不安', 'gw': '友好', 'int': '暗躍', 'guard': '護衛'}


def name(c):
    if isinstance(c, str) and c.startswith('B:'):
        return AREA_JA[c[2:]]
    return CHARS[c]['name'] if c in CHARS else str(c)


PHASE_JA = {'loop_start': 'ループの準備', 'turn_start': 'ターン開始フェイズ', 'mm_cards': '脚本家行動フェイズ', 'pc_cards': '主人公行動フェイズ',
            'actions': '行動解決フェイズ', 'mm_ability': '脚本家能力フェイズ', 'ability': '主人公能力フェイズ', 'incident': '事件フェイズ',
            'turn_end': 'ターン終了フェイズ', 'loop_end_check': 'ループ終了時の判定'}


class Table:
    """ゲームの置き場所（state.json・log.jsonl・inbox.jsonl・deduce.json）の読み書き。"""

    def __init__(self, d, script):
        self.d, self.script = d, script
        self._reset_files()
        self.n, self.phase_id, self.read = 0, 0, 0
        self.game = int(time.time())  # 試合の識別子（盤面が前の試合の送信済みを出さないため）
        self.s, self.placed, self.phase_name = None, [], '準備'
        self.waiting = {'kind': 'none', 'text': '脚本家の番', 'options': []}
        self.reveal = None  # ゲーム終了後の非公開シート（盤面のダイアログ）
        self.reply = ''  # 「脚本家の返答」の欄（反則の置き方などの知らせ。公開ログには書かない＝振り返りに出さない）
        self.inc_result = {}
        self.last_phase = None
        self.ability_options = None
        self.mm_targets_hist = []
        # リプレイの URL 用の記録（record.json）: 主人公の受け付けた操作 h と、脚本家の判断の結果 m。再生すれば同じ試合になる
        # （エンジンのルール処理は乱数を使わない）。crc: 公開ログの文の照合値（再生が食い違えば、エンジンの版が違う）
        # v2: 判断ごとに、その時点の公開ログの行数 n を付ける（途中の場面から再開するときの切り口。m は [n, 値]、h は {'n': …}）
        self.record = {'v': 2, 'h': [], 'm': [], 'n': 0, 'crc': 0}
        # 目隠しでは試合中の記録を private/ に書く（record.json は盤面から /record で配信され、脚本の id と脚本家の判断の並び＝役職の手がかりが見える）。
        # 試合の後に record.json として公開する
        self.record_file = 'record.json'
        self.replay = None  # 再生中なら記録（record の形）。wait は記録から返す
        self.rejected = []  # 受け付けなかった操作（公開ログには書かない。リプレイで見返せるように rejected.json に残す。ユーザー指摘 2026-10-10）

    # ファイルの読み書きはここにまとめる（ブラウザ版 web/web_gm.py はこれを差し替えて、ファイルの代わりに盤面へ直接送る）
    def _reset_files(self):
        os.makedirs(self.d, exist_ok=True)
        for f in ('log.jsonl', 'inbox.jsonl', 'snapshots.jsonl', 'beliefs.jsonl'):
            open(os.path.join(self.d, f), 'w').close()
        for f in ('deduce.json', 'record.json', 'rejected.json'):  # 前の試合の推理・記録を残さない
            if os.path.exists(os.path.join(self.d, f)):
                os.remove(os.path.join(self.d, f))

    def _append(self, f, obj):
        with open(os.path.join(self.d, f), 'a', encoding='utf-8') as fp:
            fp.write(json.dumps(obj, ensure_ascii=False) + '\n')

    def phase(self, loop, day, key, extra=''):
        """フェイズの区切りをログに出す（フェイズの進行は常に公開する）。"""
        if (loop, day, key) == self.last_phase or key not in PHASE_JA:
            return
        self.last_phase = (loop, day, key)
        where = f'L{min(loop, self.script["loops"])}' + (f'D{day}' if day else '') if loop else ''
        self.log(f'── {where} {PHASE_JA[key]} ──' + (f' {extra}' if extra else ''))

    def _write(self, f, obj):
        tmp = os.path.join(self.d, f + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as fp:
            json.dump(obj, fp, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.d, f))

    def log(self, text):
        import zlib
        self.n += 1
        self.record['n'], self.record['crc'] = self.n, zlib.crc32(text.encode('utf-8'), self.record['crc'])
        self._write(self.record_file, self.record)
        s = self.s or {}
        loop = min(s['loop'], self.script['loops']) if s.get('loop') else None  # 最後の戦いではエンジンのループ番号が1つ進んでいる
        self._append('log.jsonl', {'n': self.n, 'loop': loop, 'day': s.get('day'), 'text': text})

    def show(self, s=None, phase_name=None, waiting=None, placed=None, bump=True):
        """bump=False: 同じ段階の中での表示の更新（phaseId を変えないので、送信済みの操作が古い扱いにならない）。"""
        if s is not None:
            self.s = s
        if phase_name is not None:
            self.phase_name = phase_name
        if waiting is not None:
            self.waiting = waiting
        if placed is not None:
            self.placed = placed
        if self.s is None:
            return
        if bump:
            self.phase_id += 1
        pub = project(self.s, self.placed)
        for i in pub['incidents']:
            i['effect'] = INC_EFFECT.get(i['name'], '')
            r = self.inc_result.get((self.s['loop'], i['day']))
            if r:
                i['result'] = r
        sc = self.script
        # 公開シートに書かれること（惨劇セット・ループ数・日数・事件予定・特殊ルール）。テリトリーはゲームの準備で縄張りトークンを置くので公開（大物の特性）。
        # 神格・転校生の登場の時期は脚本作成時に脚本家が決める非公開の情報（カードの特性、wiki/concepts/script.md）。以前は出していた（ユーザー指摘 2026-10-10）
        pub['publicSheet'] = {'set': SET_JA.get(sc.get('set', 'BTX'), sc.get('set')), 'loops': sc['loops'], 'days': sc['days'],
                              'territory': AREA_JA.get(sc.get('territory')) if sc.get('territory') else None,
                              'special': sc.get('special') or ''}
        pub['loop'] = min(pub['loop'], self.script['loops'])  # 最後の戦いではエンジンのループ番号が1つ進んでいる
        pub['live'] = self.replay is None  # 記録から作り直している間は False（途中から再開する盤面が、作り直しの終わりを待つ）
        pub.update({'mode': 'ai', 'reply': self.reply, 'reveal': self.reveal,  # 対AIモード（盤面は手動モード用の欄＝脚本家に伝える・ハッシュ・異議を出さない）
                    'game': self.game, 'phaseId': self.phase_id, 'phaseName': self.phase_name, 'waiting': self.waiting,
                    'loops': self.script['loops'], 'days': self.script['days'],
                    # 盤面は「キャラクター名#番号」で使用済みを判定する（エンジンは「C07#0」）
                    'abilityUsedToday': [self._tag(x) for x in self.s.get('ability_used_today', [])],
                    'abilityUsedLoop': [self._tag(x) for x in self.s.get('ability_used_loop', []) if '#' in str(x)],  # 主人公の能力だけ（不穏な噂は非公開）
                    # 主人公能力フェイズの間だけ、いま合法な宣言（盤面の能力のメニューを絞る）
                    'abilityOptions': self.ability_options if (self.waiting or {}).get('kind') == 'ability' else None})
        self._write('state.json', pub)
        # 盤面の履歴（ログ #n の処理が反映された直後の盤面）。盤面の「過去の盤面」「解決前／解決後」が読む（手動モードの gm.py と同じ形）
        if self.n > getattr(self, '_snap_n', 0):
            self._append('snapshots.jsonl', {'prev_n': getattr(self, '_snap_n', 0), 'log_n': self.n, 'state': pub})
            self._snap_n = self.n

    @staticmethod
    def _tag(x):
        c, _, i = str(x).partition('#')
        return f'{name(c)}#{i}'

    def reject(self, what, why):
        """受け付けなかった操作を「脚本家の返答」に出し、rejected.json に残す（n は直前の公開ログの番号）。"""
        self.reply = f'{why}。置き直してください' if what.startswith('札') else why
        s = self.s or {}
        self.rejected.append({'n': self.n, 'loop': min(s['loop'], self.script['loops']) if s.get('loop') else None, 'day': s.get('day'),
                              'phase': self.phase_name, 'what': what, 'why': why})
        self._write('rejected.json', self.rejected)

    def take(self, types):
        """主人公の操作を1つ受け取る（wait）。リプレイの記録に残し、再生中は記録から返す。"""
        if self.replay is not None:
            if self.replay['h']:
                rec = dict(self.replay['h'].pop(0))
                self.record['h'].append(dict(rec, n=self.n))
                return rec
            if not self.replay.get('resume'):
                raise ReplayEnd()
            self.replay = None  # 再開: ここから先は盤面からの操作を待つ
            self.show(bump=True)
        rec = self.wait(types)
        self.record['h'].append({**{k: v for k, v in rec.items() if k not in ('seq', 'at', 'game', 'phaseId', 'label')}, 'n': self.n})
        self._write(self.record_file, self.record)
        return rec

    def wait(self, types):
        """いまの phaseId に対する、types のどれかの操作を1つ待って返す。"""
        path = os.path.join(self.d, 'inbox.jsonl')
        while True:
            lines = open(path, encoding='utf-8').read().splitlines()
            for line in lines[self.read:]:
                self.read += 1
                rec = json.loads(line)
                if rec.get('phaseId') == self.phase_id and rec.get('type') in types:
                    return rec
                if rec.get('type') == 'message':
                    self.log(f'（伝言を受け取った: {rec.get("text")}）')
            time.sleep(0.5)


PUBCHARS = json.load(open(os.path.join(HERE, 'engine', 'data', 'characters_public.json'), encoding='utf-8'))


def _cards_text(rec):
    return '、'.join(f"{p.get('actor')} {p.get('target')}={p.get('card')}" for p in rec.get('placements') or [])


class ReplayEnd(Exception):
    """リプレイの記録を使い切った（共有したときの試合の位置まで再生した）。"""


def ability_label(cid, idx):
    """友好能力の表示名（盤面のメニューと同じ「[友好]★ 文面」。公開のキャラクター情報から）。"""
    a = next((x for x in PUBCHARS.get(cid, {}).get('abilities', []) if x['idx'] == idx), None)
    return f"[{a['need']}]{'★' if a['once'] else ''} {a['text']}" if a else f'{cid}#{idx}'


class RecordingMM:
    """自動の脚本家の判断の結果を記録する（リプレイの URL 用。思考は記録しない）。"""
    def __init__(self, mm, record, table, queue=None):
        # queue: 再開のとき、記録した判断（[n, 値]）。残っている間は考えずに返し、尽きたら本物の脚本家が考える
        self.__dict__.update(_mm=mm, _rec=record, _t=table, _q=list(queue or []))

    def __getattr__(self, n):
        v = getattr(self._mm, n)
        if n in Combo.MM and callable(v):
            def f(*a, **k):
                r = (_mval(self._q.pop(0)) if self._q else v(*a, **k))
                self._rec['m'].append([self._t.n, json.loads(json.dumps(r))])
                return r
            return f
        return v


def _mval(x):
    """記録の脚本家の判断の値（v1 は値だけ、v2 は [n, 値]）。"""
    return x[1] if _V2[0] else x


_V2 = [True]  # 再生する記録が v2 か（ReplayMM・RecordingMM が _mval で使う）


class ReplayMM:
    """記録した脚本家の判断の結果を順に返す（考えない）。持っていた判断の種類（has）も元の脚本家と同じにする。"""
    def __init__(self, record, has):
        self._q, self._has, self.rng = list(record['m']), set(has), random.Random(0)

    def __getattr__(self, n):
        if n.startswith('_') or n not in self._has:
            raise AttributeError(n)
        if n == 'observe':
            return lambda *a, **k: None

        def f(*a, **k):
            if not self._q:
                raise ReplayEnd()
            return _mval(self._q.pop(0))
        return f


def describe(e):
    """公開の出来事1つを日本語の1行に。秘匿の項目は使わない。"""
    k = e.get('kind')
    if k == 'revealed':  # 行動解決で6枚が公開された。脚本家の札は「脚本家の札が公開された」の行で出すので、ここは主人公の札（振り返り用）
        pc = [x for x in e.get('cards', []) if x.get('by') != 'M']
        return ('主人公の札: ' + '、'.join(f'{x["by"]} {name(x["target"])}={CARD_UI.get(x["card"], x["card"])}' for x in pc)) if pc else None
    if k == 'move':
        src = f'{AREA_JA.get(e["from"], e["from"])} → ' if e.get('from') else ''  # 行方不明の移動には移動元が無い
        return f'{name(e["char"])} が {src}{AREA_JA.get(e.get("to"), e.get("to"))} へ移動'
    if k == 'mm_ability':
        # 脚本家は理由を説明しない。役職の能力は「どこからともなく」と伝える（主人公の書 印刷p23）
        if e.get('role') == 'DOCTOR':  # 医者[2] を脚本家が使った（使ったこと自体は公開）
            return f'脚本家が{name(e.get("actor"))}の能力を使った: {name(e["target"])} の不安を' + ('取り除いた' if e.get('mode') == 'remove' else '置いた')
        ja = next((j for c, j in (('int', '暗躍'), ('par', '不安'), ('gw', '友好')) if c in e), 'カウンター')
        return f'どこからともなく {name(e["target"])} に{ja}+1'
    if k == 'no_death':
        return f'{name(e["char"])} は死亡しなかった'
    if k == 'guarded':
        return f'{name(e["char"])} の死亡は護衛で防がれた'
    if k == 'substitute':
        return f'{name(e["char"])} が {"・".join(name(x) for x in (e.get("for") or []))} の身代わりになった'
    if k == 'gwx_ignored':
        return f'{name(e["target"])} への友好禁止は無視された'
    if k == 'intx_ignored':
        return f'{name(e["target"])} への暗躍禁止は無視された'
    if k == 'move_blocked':
        return f'{name(e["char"])} への移動は移動禁止で止まった'
    if k == 'move_forbidden':
        return f'{name(e["char"])} の移動は禁止エリアのため無効'
    if k in ('par', 'gw', 'int'):
        if e.get('from') is None and e.get('to') is None:
            if e.get('n'):  # 因果の糸・事件の効果など、増えた数だけの記録（止められない増減。公開される）
                ja = {'par': '不安', 'gw': '友好', 'int': '暗躍'}[k]
                # 出典がルール・事件・カードの特性のときだけ出す（キャラクターの役職が出典なら割れうる）
                src = RULE_JA.get(e.get('by')) or INC_JA.get(e.get('by')) or {'SCHOLAR': '学者の特性', 'BLACK_CAT': '黒猫の特性'}.get(e.get('by'))
                return f'{name(e["target"])} に{ja}{e["n"]:+d}' + (f'（{src}）' if src else '')
            return None  # ループの準備での片付けなど
        ja = {'par': '不安', 'gw': '友好', 'int': '暗躍'}[k]
        card = f'（{CARD_UI.get(e.get("card"), e.get("card"))}）' if e.get('card') else ''
        return f'{name(e["target"])} の{ja} {e.get("from")} → {e.get("to")}{card}'
    if k == 'nullified':
        return f'{name(e["target"])} への {"・".join(CARD_UI.get(c, c) for c in e.get("cards", []))} は無効になった'
    if k == 'intx_void':
        return f'暗躍禁止が複数出たので、{name(e["target"])} の暗躍禁止は無効'
    if k == 'used':
        return None
    if k == 'incident':
        return f'{e["day"]}日目 {INC_JA.get(e["id"], e["id"])}: ' + ('発生した' if e['occurred'] else '発生しなかった') + \
            (f'（不安が臨界以上: {"・".join(name(c) for c in e.get("critical", []))}）' if e.get('critical') else '')
    if k == 'death':
        return f'{name(e["char"])} が死亡した'
    if k == 'appear':
        return f'{name(e["char"])} が {AREA_JA.get(e.get("area"), e.get("area"))} に登場した'
    if k == 'refused':
        return f'{name(e["char"])} の友好能力は拒否された'
    if k == 'ability':
        extra = ''
        if 'reveal_role' in e:
            (c, r), = e['reveal_role'].items()
            extra = f' → {name(c)} の役職は {ROLE_JA.get(r, r)}'
        if isinstance(e.get('reveal_rule_x'), str):
            extra = f' → ルールX {RULE_JA.get(e["reveal_rule_x"], e["reveal_rule_x"])}'
        if e.get('returned'):  # 委員長[2]: 手札に戻した札
            extra += f" → {CARD_UI.get(e['returned'], e['returned'])} を手札に戻した"
        if 'reveal_culprit' in e:
            extra = ' → 犯人: ' + '、'.join(f'{INC_JA.get(i, i)}={name(c)}' for i, c in e['reveal_culprit'].items())
        # カウンターの増減・移動などの結果（公開の盤面に現れる事実）も1行に添える
        eff = {'int-': '暗躍-1', 'int+': '暗躍+1', 'par-': '不安-1', 'par+': '不安+1', 'par+2': '不安+2', 'gw+': '友好+1',
               'guard+': '護衛+1', 'int_to_gw': '暗躍→友好', 'move': '移動', 'transfer': 'カウンターを移動', 'revive': '蘇生',
               'clear': '全てのカウンターを取り除いた'}
        if isinstance(e.get('par'), (list, tuple)) and len(e['par']) == 2:  # 医者[2] など: (対象, 'place'|'remove')
            t, mode = e['par']
            extra += f" → {name(t)} 不安{'+1' if mode == 'place' else '-1'}"
        if isinstance(e.get('transfer'), (list, tuple)) and len(e['transfer']) == 3:  # 鑑識官[2]: (元, 先, カウンター)
            a, b, cnt = e['transfer']
            extra += (f" → {name(a)} から {name(b)} へ{COUNTER_JA.get(cnt, cnt)}を1つ移動" if cnt else
                      f" → {name(a)} と {name(b)} に動かせるカウンターが無かった（何も起きない）")
        for key, ja in eff.items():
            if key in e and not (key == 'transfer' and isinstance(e[key], (list, tuple)) and len(e[key]) == 3):
                v = e[key]
                v = '・'.join(name(x) for x in v) if isinstance(v, (list, tuple)) else name(v) if isinstance(v, str) else v
                extra += f' → {v} {ja}' if not isinstance(v, dict) else f' → {ja}'
        return f'{name(e["char"])} の友好能力を使った{extra}'
    if k == 'role_public':
        return f'{name(e["char"])} の役職が公開された: {ROLE_JA.get(e["role"], e["role"])}'
    if k == 'loop_end_check':
        return 'ループ終了時の判定: ' + ('主人公の敗北' if e.get('loss') else '敗北条件は無い')
    if k == 'loop_end':
        # 脚本家は理由を説明しない（主人公の書 印刷p23、wiki/concepts/script.md）。主人公の死亡は「あなたたちは死亡しました」とだけ伝え、
        # キーパーソンの死亡・タイムトラベラーなどは敗北したことだけを伝える（理由の文をそのまま出すと役職が割れる）
        # どのフェイズで終わったかは公開情報（ユーザー指摘）
        ph_ja = {'turn_start': 'ターン開始フェイズ', 'actions': '行動解決フェイズ', 'mm_ability': '脚本家能力フェイズ', 'ability': '主人公能力フェイズ',
                 'incident': '事件フェイズ', 'turn_end': 'ターン終了フェイズ'}.get(e.get('at_phase'), '')
        head = f'{ph_ja}に' if ph_ja else ''
        return head + ('あなたたちは死亡した。ループが終わった' if e.get('reason') == '主人公死亡' else 'ループが終わった（主人公の敗北）')
    rest = {kk: vv for kk, vv in e.items() if kk not in SECRET and kk != 'kind'}
    return f'{k} {json.dumps(rest, ensure_ascii=False)}'


class HumanProtagonist:
    """主人公3人の判断を、盤面からの操作で行う。"""

    def __init__(self, table, rng):
        self.t, self.rng = table, rng

    def pc_cards(self, s, order, mm_targets=None):
        self.t.s = s  # ログの行の L・D を正しくする
        self.t.mm_targets_hist += list(mm_targets or [])  # 狙いの読み（推理シートの「狙い込み」）に使う。伏せ札の置き場所は公開情報
        mm = [{'by': 'M', 'target': x, 'card': 'INT1'} for x in (mm_targets or [])]
        self.t.phase(s['loop'], s['day'], 'mm_cards', '伏せ札: ' + '・'.join(name(x) for x in mm_targets or []))
        self.t.phase(s['loop'], s['day'], 'pc_cards', f'リーダー {order[0]}')
        while True:
            self.t.show(s, f'L{s["loop"]} {s["day"]}日目 主人公行動フェイズ',
                        {'kind': 'cards', 'text': (f'{self.t.reply} ／ ' if self.t.reply else '') + f'行動カードを3枚（{"→".join(order)}）置いて『確定』。脚本家の伏せ札は '
                         + '・'.join(name(x) for x in mm_targets or []), 'options': []}, placed=mm)
            rec = self.t.take(('cards',))
            # 受け付けなかった置き方はログに書かない（振り返りには成功した配置だけを出す。行動解決で「主人公の札: …」の行になる）
            try:
                pc = placements_from_inbox(rec)
            except KeyError as e:
                self.t.reject('札: ' + _cards_text(rec), f'札を読めなかった（{e}）')
                continue
            err = self._check(s, pc)
            if err:
                self.t.reject('札: ' + _cards_text(rec), f'札の置き方が合法でない: {err}')
                continue
            self.t.reply = ''
            return sorted(pc, key=lambda x: order.index(x['by']))

    @staticmethod
    def _check(s, pc):
        from engine.options import pc_placement_error  # 窓口（env.py）と同じ判定（plan/engine-api.md 3章）
        return pc_placement_error(s, pc)

    def ability_one(self, s):
        """主人公能力フェイズの宣言を1件受け付けて返す（エンジンがその場で解決し、結果を公開ログに書く）。
        『能力の宣言を終える』なら None。前の能力の結果を見て次を選べるように、1件ずつ解決する（ユーザー指摘）。"""
        key = (s['loop'], s['day'])
        if getattr(self, '_ab_day', None) != key:
            self._ab_day = key
            # 脚本家能力フェイズの区切りは毎日必ず出す（能力を持つ役職がいる日だけ出すと、その存在が漏れる）
            self.t.phase(s['loop'], s['day'], 'mm_ability')
            self.t.phase(s['loop'], s['day'], 'ability')
        while True:
            self.t.ability_options = self._options(s)  # 前の能力の解決で盤面が変わるので毎回作り直す
            self.t.show(s, f'L{s["loop"]} {s["day"]}日目 主人公能力フェイズ',
                        {'kind': 'ability', 'text': (f'{self.t.reply} ／ ' if self.t.reply else '') + 'リーダーから友好能力を宣言（右クリック）。1件ずつその場で解決する。宣言し終えたら『' + END_ABILITY + '』',
                         'options': [END_ABILITY]})
            rec = self.t.take(('ability', 'option'))
            if rec['type'] == 'option':
                return None
            dec = self._match(s, rec)
            if dec is None:  # 受け付けなかった宣言はログに書かない（「脚本家の返答」に出す）
                lab = ability_label(NAME2C.get(rec.get('char')), rec.get('ability'))
                tg = '・'.join(f'{k}={rec[k]}' for k in ('target', 'mode', 'from', 'to', 'counter', 'declare', 'incident', 'day', 'board', 'card') if rec.get(k) is not None)
                self.t.reject(f'能力: {rec.get("char")} {lab}' + (f'（{tg}）' if tg else ''),
                              f'{rec.get("char")} の能力 {lab} は、いまは使えない（友好・場所・対象・回数を確認）')
                self.t.show(bump=False)
                continue
            self.t.reply = ''
            self.t.log(f'宣言を受け付けた: {rec.get("char")} {ability_label(dec[0], dec[1])}')
            return dec

    @staticmethod
    def _options(s):
        """いま合法な宣言を、盤面のメニュー向けに {'キャラ名#番号': {'targets': [...], 'modes': [...]}}。"""
        out = {}
        for c, i, a in ab.declaration_options(s):
            a = a or {}  # 引数の無い能力（巫女[3] など）は None
            o = out.setdefault(f'{name(c)}#{i}', {'targets': [], 'modes': []})
            t = a.get('target')
            if t and name(t) not in o['targets']:
                o['targets'].append(name(t))
            if 'from' in a:  # 鑑識官[2]★: 同じエリアの2人の間でカウンター1つを移す
                for key, v in (('froms', name(a['from'])), ('tos', name(a['to'])), ('counters', COUNTER_JA.get(a.get('counter')))):
                    if v and v not in o.setdefault(key, []):
                        o[key].append(v)
            m = {'remove': '取り除く', 'place': '置く'}.get(a.get('mode'))
            if m and m not in o['modes']:
                o['modes'].append(m)
            # 合法な宣言1件ずつ（表示名と、盤面が送る中身）。盤面はこれを並べるだけ（plan/engine-api.md 3章: 合法な手はエンジンだけが出す）
            send, lab = {}, []
            if a.get('target'):
                send['target'] = name(a['target']) if a['target'] in CHARS else a['target']
                lab.append(AREA_JA.get(a['target'][2:], a['target']) + '（ボード）' if a['target'].startswith('B:') else name(a['target']))
            if 'from' in a:
                send.update({'from': name(a['from']), 'to': name(a['to']), 'counter': COUNTER_JA.get(a.get('counter'))})
                lab.append(f"{name(a['from'])} → {name(a['to'])}" + (f"（{COUNTER_JA.get(a.get('counter'))}）" if a.get('counter') else ''))
            if a.get('mode'):
                send['mode'] = m
                lab.append(f'不安を{m}')
            if a.get('board'):
                send['board'] = a['board']
                lab.append(f"→ {AREA_JA.get(a['board'], a['board'])}")
            if a.get('counter') and 'from' not in a:
                send['counter'] = COUNTER_JA.get(a['counter'], a['counter'])
                lab.append(f"{COUNTER_JA.get(a['counter'], a['counter'])}を移す")
            if a.get('declare'):
                send['declare'] = RULE_JA.get(a['declare'], a['declare'])
                lab.append(f"ルール「{send['declare']}」を宣言")
            if a.get('incident'):
                send.update({'incident': INC_JA.get(a['incident'], a['incident']), **({'day': a['day']} if a.get('day') else {})})
                lab.append(f"{a.get('day', '')}日 {INC_JA.get(a['incident'], a['incident'])}")
            if a.get('card'):
                send['card'] = CARD_UI.get(a['card'], a['card'])
                lab.append(f"札 {send['card']}")
            o.setdefault('choices', []).append({'label': '・'.join(lab) or '（対象なし）', 'send': send})
        return out

    @staticmethod
    def _match(s, rec):
        cid = NAME2C.get(rec.get('char'))
        idx = rec.get('ability')
        want = {}
        if rec.get('target'):
            want['target'] = NAME2C.get(rec['target'], rec['target'])
        if rec.get('from'):
            want['from'] = NAME2C.get(rec['from'], rec['from'])
            want['to'] = NAME2C.get(rec.get('to'), rec.get('to'))
            want['counter'] = {v: k for k, v in COUNTER_JA.items()}.get(rec.get('counter'))
        if rec.get('board'):  # 幻想[3]: 移動先のボード（HOS など）
            want['board'] = rec['board']
        if rec.get('counter') and not rec.get('from'):  # ご神木: 移すカウンター
            want['counter'] = {v: k for k, v in COUNTER_JA.items()}.get(rec['counter'], rec['counter'])
        if rec.get('mode'):
            want['mode'] = {'取り除く': 'remove', '置く': 'place'}.get(rec['mode'], rec['mode'])
        if rec.get('declare'):
            want['declare'] = JA2RULE.get(rec['declare'], rec['declare'])
        if rec.get('incident'):
            want['incident'] = JA2INC.get(rec['incident'], rec['incident'])
        if rec.get('day'):  # 同じ名前の事件が2つある脚本（2日・4日の遠隔殺人など）
            want['day'] = rec['day']
        if rec.get('card'):  # 委員長[2]: 手札に戻す札（盤面は表示名で送る）。以前は見ておらず、使用済みの先頭の札が戻っていた
            want['card'] = UI2CARD.get(rec['card'], rec['card'])
        for c, i, a in ab.declaration_options(s):
            if c == cid and i == idx and all((a or {}).get(k) == v for k, v in want.items() if k in (a or {})):
                return (c, i, a)
        return None

    def ability_candidates(self, s):
        return []

    def final_guesses(self, s):
        order, first = [], True
        while True:
            left = [c for c in s['chars'] if c not in {g['char'] for g in order}]
            self.t.show(s, '最後の戦い', {'kind': 'final', 'text': '右クリックで役職を1人ずつ指摘（宣言順に判定。外れた時点で脚本家の勝ち）。'
                                         f'残り {len(left)} 人。全員を指摘したら『{END_FINAL}』', 'options': [END_FINAL]}, bump=first)
            first = False
            rec = self.t.take(('final', 'option'))
            if rec['type'] == 'option':
                self.guesses = order + [{'char': c, 'role': 'PERSON'} for c in left]  # 終了後の非公開シートで答え合わせに使う
                return self.guesses
            c, r = NAME2C.get(rec['char']), JA2ROLE.get(rec['role'])
            if c in s['chars'] and r:
                order = [g for g in order if g['char'] != c] + [{'char': c, 'role': r}]
                self.t.log(f'指摘: {rec["char"]} は {rec["role"]}')


class Narrator:
    """公開の出来事を盤面・公開ログ・推理（deduce.json）に反映する観測者。"""

    def __init__(self, table):
        self.t = table
        self.pub = PublicObserver()

    def observe(self, s, phase, events):
        self.t.s = s
        if phase == 'incident':
            for e in events:
                if e.get('kind') == 'incident':
                    self.t.inc_result[(s['loop'], e['day'])] = '発生' if e['occurred'] else '不発'
        # 公開された札（主人公の札）を、移動やカウンターの行より先に（脚本家の札の行のすぐ後）
        for e in sorted(events, key=lambda x: not (isinstance(x, dict) and x.get('kind') == 'revealed')):
            if isinstance(e, dict):
                try:
                    line = describe(e)
                except Exception:  # ログの1行が作れなくても試合は止めない（秘匿の項目は除いて出す）
                    line = f'{e.get("kind")} ' + json.dumps({k: v for k, v in e.items() if k not in SECRET and k != 'kind'}, ensure_ascii=False)
                if line:
                    self.t.log(line)
        self.pub.observe(s, phase, events)
        # 行動解決の後は、公開された6枚（脚本家の札も表向き）を盤面に残す（その日のターン終了フェイズまで。リプレイでも見える）
        rev = next((e['cards'] for e in events if isinstance(e, dict) and e.get('kind') == 'revealed'), None)
        self.t.show(s, placed=[dict(x, open=True) for x in rev] if rev else [] if phase in ('actions', 'turn_end', 'loop_start') else None)
        self.deduce(s)

    def loop_done(self, s, loop):
        """ループ終了ごとに、主人公の推理シートの印（notes.json）と推理エンジンの割合（deduce.json）を beliefs.jsonl に並べて残す。
        ブラフが人間・Claude に効いたかを、エンジンの推理との差で見る（plan/mastermind_algorithm_evolution_plan.md H7）。"""
        if loop == 'final':
            return
        rd = lambda f: json.load(open(os.path.join(self.t.d, f), encoding='utf-8')) if os.path.exists(os.path.join(self.t.d, f)) else None
        self.t._append('beliefs.jsonl', {'loop': loop, 'notes': rd('notes.json'), 'deduce': rd('deduce.json')})

    def deduce(self, s):
        """推理エンジンの候補の割合（役職・ルール・犯人）を deduce.json に書く。"""
        import numpy as np
        from engine.deduce import incident_candidates
        ded = self.pub.ded
        if ded is None:
            return
        m, tot = ded.marginals()
        R, C = ded._sync()
        w = ded._w(C)
        wt = float(w.sum()) or 1.0
        rules = {}
        for rid in [x for x in RULE_JA if ded.T.has(x).any()]:  # そのセットのルール
            idx = [i for i, cb in enumerate(ded.T.combos) if rid in cb]
            rules[RULE_JA[rid]] = round(float(w[np.isin(C, idx)].sum()) / wt, 3)
        roles = {name(c): {ROLE_JA[r]: round(float(v) / tot, 3) for r, v in d.items() if v > 0} for c, d in m.items()} if tot else {}
        cands = incident_candidates(list(s['chars']), self.pub.inc_history, self.pub.culprits)
        culprits = {}
        for key, cs in cands.items():
            iid, _, day = key.partition('@')
            culprits[f'{day}日 {INC_JA.get(iid, iid)}'] = [name(c) for c in sorted(cs)]
        out = {'loop': s['loop'], 'day': s['day'], 'hypotheses': int(ded.n_hyp()),
               'roles': roles, 'rules': rules, 'culprits': culprits}
        if ded.empty_by:  # 推理の矛盾（仮説が全部消えた）。進行役の端末と推理のデータに出す（エンジンか推理の不具合）
            out['empty_by'] = [ded.empty_by[0], str(ded.empty_by[1])[:200]]
            if not getattr(self, '_warned_empty', False):
                self._warned_empty = True
                print(f'[推理の矛盾] 公開情報の推理の仮説が全部消えた（観測 {ded.empty_by[0]}）。エンジンか推理の不具合', file=sys.stderr, flush=True)
        else:
            out['intent'] = self._intent(s, ded)
        self.t._write('deduce.json', out)

    def _intent(self, s, ded, n=300, k=0.6):
        """狙い込みの見込み: 仮説を n 個引き、これまでの伏せ札の対象（全ループ）が、その仮説の近い勝ち筋に関わる数で
        exp(k×数) の重みを付けた、ルールと役職の割合（ユーザー「あからさまな動きでもキーパーソン・キラーの線が上がらない」。
        自動の主人公の intent と同じ考え。ponytail: 標本 n 個の近似で、犯人は数えない）。"""
        import math
        import random
        from selfplay.pc_v2 import involved
        past = self.t.mm_targets_hist
        if not past:
            return None
        picks = ded.sample(random.Random(f'{s["loop"]}:{s["day"]}:{len(past)}'), n)
        if not picks:
            return None
        rules, roles, tot = {}, {}, 0.0
        incs = [dict(i, culprit=None) for i in s['script']['incidents']]
        for combo, rl in picks:
            inv = involved(s, {'rules': list(combo), 'roles': rl, 'incidents': incs})
            w = math.exp(k * sum(t in inv for t in past))
            tot += w
            for r in combo:
                rules[RULE_JA.get(r, r)] = rules.get(RULE_JA.get(r, r), 0) + w
            for c in s['chars']:
                rj = ROLE_JA.get(rl.get(c, 'PERSON'), rl.get(c, 'PERSON'))
                roles.setdefault(name(c), {})
                roles[name(c)][rj] = roles[name(c)].get(rj, 0) + w
        return {'rules': {r: round(v / tot, 3) for r, v in rules.items()},
                'roles': {c: {r: round(v / tot, 3) for r, v in d.items()} for c, d in roles.items()}}


HISTORY = os.path.join(HERE, 'records', 'blind_history.jsonl')  # 目隠しで遊んだ脚本の記録（試合の後に追記。ルールの組は試合後に公開済み）
SEEN_PREFIX = ('gen_s21', 'gen_s22', 'gen_s23', 'gen_s24', 'gen_s25')  # Claude が一覧でルールを見てしまった生成脚本


def pick_blind(rng, pool='recommended'):
    """脚本を伏せて選ぶ。pool='recommended'（既定。開始画面の「脚本を伏せて遊ぶ」）: おすすめ（RECOMMENDED）のうち遊んだことのない
    脚本から一様に選ぶ（ユーザー 2026-10-17）。遊び尽くしたら自動生成の脚本から選ぶ。
    pool='generated'（Claude の目隠し対戦。おすすめの設計の脚本は Claude が中身を知っているので使わない）: 以下の選び方。
    Claude が中身を知らない生成脚本を、進行役が選ぶ（ユーザーの方針、2026-09-24）。
    - 遊んだことのあるルールの組（Y＋X2つ）は選ばない（同じルールは出てよいが、組み合わせは変える）。
    - 遊んだ回数が少ないルールほど少し選ばれやすい（重み＝各ルールの 1/(1+回数) の積の平方根）。確実に選ばれるルールは作らない。
    - 選んだ脚本の id もルールも表示しない。"""
    import glob
    from collections import Counter
    hist = [json.loads(l) for l in open(HISTORY, encoding='utf-8')] if os.path.exists(HISTORY) else []
    played_ids = {h['id'] for h in hist}
    played_combos = {tuple(sorted(h['rules'])) for h in hist}
    count = Counter(r for h in hist for r in h['rules'])
    from engine.scripts import glob_scripts
    if pool == 'recommended':
        rec = [i for i in RECOMMENDED if i.split('/')[-1] not in played_ids and glob_scripts(i + '.json')]
        if rec:
            return rng.choice(rec), len(rec)
    cands, ws = [], []
    for f in glob_scripts('generated/*.json'):
        sc = json.load(open(f, encoding='utf-8'))
        if sc['id'] in played_ids or sc['id'].startswith(SEEN_PREFIX) or tuple(sorted(sc['rules'])) in played_combos:
            continue
        w = 1.0
        for r in sc['rules']:
            w *= 1 / (1 + count[r])
        try:  # 勝ち筋の厚い脚本ほど選ばれやすく（ユーザーの感想「勝ち筋が少ない脚本は弱い」。selfplay.script_routes）
            from selfplay.script_routes import route_richness
            w *= max(0.1, route_richness(by_id('generated/' + sc['id']))['spread'])
        except Exception:
            pass
        cands.append('generated/' + sc['id'])
        ws.append(w ** 0.5)
    if not cands:
        raise SystemExit('目隠しで選べる脚本が無い（生成で補充する）')
    return rng.choices(cands, weights=ws)[0], len(cands)


def blind_count():
    """伏せて選べる脚本の数（おすすめ＋自動生成）。開始画面で「伏せて遊ぶ」を出すかの判定に使う。"""
    from engine.scripts import glob_scripts
    return sum(1 for i in RECOMMENDED if glob_scripts(i + '.json')) + len(glob_scripts('generated/*.json'))


THINK = {'loop_setup': 'ループの準備', 'mm_cards': '伏せ札', 'mm_ability': '脚本家能力', 'incident_choice': '事件の対象',
         'refuse': '友好能力を拒否するか', 'killer_pick': '任意能力', 'choose_rule_x': 'ルールの選択'}


def _thinking(t, what, f):
    def g(*a, **k):
        s = a[0] if a and isinstance(a[0], dict) and 'chars' in a[0] else None  # 1日目は盤面がまだ書かれていないので、ここで渡す
        t.show(s, waiting={'kind': 'thinking', 'text': f'脚本家が考えています（{what}）', 'what': what, 'since': time.time(), 'options': []}, bump=False)
        return f(*a, **k)
    return g


def _progress(t, every=0.3):
    """脚本家の進み具合（SearchMastermind._prog）を盤面に書く。書き込みは every 秒に1回まで（最後の 1.0 は必ず）。"""
    last = [0.0]

    def cb(frac, text):
        now = time.time()
        if (t.waiting or {}).get('kind') != 'thinking' or (now - last[0] < every and frac < 1.0):
            return
        last[0] = now
        t.show(waiting={**t.waiting, 'progress': round(frac, 3), 'step': text}, bump=False)
    return cb


def short_id(sid):
    """開始時に出す脚本の ID。最後の _ 以降（例 s03_bomb の bomb、gen_s11_036 の 036）は隠す（名前は内容の手がかりになる）。"""
    head, _, base = sid.rpartition('/')
    return (head + '/' if head else '') + base.rsplit('_', 1)[0]


# おすすめの脚本: selfplay/select_scripts の選定（2026-10-17、脚本家 calcGBxhd × 主人公 read2 × seed 1〜3、エントロピーの上限 12）を通った設計の脚本。
# 要素が脚本家にとって意味がある・日数5〜7・主人公が情報を取る手立てがある・脚本家がループを取りきる・最後の戦いがもつれる、で選んだ。
# 前回（2026-10-16、calcGB）の17本のうち d02（割れすぎ）・d26（最後の戦いに届かない）は外れ、14本が加わった。
# 非公開の脚本（投稿シナリオ・有志サイト）は入れない（tools/export_public.py の FORBID）
RECOMMENDED = ('designed/d05_contract', 'designed/d06', 'designed/d07', 'designed/d10', 'designed/d11', 'designed/d12',
               'designed/d14', 'designed/d15', 'designed/d18', 'designed/d19', 'designed/d23', 'designed/d27',
               'designed/d30', 'designed/d31', 'designed/d33', 'designed/d01_seal', 'designed/d03_future', 'designed/d08',
               'designed/d09', 'designed/d13', 'designed/d16', 'designed/d17', 'designed/d22', 'designed/d25',
               'designed/d28', 'designed/d29', 'designed/d32', 'designed/d34', 'designed/d35')


def list_scripts():
    """開始画面に出す脚本: おすすめ（選定した設計の脚本）・同梱（試験用）・設計した脚本。自動生成の脚本は「伏せて選ぶ」で使う。
    公開情報だけ（ネタバレを出さない）。"""
    from engine.scripts import glob_scripts, load, script_dirs
    rows = []
    for group, pat in (('同梱', '*.json'), ('設計', 'designed/*.json')):
        for f in glob_scripts(pat):
            sc = load(f)
            rel = next(os.path.relpath(f, d) for d in script_dirs() if f.startswith(d))
            sid = rel[:-5].replace(os.sep, '/')
            if sid.startswith('toukou_') or sid == 'blind_001':  # 投稿シナリオは手で指定（--script）。目隠しの記録は出さない
                continue
            rows.append({'id': sid, 'title': sc.get('title') or sid, 'loops': sc.get('loops'), 'days': sc.get('days'),
                         'set': sc.get('set', 'BTX'), 'group': 'おすすめ' if sid in RECOMMENDED else group})
    rec = sorted((r for r in rows if r['group'] == 'おすすめ'), key=lambda r: RECOMMENDED.index(r['id']))
    return rec + [r for r in rows if r['group'] != 'おすすめ']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--script', default='s03_bomb')
    ap.add_argument('--blind-pool', default='recommended', choices=['recommended', 'generated'],
                    help='--blind の選び先: おすすめ（遊び尽くしたら自動生成）か、自動生成（Claude の目隠し対戦）')
    ap.add_argument('--blind', action='store_true', help='脚本を進行役が目隠しで選ぶ（id もルールも表示しない。遊んだ組は選ばない）')
    ap.add_argument('--mm', default='search', help='自動の脚本家（selfplay.league.make の種類）')
    ap.add_argument('--pc', default='', help='自動の主人公（league の種類。read2・read・脚本を知る oread など）。指定すると自動同士で最後まで進める')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--dir', default=os.path.join(HERE, 'game_ai'))
    ap.add_argument('--list', action='store_true', help='開始画面の脚本の一覧（題名・ループ数・日数だけ。役職・犯人は出さない）を JSON で出して終わる')
    ap.add_argument('--code', default='', help='シナリオエディタのコード（s1.…）の脚本で遊ぶ（--script の代わり）')
    ap.add_argument('--decode', action='store_true', help='標準入力のコードを読んで、シナリオの JSON を出して終わる（シナリオエディタ）')
    ap.add_argument('--replay', default='', help='リプレイの記録（record.json の形）のファイルを再生する（URL の #p=）')
    ap.add_argument('--resume', action='store_true', help='--replay の記録の終わりから、普通の対戦として続ける（途中から再開する URL）')
    ap.add_argument('--check', action='store_true', help='標準入力のシナリオ（JSON）を検証し、{errors, warnings} を JSON で出して終わる（シナリオエディタ）')
    a = ap.parse_args()
    if a.decode:
        from engine.scripts import decode
        try:
            print(json.dumps({'script': decode(sys.stdin.read())}, ensure_ascii=False))
        except ValueError as e:
            print(json.dumps({'error': str(e)}, ensure_ascii=False))
        return
    if a.check:
        from engine.scripts import encode, report
        try:
            sc = json.loads(sys.stdin.read())
        except ValueError:
            sc = None
        rep = report(sc)
        if not rep['errors']:
            rep['code'] = encode(sc)  # 遊ぶためのコード（ブラウザ版と同じ形）
        print(json.dumps(rep, ensure_ascii=False))
        return
    if a.list:  # 開始画面用: 選べる脚本と、伏せて選べる脚本（自動生成）の数
        from engine.scripts import glob_scripts
        print(json.dumps({'scripts': list_scripts(), 'blind': blind_count()}, ensure_ascii=False))
        return
    a.replay = json.load(open(a.replay, encoding='utf-8')) if a.replay else None
    run(a)


def run(a, table_cls=None):
    """1試合を進める。a: --script・--blind・--mm・--seed・--dir の値。table_cls: 盤面とのやりとり（既定はファイル＝server.mjs）。"""
    table_cls = table_cls or Table
    rp = getattr(a, 'replay', None)  # リプレイの記録（URL の #p=）: 記録の脚本・脚本家で、記録した判断を流し込んで再生する
    if rp:
        if rp.get('v') not in (1, 2):
            raise ValueError('リプレイの記録の形が違う')
        _V2[0] = rp.get('v') == 2
        a.script, a.code, a.mm, a.blind = rp.get('s') or '', rp.get('c') or '', rp.get('mm') or 'calcG', False
        if rp.get('pc'):  # 自動同士の試合の記録: 主人公の種類と乱数の種で、主人公の手を作り直す
            a.pc, a.seed = rp['pc'], rp.get('seed', a.seed)
    if a.blind:
        a.script, n = pick_blind(random.Random(f'blind:{a.seed}:{time.time()}'), pool=getattr(a, 'blind_pool', 'recommended'))
        os.makedirs(os.path.join(a.dir, 'private'), exist_ok=True)
        json.dump({'script': a.script}, open(os.path.join(a.dir, 'private', 'pick.json'), 'w'))  # Claude は試合中に読まない
        print(f'目隠しで脚本を選んだ（候補 {n} 本）')
    if getattr(a, 'code', ''):  # シナリオエディタで作ったシナリオ（誰が作ったか分からない入力なので、検証してから使う）
        from engine.scripts import decode, report, to_game
        user = decode(a.code)
        errs = report(user)['errors']
        if errs:
            raise ValueError('シナリオに誤りがある: ' + ' ／ '.join(errs))
        script, a.script, a.blind = to_game(user), 'user', False
    else:
        script = by_id(a.script)
    t = table_cls(a.dir, script)
    if a.blind:
        t.record_file = os.path.join('private', 'record.json')
    t.record.update({'s': '' if a.script == 'user' else a.script, 'c': getattr(a, 'code', '') or '', 'mm': a.mm})
    auto_pc = getattr(a, 'pc', '') or ''
    if auto_pc:  # 自動同士（ユーザー 2026-10-17）: 主人公も自動。記録に種類と乱数の種を残し、再生で作り直す
        t.record.update({'pc': auto_pc, 'seed': a.seed})
    if rp:
        t.replay = {'h': [{k: v for k, v in x.items() if k != 'n'} for x in rp.get('h', [])], 'resume': bool(getattr(a, 'resume', False))}
    rng = random.Random(a.seed)
    # 自動の主人公は専用の乱数（脚本家と共有すると、再生で脚本家を記録に置き換えたときに主人公の手がずれる）
    pc = make(auto_pc, random.Random(f'pc:{a.seed}:{auto_pc}'), script) if auto_pc else HumanProtagonist(t, rng)
    if auto_pc == 'oread':
        pc.final_truth = True  # 脚本を知っている主人公は、最後の戦いも本当の配役で答える
    if a.mm == 'claude' and not rp:  # Claude が脚本家（問いは private/ に書くので盤面からは見えない）
        from selfplay.play_cli import BlockingMastermind
        mm = BlockingMastermind(os.path.join(a.dir, 'private', 'mm_question.txt'), os.path.join(a.dir, 'private', 'mm_decisions.jsonl'),
                                make('search', rng, script))
    else:
        mm = make('search' if a.mm == 'claude' else a.mm, rng, script)
    # 再生: 記録の判断を順に返し、記録し直す（作り直した試合からまた共有できるように）。尽きたら、ただの再生なら止まり（ReplayEnd）、
    # 再開（resume）なら本物の脚本家が考える。持っている判断の種類は、同じ型の脚本家と同じ
    if rp and not getattr(a, 'resume', False):
        mm = ReplayMM({'m': []}, [n for n in Combo.MM + ('observe',) if hasattr(mm, n)])
    else:
        mm.progress = _progress(t)
    mm = RecordingMM(mm, t.record, t, queue=rp.get('m') if rp else None)
    # 脚本家が考えている間は、盤面に「考え中」を出す（何を考えているかと開始時刻。calcG は1手に数十秒かかる）
    for meth, what in THINK.items():
        f = getattr(mm, meth, None)
        if callable(f):
            setattr(mm, meth, _thinking(t, what, f))
    player = Combo(mm, pc)
    nar = Narrator(t)
    # 題名は内容の手がかりになるので、開始時は ID だけ出す（題名はゲーム終了時の非公開シートの公開で出す）
    shown = f'「{script["title"]}」（ユーザーのシナリオ）' if a.script == 'user' else short_id(a.script)
    t.log(f'対AIモード開始。脚本 {shown}（ループ {script["loops"]} 回・1ループ {script["days"]} 日・'
          f'{script.get("set", "BTX")}）。脚本家は' + ('Claude' if a.mm == 'claude' else f'自動（{a.mm}）')
          + (f'、主人公は自動（{auto_pc}）' if auto_pc else ''))

    def log0(kind, rec):
        if rec.get('phase') in PHASE_JA and rec.get('phase') != 'ability':  # 主人公能力フェイズは宣言の受付の前に出す
            t.phase(rec.get('loop'), rec.get('day'), rec['phase'])
        if rec.get('phase') == 'loop_end':  # ループが途中で終わったフェイズの見出しを、その出来事（死亡など）より前に出す
            # （ターン終了フェイズのシリアルキラーの殺しが、見出しなしで事件フェイズの死亡に続いて見えた。d02_traits の報告 2026-10-17）
            at = next((e.get('at_phase') for e in rec.get('events', []) if e.get('kind') == 'loop_end'), None)
            if at in PHASE_JA:
                t.phase(rec.get('loop'), rec.get('day'), at)
        # 行動解決では、脚本家の伏せ札も表向きになる（効果の無かった札も公開される＝推理の材料）
        if kind == 'decisions' and rec.get('kind') == 'place_cards' and rec.get('who') == 'M':
            t.log('脚本家の札が公開された: ' + '、'.join(f'{name(x["target"])}={CARD_UI.get(x["card"], x["card"])}' for x in rec['choice']))
        if rec.get('phase') == 'final_battle':
            t.log('最後の戦い: ' + ('主人公の勝ち' if rec['result']['winner'] == 'protagonists' else
                                 f'{rec["result"].get("stopped_at", 0) + 1} 人目の指摘で外れた。脚本家の勝ち'))
    try:
        r = play_game(script, player, log0, observers=(nar,))
    except ReplayEnd:  # 共有したときの位置まで再生した（試合の途中）
        t.show(phase_name='リプレイ（試合の途中まで）', waiting={'kind': 'replay_done', 'text': '再生を終えた',
                                                         'ok': (t.n, t.record['crc']) == (rp['n'], rp['crc']), 'options': []})
        return
    winner = '主人公' if r.get('winner') == 'protagonists' else '脚本家'
    how = '最後の戦い' if r.get('loop') == 'final' else f'ループ{r.get("loop")}を守りきった'
    t.log(f'ゲーム終了: {winner}の勝ち（{how}）')
    t.log(f'非公開シート: 脚本「{script.get("title") or a.script}」（{a.script}） ／ ルール ' + '・'.join(RULE_JA.get(x, x) for x in script['rules']) + ' ／ 配役 '
          + '・'.join(f'{name(c)}={ROLE_JA.get(v, v)}' for c, v in script['roles'].items()) + ' ／ 犯人 '
          + '・'.join(f'{i["day"]}日 {INC_JA.get(i["id"], i["id"])}={name(i["culprit"])}' for i in script['incidents']))
    # 非公開シート（盤面のダイアログ）: 勝敗・脚本・ルール・全員の役職（パーソンも）・事件と犯人。最後の戦いがあれば指摘との答え合わせ
    guesses = getattr(pc, 'guesses', None) or []
    gi = {g['char']: (i, g['role']) for i, g in enumerate(guesses)}
    stop = r.get('final_stopped_at')
    chars = [c for c in script.get('characters') or script['init']]
    t.reveal = {
        'winner': winner, 'how': how, 'title': script.get('title') or a.script, 'id': a.script,
        'loops': script['loops'], 'days': script['days'], 'set': SET_JA.get(script.get('set', 'BTX'), script.get('set')),
        'ruleY': [RULE_JA.get(x, x) for x in script['rules'] if x.startswith('Y_')],
        'ruleX': [RULE_JA.get(x, x) for x in script['rules'] if x.startswith('X_')],
        # 役職はゲーム中の扱い（アルバイトは特性でパーソン、アルバイト？はアルバイトの配役）＝エンジンの base_role
        'roles': [{'char': name(c), 'role': ROLE_JA.get(ph.base_role({'script': script}, c), ph.base_role({'script': script}, c)),
                   **({'guess': ROLE_JA.get(gi[c][1], gi[c][1]), 'order': gi[c][0] + 1,
                       'judged': stop is None or gi[c][0] <= stop, 'ok': stop is None or gi[c][0] < stop} if c in gi else {})}
                  for c in chars],
        'incidents': [{'day': i['day'], 'name': INC_JA.get(i['id'], i['id']), 'culprit': name(i['culprit'])} for i in script['incidents']],
        'special': script.get('special') or '',
        'final': bool(guesses), 'stopped_at': (stop + 1) if stop is not None else None,
    }
    if a.blind:  # 試合の後に、遊んだルールの組を記録する（次の目隠しの選択で同じ組を避ける）。伏せていた記録も公開する
        t._write('record.json', t.record)
        with open(HISTORY, 'a', encoding='utf-8') as fp:
            fp.write(json.dumps({'id': a.script.split('/')[-1], 'rules': script['rules'], 'mm': a.mm, 'winner': r.get('winner')}, ensure_ascii=False) + '\n')
    if rp:  # 最後まで再生した。記録の照合値と同じなら、同じ試合を再現できた
        t.show(phase_name=f'ゲーム終了 ― {winner}の勝ち', waiting={'kind': 'replay_done', 'text': '再生を終えた',
                                                             'ok': (t.n, t.record['crc']) == (rp['n'], rp['crc']), 'options': []})
        return
    t.show(phase_name=f'ゲーム終了 ― {winner}の勝ち', waiting={'kind': 'none', 'text': 'ゲーム終了。非公開シートを公開ログに出した', 'options': []})


if __name__ == '__main__':
    main()
