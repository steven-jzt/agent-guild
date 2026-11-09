import { managedSpawn } from '../../src/transport/managed-process.ts';
import { fileURLToPath } from 'node:url';
const child = managedSpawn(process.execPath, [fileURLToPath(new URL('./process-worker.mjs', import.meta.url))]);
child.stdout.pipe(process.stdout);
child.stderr.pipe(process.stderr);
setInterval(() => {}, 1000);
