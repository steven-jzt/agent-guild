import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { homedir } from 'node:os';

export async function dshLaunch(root: string, workspace: string, pluginApprovalProbe = false) {
  const dshHome = join(root, 'dsh-home'); await mkdir(dshHome);
  const patch = join(root, 'credentials.patch.yml');
  const store = join(homedir(), '.dsh', '.credentials.yaml').replaceAll('\\', '/');
  await writeFile(patch, `- id: credentials\n  config:\n    path: ${JSON.stringify(store)}\n    watch: true\n- id: tool-plugin-manager\n  disabled: ${!pluginApprovalProbe}\n`);
  return {
    command: process.execPath,
    args: [resolve('node_modules/@deepseek-ai/dsh/lib/bin.js'), '--profile', 'sdk', '--patch', patch],
    cwd: workspace,
    env: { ...process.env, DSH_HOME: dshHome, DSH_PRIMARY_RUNTIME: '', DSH_DISABLE_TELEMETRY: 'true' },
  };
}
