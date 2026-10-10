"""ブラウザ版（Pyodide）の進行役。ai_gm.Table のファイルでのやりとりを、Web Worker を通した盤面とのやりとりに差し替える。
サーバー版（server.mjs ＋ ai_gm.py）はそのまま。web/worker.js から呼ぶ。"""
import json
from types import SimpleNamespace

import js
from pyodide.ffi import run_sync

import ai_gm


class WebTable(ai_gm.Table):
    def _reset_files(self):
        js.post_reset()

    def _append(self, f, obj):
        js.post_append(f, json.dumps(obj, ensure_ascii=False))

    def _write(self, f, obj):
        js.post_write(f, json.dumps(obj, ensure_ascii=False))

    def wait(self, types):
        """盤面からの操作を1つ待つ（JSPI: JavaScript の Promise をここで待つ）。いまの phaseId・種類に合うものだけ受け取る。"""
        while True:
            rec = json.loads(run_sync(js.next_action()))
            if rec.get('phaseId') == self.phase_id and rec.get('type') in types:
                return rec


def start(script, mm, seed, blind, code=''):
    ai_gm.run(SimpleNamespace(script=script, mm=mm, seed=int(seed), blind=bool(blind), dir='/tmp/game', code=code or ''), WebTable)


def check_json(s):
    """シナリオエディタの検証（engine.scripts.report）。s: シナリオの JSON の文字列。"""
    from engine.scripts import encode, report
    try:
        sc = json.loads(s)
    except ValueError:
        return json.dumps({'errors': ['JSON として読めない'], 'warnings': []}, ensure_ascii=False)
    rep = report(sc)
    if not rep['errors']:
        rep['code'] = encode(sc)
    return json.dumps(rep, ensure_ascii=False)


def decode_json(code):
    """シナリオエディタ: コード → シナリオの JSON（読み込み用）。"""
    from engine.scripts import decode
    try:
        return json.dumps({'script': decode(code)}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({'error': str(e)}, ensure_ascii=False)


def list_json():
    from engine.scripts import glob_scripts
    return json.dumps({'scripts': ai_gm.list_scripts(), 'blind': len(glob_scripts('generated/*.json'))}, ensure_ascii=False)
