import { spawn, execFileSync, type SpawnOptionsWithoutStdio } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, mkdirSync, renameSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export function windowsHost(): string {
  const source = fileURLToPath(new URL('./JobHost.cs', import.meta.url));
  const hash = createHash('sha256').update(readFileSync(source)).digest('hex').slice(0, 16);
  const directory = join(process.env.LOCALAPPDATA!, 'AgentGuild', 'bin');
  const output = join(directory, `job-host-${hash}.exe`);
  if (!existsSync(output)) {
    mkdirSync(directory, { recursive: true });
    const temporary = join(directory, `job-host-${hash}-${process.pid}.exe`);
    const compiler = join(process.env.SystemRoot ?? 'C:\\Windows', 'Microsoft.NET', 'Framework64', 'v4.0.30319', 'csc.exe');
    execFileSync(compiler, ['/nologo', '/optimize+', `/out:${temporary}`, source], { windowsHide: true });
    try { renameSync(temporary, output); } catch (error) {
      if (!existsSync(output)) throw error;
      rmSync(temporary, { force: true });
    }
  }
  return output;
}

export function managedSpawn(executable: string, args: string[], options: SpawnOptionsWithoutStdio = {}) {
  const command = process.platform === 'win32' ? windowsHost() : executable;
  const arguments_ = process.platform === 'win32' ? [String(process.pid), executable, ...args] : args;
  return spawn(command, arguments_, { ...options, detached: false, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
}
