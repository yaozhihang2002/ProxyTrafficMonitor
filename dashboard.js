'use strict';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[char]);
const stamp = time => time ? new Date(time * 1000).toLocaleString('zh-CN', {hour12:false}) : '—';
function bytes(value) {
    let amount = Number(value) || 0;
    let unit = 0;
    while (amount >= 1024 && unit < 4) {
        amount /= 1024;
        unit++;
    }
    return amount.toFixed(unit ? (amount >= 100 ? 1 : 2) : 0) + ' ' + ['B','KiB','MiB','GiB','TiB'][unit];
}

let paused = false;
let statusRequest;
let latestMeta;
let recentActivity;
let recentRatesValid = false;
let activePage;
let changingRange = false;
const pages = ['overview', 'usage', 'history', 'diagnostics'];
const trafficChart = new TrafficTrend($('trend'), bytes);

function navigate(focus = false) {
    const hash = location.hash.slice(1);
    if (hash === 'mainContent') return;
    const page = pages.includes(hash) ? hash : 'overview';
    if (page === activePage) return;
    const previous = activePage;
    activePage = page;
    for (const name of pages) $(name + 'Panel').hidden = name !== page;
    if (previous) document.dispatchEvent(new Event(previous + '-deactivate'));
    for (const link of document.querySelectorAll('.navigation a')) {
        if (link.hash === '#' + page) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
    }
    if (focus) {
        $(page + 'Title').focus({preventScroll:true});
        window.scrollTo(0, 0);
    }
    document.dispatchEvent(new Event(page + '-activate'));
    if (page === 'overview' && recentActivity) trafficChart.update(recentActivity.trend);
}

function setSidebar(collapsed) {
    $('appShell').classList.toggle('railCollapsed', collapsed);
    $('sidebarToggle').setAttribute('aria-expanded', String(!collapsed));
    $('sidebarToggle').textContent = collapsed ? '展开侧栏' : '收起侧栏';
    try { localStorage.setItem('proxy-monitor-sidebar-collapsed', String(collapsed)); } catch {}
}
try { setSidebar(localStorage.getItem('proxy-monitor-sidebar-collapsed') === 'true'); } catch {}
$('sidebarToggle').onclick = () => setSidebar(!$('appShell').classList.contains('railCollapsed'));

function showRecent() {
    if (!recentActivity) return;
    const rates = $('recentSort').value === 'rate' && recentRatesValid;
    const amount = item => rates ? item.rate_up + item.rate_down : item.up + item.down;
    const rows = [...recentActivity.programs].sort((a, b) => amount(b) - amount(a));
    $('programSummary').textContent = '近 5 分钟 · ' + rows.length + ' 个程序';
    const html = rows.slice(0, 50).map(row => `<tr>
        <td title="${esc(row.path || '路径未能识别')}"><span class="primary">${esc(row.process || '未知程序')}</span>
        <span class="secondary tableSub">${esc(row.parent || '父进程未知')}${row.binding !== 'socket' ? ' · 归属待核实' : ''}</span></td>
        <td class="number"><strong>${bytes(row.up + row.down)}</strong><span class="secondary tableSub">${bytes(row.up)} ↑ / ${bytes(row.down)} ↓</span></td>
        <td class="number">${recentRatesValid ? bytes(row.rate_up + row.rate_down) + '/s' : '—'}</td>
    </tr>`).join('');
    if ($('recentRows').dataset.signature !== html) {
        $('recentRows').innerHTML = html || '<tr><td colspan="3" class="empty">近 5 分钟暂无代理流量。</td></tr>';
        $('recentRows').dataset.signature = html;
    }
}
$('recentSort').onchange = showRecent;

function renderRange(meta, down, up) {
    const observing = Boolean(meta.view_active);
    $('rangeTitle').textContent = observing ? '从观察起点开始' : '全部累计';
    $('viewLabel').textContent = observing
        ? (meta.view_started ? '起点 ' + stamp(meta.view_started) : '等待首次有效采样')
        : '自 ' + stamp(meta.started) + ' 开始记录';
    $('totalLabel').textContent = observing ? '本次观察流量' : '累计代理流量';
    $('total').textContent = bytes(down + up);
    $('directions').textContent = '上传 ' + bytes(up) + ' / 下载 ' + bytes(down);
    $('startView').textContent = observing ? '重设观察起点' : '从现在开始';
    $('startView').disabled = changingRange;
    $('allView').disabled = changingRange || !observing;
}

function renderDiagnostics(data) {
    const m = data.meta;
    const format = amount => bytes(amount || 0);
    $('quality').textContent = `${m.samples || 0} 次采样，${m.gaps || 0} 次间断`;
    $('unknown').textContent = format(m.unknown_bytes);
    $('diagnostics').textContent = `接口采样 ${m.sample_ms || 0} ms，入库处理 ${data.pipeline?.processing_ms ?? '—'} ms，
        队列 ${data.pipeline?.queue_depth ?? '—'}，延迟 ${data.pipeline?.processing_lag?.toFixed(1) ?? '—'} 秒。
        未分类活动连接 ${m.unclassified || 0}。${m.error ? ' 错误：' + m.error : ''}`;
    $('events').innerHTML = data.events.map(event =>
        `<p>${esc(stamp(event.ts))} · ${esc(event.kind)} · ${esc(event.detail)}</p>`).join('') || '<p>暂无采集事件。</p>';
    const csv = data.csv;
    const size = data.storage;
    $('storageStatus').textContent = `${m.csv_blocked ? 'CSV 正在暂存，文件可写后将自动补写。' : ''}
        待写 ${csv.pending_records} 条（${format(csv.pending_bytes)}），已写 ${csv.files} 个分卷（${format(csv.bytes)}）。
        CSV 保留 ${data.settings.csv_days} 天 / ${data.settings.csv_budget_mb} MiB，最近写入 ${stamp(m.csv_last_write)}。
        数据库 ${format(size.database_bytes + size.wal_bytes + size.shm_bytes)}。CSV 目录 ${csv.folder}。`;
    $('alertSummary').textContent = data.settings.alert_enabled
        ? `大流量提醒已开启（${data.settings.alert_window} 分钟 / ${data.settings.alert_mb} MiB）` : '大流量提醒已关闭';
    $('alertList').innerHTML = (recentActivity?.alerts || []).map(event => {
        let detail;
        try { detail = JSON.parse(event.detail); } catch { detail = {program:event.detail}; }
        return `<p>${esc(stamp(event.ts))} · ${esc(detail.program)} · ${bytes(detail.bytes)}</p>`;
    }).join('') || '<p>暂无提醒记录。</p>';
}

function setLiveStatus(state, offline = false) {
    $('status').classList.toggle('offline', offline);
    $('status').classList.toggle('paused', paused);
    const label = paused ? '刷新已暂停，后台继续记录' : state;
    if ($('stateText').textContent !== label) $('stateText').textContent = label;
}

async function refreshStatus(force = false) {
    if (paused && !force) return;
    if (statusRequest && !force) return;
    statusRequest?.abort();
    const request = new AbortController();
    statusRequest = request;
    try {
        const response = await fetch('/api/status?details=0', {
            cache:'no-store', signal:AbortSignal.any([request.signal, AbortSignal.timeout(10000)])
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const data = await response.json();
        if (request.signal.aborted) return;
        const meta = data.meta;
        const live = meta.last_sample && data.now - meta.last_sample < Math.max(8, data.settings.interval * 2 + 5)
            && !meta.error && !data.runtime_error;
        latestMeta = meta;
        recentActivity = data.activity;
        recentRatesValid = Boolean(live && recentActivity.rate_valid && meta.rate_valid !== false);
        setLiveStatus(live ? '正在采集' : meta.error ? '采集异常，正在重连' : meta.last_sample ? '采样延迟' : '正在等待采样', !live);
        $('connectionHint').hidden = !data.diagnosis;
        if (data.diagnosis) $('connectionHint').textContent = data.diagnosis.title + '。' + data.diagnosis.help;
        $('updated').textContent = meta.last_sample ? '最近采样 ' + new Date(meta.last_sample * 1000).toLocaleTimeString('zh-CN',{hour12:false}) : '';
        $('versionLabel').textContent = 'v' + data.version;
        $('speed').textContent = recentRatesValid ? bytes(meta.rate_up + meta.rate_down) + '/s' : '—';
        $('speedDetail').textContent = recentRatesValid
            ? `↑ ${bytes(meta.rate_up)}/s / ↓ ${bytes(meta.rate_down)}/s` : '等待有效采样';
        $('active').textContent = live ? meta.active : '—';
        renderRange(meta, data.total_down, data.total_up);
        renderDiagnostics(data);
        showRecent();
        if (activePage === 'overview') trafficChart.update(recentActivity.trend);
    } catch {
        if (request.signal.aborted) return;
        setLiveStatus('与后台连接中断，正在重试', true);
        recentRatesValid = false;
        $('speed').textContent = $('active').textContent = '—';
        $('speedDetail').textContent = '实时读数暂不可用';
        showRecent();
    } finally {
        if (statusRequest === request) statusRequest = null;
    }
}

$('pauseView').onclick = () => {
    paused = !paused;
    $('pauseView').textContent = paused ? '恢复刷新' : '暂停刷新';
    $('pauseView').setAttribute('aria-pressed', String(paused));
    if (paused) {
        statusRequest?.abort();
        setLiveStatus('', $('status').classList.contains('offline'));
    } else {
        refreshStatus(true);
        document.dispatchEvent(new Event('usage-resume'));
    }
};

async function changeRange(action) {
    if (!latestMeta || changingRange) return;
    changingRange = true;
    $('startView').disabled = $('allView').disabled = true;
    $('rangeMessage').hidden = false;
    $('rangeMessage').textContent = '正在切换统计范围…';
    try {
        const response = await fetch('/api/view', {
            method:'POST', headers:{'Content-Type':'application/json','X-Local-Request':'1'},
            body:JSON.stringify({action}), signal:AbortSignal.timeout(10000)
        });
        if (!response.ok) throw new Error();
        $('day').value = '';
        if (action === 'all') $('search').value = '';
        await refreshStatus(true);
        document.dispatchEvent(new Event('history-refresh'));
        $('rangeMessage').hidden = true;
    } catch {
        $('rangeMessage').textContent = '切换失败，请检查后台连接后重试。';
    } finally {
        changingRange = false;
        $('startView').disabled = false;
        $('allView').disabled = !latestMeta.view_active;
    }
}
$('startView').onclick = () => changeRange('start');
$('allView').onclick = () => changeRange('all');

const fields = ['mode','endpoint','pipe','interval','connection_days','total_days','target_mb','csv_days','csv_budget_mb','alert_window','alert_mb'];
const settingsDialog = $('settingsDialog');
const settingsForm = $('settingsForm');
let settingsBusy = false;

async function openSettings() {
    if (settingsDialog.open || settingsBusy) return;
    settingsDialog.showModal();
    settingsForm.hidden = true;
    $('settingsLoad').textContent = '正在读取设置…';
    $('settingsLoad').hidden = false;
    try {
        const response = await fetch('/api/settings', {signal:AbortSignal.timeout(10000)});
        if (!response.ok) throw new Error();
        const config = await response.json();
        if (!settingsDialog.open) return;
        for (const id of fields) $(id).value = config[id];
        $('alert_enabled').checked = config.alert_enabled;
        $('secret').value = '';
        $('clear_secret').checked = false;
        $('secretState').textContent = config.has_secret ? '已保存 Secret。' : '尚未保存 Secret。';
        $('settingsResult').textContent = '';
        $('settingsLoad').hidden = true;
        settingsForm.hidden = false;
    } catch {
        $('settingsLoad').textContent = '设置读取失败，请关闭后重试。';
    }
}

async function submitSettings(test) {
    if (settingsBusy || !settingsForm.reportValidity()) return;
    const config = Object.fromEntries(fields.map(id => [id, $(id).value]));
    config.alert_enabled = $('alert_enabled').checked;
    config.secret = $('secret').value;
    config.clear_secret = $('clear_secret').checked;
    const controls = [...settingsForm.querySelectorAll('input, select, button')];
    settingsBusy = true;
    controls.forEach(element => { element.disabled = true; });
    $('settingsResult').textContent = test ? '正在测试连接…' : '正在保存…';
    try {
        const response = await fetch(test ? '/api/test-settings' : '/api/settings', {
            method:'POST', headers:{'Content-Type':'application/json','X-Local-Request':'1'},
            body:JSON.stringify(config), signal:AbortSignal.timeout(20000)
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || '操作失败');
        $('settingsResult').textContent = test
            ? `连接成功，发现 ${result.proxy_connections} 条代理连接。` : '已保存，将在下一次采样时生效。';
        if (!test) {
            $('secret').value = '';
            if (config.clear_secret) $('secretState').textContent = '尚未保存 Secret。';
            else if (config.secret) $('secretState').textContent = '已保存 Secret。';
            $('clear_secret').checked = false;
            await refreshStatus(true);
        }
    } catch (error) {
        $('settingsResult').textContent = error.name === 'TimeoutError' ? '请求超时，请核对本机连接设置。' : error.message;
    } finally {
        settingsBusy = false;
        controls.forEach(element => { element.disabled = false; });
    }
}
$('openSettings').onclick = $('alertSettings').onclick = openSettings;
$('closeSettings').onclick = () => settingsDialog.close();
$('testSettings').onclick = () => submitSettings(true);
settingsForm.onsubmit = event => { event.preventDefault(); submitSettings(false); };

document.addEventListener('DOMContentLoaded', () => {
    navigate();
    window.addEventListener('hashchange', () => navigate(true));
    refreshStatus();
    setInterval(() => refreshStatus(), 2000);
});
