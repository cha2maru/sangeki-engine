// ブラウザ版の差し込み: 盤面（board.html）がサーバーに出す要求（/state・/log・/action・/new など）を、ブラウザの中で答える。
// 中身の計算は Web Worker（worker.js、Pyodide）。board.html はサーバー版と同じものを使う（build.sh がこれを先に読み込ませる）。
(() => {
  const store = { state: '{}', log: [], snaps: [], deduce: '{}' };
  let worker = null, ready = false, list = null, waitList = [], step = '準備しています', since = Date.now() / 1000, started = false, pendingNew = null, seq = 0;
  function boot() {
    ready = false; since = Date.now() / 1000; step = '準備しています';
    const q = new URLSearchParams(location.search).get('pyodide');
    worker = new Worker('worker.js' + (q ? '?pyodide=' + encodeURIComponent(q) : ''), { type: 'module' });
    worker.onerror = e => fail(e.message || 'Worker を起動できなかった');
    worker.onmessage = e => {
      const m = e.data;
      if (m.type === 'status') step = m.step;
      else if (m.type === 'ready') {
        ready = true; list = JSON.parse(m.list); waitList.splice(0).forEach(f => f(list));
        if (pendingNew) { worker.postMessage(pendingNew); pendingNew = null; }
      } else if (m.type === 'write') { if (m.f === 'state.json') store.state = m.s; else if (m.f === 'deduce.json') store.deduce = m.s; }
      else if (m.type === 'append') { (m.f === 'log.jsonl' ? store.log : m.f === 'snapshots.jsonl' ? store.snaps : []).push(m.s); }
      else if (m.type === 'reset') { store.log = []; store.snaps = []; store.deduce = '{}'; }
      else if (m.type === 'error') fail(m.msg);
    };
  }
  function fail(msg) {  // 起動や試合中のエラーを盤面の帯に出す（出さないと「準備しています」のまま止まって見えた）
    console.error(msg);
    let st = {}; try { st = JSON.parse(store.state); } catch {}
    started = true;
    store.state = JSON.stringify({ ...st, waiting: { kind: 'none', text: 'エラーで止まりました: ' + String(msg).split('\n').filter(Boolean).slice(-2).join(' ') } });
  }
  // 人の操作を待つところに JSPI（WebAssembly.Suspending）を使う。無いブラウザでは始めずに案内を出す
  if (typeof WebAssembly === 'undefined' || typeof WebAssembly.Suspending !== 'function') {
    setTimeout(() => fail('このブラウザは未対応です。Chrome・Edge の最近の版（137 以降）で開いてください'), 0);
  } else boot();
  const loading = () => JSON.stringify({ waiting: { kind: 'thinking', text: 'ブラウザで Python を準備しています', step, since, options: [] } });
  const reply = (body, type = 'application/json') => new Response(body, { status: 200, headers: { 'Content-Type': type } });
  const origFetch = window.fetch.bind(window);
  const NOTES = 'sangeki-notes';
  window.fetch = async (url, opt = {}) => {
    const u = typeof url === 'string' ? url : url.url;
    const body = () => JSON.parse(opt.body || '{}');
    switch (u) {
      case '/state': return reply(!ready && !started ? loading() : store.state);
      case '/log': return reply(store.log.join('\n'), 'text/plain');
      case '/snapshots': return reply(store.snaps.join('\n'), 'text/plain');
      case '/deduce': return reply(store.deduce);
      case '/scripts': return reply(JSON.stringify({ ...(ready ? list : await new Promise(r => waitList.push(r))), mms: ['calcG', 'searchL', 'search', 'route'] }));
      case '/new': {
        const a = body();
        if (started) { worker.terminate(); boot(); }  // 試合の途中は Python が操作を待って止まっているので、Worker ごと作り直す
        started = true; store.state = '{}'; store.log = []; store.snaps = []; store.deduce = '{}';
        const msg = { type: 'new', ...a };
        if (ready) worker.postMessage(msg); else pendingNew = msg;
        return reply('{}');
      }
      case '/action': { const a = body(); worker.postMessage({ type: 'action', rec: { seq: ++seq, ...a } }); return reply(JSON.stringify({ seq })); }
      case '/notes':
        if ((opt.method || 'GET') === 'POST') { try { localStorage.setItem(NOTES, opt.body); } catch {} return reply('{}'); }
        try { return reply(localStorage.getItem(NOTES) || '{}'); } catch { return reply('{}'); }
      case '/chars': return origFetch('data/chars.json');
      case '/data/rules': return origFetch('data/rules.json');
      case '/data/roles': return origFetch('data/roles.json');
    }
    return origFetch(url, opt);
  };
})();
