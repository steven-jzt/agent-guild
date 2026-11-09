import { createInterface } from 'node:readline';
const send = frame => process.stdout.write(JSON.stringify(frame) + '\n');
let questionId;
createInterface({ input: process.stdin }).on('line', line => {
  const frame = JSON.parse(line);
  if (frame.method === 'echo') send({ id: frame.id, result: frame.params });
  if (frame.method === 'bad-id') send({ id: [frame.id], result: {} });
  if (frame.method === 'bad-result') send({ id: frame.id });
  if (frame.method === 'ask') {
    questionId = frame.id;
    send({ id: 'server-question', method: 'question', params: { text: 'Approve?' } });
  }
  if (frame.id === 'server-question') send({ id: questionId, result: frame.result });
}).on('close', () => process.exit(0));
