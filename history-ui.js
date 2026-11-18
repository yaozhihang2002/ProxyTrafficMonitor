/* Historical queries are independent from the live sample refresh. */
(() => {
  const panel = $('historyPanel');
  const detail = $('detail');
  const table = $('rows');
  const search = $('search');
  const day = $('day');
  const group = $('group');
  const allRows = $('allRows');
  const rowLimit = $('rowLimit');
  const reload = $('historyReload');
  const message = $('historyMessage');
  const count = $('count');
  const groupLabels = {detail: '目标 / IP', process: '程序', host: '域名 / IP', node: '代理节点'};

  let result;
  let visibleRows = [];
  let historyRequest;
  let detailRequest;
  let searchTimer;

  function filters() {
    const params = new URLSearchParams();
    if (search.value.trim()) params.set('q', search.value.trim());
    if (day.value) params.set('day', day.value);
    return params;
  }

  function updateExport(params) {
    const query = params.toString();
    $('export').href = '/export.csv' + (query ? '?' + query : '');
    $('clearDay').disabled = !day.value;
  }

  function setMessage(text, state) {
    if (!message) return;
    message.hidden = !text;
    message.textContent = text;
    message.dataset.state = state;
  }

  function emptyRow(text) {
    visibleRows = [];
    table.innerHTML = `<tr><td colspan="8" class="empty">${esc(text)}</td></tr>`;
    count.textContent = '—';
  }

  function rowBytes(row) {
    return row.down + row.up;
  }

  function summarize(rows, mode) {
    if (mode === 'detail') return [...rows];
    const grouped = new Map();
    for (const row of rows) {
      const key = mode === 'process'
        ? JSON.stringify([row.path || row.process, row.parent_path || row.parent])
        : mode === 'host' ? row.host || row.ip : row.chains?.[0];
      if (!grouped.has(key)) grouped.set(key, {...row, down: 0, up: 0, count: 0});
      const summary = grouped.get(key);
      summary.down += row.down;
      summary.up += row.up;
      summary.count++;
      summary.last_seen = Math.max(summary.last_seen, row.last_seen);
    }
    return [...grouped.values()];
  }

  function renderHistory() {
    const mode = group.value;
    $('headTarget').textContent = groupLabels[mode];
    if (!result) return;
    const rows = summarize(result.rows, mode).sort((a, b) => rowBytes(b) - rowBytes(a));
    if (!rows.length) {
      emptyRow('此范围暂无匹配记录。可调整搜索条件或日期。');
      count.textContent = '0 项';
      return;
    }

    const limit = allRows.checked ? 2000 : rowLimit.checkValidity() ? Number(rowLimit.value) : 50;
    const biggest = rowBytes(rows[0]) || 1;
    const sum = rows.reduce((total, row) => total + rowBytes(row), 0);
    visibleRows = rows.slice(0, limit);
    count.textContent = `${rows.length} 项，显示 ${visibleRows.length} 项 · 明细合计 ${bytes(sum)}`
      + (result.truncated ? ' · 仅含流量最大的前 2000 条明细，可按日期或关键词缩小范围' : '');

    table.innerHTML = visibleRows.map((row, index) => {
      const target = mode === 'process' ? row.process || '未知程序'
        : mode === 'node' ? row.chains?.[0] || '未知节点' : row.host || row.ip || '未知目标';
      const targetDetail = mode === 'detail'
        ? [row.ip, row.port, row.network].filter(Boolean).join(' · ')
        : `${row.count} 条明细`;
      const program = mode === 'detail' || mode === 'process' ? row.process || '未知程序' : '—';
      const parent = mode === 'detail' || mode === 'process' ? row.parent || '父进程未知' : '';
      const node = mode === 'detail' || mode === 'node' ? row.chains?.[0] || '未知节点' : '—';
      const confidence = mode !== 'detail' ? '' : row.binding === 'socket'
        ? '<span class="tag">端口与进程匹配</span>' : '<span class="tag warning">归属待核实</span>';
      const action = mode === 'detail'
        ? `<button type="button" data-index="${index}" aria-label="查看 ${esc(target)} 的归属详情">详情</button>` : '';
      return `<tr>
        <td><div class="primary">${esc(target)}</div><div class="secondary">${esc(targetDetail)}</div></td>
        <td><div class="primary">${esc(program)}</div><div class="secondary">${esc(parent)}${mode === 'detail' ? ` · PID ${esc(row.pid || '—')}` : ''}</div>${confidence}</td>
        <td>${esc(node)}${mode === 'detail' ? `<div class="secondary">${esc(row.chains?.slice(1).join(' ← '))}</div>` : ''}</td>
        <td class="number">${bytes(row.up)}</td><td class="number">${bytes(row.down)}</td>
        <td class="number primary">${bytes(rowBytes(row))}<div class="smallbar" aria-hidden="true"><span style="width:${100 * rowBytes(row) / biggest}%"></span></div></td>
        <td class="secondary">${esc(stamp(row.last_seen))}</td><td>${action}</td>
      </tr>`;
    }).join('');
  }

  function cancelHistory() {
    clearTimeout(searchTimer);
    historyRequest?.abort();
    historyRequest = undefined;
    table.removeAttribute('aria-busy');
    reload?.removeAttribute('aria-disabled');
  }

  async function loadHistory({clear = false} = {}) {
    cancelHistory();
    const params = filters();
    updateExport(params);
    if (panel.hidden) return;

    params.set('details', '1');
    if (clear) result = undefined;
    if (!result) emptyRow('正在加载历史明细…');
    setMessage('正在加载历史明细…', 'loading');
    table.setAttribute('aria-busy', 'true');
    reload?.setAttribute('aria-disabled', 'true');
    const controller = new AbortController();
    historyRequest = controller;
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/status?' + params, {signal: controller.signal});
      if (!response.ok) throw new Error('历史明细请求失败');
      const data = await response.json();
      if (historyRequest !== controller || panel.hidden) return;
      if (!Array.isArray(data.rows)) throw new Error('历史明细响应无效');
      result = {rows: data.rows, truncated: data.truncated};
      renderHistory();
      setMessage('', 'ready');
    } catch {
      if (historyRequest !== controller) return;
      const error = controller.signal.aborted ? '历史查询超时，可缩小范围后重试。' : '历史明细暂时无法加载，请重试。';
      if (!result) emptyRow(error);
      setMessage(result ? error + ' 下方仍显示上次结果。' : error, 'error');
    } finally {
      clearTimeout(timer);
      if (historyRequest === controller) cancelHistory();
    }
  }

  function formatDetail(row) {
    const target = [row.host || row.ip || '未知目标', row.port].filter(Boolean).join(' · ');
    const values = [
      ['程序', row.process || '未知程序'], ['完整路径', row.path || '未能识别'],
      ['父进程', row.parent || '未能识别'], ['父进程路径', row.parent_path || '未能识别'],
      ['最近 PID', row.pid || '未能识别'], ['归属可信度', row.binding === 'socket' ? '端口与进程匹配' : '归属待核实'],
      ['首次记录', stamp(row.first_seen)], ['最近活动', stamp(row.last_seen)],
      ['上传 / 下载', bytes(row.up) + ' / ' + bytes(row.down)],
      ['目的地址', target], ['匹配规则', [row.rule, row.rule_payload].filter(Boolean).join(' ') || '未提供'],
    ];
    $('detailSummary').innerHTML = '<dl class="detailsGrid">'
      + values.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')
      + '</dl><p class="secondary">最近连接历史（最多显示 20 条，可能包含观察起点之前的记录）</p>'
      + '<div id="connectionHistory" role="status">正在读取连接历史…</div>';
  }

  async function loadConnectionHistory(row, output) {
    detailRequest?.abort();
    const controller = new AbortController();
    detailRequest = controller;
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const params = new URLSearchParams();
      for (const key of ['host', 'ip', 'path', 'parent', 'parent_path', 'process']) params.set(key, row[key] || '');
      const response = await fetch('/api/history?' + params, {signal: controller.signal});
      if (!response.ok) throw new Error('连接历史请求失败');
      const records = await response.json();
      if (detailRequest !== controller || !detail.open) return;
      if (!Array.isArray(records)) throw new Error('连接历史响应无效');
      output.innerHTML = records.slice(0, 20).map(record => {
        const line = `${stamp(record.first_seen)} — ${stamp(record.last_seen)} · PID ${record.pid || '未知'} · ${record.process || '未知程序'} / ${record.parent || '父进程未知'}`;
        return `<p class="secondary">${esc(line)}</p>`;
      }).join('') || '<p class="secondary">没有保留的连接记录。</p>';
      $('detailText').textContent = JSON.stringify({汇总: row, 最近连接历史: records}, null, 2);
    } catch {
      if (detailRequest !== controller || !detail.open) return;
      output.textContent = '暂时无法读取连接历史。';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = '重试';
      retry.addEventListener('click', () => {
        output.textContent = '正在读取连接历史…';
        loadConnectionHistory(row, output);
      });
      output.append(retry);
    } finally {
      clearTimeout(timer);
      if (detailRequest === controller) detailRequest = undefined;
    }
  }

  table.addEventListener('click', event => {
    const button = event.target.closest('[data-index]');
    const row = button && visibleRows[Number(button.dataset.index)];
    if (!row) return;
    formatDetail(row);
    $('detailText').textContent = JSON.stringify(row, null, 2);
    detail.querySelector('details')?.removeAttribute('open');
    if (!detail.open) detail.showModal();
    loadConnectionHistory(row, $('connectionHistory'));
  });

  $('close').addEventListener('click', () => detail.close());
  detail.addEventListener('close', () => {
    if (detail.open) return;
    detailRequest?.abort();
    detailRequest = undefined;
  });

  search.addEventListener('input', () => {
    cancelHistory();
    result = undefined;
    updateExport(filters());
    emptyRow('正在更新搜索结果…');
    setMessage('', 'loading');
    searchTimer = setTimeout(() => loadHistory(), 300);
  });
  day.addEventListener('change', () => loadHistory({clear: true}));
  $('clearDay').addEventListener('click', () => {
    day.value = '';
    day.focus();
    loadHistory({clear: true});
  });
  group.addEventListener('change', renderHistory);
  rowLimit.addEventListener('input', renderHistory);
  allRows.addEventListener('change', renderHistory);
  reload?.addEventListener('click', () => {
    if (!historyRequest) loadHistory();
  });
  document.addEventListener('history-activate', () => loadHistory({clear: true}));
  document.addEventListener('history-deactivate', cancelHistory);
  document.addEventListener('history-refresh', () => {
    result = undefined;
    loadHistory({clear: true});
  });
  updateExport(filters());
  if (!panel.hidden) loadHistory({clear: true});
})();
