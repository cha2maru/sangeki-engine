// 惨劇RoopeR 対戦用のローカルサーバー。依存なし (node:http のみ)。
// 使い方: node play/server.mjs   → http://127.0.0.1:8765/
// ルールの判定はしない。操作を inbox.jsonl に積み、Claude が読んで処理する。
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';

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
const MMS = ['calcG', 'searchL', 'search', 'route'];  // 先頭が既定（最強）  // 開始画面で選べる自動の脚本家（ほかの型は ai_gm.py を直接起動）
let child = null, scriptList = null;
function listScripts() {
  if (!scriptList) scriptList = JSON.parse(execFileSync(PY, [path.join(HERE, 'ai_gm.py'), '--list'], { cwd: HERE }).toString());
  return scriptList;
}
function startGame({ script, mm, seed, blind, code }) {
  if (!MMS.includes(mm)) throw new Error('脚本家の型が不正');
  // code: シナリオエディタのコード（s1.＋base64url）。中身の検証は ai_gm.py がする
  if (code && !/^s1\.[A-Za-z0-9_-]{1,20000}$/.test(code)) throw new Error('シナリオのコードが不正');
  if (code) blind = false;
  else if (!blind && !listScripts().scripts.some(s => s.id === script)) throw new Error('脚本が無い');
  if (blind && !listScripts().blind) throw new Error('伏せて選べる脚本（自動生成）が無い');
  seed = Number.isInteger(+seed) ? String(+seed) : '1';
  if (child && child.exitCode === null) child.kill('SIGTERM');
  for (const f of ['notes.json', 'state.json']) fs.rmSync(path.join(GAME, f), { force: true });  // 前の試合の推理の書き込み・盤面を残さない
  const out = fs.openSync(path.join(GAME, 'ai_gm.log'), 'w');
  const args = [path.join(HERE, 'ai_gm.py'), '--mm', mm, '--seed', seed, '--dir', GAME, ...(code ? ['--code', code] : blind ? ['--blind'] : ['--script', script])];
  child = spawn(PY, args, { cwd: HERE, stdio: ['ignore', out, out] });
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
      if (p === '/sheet') return sendFile(res, path.join(HERE, 'sheet.html'));
      if (p === '/editor' || p === '/editor.html') return sendFile(res, path.join(HERE, 'editor.html'));
      if (p === '/data/incidents') return sendFile(res, path.join(HERE, 'engine', 'data', 'incidents.json'), '{}');
      if (p === '/state') return sendFile(res, path.join(GAME, 'state.json'), '{}');
      if (p === '/log') return sendFile(res, path.join(GAME, 'log.jsonl'), '');
      if (p === '/notes') return sendFile(res, path.join(GAME, 'notes.json'), '{}');
      if (p === '/deduce') return sendFile(res, path.join(GAME, 'deduce.json'), '{}');
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
    if (req.method === 'POST' && (p === '/check' || p === '/decode')) {  // シナリオエディタの検証・コードの読み込み（ai_gm.py --check／--decode）
      const b = await readBody(req);
      const input = p === '/check' ? JSON.stringify(b) : String(b.code || '');
      const out = execFileSync(PY, [path.join(HERE, 'ai_gm.py'), p === '/check' ? '--check' : '--decode'], { cwd: HERE, input }).toString();
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
