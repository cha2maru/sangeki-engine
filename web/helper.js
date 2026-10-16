// ブラウザ版の補助の Web Worker: 自動の脚本家が主人公の応手を読む計算の一部を受け持つ（worker.js が起動する）。
// 脚本家・盤面・置き場所の組を pickle で受け取り、i % k == idx の組を計算して pickle で返す（selfplay.search_mm.par_compute）。
const PYODIDE = new URL(self.location.href).searchParams.get('pyodide') === 'local'
  ? new URL('pyodide/', self.location.href).href : 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
let py = null;
async function init() {
  const { loadPyodide } = await import(PYODIDE + 'pyodide.mjs');
  py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage(['numpy', 'scipy']);
  const zip = await (await fetch('engine.zip')).arrayBuffer();
  py.unpackArchive(zip, 'zip', { extractDir: '/home/pyodide/play' });
  py.runPython("import sys, os; sys.path.insert(0, '/home/pyodide/play'); os.chdir('/home/pyodide/play'); from selfplay.search_mm import par_compute");
  postMessage({ type: 'ready' });
}
const booted = init().catch(err => postMessage({ type: 'error', e: String(err) }));
onmessage = async e => {
  await booted;
  const m = e.data;
  try {
    py.globals.set('P', m.payload);
    const out = py.runPython(`par_compute(P.to_py(), ${m.idx}, ${m.k})`);
    const u8 = out.toJs();
    out.destroy();
    postMessage({ type: 'out', out: u8 }, [u8.buffer]);
  } catch (err) {
    postMessage({ type: 'error', e: String(err) });
  }
};
