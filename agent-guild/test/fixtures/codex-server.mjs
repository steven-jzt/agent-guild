import { createInterface } from 'node:readline';
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
createInterface({ input: process.stdin }).on('line', line => {
  const request = JSON.parse(line);
  if (request.method === 'initialize') send({ id: request.id, result: {} });
  if (request.method === 'thread/start') send({ id: request.id, result: { thread: { id: 'test-thread' } } });
  if (request.method === 'turn/start') {
    send({ method: 'item/completed', params: { threadId: 'test-thread', item: { type: 'agentMessage', text: '交付完成' } } });
    send({ method: 'turn/completed', params: { threadId: 'test-thread', turn: { id: 'test-turn', status: 'completed' } } });
    send({ id: request.id, result: { turn: { id: 'test-turn' } } });
  }
}).on('close', () => process.exit(0));
