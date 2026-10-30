// 惨劇RoopeR 対戦用のローカルサーバー。依存なし (node:http のみ)。
// 使い方: node play/server.mjs   → http://127.0.0.1:8765/
// ルールの判定はしない。操作を inbox.jsonl に積み、Claude が読んで処理する。
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFile, execFileSync } from 'node:child_process';
import os from 'node:os';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// SANGEKI_GAME: ゲームの置き場所（既定は手動モードの play/game。対AIモードは play/game_ai を使う＝ai_gm.py）
const GAME = process.env.SANGEKI_GAME ? path.resolve(process.env.SANGEKI_GAME) : path.join(HERE, 'game');
// 盤面の画像（惨劇コモンズ5th）: SANGEKI_IMG > play/assets（setup_images.sh で取得）> originals/（手元の資料）
const IMG = [process.env.SANGEKI_IMG, path.join(HERE, 'assets/tragedy_commons_5th'), path.resolve(HERE, '../originals/tragedy_commons_5th')]
  .filter(Boolean).map(p => path.resolve(p)).find(p => fs.existsSync(p)) || path.join(HERE, 'assets/tragedy_commons_5th');
const PORT = Number(process.env.PORT || 8765);
const HOST = process.env.HOST || '127.0.0.1';

fs.mkdirSync(GAME, { recursive: true });
// 対AIモードの開始画面（SANGEKI_AI=1 のときだけ）: 脚本の一覧を返し、『開始』で進行役（ai_gm.py）を起動し直す。
// SANGEKI_PYTHON: 使う Python（既定 python3。仮想環境なら .venv/bin/python を渡す）
const AI = process.env.SANGEKI_AI === '1';
const PY = process.env.SANGEKI_PYTHON || 'python3';
// SANGEKI_WORKERS: 自動の脚本家が主人公の応手を読むときの並列の数（fork。既定はコア数−1、最大6。1 で並列なし）
const WORKERS = process.env.SANGEKI_WORKERS || String(Math.max(1, Math.min(6, os.cpus().length - 1)));
const ENV = { ...process.env, SANGEKI_WORKERS: WORKERS };
const PCS = ['read2', 'read', 'lockQA', 'oreadKQL', 'oread'];  // 自動の主人公（自動同士で観る。lockQA はどこからともなく置かれたカウンターも読む。oreadKQL は脚本を知る。oread は v0.6 の版）。空なら人が主人公
const MMS = ['calcGBxhdKN', 'calcGBxhdK', 'calcGBxhd', 'calcD', 'calcG', 'searchL', 'search', 'route'];  // 先頭が既定（最強）  // 開始画面で選べる自動の脚本家（ほかの型は ai_gm.py を直接起動）
let child = null, scriptList = null;
// サーバーが止まるときは進行役（子の ai_gm.py）も止める。止めないと、再起動した後も古い進行役が同じ置き場所（game/）に書き続け、
// 新しい試合の記録と書き合って落ちた（モンキー試験で見つけた。1日半前の進行役が残っていた 2026-10-30）
for (const sig of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(sig, () => { if (child && child.exitCode === null) child.kill('SIGTERM'); process.exit(0); });
process.on('exit', () => { if (child && child.exitCode === null) child.kill('SIGTERM'); });
function listScripts() {
  if (!scriptList) scriptList = JSON.parse(execFileSync(PY, [path.join(HERE, 'ai_gm.py'), '--list'], { cwd: HERE }).toString());
  return scriptList;
}
function startGame({ script, mm, pc, seed, blind, code, replay, resume }) {
  if (replay) {  // リプレイの記録を再生する（URL の #p=）。中身の検証は ai_gm.py がする
    if (typeof replay !== 'object' || JSON.stringify(replay).length > 200000) throw new Error('リプレイの記録が不正');
    const f = path.join(GAME, 'replay_in.json');
    fs.writeFileSync(f, JSON.stringify(replay));
    if (child && child.exitCode === null) child.kill('SIGTERM');
    const out = fs.openSync(path.join(GAME, 'ai_gm.log'), 'w');
    child = spawn(PY, [path.join(HERE, 'ai_gm.py'), '--replay', f, ...(resume ? ['--resume'] : []), '--dir', GAME], { cwd: HERE, env: ENV, stdio: ['ignore', out, out] });
    return;
  }
  if (!MMS.includes(mm) && mm !== 'human') throw new Error('脚本家の型が不正');  // human: 人が脚本家（主人公は自動。ai_gm の既定は read2）
  if (pc && !PCS.includes(pc)) throw new Error('主人公の型が不正');
  // code: シナリオエディタのコード（s1.＋base64url）。中身の検証は ai_gm.py がする
  if (code && !/^s1\.[A-Za-z0-9_-]{1,20000}$/.test(code)) throw new Error('シナリオのコードが不正');
  if (code) {  // 進行役を起動する前に確かめ、誤りは開始画面に返す（以前は進行役が開始時に落ち、理由が出ないまま盤面が固まった）
    const run = (flag, input) => JSON.parse(execFileSync(PY, [path.join(HERE, 'ai_gm.py'), flag], { cwd: HERE, input }).toString());
    const d = run('--decode', code);
    if (d.error) throw new Error('シナリオのコードを読めない: ' + d.error);
    const r = run('--check', JSON.stringify(d.script));
    if ((r.errors || []).length) throw new Error('シナリオに誤りがある: ' + r.errors.join(' ／ '));
  }
  if (code || mm === 'human') blind = false;  // 人が脚本家なら脚本を知っている（伏せて選ばない）
  if (!code && !blind && !listScripts().scripts.some(s => s.id === script)) throw new Error('脚本が無い');  // 人の脚本家も確かめる（空の脚本で進行役が落ち、盤面が固まった。モンキー試験）
  if (blind && !listScripts().blind) throw new Error('伏せて選べる脚本（自動生成）が無い');
  seed = Number.isInteger(+seed) ? String(+seed) : '1';
  if (child && child.exitCode === null) child.kill('SIGTERM');
  for (const f of ['notes.json', 'state.json']) fs.rmSync(path.join(GAME, f), { force: true });  // 前の試合の推理の書き込み・盤面を残さない
  const out = fs.openSync(path.join(GAME, 'ai_gm.log'), 'w');
  const args = [path.join(HERE, 'ai_gm.py'), '--mm', mm, ...(pc ? ['--pc', pc] : []), '--seed', seed, '--dir', GAME, ...(code ? ['--code', code] : blind ? ['--blind'] : ['--script', script])];
  child = spawn(PY, args, { cwd: HERE, env: ENV, stdio: ['ignore', out, out] });
  console.log('開始:', args.slice(1).join(' '));
}
const INBOX = path.join(GAME, 'inbox.jsonl');
const countLines = f => fs.existsSync(f) ? fs.readFileSync(f, 'utf8').split('\n').filter(Boolean).length : 0;
let seq = countLines(INBOX);

const TYPES = { '.html': 'text/html; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.json': 'application/json; charset=utf-8', '.jsonl': 'text/plain; charset=utf-8' };

function send(res, code, body, type = 'text/plain; charset=utf-8') {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store' });
  res.end(body);
}
function sendFile(res, file, fallback) {
  if (!fs.existsSync(file)) return fallback === undefined ? send(res, 404, 'not found') : send(res, 200, fallback, TYPES[path.extname(file)]);
  send(res, 200, fs.readFileSync(file), TYPES[path.extname(file)] || 'application/octet-stream');
}
function readBody(req) {
  return new Promise((ok, ng) => {
    let s = '';
    req.on('data', c => { s += c; if (s.length > 1e6) req.destroy(); });
    req.on('end', () => { try { ok(JSON.parse(s)); } catch (e) { ng(e); } });
  });
}

http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const p = decodeURIComponent(url.pathname);
  try {
    if (req.method === 'GET') {
      if (p === '/' || p === '/index.html') return sendFile(res, path.join(HERE, 'board.html'));  // index.html: ブラウザ版と同じ名前（シナリオエディタの「遊ぶ」）
      if (p === '/editor' || p === '/editor.html') return sendFile(res, path.join(HERE, 'editor.html'));
      if (p === '/open' || p === '/open.html') return sendFile(res, path.join(HERE, 'open.html'));  // 公開シート（URL の ?o= だけから描く）
      if (p === '/data/incidents') return sendFile(res, path.join(HERE, 'engine', 'data', 'incidents.json'), '{}');
      if (p === '/state') return sendFile(res, path.join(GAME, 'state.json'), '{}');
      if (p === '/log') return sendFile(res, path.join(GAME, 'log.jsonl'), '');
      if (p === '/notes') return sendFile(res, path.join(GAME, 'notes.json'), '{}');
      if (p === '/deduce') return sendFile(res, path.join(GAME, 'deduce.json'), '{}');
      if (p === '/record') return sendFile(res, path.join(GAME, 'record.json'), '{}');  // リプレイの URL（#p=）の元
      if (p === '/record-blind') return sendFile(res, path.join(GAME, 'private', 'record.json'), '{}');  // 伏せた試合の途中の記録（脚本名入り。盤面で確かめてから取る）
      if (p === '/rejected') return sendFile(res, path.join(GAME, 'rejected.json'), '[]');  // 受け付けなかった操作（リプレイで見返す）
      if (p === '/snapshots') return sendFile(res, path.join(GAME, 'snapshots.jsonl'), '');
      if (p === '/scripts') return AI ? send(res, 200, JSON.stringify({ ...listScripts(), mms: MMS }), TYPES['.json']) : send(res, 404, 'not found');
      // 公開のキャラクター情報（不安臨界・初期エリア・禁止エリア・友好能力の文面）。engine/data/build_public_chars.py が作る
      if (p === '/chars') return sendFile(res, path.join(HERE, 'engine', 'data', 'characters_public.json'), '{}');
      if (p === '/data/rules') return sendFile(res, path.join(HERE, 'engine', 'data', 'rules.json'), '{}');
      if (p === '/data/roles') return sendFile(res, path.join(HERE, 'engine', 'data', 'roles.json'), '{}');
      if (p.startsWith('/revealed/')) {
        // 公開済みの固定ファイルだけ。private/ は返さない
        const dir = path.join(GAME, 'revealed');
        const f = path.resolve(dir, '.' + p.slice(9));
        if (!f.startsWith(dir + path.sep)) return send(res, 403, 'forbidden');
        return sendFile(res, f);
      }
      // SANGEKI_NOIMG=1: 画像を配信しない（盤面は画像が読めないので自動で文字の表示になる）
      if (p.startsWith('/img/') && process.env.SANGEKI_NOIMG === '1') return send(res, 404, 'no images');
      if (p.startsWith('/img/')) {
        // originals/tragedy_commons_5th の外には出さない
        const f = path.resolve(IMG, '.' + p.slice(4));
        if (!f.startsWith(IMG + path.sep)) return send(res, 403, 'forbidden');
        return sendFile(res, f);
      }
    }
    if (req.method === 'POST' && p === '/action') {
      const a = await readBody(req);
      const rec = { seq: ++seq, at: new Date().toISOString(), ...a };
      fs.appendFileSync(INBOX, JSON.stringify(rec) + '\n');
      return send(res, 200, JSON.stringify({ seq }), TYPES['.json']);
    }
    if (req.method === 'POST' && p === '/new' && AI) {
      startGame(await readBody(req));
      return send(res, 200, '{}', TYPES['.json']);
    }
    if (req.method === 'POST' && ['/check', '/decode', '/analyze', '/generate', '/encode'].includes(p)) {  // シナリオエディタの検証・コードの読み込み・分析・生成（ai_gm.py --check など）
      const b = await readBody(req);
      const input = p === '/decode' ? String(b.code || '') : JSON.stringify(b);
      // 待つ間もほかの要求（盤面の更新・すぐ出す分析）に答える（execFileSync はサーバー全体を止める。守れるかの判定は十数秒かかる）
      const out = await new Promise((ok, ng) => {
        const ch = execFile(PY, [path.join(HERE, 'ai_gm.py'), '--' + p.slice(1)], { cwd: HERE, maxBuffer: 1 << 26 }, (err, so) => err ? ng(err) : ok(so));
        ch.stdin.end(input);
      });
      return send(res, 200, out, TYPES['.json']);
    }
    if (req.method === 'POST' && p === '/notes') {
      const n = await readBody(req);
      const tmp = path.join(GAME, 'notes.json.tmp');
      fs.writeFileSync(tmp, JSON.stringify(n, null, 1));
      fs.renameSync(tmp, path.join(GAME, 'notes.json'));
      return send(res, 200, '{}', TYPES['.json']);
    }
    send(res, 404, 'not found');
  } catch (e) {
    send(res, 400, String(e));
  }
}).listen(PORT, HOST, () => console.log(`http://${HOST}:${PORT}/`));
