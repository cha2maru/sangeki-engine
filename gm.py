"""進行役 (Claude) の補助。ルールの判定はしない。状態の書き換え・ログ・ハッシュ固定だけを行う。

    python3 play/gm.py log "<本文>" [--basis "<出典>"] [--correction]
    役職などの理由を伴う処理は gm.log(text, secret="理由") で書く（公開側に理由を出さない）
    python3 play/gm.py commit <label> <name> <base64>   # 非公開ファイルを書いて固定し、ハッシュだけ出す
    python3 play/gm.py reveal <name>                     # 固定したファイルを公開する
    python3 play/gm.py wait                              # inbox に新しい行が来るまで待ち、その行を出す
    python3 play/gm.py preview <cards_L?D?.txt> <inbox seq> [script.json]  # エンジンによる行動解決の結果（書き込みなし）

盤面の欄（公開シート帯・当日の流れ・ループの記録・役職の札）のために、進行役は次も呼ぶ:
    sheet(セット名, special, territory) / phase('行動解決フェイズ')（各フェイズの頭）/ death(名前) / role_public(名前, 役職) / loop_end(終わったフェイズ, protagonist_dead, loss)

状態の書き換えは import して使う:
    python3 -c "import sys; sys.path.insert(0,'play'); from gm import *; s=load(); ...; save(s)"
"""
import base64, hashlib, json, os, re, secrets, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.join(HERE, 'game')
PRIV = os.path.join(GAME, 'private')
REVEALED = os.path.join(GAME, 'revealed')
STATE, LOG, INBOX = (os.path.join(GAME, f) for f in ('state.json', 'log.jsonl', 'inbox.jsonl'))
SEEN = os.path.join(PRIV, 'inbox_seen')
SNAPS = os.path.join(GAME, 'snapshots.jsonl')

# 伏せ札の記法。平文を端末に出さないため、中身は base64 で渡す。
# 1行1枚: "L<ループ>D<日> T=<対象> K=<札>"  対象は C01〜C09（カード番号）か B:HOS/SHR/CIT/SCH
CARD_LINE = re.compile(r'^L\d+D\d+ T=(C0[1-9]|B:(HOS|SHR|CIT|SCH)) K=(PAR\+|PAR-|PARX|GWX|INT1|INT2|MV_V|MV_H|MV_D)$')


def load():
    with open(STATE, encoding='utf-8') as f:
        return json.load(f)


def save(s):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)
    # 盤面の履歴。保存のたびに「公開ログが何行目まで進んだ時点の盤面か」と一緒に残す。
    # 盤面でログの行をクリックすると、その行以降で最初のスナップショットを表示する
    n = sum(1 for _ in open(LOG, encoding='utf-8')) if os.path.exists(LOG) else 0
    snap = {k: s.get(k) for k in ('loop', 'day', 'phaseName', 'leader', 'characters', 'boards', 'cards', 'used', 'publicSheet', 'loopHistory')}
    # prev_n: ひとつ前のスナップショットの log_n。prev_n < 行番号 <= log_n の行が、この盤面に対応する
    prev = None
    if os.path.exists(SNAPS):
        lines = open(SNAPS, encoding='utf-8').read().splitlines()
        if lines: prev = json.loads(lines[-1])['log_n']
    with open(SNAPS, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'prev_n': prev, 'log_n': n, 'state': snap}, ensure_ascii=False) + '\n')


def log(text, basis=None, kind=None, secret=None):
    """公開ログに1行足す。secret を渡すと、その理由を非公開ログに「[公開#n]」として残し、
    公開側には「（理由は非公開・事後公開）」とだけ書く。"""
    s = load() if os.path.exists(STATE) else {}
    n = sum(1 for _ in open(LOG, encoding='utf-8')) + 1 if os.path.exists(LOG) else 1
    if secret:
        text += '（理由は非公開・事後公開）'
        _privwrite(f"[公開#{n}] {secret}")
    rec = {'n': n, 'loop': s.get('loop'), 'day': s.get('day'), 'text': text}
    if basis: rec['basis'] = basis
    if kind: rec['kind'] = kind
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return n


def incident(name, occurred):
    """事件フェイズの判定時点の公開情報を残す（推理シートが表示する）。
    臨界以上の一覧は盤面のカウンターから機械的に数えるだけで、犯人の判定はしない。"""
    s = load()
    crit = [c['name'] for c in s['characters'] if c.get('alive', True) and c['paranoia'] >= c['limit']]
    s.setdefault('incidentHistory', []).append(
        {'loop': s['loop'], 'day': s['day'], 'name': name, 'result': '発生' if occurred else '不発', 'critical': crit})
    save(s)
    return crit


# ---- 盤面の新しい欄（公開シート帯・当日の流れ・ループの記録・役職の札）に載せる公開の事実。対AIモードと同じ形（設計資料 段階 M）----
# 進行役の手順: ゲームの準備で sheet()、各フェイズの頭で phase('行動解決フェイズ') など、死亡で death()、
# 役職の公開で role_public()、ループの終わりに loop_end()。事件は今どおり incident()。

def sheet(set_name, special='', territory=None):
    """公開シート（惨劇セット・特殊ルール・テリトリー）。ループ数・日数は state の loops・days。"""
    s = load()
    s['publicSheet'] = {'set': set_name, 'loops': s.get('loops'), 'days': s.get('days'), 'territory': territory, 'special': special or ''}
    save(s)


def phase(name):
    """フェイズの区切り。帯のフェイズ名を更新し、公開ログに対AIモードと同じ形の区切りの行（── L2D3 行動解決フェイズ ──）を書く。"""
    s = load()
    loop, day = s.get('loop'), s.get('day')
    s['phaseName'] = f'L{loop} ' + (f'{day}日目 ' if day else '') + name
    save(s)
    log(f'── L{loop}' + (f'D{day}' if day else '') + f' {name} ──')


def _hist(s):
    lh = s.setdefault('loopHistory', [])
    h = next((x for x in lh if x['loop'] == s.get('loop')), None)
    if h is None:
        h = {'loop': s.get('loop'), 'incidents': [], 'deaths': [], 'roles': [], 'end': None}
        lh.append(h)
    return h


def death(name):
    """死亡（ループの記録に残し、駒を死亡にする）。"""
    s = load()
    _hist(s)['deaths'].append({'day': s.get('day'), 'name': name})
    for c in s.get('characters', []):
        if c['name'] == name:
            c['alive'] = False
    save(s)


def role_public(name, role):
    """役職の公開（友好能力・フレンドの死亡など）。駒に役職の札が付き、ループの記録に残る。"""
    s = load()
    _hist(s)['roles'].append({'name': name, 'role': role, 'day': s.get('day')})
    for c in s.get('characters', []):
        if c['name'] == name:
            c['role'] = role
    save(s)


def loop_end(at_phase=None, protagonist_dead=False, loss=True):
    """ループの終わり方（どのフェイズで終わったか・主人公の死亡かは公開。理由は書かない）。"""
    s = load()
    head = f'{s.get("day")}日目 {at_phase}に' if at_phase else ''
    _hist(s)['end'] = {'day': s.get('day'), 'text': head + ('主人公死亡' if protagonist_dead else '敗北') if loss else '敗北条件なし（守りきった）'}
    save(s)


def _privwrite(line):
    s = load()
    os.makedirs(PRIV, exist_ok=True)
    with open(os.path.join(PRIV, f"log_L{s['loop']}.txt"), 'a', encoding='utf-8') as f:
        f.write(f"L{s['loop']}D{s['day']} {line}\n")


def priv(text):
    """公開ログに対応しない非公開のメモ（判定の記録など）。番号を振らない。
    通し番号を公開ログに出すと、番号の飛びから「非公開の処理があった」ことが漏れるため（2026-09-23 事後検証 B類1）。"""
    _privwrite(f"[メモ] {text}")


def commit(label, name, b64):
    body = base64.b64decode(b64, validate=True).decode('utf-8')
    if name.startswith('cards_'):
        bad = [l for l in body.splitlines() if l and not CARD_LINE.match(l)]
        if bad:
            sys.exit(f'記法に合わない行が {len(bad)} 行ある（中身は表示しない）')
    os.makedirs(PRIV, exist_ok=True)
    path = os.path.join(PRIV, name)
    data = (body.rstrip('\n') + f'\nnonce={secrets.token_hex(16)}\n').encode('utf-8')
    with open(path, 'wb') as f:
        f.write(data)
    h = hashlib.sha256(data).hexdigest()
    s = load()
    s.setdefault('commitments', []).append({'label': label, 'name': name, 'sha256': h})
    save(s)
    print(f'{label}: {h}  ({len(body.splitlines())} 行)')


def reveal(name):
    os.makedirs(REVEALED, exist_ok=True)
    shutil.copy(os.path.join(PRIV, name), os.path.join(REVEALED, name))
    s = load()
    for c in s.get('commitments', []):
        if c.get('name') == name:
            c['revealed'] = True
    save(s)
    print(open(os.path.join(REVEALED, name), encoding='utf-8').read(), end='')


def wait():
    """inbox の未処理の行を返す。無ければ来るまで待つ。処理済みの連番は private/inbox_seen に残す。"""
    seen = int(open(SEEN).read()) if os.path.exists(SEEN) else 0
    while True:
        lines = open(INBOX, encoding='utf-8').read().splitlines() if os.path.exists(INBOX) else []
        new = [json.loads(l) for l in lines if l and json.loads(l)['seq'] > seen]
        if new:
            os.makedirs(PRIV, exist_ok=True)
            with open(SEEN, 'w') as f:
                f.write(str(new[-1]['seq']))
            for r in new:
                print(json.dumps(r, ensure_ascii=False))
            return
        time.sleep(1)


def preview(cards_name, seq, script_path=None):
    """手動モードの補助: 伏せ札（private/cards_*.txt）と主人公の札（inbox の seq）から、エンジンによる行動解決の結果を出す。
    書き込みはしない。Claude は自分の処理と突き合わせ、食い違えば一次資料に当たる。
    エンジンはここでだけ import する（エンジンが壊れていても log・commit・reveal は動く）。"""
    from engine.adapter import full_from_public, placements_from_cards_file, placements_from_inbox
    from engine.resolve import resolve_actions
    script = json.load(open(script_path or os.path.join(PRIV, 'script.json'), encoding='utf-8'))
    full = full_from_public(load(), {k: script[k] for k in ('rules', 'roles', 'incidents')})
    mm = placements_from_cards_file(open(os.path.join(PRIV, cards_name), encoding='utf-8').read())
    rec = next(json.loads(l) for l in open(INBOX, encoding='utf-8') if json.loads(l)['seq'] == int(seq))
    new, events = resolve_actions(full, mm + placements_from_inbox(rec))
    for e in events:
        print(json.dumps(e, ensure_ascii=False))
    for c, v in new['chars'].items():
        if v != full['chars'][c]:
            print('＝>', c, v)
    return new, events


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == 'log':
        basis = a[a.index('--basis') + 1] if '--basis' in a else None
        print('#', log(a[1], basis, 'correction' if '--correction' in a else None))
    elif a[0] == 'commit':
        commit(*a[1:4])
    elif a[0] == 'reveal':
        reveal(a[1])
    elif a[0] == 'wait':
        wait()
    elif a[0] == 'preview':
        sys.path.insert(0, HERE)
        preview(*a[1:4])
    else:
        sys.exit(__doc__)
