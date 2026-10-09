// 惨劇RoopeR 対戦用のローカルサーバー。依存なし (node:http のみ)。
// 使い方: node play/server.mjs   → http://127.0.0.1:8765/
// ルールの判定はしない。操作を inbox.jsonl に積み、Claude が読んで処理する。
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// SANGEKI_GAME: ゲームの置き場所（既定は手動モードの play/game。対AIモードは play/game_ai を使う＝ai_gm.py）
const GAME = process.env.SANGEKI_GAME ? path.resolve(process.env.SANGEKI_GAME) : path.join(HERE, 'game');
// 盤面の画像（惨劇コモンズ5th）: SANGEKI_IMG > play/assets（setup_images.sh で取得）> originals/（手元の資料）
const IMG = [process.env.SANGEKI_IMG, path.join(HERE, 'assets/tragedy_commons_5th'), path.resolve(HERE, '../originals/tragedy_commons_5th')]
  .filter(Boolean).map(p => path.resolve(p)).find(p => fs.existsSync(p)) || path.join(HERE, 'assets/tragedy_commons_5th');
const PORT = Number(process.env.PORT || 8765);
const HOST = process.env.HOST || '127.0.0.1';

fs.mkdirSync(GAME, { recursive: true });
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
      if (p === '/') return sendFile(res, path.join(HERE, 'board.html'));
      if (p === '/sheet') return sendFile(res, path.join(HERE, 'sheet.html'));
      if (p === '/state') return sendFile(res, path.join(GAME, 'state.json'), '{}');
      if (p === '/log') return sendFile(res, path.join(GAME, 'log.jsonl'), '');
      if (p === '/notes') return sendFile(res, path.join(GAME, 'notes.json'), '{}');
      if (p === '/deduce') return sendFile(res, path.join(GAME, 'deduce.json'), '{}');
      if (p === '/snapshots') return sendFile(res, path.join(GAME, 'snapshots.jsonl'), '');
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
