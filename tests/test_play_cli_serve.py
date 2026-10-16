"""play_cli の常駐の郵便受けモード: 決定を1行ずつ書き足して終局まで進み、反則は落ちずに聞き直し、同じファイルの再生で同じ結果になる。"""
import json
import os
import re
import threading
import time

from selfplay import play_cli


def _answer(head, text):
    kind = head['kind']
    if kind == 'pc_cards':
        return [{'by': p, 'target': t, 'card': 'INTX'} for p, t in zip('ABC', ('B:HOS', 'B:SHR', 'B:CIT'))]
    if kind == 'abilities':
        return []
    return [{'char': c, 'role': 'PERSON'} for c in re.findall(r'^  (C\d\d) ', text, re.M)]


def test_serve_to_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(play_cli.Mailbox, 'interval', 0.01)
    dec = tmp_path / 'decisions.jsonl'
    (tmp_path / 'sid').write_text('s03_bomb\n')
    argv = ['--side', 'pc', '--opp', 'route', '--seed', '1', '--script-file', str(tmp_path / 'sid'), '--file', str(dec)]
    out = {}
    th = threading.Thread(target=lambda: out.setdefault('r', play_cli.main(argv + ['--serve'])), daemon=True)
    th.start()
    q, seen, t0, errors = tmp_path / 'question.txt', None, time.time(), []
    while th.is_alive() and time.time() - t0 < 120:
        if q.exists():
            head, _, text = q.read_text(encoding='utf-8').partition('\n')
            head = json.loads(head)
            key = (head.get('n'), head.get('error'))
            if 'n' in head and key != seen:
                seen = key
                errors += [head['error']] if head.get('error') else []
                ans = _answer(head, text)
                if head['n'] == 1 and not head.get('error'):  # 反則: 2人の主人公が同じ対象に置く
                    ans = [dict(ans[0]), dict(ans[1], target='B:HOS'), ans[2]]
                with open(dec, 'a') as f:
                    f.write(json.dumps({'n': head['n'], 'kind': head['kind'], 'choice': ans}) + '\n')
        time.sleep(0.005)
    th.join(1)
    assert 'r' in out, '終局まで進まなかった'
    assert errors and errors[0].startswith('IllegalPlacement'), errors  # 落ちずに聞き直した
    assert json.loads(q.read_text(encoding='utf-8').partition('\n')[0])['end']
    res = (tmp_path / 'result.txt').read_text(encoding='utf-8')
    assert '"culprit"' not in res and '"reason"' not in res and 's03_bomb' not in res
    assert play_cli.main(argv) == out['r']  # 同じ決定ファイル（直した行を含む）を再生すると同じ結果
