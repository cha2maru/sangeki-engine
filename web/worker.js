// ブラウザ版の Web Worker: Pyodide（ブラウザの中の Python）でエンジンと自動の脚本家を動かす。盤面（shim.js）とはメッセージでやりとりする。
// 人の操作を待つところは JSPI（pyodide.ffi.run_sync）で JavaScript の Promise を待つ。
// Pyodide の置き場所: 既定は CDN。?pyodide=local（build.sh --local-pyodide で dist/pyodide に同梱したとき）なら同じ場所から
const PYODIDE = new URL(self.location.href).searchParams.get('pyodide') === 'local'
  ? new URL('pyodide/', self.location.href).href : 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
let py = null, waiter = null;
const actions = [];
self.post_write = (f, s) => postMessage({ type: 'write', f, s });
self.post_append = (f, s) => postMessage({ type: 'append', f, s });
self.post_reset = () => postMessage({ type: 'reset' });
self.next_action = () => actions.length ? Promise.resolve(actions.shift()) : new Promise(r => { waiter = r; });
const status = step => postMessage({ type: 'status', step });

async function init() {
  status('Python（Pyodide）を読み込んでいます');
  const { loadPyodide } = await import(PYODIDE + 'pyodide.mjs');  // モジュールの Worker（importScripts は CDN から読めないことがあった）
  py = await loadPyodide({ indexURL: PYODIDE });
  status('numpy・scipy を読み込んでいます');
  await py.loadPackage(['numpy', 'scipy']);
  status('エンジンを展開しています');
  const zip = await (await fetch('engine.zip')).arrayBuffer();
  py.unpackArchive(zip, 'zip', { extractDir: '/home/pyodide/play' });
  py.runPython("import sys, os; sys.path.insert(0, '/home/pyodide/play'); os.chdir('/home/pyodide/play')");
  status('エンジンと推理を読み込んでいます');
  py.runPython('import web_gm');
  postMessage({ type: 'ready', list: py.runPython('web_gm.list_json()') });
}
const booted = init();

// 補助の Worker（helper.js）: 自動の脚本家が主人公の応手を読む計算を分ける（search_mm.SearchMastermind._par_browser）。
// メインの準備が終わってから裏で読み込み、読み込み中・失敗したときはメインだけで計算する。数は ?helpers=N（既定2、0で使わない、最大4）
const HELP_N = Math.max(0, Math.min(4, Number(new URL(self.location.href).searchParams.get('helpers') ?? 2)));
const helpers = [];
let parWaits = [];
booted.then(() => {
  const q = new URL(self.location.href).searchParams.get('pyodide') === 'local' ? '?pyodide=local' : '';
  for (let i = 0; i < HELP_N; i++) {
    const h = { w: new Worker(new URL('helper.js' + q, self.location.href), { type: 'module' }), ready: false, done: null, fail: null };
    h.w.onmessage = e => {
      const m = e.data;
      if (m.type === 'ready') h.ready = true;
      else if (m.type === 'out' && h.done) { const r = h.done; h.done = h.fail = null; r(m.out); }
      else if (m.type === 'error') { h.ready = false; if (h.fail) { const r = h.fail; h.done = h.fail = null; r(new Error(m.e)); } }
    };
    h.w.onerror = () => { h.ready = false; if (h.fail) { const r = h.fail; h.done = h.fail = null; r(new Error('helper error')); } };
    helpers.push(h);
  }
});
self.par_helpers_ready = () => helpers.filter(h => h.ready).length;
self.par_start = (payload, k) => {  // payload: 脚本家・盤面・組の pickle（Uint8Array）。補助 j は i % k == j+1 の組を計算する
  parWaits = helpers.filter(h => h.ready).slice(0, k - 1).map((h, j) => new Promise((done, fail) => {
    h.done = done; h.fail = fail;
    h.w.postMessage({ type: 'run', payload, idx: j + 1, k });
  }));
};
self.par_wait = () => Promise.all(parWaits);

onmessage = async e => {
  const m = e.data;
  if (m.type === 'action') {
    const s = JSON.stringify(m.rec);
    if (waiter) { const w = waiter; waiter = null; w(s); } else actions.push(s);
  }
  if (m.type === 'call' && ['check_json', 'decode_json', 'analyze_json', 'generate_json'].includes(m.fn)) {  // シナリオエディタ: 検証とコードの作成・読み込み（engine.scripts）
    await booted;
    py.globals.set('ARG', m.arg);
    postMessage({ type: 'called', id: m.id, s: py.runPython(`web_gm.${m.fn}(ARG)`) });
  }
  if (m.type === 'new') {
    await booted;
    py.globals.set('ARGS', py.toPy({ script: m.script || '', mm: m.mm, pc: m.pc || '', seed: m.seed, blind: !!m.blind, code: m.code || '', replay: m.replay ? JSON.stringify(m.replay) : '', resume: !!m.resume }));
    // 試験用（?mmhint=1）: 人の脚本家の問いに自動の脚本家の答えを添える（サーバー版の SANGEKI_MM_HINT と同じ。通し試験で最後の戦いまで進める）
    if (new URL(self.location.href).searchParams.get('mmhint') === '1') py.runPython("import os; os.environ['SANGEKI_MM_HINT'] = '1'");
    try {
      await py.runPythonAsync('web_gm.start(**ARGS)');
    } catch (err) { postMessage({ type: 'error', msg: String(err) }); }
  }
};
booted.catch(err => postMessage({ type: 'error', msg: String(err) }));
