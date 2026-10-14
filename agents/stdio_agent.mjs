// 見本エージェント（JSON を1行ずつやりとりする。Python 以外の言語の例として Node）。docs/env.md
//   node agents/stdio_agent.mjs [脚本 ID]      （play/ で実行。python は環境変数 PYTHON で変えられる）
// env.py --stdio を子プロセスで起動し、判断（1行1つの JSON）を読んで、選べる手の番号を1行で返す。
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

const env = spawn(process.env.PYTHON || 'python3', ['env.py', '--stdio', '--script', process.argv[2] || 's03_bomb', '--mm', 'calcD'],
  { stdio: ['pipe', 'pipe', 'inherit'] });
for await (const line of createInterface({ input: env.stdout })) {
  const d = JSON.parse(line);
  for (const l of d.view?.log || []) console.log(l);
  if (d.kind === 'end' || d.kind === 'error') { console.log(d.kind, JSON.stringify(d.result || d.error)); break; }
  // 最後の戦いは指摘しない（null）。ほかは選べる手から無作為に（番号で答える）
  const answer = d.kind === 'final_guess' ? null : Math.floor(Math.random() * d.options.length);
  env.stdin.write(JSON.stringify(answer) + '\n');
}
env.stdin.end();
