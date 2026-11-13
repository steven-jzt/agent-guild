const $ = (id) => document.getElementById(id);
const projectSelect = $('projects');
const agentSelect = $('agent');
const approverRole = $('approverRole');
const repoInput = $('repo');
const worktreeInput = $('worktree');
const checkInput = $('check');
const noticeBox = $('notice');
const errorBox = $('error');
const pendingActions = new Map();

async function runAction(key, command, payload) {
  if (pendingActions.has(key)) throw new Error('该操作仍在执行，请勿重复提交');
  const request = window.guild.rpc(command, payload);
  pendingActions.set(key, request);
  renderTasks();
  renderCandidates();
  renderRecovery();
  try { return await request; }
  finally {
    pendingActions.delete(key);
    renderTasks();
    renderCandidates();
    renderRecovery();
  }
}

function cancelButton(key) {
  const request = pendingActions.get(key);
  return request && typeof request.cancel === 'function'
    ? `<button class="btn ghost" data-action="cancel" data-key="${esc(key)}">停止当前请求</button>`
    : '<button class="btn ghost" disabled>执行中…</button>';
}

async function cancelAction(key) {
  const request = pendingActions.get(key);
  if (!request || typeof request.cancel !== 'function') return;
  await request.cancel();
  showNotice('已请求停止本地进程，等待退出；业务结果需刷新核对，不要直接重试。');
}

window.__lastError = '';
window.addEventListener('error', (event) => {
  window.__lastError = String(event.message || event.error || 'unknown error');
});
window.addEventListener('unhandledrejection', (event) => {
  window.__lastError = String(event.reason && event.reason.message ? event.reason.message : event.reason);
});

const state = {
  projects: [],
  tasks: [],
  candidates: [],
  runs: [],
  adapters: [],
  metrics: {},
  recovery: { executions: [], merges: [] },
};

const CANDIDATE_COLORS = {
  awaiting_human: '#d97706',
  approved: '#7c3aed',
  merged: '#059669',
  building: '#2563eb',
  validating: '#0891b2',
  rejected: '#dc2626',
};

const LIFECYCLE = [
  { label: '就绪', match: ['ready', 'assigned'], cls: 's-ready' },
  { label: '执行中', match: ['in_progress'], cls: 's-run' },
  { label: '待审', match: ['auto_review', 'human_review'], cls: 's-review' },
  { label: '待合入', match: ['ready_to_merge'], cls: 's-merge' },
  { label: '已完成', match: ['accepted'], cls: 's-done' },
  { label: '需修复', match: ['needs_fix'], cls: 's-fix' },
];

const LIFECYCLE_TEXT = {
  ready: '就绪', assigned: '已分配', in_progress: '执行中',
  auto_review: '自动验收', human_review: '人工验收',
  ready_to_merge: '待合入', accepted: '已完成', needs_fix: '需修复',
};
const STATUS_TEXT = {
  ready: '就绪', claimed: '已领取', verification: '验收中',
  review: '待审', done: '完成', failed: '失败',
};
const CANDIDATE_TEXT = {
  building: '构建中', validating: '验证中', awaiting_human: '待审批',
  approved: '已批准', merged: '已合入', rejected: '已拒绝',
};
const AGENT_TEXT = {
  'codex-deepseek': 'Codex-DeepSeek', 'codex': 'Codex',
  'codex-exec': 'Codex Exec', 'codex-appserver': 'App Server',
  'dsh': 'DSH', 'mock': 'Mock', 'codex-zhipu': 'Codex-智谱',
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function textOf(map, value) {
  return map[value] || value;
}

function pill(text, tone) {
  return `<span class="pill ${tone || ''}">${esc(text)}</span>`;
}

function lifecycleTone(value) {
  if (value === 'accepted') return 'green';
  if (value === 'needs_fix') return 'red';
  if (value === 'ready_to_merge') return 'violet';
  if (value === 'human_review' || value === 'auto_review') return 'amber';
  if (value === 'in_progress') return 'blue';
  return '';
}

function candidateTone(value) {
  if (value === 'merged') return 'green';
  if (value === 'approved') return 'violet';
  if (value === 'awaiting_human') return 'amber';
  if (value === 'rejected') return 'red';
  return 'blue';
}

function fmtCost(value) {
  const n = Number(value || 0);
  return n >= 0.01 ? `¥${n.toFixed(2)}` : `¥${n.toFixed(4)}`;
}

function estimateCost(runs) {
  let total = 0;
  for (const r of runs) {
    if (Number(r.cost_usd) > 0) {
      total += Number(r.cost_usd);
    } else {
      total += Number(r.input_tokens || 0) * 1e-6 + Number(r.output_tokens || 0) * 4e-6;
    }
  }
  return total;
}

function showNotice(text) {
  errorBox.textContent = '';
  noticeBox.textContent = text;
}

function showError(err) {
  noticeBox.textContent = '';
  const message = String(err && err.message ? err.message : err);
  errorBox.textContent = message;
  window.__lastError = message;
}

async function loadProjects() {
  state.projects = await window.guild.rpc('projects');
  const current = projectSelect.value;
  projectSelect.innerHTML = '';
  for (const p of state.projects) {
    const opt = document.createElement('option');
    opt.value = p.project_id;
    opt.textContent = p.name;
    projectSelect.appendChild(opt);
  }
  if (state.projects.some((p) => p.project_id === current)) {
    projectSelect.value = current;
  }
  applyProjectRole();
}

function applyProjectRole() {
  const project = state.projects.find((p) => p.project_id === projectSelect.value);
  if (project && project.required_approver_role) {
    approverRole.value = project.required_approver_role;
  }
}

async function loadProjectData() {
  const projectId = projectSelect.value;
  if (!projectId) {
    state.tasks = [];
    state.metrics = {};
    return;
  }
  const status = await window.guild.rpc('status', { projectId });
  state.tasks = status.tasks || [];
  state.metrics = status.metrics || {};
}

async function loadAll() {
  await loadProjects();
  const tasks = [loadProjectData(), window.guild.rpc('candidates'), window.guild.rpc('runs'), window.guild.rpc('adapters'), window.guild.rpc('recovery', { projectId: projectSelect.value })];
  const [, candidates, runs, adapters, recovery] = await Promise.all(tasks);
  state.candidates = candidates || [];
  state.runs = runs || [];
  state.adapters = adapters || [];
  state.recovery = recovery && Array.isArray(recovery.executions) ? recovery : { executions: [], merges: [] };
  renderAll();
}

function renderAll() {
  const project = state.projects.find((p) => p.project_id === projectSelect.value);
  $('sideProject').textContent = project ? project.name : '—';
  renderKpis();
  renderPipeline();
  renderDonut();
  renderTasks();
  renderAgents();
  renderCandidates();
  renderAdapters();
  renderRecovery();
}

function renderKpis() {
  const merged = state.candidates.filter((c) => c.status === 'merged').length;
  const pending = state.candidates.filter((c) => c.status === 'awaiting_human').length;
  $('kpiTasks').textContent = state.tasks.length;
  $('kpiTasksSub').textContent = `${state.projects.length} 个项目`;
  $('kpiMerged').textContent = merged;
  $('kpiPending').textContent = pending;
  $('kpiCost').textContent = fmtCost(estimateCost(state.runs));
}

function renderPipeline() {
  const counts = LIFECYCLE.map((g) => state.tasks.filter((t) => g.match.includes(t.lifecycle)).length);
  const max = Math.max(1, ...counts);
  $('pipeline').innerHTML = LIFECYCLE.map((g, i) => `
    <div class="stage ${g.cls}">
      <div class="s-label">${g.label}</div>
      <div class="s-num">${counts[i]}</div>
      <div class="s-bar"><i style="width:${Math.round(counts[i] / max * 100)}%"></i></div>
    </div>`).join('');
}

function renderDonut() {
  const counts = {};
  for (const c of state.candidates) counts[c.status] = (counts[c.status] || 0) + 1;
  const total = state.candidates.length;
  $('donutTotal').textContent = total;
  $('donutHint').textContent = `${total} 个候选`;
  let start = 0;
  const parts = [];
  for (const [status, count] of Object.entries(counts)) {
    const pct = total ? count / total * 100 : 0;
    const color = CANDIDATE_COLORS[status] || '#64748b';
    parts.push(`${color} ${start}% ${start + pct}%`);
    start += pct;
  }
  $('donut').style.background = parts.length
    ? `conic-gradient(${parts.join(',')})`
    : 'conic-gradient(#e2e8f0 0 100%)';
  $('donutLegend').innerHTML = Object.entries(counts).length
    ? Object.entries(counts).map(([status, count]) => `
      <div class="legend-item"><span class="dot" style="background:${CANDIDATE_COLORS[status] || '#64748b'}"></span>
        ${esc(textOf(CANDIDATE_TEXT, status))}<b>${count}</b></div>`).join('')
    : '<div class="empty">暂无候选</div>';
}

function taskCheck(task) {
  const checks = task.checks || [];
  const found = checks.find((item) => item && item.type === 'command' && item.command);
  return found ? found.command : '';
}

function taskAction(t) {
  if (pendingActions.has(`run:${t.id}`)) return cancelButton(`run:${t.id}`);
  const active = state.candidates.find((c) =>
    c.task_id === t.id && ['building', 'validating', 'awaiting_human', 'approved'].includes(c.status));
  if (active) {
    const label = {
      building: '构建中', validating: '验证中',
      awaiting_human: '待审批', approved: '待合入',
    }[active.status] || active.status;
    return `<button class="btn ghost" disabled>${esc(label)}</button>`;
  }
  if (!['ready', 'assigned', 'needs_fix'].includes(t.lifecycle)) {
    return `<button class="btn ghost" disabled>${esc(textOf(LIFECYCLE_TEXT, t.lifecycle))}</button>`;
  }
  return `<button class="btn green" data-action="run" data-id="${esc(t.id)}" data-check="${esc(taskCheck(t))}">启动 Run</button>`;
}

function renderTasks() {
  if (!state.tasks.length) {
    $('taskRows').innerHTML = '<tr><td colspan="5" class="empty">暂无任务</td></tr>';
    return;
  }
  $('taskRows').innerHTML = state.tasks.map((t) => `
    <tr>
      <td class="title-cell" title="${esc(t.title)}">${esc(t.title)}</td>
      <td>${pill(textOf(LIFECYCLE_TEXT, t.lifecycle), lifecycleTone(t.lifecycle))}</td>
      <td class="mono">${esc(textOf(STATUS_TEXT, t.status))}</td>
      <td>${esc(t.owner || '—')}</td>
      <td>${taskAction(t)}</td>
    </tr>`).join('');
}

function renderAgents() {
  const groups = {};
  for (const r of state.runs) {
    const key = r.worker || 'unknown';
    groups[key] = groups[key] || { count: 0, tokens: 0 };
    groups[key].count += 1;
    groups[key].tokens += Number(r.input_tokens || 0) + Number(r.output_tokens || 0);
  }
  const entries = Object.entries(groups).sort((a, b) => b[1].tokens - a[1].tokens);
  const max = Math.max(1, ...entries.map(([, v]) => v.tokens));
  $('agentBars').innerHTML = entries.length
    ? entries.map(([name, v]) => `
      <div class="agent-row">
        <div class="agent-name" title="${esc(name)}">${esc(textOf(AGENT_TEXT, name))}</div>
        <div class="agent-track"><i style="width:${Math.round(v.tokens / max * 100)}%"></i></div>
        <div class="agent-val">${v.count} 次</div>
      </div>`).join('')
    : '<div class="empty">暂无运行记录</div>';
}

function renderAdapters() {
  const chips = state.adapters.map((a) => {
    const on = a.available ? 'on' : 'off';
    return `<span class="chip" title="${esc(a.reason || '')}"><span class="dot ${on}"></span>${esc(textOf(AGENT_TEXT, a.name))}</span>`;
  }).join('');
  $('adapterChips').innerHTML = chips || '<span class="empty">未探测到适配器</span>';
  $('sideAdapters').innerHTML = state.adapters.map((a) => `
    <div class="adapter" title="${esc(a.reason || '')}">
      <span class="dot ${a.available ? 'on' : 'off'}"></span>${esc(textOf(AGENT_TEXT, a.name))}
    </div>`).join('');
}

function hasPaths() {
  return Boolean(repoInput.value.trim() && worktreeInput.value.trim());
}

function renderCandidates() {
  if (!state.candidates.length) {
    $('candidateCards').innerHTML = '<div class="empty">暂无候选</div>';
    return;
  }
  $('candidateCards').innerHTML = state.candidates.map((c) => {
    const busy = pendingActions.has(`candidate:${c.candidate_id}`);
    const canApprove = !busy && c.status === 'awaiting_human';
    const canMerge = !busy && c.status === 'approved' && hasPaths();
    return `
      <div class="candidate">
        <div class="c-top"><span class="c-id">${esc(c.candidate_id)}</span>${pill(textOf(CANDIDATE_TEXT, c.status), candidateTone(c.status))}</div>
        <div class="c-task">${esc(c.task_id)} · ${esc(c.task_title || '')}</div>
        ${c.approved_by ? `<div class="c-task">批准：${esc(c.approved_by)} · ${esc(c.approver_role || 'reviewer')}</div>` : ''}
        <div class="c-sha mono">${esc((c.candidate_sha || '').slice(0, 14))}</div>
        ${c.status === 'approved' && !hasPaths() ? '<div class="c-task">先填写运行设置，或点“填入示例”</div>' : ''}
        <div class="c-actions">
          ${busy ? cancelButton(`candidate:${c.candidate_id}`) : ''}
          ${canApprove ? `<button class="btn green" data-action="approve" data-id="${esc(c.candidate_id)}">批准</button>` : ''}
          ${canMerge ? `<button class="btn" data-action="merge" data-id="${esc(c.candidate_id)}">合入</button>` : ''}
        </div>
      </div>`;
  }).join('');
}

function renderRecovery() {
  const rows = state.recovery || { executions: [], merges: [] };
  const localText = { running: '本地进程仍在运行', stopped: '所属本地进程已退出', unknown: '进程归属需手工核对' };
  const mergeText = { published: '目标已是准备结果，待补全记录', prepared: '已准备，尚未合入',
    unprepared: '准备未完成，需核对工作区', target_changed: '目标已变化，禁止恢复', unknown: '无法确认目标状态' };
  const executions = rows.executions.map(e => {
    const busy = pendingActions.has(`execution:${e.execution_id}`);
    const enabled = !busy && (e.can_reconcile || e.manual_confirmation_allowed);
    return `<div class="candidate">
      <div class="c-top"><span class="c-id">${esc(e.execution_id)}</span><span>${esc(localText[e.local_state] || '待核对')}</span></div>
      <div class="c-task">${esc(e.task_id)} · 第 ${esc(e.generation)} 次执行</div>
      <div class="c-task">工作区：${esc(e.workspace || '未记录')}<br>改动（前20项）：${(e.workspace_changes || []).slice(0,20).map(esc).join('、') || '无已识别改动'}${e.workspace_error ? '<br>' + esc(e.workspace_error) : ''}</div>
      <label>核对说明 <input id="recovery-reason-${esc(e.execution_id)}" placeholder="检查进程与工作区后填写" /></label>
      ${e.manual_confirmation_allowed ? `<label><input type="checkbox" id="recovery-confirm-${esc(e.execution_id)}" />我已手工确认本地执行及其子进程均已停止</label>` : ''}
      <div class="c-actions"><button class="btn ghost" data-recovery="execution" data-id="${esc(e.execution_id)}" ${enabled ? '' : 'disabled'}>记录中断，保留工作区</button></div>
    </div>`;
  });
  const merges = rows.merges.map(m => `<div class="candidate">
    <div class="c-top"><span class="c-id">${esc(m.attempt_id)}</span><span>${esc(mergeText[m.recovery_state] || '待核对')}</span></div>
    <div class="c-task">${esc(m.candidate_id)} · ${esc(m.target_ref)}<br>目标：${esc(m.repo_path)}<br>实际 SHA：${esc(m.actual_target_sha || '未知')}<br>准备 SHA：${esc(m.git_result_sha || '尚无凭据')}${m.inspection_error ? '<br>' + esc(m.inspection_error) : ''}</div>
    <div class="c-actions">
      ${m.can_confirm ? `<button class="btn green" data-recovery="confirm" data-id="${esc(m.attempt_id)}" ${pendingActions.has('attempt:' + m.attempt_id) ? 'disabled' : ''}>确认已合入并补全记录</button>` : ''}
      ${m.can_abandon ? `<button class="btn ghost" data-recovery="abandon" data-id="${esc(m.attempt_id)}" ${pendingActions.has('attempt:' + m.attempt_id) ? 'disabled' : ''}>放弃本次合入，保留候选</button>` : ''}
    </div></div>`);
  $('recoveryRows').innerHTML = executions.concat(merges).join('') || '<div class="empty">暂无待核对记录</div>';
}

async function reconcileExecution(id) {
  const row = state.recovery.executions.find(e => e.execution_id === id);
  if (!row || row.local_state === 'running') throw new Error('执行状态已变化或进程仍在运行，请刷新核对');
  const reason = $('recovery-reason-' + id).value.trim();
  const manual = row.manual_confirmation_allowed && $('recovery-confirm-' + id).checked;
  if (!reason || (!row.can_reconcile && !manual)) throw new Error('请填写核对说明，并明确确认本地执行已停止');
  const result = await runAction(`execution:${id}`, 'reconcile_execution', {
    executionId: id, generation: row.generation, reason, confirmLocalStopped: Boolean(manual),
  });
  if (result.status !== 'interrupted') throw new Error('收尾结果尚未确认，请刷新');
  showNotice('已记录中断；工作区和文件预约保留，检查改动后再决定下一步。');
  await loadAll();
}

async function reconcileMerge(id, action) {
  const row = state.recovery.merges.find(m => m.attempt_id === id);
  if (!row || (action === 'confirm' ? !row.can_confirm : !row.can_abandon)) throw new Error('合入状态已变化，请刷新核对');
  const result = await runAction(`attempt:${id}`, 'reconcile_merge', {
    attemptId: id, action, resultSha: row.git_result_sha,
    reason: action === 'abandon' ? '用户显式放弃准备结果，保留候选与工作区' : '',
  });
  if (result.status !== (action === 'confirm' ? 'succeeded' : 'failed')) throw new Error('合入恢复结果尚未确认，请刷新');
  showNotice(action === 'confirm' ? '已核对真实目标并补全合入记录。' : '已放弃本次合入；没有回滚代码，候选与工作区保留。');
  await loadAll();
}

$('recoveryRows').addEventListener('click', async event => {
  const button = event.target.closest('button[data-recovery]');
  if (!button || button.disabled) return;
  try {
    if (button.dataset.recovery === 'execution') await reconcileExecution(button.dataset.id);
    else await reconcileMerge(button.dataset.id, button.dataset.recovery);
  } catch (err) { try { await loadAll(); } catch (_) { } showError(err); }
});

async function startRun(taskId, button) {
  const check = checkInput.value.trim() || button.dataset.check || '';
  if (!check) {
    throw new Error('任务没有 checks，且未填写检查命令；候选会停在构建中');
  }
  button.disabled = true;
  const old = button.textContent;
  button.textContent = '运行中…';
  try {
    const result = await runAction(`run:${taskId}`, 'start_run', {
      taskId, agent: agentSelect.value, check,
    });
    if (result.ok) {
      const orText = result.or ? `，OR 选择 ${result.or.recommended.agent}` : '';
      showNotice(`Run ${result.execution_id} 完成：候选 ${result.candidate_id}（${result.candidate_status}）${orText}`);
    } else {
      showNotice(`运行失败：${result.error || '没有改动'}`);
    }
    await loadAll();
  } catch (err) {
    try { await loadAll(); } catch (_) { }
    showError(err);
  } finally {
    button.disabled = false;
    button.textContent = old;
  }
}

async function approveCandidate(candidateId) {
  const result = await runAction(`candidate:${candidateId}`, 'approve', {
    candidateId, approver: 'webview2-reviewer', note: '界面批准',
    role: approverRole.value,
  });
  if (result.pending_roles && result.pending_roles.length) {
    showNotice(`已记录 ${result.approver_role || approverRole.value}，等待 ${result.pending_roles.join(' → ')}`);
  } else {
    showNotice(`已批准：${result.candidate_id || candidateId}`);
  }
  await loadAll();
}

async function mergeCandidate(candidateId) {
  const repoPath = repoInput.value.trim();
  const worktreePath = worktreeInput.value.trim();
  if (!repoPath || !worktreePath) throw new Error('请先填写仓库和合入 Worktree 路径');
  const result = await runAction(`candidate:${candidateId}`, 'merge', {
    candidateId, repoPath, worktreePath, approver: 'webview2-reviewer',
    check: checkInput.value.trim(),
  });
  if (!result.merge_attempt || result.merge_attempt.status !== 'succeeded')
    throw new Error(result.merge_attempt?.error || '合入结果尚未确认，请刷新核对');
  showNotice(`已合入：${result.result_sha}`);
  await loadAll();
}

$('taskRows').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  if (button.dataset.action === 'cancel') {
    try { await cancelAction(button.dataset.key); } catch (err) { showError(err); }
    return;
  }
  try { await startRun(button.dataset.id, button); } catch (err) { showError(err); }
});

$('candidateCards').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  if (button.dataset.action === 'cancel') {
    try { await cancelAction(button.dataset.key); } catch (err) { showError(err); }
    return;
  }
  button.disabled = true;
  try {
    if (button.dataset.action === 'approve') await approveCandidate(button.dataset.id);
    if (button.dataset.action === 'merge') await mergeCandidate(button.dataset.id);
  } catch (err) {
    showError(err);
    const settings = document.querySelector('.settings');
    if (settings) settings.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } finally {
    button.disabled = false;
  }
});

async function refresh() {
  errorBox.textContent = '';
  try {
    await loadAll();
  } catch (err) {
    showError(err);
  }
}

$('refresh').addEventListener('click', refresh);
projectSelect.addEventListener('change', async () => {
  try {
    applyProjectRole();
    await loadAll();
  } catch (err) { showError(err); }
});
$('saveSettings').addEventListener('click', () => {
  localStorage.setItem('guild.repo', repoInput.value);
  localStorage.setItem('guild.worktree', worktreeInput.value);
  localStorage.setItem('guild.check', checkInput.value);
  renderCandidates();
  showNotice('设置已保存');
});

$('fillDemo').addEventListener('click', async () => {
  try {
    await fillDemoSettings(false);
  } catch (err) { showError(err); }
});

async function fillDemoSettings(silent) {
  const demo = await window.guild.rpc('demo_paths');
  if (!repoInput.value) repoInput.value = demo.repo || '';
  if (!worktreeInput.value) worktreeInput.value = demo.worktree || '';
  if (!checkInput.value) checkInput.value = demo.check || '';
  localStorage.setItem('guild.repo', repoInput.value);
  localStorage.setItem('guild.worktree', worktreeInput.value);
  localStorage.setItem('guild.check', checkInput.value);
  renderCandidates();
  if (!silent) showNotice('已填入 agent-demo 示例路径，可直接批准/合入');
}

repoInput.addEventListener('input', renderCandidates);
worktreeInput.addEventListener('input', renderCandidates);
repoInput.value = localStorage.getItem('guild.repo') || '';
worktreeInput.value = localStorage.getItem('guild.worktree') || '';
checkInput.value = localStorage.getItem('guild.check') || '';

(async () => {
  if (!repoInput.value && !worktreeInput.value && !checkInput.value) {
    try { await fillDemoSettings(true); } catch (err) { showError(err); }
  }
  await refresh();
})();
