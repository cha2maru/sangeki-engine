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

onmessage = async e => {
  const m = e.data;
  if (m.type === 'action') {
    const s = JSON.stringify(m.rec);
    if (waiter) { const w = waiter; waiter = null; w(s); } else actions.push(s);
  }
  if (m.type === 'call' && ['check_json', 'decode_json'].includes(m.fn)) {  // シナリオエディタ: 検証とコードの作成・読み込み（engine.scripts）
    await booted;
    py.globals.set('ARG', m.arg);
    postMessage({ type: 'called', id: m.id, s: py.runPython(`web_gm.${m.fn}(ARG)`) });
  }
  if (m.type === 'new') {
    await booted;
    py.globals.set('ARGS', py.toPy({ script: m.script || '', mm: m.mm, seed: m.seed, blind: !!m.blind, code: m.code || '' }));
    try {
      await py.runPythonAsync('web_gm.start(**ARGS)');
    } catch (err) { postMessage({ type: 'error', msg: String(err) }); }
  }
};
booted.catch(err => postMessage({ type: 'error', msg: String(err) }));
