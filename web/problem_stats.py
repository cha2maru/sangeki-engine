"""次の一手問題の回答の集計を、問題の JSON（web/problems/*.json）の stats に書き込む。
GitHub Pages の組み立て（.github/workflows/pages.yml）が、組み立てた web/dist/play/problems に対して毎回動かす（毎日 1 回の組み直しもある）。
集計はリポジトリにコミットしない（組み立てたページの中だけ）。ページは 1 つの問題につき最初の回答だけを送る。

GA4 の Data API（runReport）で、イベント problem_answer を問題（problem_id）と手（move）ごとに数える。
move は AI と同じ手なら 'ai'、違えば札の組の文字列（problems.html の key()）。'ai' は AI の手の札の組に直して数える。

前提（GA4 の管理画面で 1 度だけ）:
  - イベントのパラメータ problem_id・move を「カスタム ディメンション（範囲: イベント）」に登録する。登録より前の回答は集計に出ない
  - 環境変数 GA4_PROPERTY_ID にプロパティ ID（数字。測定 ID の G-… ではない。sangeki-engine は 561751039）
  - アクセストークン: 環境変数 GA4_TOKEN（Pages の組み立てでは Workload Identity 連携のサービスアカウント
    sangeki-stats-gh@sangeki-stats-11151255.iam.gserviceaccount.com。GA4 の閲覧者）か、
    無ければ `gcloud auth application-default print-access-token`

    python3 web/problem_stats.py [--dir web/dist/play/problems]  # GA4 から集計して書き込む（既定は web/problems）
    python3 web/problem_stats.py --csv x.csv  # GA4 の画面から書き出した CSV（problem_id,move,count の列）を使う
    python3 web/problem_stats.py --dry-run  # 書き込まずに表だけ出す
"""
import argparse
import csv
import datetime
import glob
import json
import os
import subprocess
import urllib.request

PROBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'problems')
CARD_JA = {'PAR+': '不安+1', 'PAR-': '不安-1', 'PARX': '不安禁止', 'GWX': '友好禁止', 'INT1': '暗躍+1', 'INT2': '暗躍+2',
           'MV_V': '移動↑↓', 'MV_H': '移動←→', 'MV_D': '移動斜め', 'GW1': '友好+1', 'GW2': '友好+2', 'INTX': '暗躍禁止', 'MVX': '移動禁止'}


def from_ga4(prop):
    """GA4 Data API の runReport。期間は全期間（公開を始めた日から今日まで）。"""
    token = os.environ.get('GA4_TOKEN') or subprocess.run(['gcloud', 'auth', 'application-default', 'print-access-token'],
                                                          capture_output=True, text=True, check=True).stdout.strip()
    body = {'dateRanges': [{'startDate': '2026-11-01', 'endDate': 'today'}],
            'dimensions': [{'name': 'customEvent:problem_id'}, {'name': 'customEvent:move'}],
            'metrics': [{'name': 'eventCount'}],
            'dimensionFilter': {'filter': {'fieldName': 'eventName', 'stringFilter': {'value': 'problem_answer'}}},
            'limit': 100000}
    req = urllib.request.Request(f'https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport',
                                 data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    rep = json.load(urllib.request.urlopen(req, timeout=60))
    return [(r['dimensionValues'][0]['value'], r['dimensionValues'][1]['value'], int(r['metricValues'][0]['value'])) for r in rep.get('rows', [])]


def from_csv(path):
    with open(path, encoding='utf-8') as f:
        return [(r['problem_id'], r['move'], int(r['count'])) for r in csv.DictReader(f)]


def label(p, key):
    names = {t['id']: t['name'] for t in p.get('targets', [])}
    out = []
    for x in key.split(','):
        t, c = x.rsplit(':', 1)
        out.append(f"{names.get(t, t)}に{CARD_JA.get(c, c)}")
    return '・'.join(out)


def apply(rows, dry=False, probs=PROBS):
    by = {}
    for pid, move, n in rows:
        if pid and move and pid != '(not set)' and move != '(not set)':
            by.setdefault(pid, {})
            by[pid][move] = by[pid].get(move, 0) + n
    today = datetime.date.today().isoformat()
    for f in sorted(glob.glob(os.path.join(probs, '*.json'))):
        if f.endswith(('.replay.json', '.engine.json', 'index.json')):
            continue
        p = json.load(open(f, encoding='utf-8'))
        counts = {}
        ai = ','.join(sorted(p['ai']['cards']))
        for move, n in by.get(p['id'], {}).items():
            k = ai if move == 'ai' else move
            counts[k] = counts.get(k, 0) + n
        total = sum(counts.values())
        p['stats'] = {'total': total, 'updated': today,
                      'moves': [{'key': k, 'label': label(p, k), 'count': n} for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]}
        print(p['id'], total, '件', [(m['label'], m['count']) for m in p['stats']['moves'][:3]])
        if not dry:
            json.dump(p, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default='')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--dir', default=PROBS, help='問題の JSON のフォルダ')
    a = ap.parse_args()
    if a.csv:
        rows = from_csv(a.csv)
    else:
        prop = os.environ.get('GA4_PROPERTY_ID')
        if not prop:
            raise SystemExit('GA4_PROPERTY_ID（プロパティ ID の数字）を環境変数に入れてください。CSV なら --csv')
        rows = from_ga4(prop)
    apply(rows, a.dry_run, a.dir)


if __name__ == '__main__':
    main()
