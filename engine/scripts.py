"""脚本（非公開シート）の読み込みと整合の確認（plan/engine-smarter.md 段S1・S3）。

脚本の形（play/engine/scripts/*.json）:
  id, title, set: "BTX", loops, days, rules: [Y, X1, X2], roles: {キャラ: 役職（パーソン以外）}, characters: [...],
  incidents: [{day, id, culprit}], init: {キャラ: エリア}（省略時はカードの初期エリア。手先・従者は必須）,
  appear: {"C24": {"day": n}, "C13": {"loop": n}}（転校生・神格）, special_rules: [文],
  notes: {cs, pp, solution, wins}（作者の意図。脚本家の書 p41〜の「タブー」「CS/PP」に沿って書く）
check() は誤り（ルール上ありえない脚本）と注意（ガイドの目安から外れる）を分けて返す。
根拠: 役職の員数＝早見表（data/rules.json）、犯人の相異＝wiki/concepts/script.md（決着済み）、
キャラクターごとの脚本作成時の制約＝各キャラクターカード（wiki/characters/）、少女のキーパーソン＝僕と契約しようよ！。
"""
import glob
import json
import os
from collections import Counter

from .deduce import RULES, roles_for
from .resolve import CHARS

HERE = os.path.dirname(os.path.abspath(__file__))
INCIDENTS = json.load(open(os.path.join(HERE, 'data', 'incidents.json'), encoding='utf-8'))['incidents']

# エンジンが最後まで処理できる範囲（段S5 で広げる）。ここに無いものを含む脚本は自己対戦に使わない
SUPPORTED_RULES = {'Y_MURDER', 'Y_SEAL', 'Y_CONTRACT', 'Y_FUTURE', 'Y_BOMB', 'X_THREAD', 'X_VIRUS', 'X_CIRCLE', 'X_LOVE', 'X_KILLER', 'X_RUMOR', 'X_FACTOR'}
SUPPORTED_INCIDENTS = {'MURDER', 'SPREAD', 'CORRUPT', 'SUICIDE', 'HOSPITAL', 'REMOTE', 'MISSING', 'RUMOR_SPREAD', 'BUTTERFLY'}

# 脚本家の書 印刷p48 ループ回数の目安（wiki/concepts/script-creation.md、照合済み）
LOOP_Y = {'Y_MURDER': 1.8, 'Y_SEAL': 1.5, 'Y_CONTRACT': 1.0, 'Y_FUTURE': 1.3, 'Y_BOMB': 1.0}
LOOP_X = {'X_CIRCLE': 1.0, 'X_LOVE': 1.0, 'X_KILLER': 0.8, 'X_RUMOR': 0.5, 'X_VIRUS': 0.0, 'X_THREAD': 0.5, 'X_FACTOR': 0.8}


def load(path):
    return json.load(open(path, encoding='utf-8'))


# 脚本・指針・調整済みの重みの置き場所。公開版ではエンジン（sangeki-engine）に試験用の脚本だけを置き、
# 残り（設計・生成した脚本、指針、問題集、重み）は隣の sangeki-scripts に置く（ネタバレを分ける）。
# 探す順: 環境変数（: 区切り）> engine/scripts・tuned > 隣の ../sangeki-scripts/scripts・tuned
_ROOT = os.path.dirname(HERE)  # play/（公開版ではリポジトリの根）
_SIBLING = os.path.join(os.path.dirname(_ROOT), 'sangeki-scripts')


def _dirs(env, local, sub):
    out = [d for d in os.environ.get(env, '').split(':') if d] + [local, os.path.join(_SIBLING, sub)]
    return [d for d in out if os.path.isdir(d)]


def script_dirs():
    return _dirs('SANGEKI_SCRIPTS', os.path.join(HERE, 'scripts'), 'scripts')


def tuned_dirs():
    return _dirs('SANGEKI_TUNED', os.path.join(_ROOT, 'tuned'), 'tuned')


def find(rel, dirs=None):
    """脚本の置き場所から rel（例 'designed/d16.json'・'guides/d16.json'）を探す。無ければ None。"""
    for d in (script_dirs() if dirs is None else dirs):
        p = os.path.join(d, rel)
        if os.path.exists(p):
            return p
    return None


def glob_scripts(pattern):
    """全ての置き場所で glob（例 'generated/*.json'）。同じ名前は先の置き場所を使う。"""
    seen = {}
    for d in script_dirs():
        for p in sorted(glob.glob(os.path.join(d, pattern))):
            seen.setdefault(os.path.relpath(p, d), p)
    return [seen[k] for k in sorted(seen)]


def load_all():
    return [load(p) for p in glob_scripts('*.json')]


def loops_estimate(sc):
    """p48 の表の合計。蝶の羽ばたき（未来改変プラン）と少女（僕と契約しようよ！）の加算を含む。"""
    ys = [r for r in sc['rules'] if r.startswith('Y_')]
    v = sum(LOOP_Y.get(r, 0) for r in ys) + sum(LOOP_X.get(r, 0) for r in sc['rules'] if r.startswith('X_'))
    if 'Y_CONTRACT' in ys:
        v += 0.4 * sum('少女' in CHARS[c]['tags'] for c in sc['characters'])
    if 'Y_FUTURE' in ys:
        v += 0.5 * sum(i['id'] == 'BUTTERFLY' for i in sc['incidents'])
    if any(i['id'] == 'HOSPITAL' for i in sc['incidents']):
        v += 0.4
    n = len(sc['incidents'])
    v += -0.4 if n <= 3 else (0.4 if n >= 5 else 0)
    v += -0.6 if sc['days'] <= 5 else (-0.2 if sc['days'] == 6 else 0)
    return round(v, 2)


def init_areas(sc):
    return {c: sc.get('init', {}).get(c) or sc.get('start', {}).get(c) or CHARS[c]['start'] for c in sc['characters']}


def check(sc):
    """(誤り, 注意) のリスト。"""
    err, warn = [], []
    chars = sc['characters']
    ys = [r for r in sc['rules'] if RULES.get(r, {}).get('type') == 'Y']
    xs = [r for r in sc['rules'] if RULES.get(r, {}).get('type') == 'X']
    if len(ys) != 1 or len(xs) != 2 or len(set(xs)) != 2 or len(sc['rules']) != 3:
        err.append(f"ルールは Y1つと異なる X2つ（Basic Tragedy X）: {sc['rules']}")
    unknown = [c for c in chars if c not in CHARS]
    if unknown:
        err.append(f'未知のキャラクター {unknown}')
        return err, warn
    if len(set(chars)) != len(chars):
        err.append('同じキャラクターが2度登場している')
    if any(c not in chars for c in sc['roles']):
        err.append('登場しないキャラクターに役職がある')
    need = roles_for(tuple(sc['rules'])) if not err else {}
    # イレギュラー・コピーキャットはルールの役職の枠に数えない（カードの特性「ルールより追加された役職とすることができない」）
    special = {'C11', 'C28'}
    have = Counter(r for c, r in sc['roles'].items() if c not in special)
    added = set(need)
    if 'C11' in chars:
        r = sc['roles'].get('C11', 'PERSON')
        if r == 'PERSON' or r in added:
            err.append('イレギュラーは、選ばれたルールが追加しない役職になる（パーソン不可）')
    if 'C28' in chars:
        r = sc['roles'].get('C28', 'PERSON')
        if r == 'PERSON' or r not in [x for c, x in sc['roles'].items() if c != 'C28']:
            err.append('コピーキャットは、他のキャラクター1人と同じ役職になる（パーソン不可・人数上限無視）')
    if 'C16' in chars and sc.get('territory') not in ('HOS', 'SHR', 'CIT', 'SCH'):
        err.append('大物のテリトリー（ボード）を territory で指定する（カードの特性）')
    if need and dict(have) != {r: n for r, n in need.items() if n}:
        err.append(f'役職の員数がルールと合わない: 必要 {need} / 脚本 {dict(have)}')
    # 事件
    culprits = [i['culprit'] for i in sc['incidents']]
    if len(set(culprits)) != len(culprits) and not sc.get('special_rules_allow_same_culprit'):
        err.append('事件の犯人が重複している（wiki/concepts/script.md 決着済み）')
    for i in sc['incidents']:
        if i['id'] not in INCIDENTS:
            err.append(f"未知の事件 {i['id']}")
        if i['culprit'] not in chars:
            err.append(f"犯人 {i['culprit']} が登場しない")
        if not 1 <= i['day'] <= sc['days']:
            err.append(f"事件の日 {i['day']} が 1〜{sc['days']} の外")
    # キャラクターカードの脚本作成時の制約
    role = lambda c: sc['roles'].get(c, 'PERSON')  # noqa: E731
    if 'Y_CONTRACT' in sc['rules'] and not any(role(c) == 'KEY' and '少女' in CHARS[c]['tags'] for c in chars):
        err.append('僕と契約しようよ！: キーパーソンは少女（【強制: 脚本作成時】）')
    if 'C22' in chars and role('C22') == 'PERSON':
        err.append('A.I. はパーソンにできない（カードの特性）')
    if 'C31' in chars and role('C31') in ('KILLER', 'KUROMAKU', 'FACTOR', 'CULTIST', 'WITCH'):
        err.append('妹は友好無視を持つ役職にできない（カードの特性）')
    if 'C34' in chars and (sc.get('start', {}).get('C34') or sc.get('init', {}).get('C34')) not in CHARS['C34'].get('start_choice', []):
        err.append('従者の初期エリア（都市か学校）を init で指定する（カードの特性）')
    # 手先の初期エリアは各ループの準備で脚本家が決める（loop_start の choice）ので、脚本には書かない
    if 'C24' in chars and 'day' not in sc.get('appear', {}).get('C24', {}):
        err.append('転校生の登場日を appear で指定する（カードの特性）')
    if 'C13' in chars and 'loop' not in sc.get('appear', {}).get('C13', {}):
        err.append('神格の登場ループを appear で指定する（カードの特性）')
    # 神格は公開シートのループ回数以下のループで、転校生は日数以下の日に必ず登場する（脚本家の書 p13 A6＝作者回答、裁定）
    if 'C13' in chars and sc.get('appear', {}).get('C13', {}).get('loop', 0) > sc['loops']:
        err.append('神格の登場ループはループ数以下')
    if 'C24' in chars and sc.get('appear', {}).get('C24', {}).get('day', 0) > sc['days']:
        err.append('転校生の登場日は日数以下')
    if ('C32' in chars) != ('C33' in chars):
        err.append('アルバイトとアルバイト？は一緒に登場させる（アルバイトの特性がアルバイト？を配置する）')
    for c, a in init_areas(sc).items():
        if a and a in CHARS[c].get('forbidden', []):  # noqa: E501
            err.append(f'{c} の初期エリア {a} が禁止エリア')
    # ガイドの目安（脚本家の書 p43〜p48）
    if not 6 <= len(chars) <= 11:
        warn.append(f'登場人物 {len(chars)} 人（目安 6〜11）')
    if not 5 <= sc['days'] <= 7:
        warn.append(f"日数 {sc['days']}（目安 5〜7）")
    est = loops_estimate(sc)
    if abs(est - sc['loops']) > 1.0:
        warn.append(f"ループ数 {sc['loops']} が目安 {est} から離れている")
    return err, warn


def unsupported(sc):
    """エンジンがまだ処理できない要素。空なら自己対戦に使える。"""
    out = [r for r in sc['rules'] if r not in SUPPORTED_RULES]
    out += [i['id'] for i in sc['incidents'] if i['id'] not in SUPPORTED_INCIDENTS]
    out += [c for c in sc['characters'] if not CHARS[c].get('engine')]
    return sorted(set(out))


def to_game(sc):
    """自己対戦（selfplay/run.play_game）に渡す形。"""
    return {'id': sc['id'], 'rules': sc['rules'], 'roles': sc['roles'], 'incidents': sc['incidents'],
            'loops': sc['loops'], 'days': sc['days'], 'init': init_areas(sc), 'appear': sc.get('appear', {}),
            'territory': sc.get('territory'), 'start': sc.get('start', {}),
            'title': sc.get('title'), 'set': sc.get('set', 'BTX'), 'special': sc.get('special', '')}  # 公開シートの項目


def by_id(sid):
    p = find(f'{sid}.json')
    if p is None:
        raise FileNotFoundError(f'脚本 {sid} が見つからない（置き場所: {script_dirs()}。設計した脚本は sangeki-scripts にある）')
    return to_game(load(p))


# ---- 利用者が作ったシナリオ（シナリオエディタ editor.html、2026-10-10） ----
# コード: 's1.' ＋ 番号に置き換えて詰めたバイト列を base64url（= を落とす）。作るのも読むのもエンジン（ブラウザ版は Pyodide）。
# JSON を deflate しただけだと d16 で約390文字 → 詰めると約60文字（ユーザー「json 直だと長い」）。
# 表の並びはコードの互換に関わるので、足すときは末尾に足す（並べ替えない）
CODE_PREFIX = 's1.'
T_RULES = ('Y_MURDER', 'Y_SEAL', 'Y_CONTRACT', 'Y_FUTURE', 'Y_BOMB', 'X_CIRCLE', 'X_LOVE', 'X_KILLER', 'X_RUMOR', 'X_VIRUS', 'X_THREAD', 'X_FACTOR')
T_ROLES = ('PERSON', 'KEY', 'KILLER', 'KUROMAKU', 'CULTIST', 'TT', 'WITCH', 'FRIEND', 'MISLEADER', 'LOVERS', 'MAIN_LOVERS', 'SK', 'FACTOR')
T_INC = ('MURDER', 'SPREAD', 'CORRUPT', 'SUICIDE', 'HOSPITAL', 'REMOTE', 'MISSING', 'RUMOR_SPREAD', 'BUTTERFLY')
T_AREA = (None, 'HOS', 'SHR', 'CIT', 'SCH')


def encode(sc):
    """シナリオ → コード。表に無い ID・範囲外の数は ValueError。"""
    import base64
    sc = normalize(sc)
    ix = lambda tab, v, what: tab.index(v) if v in tab else (_ for _ in ()).throw(ValueError(f'{what} {v} はコードにできない'))  # noqa: E731
    num = lambda c: int(c[1:]) if c[:1] == 'C' and c[1:].isdigit() and 0 < int(c[1:]) < 256 else ix((), c, 'キャラクター')  # noqa: E731
    if len(sc['rules']) != 3:
        raise ValueError('ルールは Y1つと X2つ')
    b = bytearray([sc['loops'] << 4 | sc['days']] + [ix(T_RULES, r, 'ルール') for r in sc['rules']] + [len(sc['characters'])])
    for c in sc['characters']:
        b += bytes([num(c), ix(T_ROLES, sc['roles'].get(c, 'PERSON'), '役職') << 3 | ix(T_AREA, sc['init'].get(c), 'エリア')])
    b.append(ix(T_AREA, sc.get('territory'), 'テリトリー'))
    b.append(len(sc['appear']))
    for c, a in sc['appear'].items():
        (k, n), = a.items()
        b += bytes([num(c), (k == 'loop') << 4 | n])
    b.append(len(sc['incidents']))
    for i in sc['incidents']:
        b += bytes([i['day'] << 4 | ix(T_INC, i['id'], '事件'), num(i['culprit'])])
    for s, w in ((sc['title'], 1), (sc['special'], 2)):
        u = s.encode('utf-8')
        b += len(u).to_bytes(w, 'big') + u
    return CODE_PREFIX + base64.urlsafe_b64encode(bytes(b)).decode('ascii').rstrip('=')


def decode(code):
    """コード → 脚本の dict。壊れていれば ValueError（誰が作ったか分からない入力なので、型と範囲を確かめる）。"""
    import base64
    code = (code or '').strip()
    if not code.startswith(CODE_PREFIX) or len(code) > 4000:
        raise ValueError('シナリオのコードではない（s1. で始まる）')
    body = code[len(CODE_PREFIX):]
    try:
        b = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
        p = [0]

        def take(n=1):
            if p[0] + n > len(b):
                raise IndexError
            p[0] += n
            return b[p[0] - n:p[0]]
        one = lambda: take()[0]  # noqa: E731
        ch = lambda: 'C%02d' % one()  # noqa: E731
        ld = one()
        rules = [T_RULES[x] for x in take(3)]
        chars, roles, init = [], {}, {}
        for _ in range(one()):
            c, x = ch(), one()
            chars.append(c)
            if x >> 3:
                roles[c] = T_ROLES[x >> 3]
            if x & 7:
                init[c] = T_AREA[x & 7]
        terr = T_AREA[one()]
        appear = {}
        for _ in range(one()):
            c, x = ch(), one()
            appear[c] = {('loop' if x >> 4 else 'day'): x & 15}
        incs = []
        for _ in range(one()):
            x, c = one(), ch()
            incs.append({'day': x >> 4, 'id': T_INC[x & 15], 'culprit': c})
        title = take(one()).decode('utf-8')
        special = take(int.from_bytes(take(2), 'big')).decode('utf-8')
    except Exception as e:
        raise ValueError(f'コードを読めない（壊れている: {type(e).__name__}）')
    sc = {'title': title, 'loops': ld >> 4, 'days': ld & 15, 'rules': rules, 'characters': chars, 'roles': roles, 'init': init,
          'appear': appear, 'incidents': incs, 'special': special}
    if terr:
        sc['territory'] = terr
    return normalize(sc)


def normalize(sc):
    """利用者のシナリオを、エンジンの脚本の形に揃える（型と範囲を確かめる）。"""
    if not isinstance(sc, dict):
        raise ValueError('シナリオは JSON のオブジェクト')
    ints = lambda v, lo, hi: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi  # noqa: E731
    strs = lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v)  # noqa: E731
    if not ints(sc.get('loops'), 1, 9) or not ints(sc.get('days'), 1, 9):
        raise ValueError('ループ数・日数は 1〜9 の整数')
    if not strs(sc.get('rules')) or not strs(sc.get('characters')):
        raise ValueError('rules・characters は文字列のリスト')
    roles, init, appear = sc.get('roles', {}), sc.get('init', {}), sc.get('appear', {})
    if not (isinstance(roles, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in roles.items())):
        raise ValueError('roles は {キャラクター: 役職}')
    if not (isinstance(init, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in init.items())):
        raise ValueError('init は {キャラクター: エリア}')
    if not (isinstance(appear, dict) and all(isinstance(v, dict) and all(ints(n, 1, 9) for n in v.values()) for v in appear.values())):
        raise ValueError('appear は {キャラクター: {"day"|"loop": 数}}')
    incs = sc.get('incidents', [])
    if not (isinstance(incs, list) and all(isinstance(i, dict) and ints(i.get('day'), 1, 9) and isinstance(i.get('id'), str)
                                           and isinstance(i.get('culprit'), str) for i in incs)):
        raise ValueError('incidents は [{day, id, culprit}]')
    out = {'id': 'user', 'title': str(sc.get('title') or 'ユーザーのシナリオ')[:60], 'set': 'BTX',
           'loops': sc['loops'], 'days': sc['days'], 'rules': list(sc['rules']), 'characters': list(sc['characters']),
           'roles': {k: v for k, v in roles.items() if v != 'PERSON'}, 'init': dict(init), 'appear': dict(appear),
           'incidents': [{'day': i['day'], 'id': i['id'], 'culprit': i['culprit']} for i in incs],
           'special': str(sc.get('special') or '')[:500]}
    if isinstance(sc.get('territory'), str):
        out['territory'] = sc['territory']
    return out


def report(sc):
    """シナリオエディタの検証: {'errors', 'warnings'}。エンジンが処理できない要素も誤りに入れる（遊べないので）。"""
    try:
        sc = normalize(sc)
        err, warn = check(sc)
    except ValueError as e:
        return {'errors': [str(e)], 'warnings': []}
    except (KeyError, TypeError) as e:
        return {'errors': [f'形が崩れている（{e}）'], 'warnings': []}
    if not err:
        uns = unsupported(sc)
        if uns:
            err.append('このエンジンでまだ遊べない要素: ' + '・'.join(uns))
    return {'errors': err, 'warnings': warn}
