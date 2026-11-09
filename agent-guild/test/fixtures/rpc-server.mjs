import { createInterface } from 'node:readline';

const send = frame => process.stdout.write(JSON.stringify(frame) + '\n');
const configuredDelay = Number(process.env.GUILD_FIXTURE_READY_DELAY_MS ?? 0);
const readyDelayMs = Number.isFinite(configuredDelay) && configuredDelay > 0 ? configuredDelay : 0;

let questionId;
const input = createInterface({ input: process.stdin });
input.on('line', line => {
  const frame = JSON.parse(line);
  if (frame.method === 'fixture/ready') return;
  if (frame.method === 'echo') send({ id: frame.id, result: frame.params });
  if (frame.method === 'bad-id') send({ id: [frame.id], result: {} });
  if (frame.method === 'bad-result') send({ id: frame.id });
  if (frame.method === 'ask') {
    questionId = frame.id;
    send({ id: 'server-question', method: 'question', params: { text: 'Approve?' } });
  }
  if (frame.id === 'server-question') send({ id: questionId, result: frame.result });
}).on('close', () => process.exit(0));

// 明确的就绪信号：测试必须先等到它，再开始计算请求期限。
// 启动延迟只影响等待就绪的时间，不能占用请求自身的超时预算。
setTimeout(() => send({ method: 'fixture/ready' }), readyDelayMs);
