(() => {
    const panel = $('usagePanel');
    const mode = $('usageMode');
    const month = $('usageDate');
    const year = $('usageYear');
    const bars = $('usageBars');
    const rows = $('usageRows');
    const readout = $('usageReadout');
    const ranking = $('usagePrograms');
    const rankingRows = $('usageProgramRows');
    const usageError = $('usageError');
    const overviewError = $('usageSummaryError');
    const numberPart = value => String(value).padStart(2, '0');
    const currentMonth = (now = new Date()) => `${now.getFullYear()}-${numberPart(now.getMonth() + 1)}`;
    const currentDay = (now = new Date()) => `${currentMonth(now)}-${numberPart(now.getDate())}`;
    const total = row => row.up + row.down;
    const periodLabel = period => period.length === 10
        ? `${period.slice(0, 4)} 年 ${Number(period.slice(5, 7))} 月 ${Number(period.slice(8, 10))} 日`
        : `${period.slice(0, 4)} 年 ${Number(period.slice(5, 7))} 月`;

    let periods = new Map();
    let selected = '';
    let hovered = '';
    let focused = '';
    let keyboardPeriod = '';
    let summaryAt = 0;
    let shownAt = 0;
    let shownKey = '';
    let pendingUsage;
    let pendingRanking;
    let followCurrent = true;
    let lastMonth;
    let lastYear;

    month.value = currentMonth();
    year.value = new Date().getFullYear();
    if (!month.min && year.min) month.min = `${year.min}-01`;
    ranking.hidden = true;

    async function request(url, controller, timeout) {
        const timer = setTimeout(() => controller.abort('timeout'), timeout);
        try {
            const response = await fetch(url, {signal: controller.signal});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } finally {
            clearTimeout(timer);
        }
    }

    function showError(element, message) {
        if (!element) return;
        element.hidden = !message;
        element.textContent = message;
    }

    function renderSummary(data) {
        for (const [name, row] of [['Today', data.today], ['Month', data.this_month]]) {
            const value = $('usage' + name);
            const split = $('usage' + name + 'Split');
            value.textContent = row.recorded ? bytes(total(row)) : '—';
            split.textContent = row.recorded ? `↑ ${bytes(row.up)}　↓ ${bytes(row.down)}` : '尚无采集记录';
            split.title = row.imported ? '含从旧明细回填的数据，可能不完整' : '';
        }
    }

    function setReadout() {
        const row = periods.get(hovered || focused || selected);
        let text = '';
        if (row) {
            const date = periodLabel(row.period);
            text = row.recorded
                ? `${date}　上传 ${bytes(row.up)}　下载 ${bytes(row.down)}　合计 ${bytes(total(row))}`
                : `${date}　无采集记录`;
        }
        if (readout.textContent !== text) readout.textContent = text;
    }

    // Move or create only changed nodes, so periodic updates preserve button focus.
    function reconcile(container, entries, create, update) {
        const existing = new Map([...container.children].map(node => [node.dataset.period, node]));
        let cursor = container.firstElementChild;
        for (const entry of entries) {
            const node = existing.get(entry.period) || create(entry);
            node.dataset.period = entry.period;
            if (node !== cursor) container.insertBefore(node, cursor);
            cursor = node.nextElementSibling;
            update(node, entry);
        }
        while (cursor) {
            const next = cursor.nextElementSibling;
            cursor.remove();
            cursor = next;
        }
    }

    function createBar() {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'usageBar';
        button.innerHTML = '<span class="usagePlot"><span class="usageStack"><span class="usageUpload"></span><span class="usageDownload"></span></span></span><span></span>';
        return button;
    }

    function updateBar(button, row, maximum) {
        const value = total(row);
        button.lastElementChild.textContent = Number(row.period.slice(-2));
        button.classList.toggle('is-empty', !row.recorded);
        button.classList.toggle('is-zero', row.recorded && value === 0);
        button.setAttribute('aria-label', `${periodLabel(row.period)}，${row.recorded ? `合计 ${bytes(value)}` : '无采集记录'}`);
        button.setAttribute('aria-pressed', String(selected === row.period));
        button.tabIndex = keyboardPeriod === row.period ? 0 : -1;
        const stack = button.querySelector('.usageStack');
        stack.style.height = value && maximum ? `${value / maximum * 100}%` : '0';
        stack.children[0].style.flexGrow = value ? row.up / value : 0;
        stack.children[1].style.flexGrow = value ? row.down / value : 0;
        stack.children[0].hidden = !row.up;
        stack.children[1].hidden = !row.down;
    }

    function createRow() {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td><button class="usagePeriodLink" type="button"></button><span hidden></span></td><td class="number"></td><td class="number"></td><td class="number primary"></td><td></td>';
        return tr;
    }

    function updateRow(tr, row) {
        const [label, up, down, sum, coverage] = tr.children;
        label.firstElementChild.textContent = row.period;
        label.lastElementChild.textContent = row.period;
        label.firstElementChild.hidden = !row.recorded || total(row) === 0;
        label.lastElementChild.hidden = !label.firstElementChild.hidden;
        up.textContent = row.recorded ? bytes(row.up) : '—';
        down.textContent = row.recorded ? bytes(row.down) : '—';
        sum.textContent = row.recorded ? bytes(total(row)) : '—';
        coverage.textContent = !row.recorded ? '无采集记录' : row.imported ? '含旧明细回填' : `采集约 ${(row.seconds / 3600).toFixed(1)} 小时`;
        coverage.title = row.imported ? '只回填仍有明细的流量，统计可能不完整' : '';
    }

    function clearRanking() {
        selected = '';
        if (pendingRanking) pendingRanking.abort();
        pendingRanking = undefined;
        ranking.hidden = true;
    }

    function renderUsage(data) {
        const limit = mode.value === 'days' ? currentDay() : currentMonth();
        const entries = data[mode.value].filter(row => row.period <= limit);
        periods = new Map(entries.map(row => [row.period, row]));
        if (!periods.has(selected)) clearRanking();
        if (!periods.has(hovered)) hovered = '';
        if (!periods.has(focused)) focused = '';
        if (!periods.has(keyboardPeriod)) keyboardPeriod = entries.at(-1)?.period || '';

        const recorded = entries.filter(row => row.recorded);
        const up = recorded.reduce((sum, row) => sum + row.up, 0);
        const down = recorded.reduce((sum, row) => sum + row.down, 0);
        $('usagePeriodTotal').hidden = false;
        $('usagePeriodLabel').textContent = (mode.value === 'days' ? periodLabel(data.month) : data.year + ' 年') + '已记录';
        $('usagePeriodValue').textContent = recorded.length ? bytes(up + down) : '—';
        $('usagePeriodSplit').textContent = recorded.length
            ? '上传 ' + bytes(up) + ' / 下载 ' + bytes(down) + (recorded.some(row => row.imported) ? ' · 含旧明细回填' : '')
            : '所选时段无采集记录';
        const maximum = Math.max(0, ...entries.map(total));
        $('usageScale').textContent = maximum ? `0 至 ${bytes(maximum)}` : entries.some(entry => entry.recorded) ? '所选时段无流量增量' : '所选时段无采集记录';
        reconcile(bars, entries, createBar, (button, row) => updateBar(button, row, maximum));
        reconcile(rows, [...entries].reverse(), createRow, updateRow);
        setReadout();
    }

    function setKeyboardPeriod(target) {
        for (const button of bars.children) button.tabIndex = button === target ? 0 : -1;
        keyboardPeriod = target.dataset.period;
    }

    function rankingState(message, retry = false) {
        rankingRows.innerHTML = `<tr><td colspan="4" class="empty">${esc(message)}${retry ? ' <button type="button" data-retry-ranking>重试</button>' : ''}</td></tr>`;
    }

    async function selectPeriod(period, fromTable = false) {
        const row = periods.get(period);
        if (!row) return;
        pendingRanking?.abort();
        pendingRanking = undefined;
        selected = period;
        for (const button of bars.children) button.setAttribute('aria-pressed', String(button.dataset.period === period));
        setReadout();
        $('usageProgramTitle').textContent = `${periodLabel(period)} 程序排行`;
        ranking.hidden = false;
        if (fromTable) {
            ranking.scrollIntoView({block: 'nearest'});
            $('usageProgramTitle').focus({preventScroll: true});
        }
        if (!row.recorded || total(row) === 0) {
            rankingState(row.recorded ? '该时段未记录到代理流量' : '该时段无采集记录');
            return;
        }
        rankingState('正在读取程序排行…');
        const controller = new AbortController();
        pendingRanking = controller;
        try {
            const query = new URLSearchParams({period});
            const result = await request('/api/usage-programs?' + query, controller, 15000);
            if (pendingRanking !== controller) return;
            if (!result.length) rankingState('该时段无保留的连接明细');
            else rankingRows.innerHTML = result.map(item => `<tr><td>${esc(item.process || '未知程序')}</td><td class="number">${bytes(item.up)}</td><td class="number">${bytes(item.down)}</td><td class="number primary">${bytes(total(item))}</td></tr>`).join('');
        } catch {
            if (pendingRanking === controller) rankingState('读取排行失败，可重试。', true);
        } finally {
            if (pendingRanking === controller) pendingRanking = undefined;
        }
    }

    function updateControls() {
        const daily = mode.value === 'days';
        const now = new Date();
        (month.closest('label') || month).hidden = !daily;
        (year.closest('label') || year).hidden = daily;
        month.max = currentMonth(now);
        year.max = now.getFullYear();
        lastMonth = month.value;
        lastYear = year.value;
        $('usagePrev').textContent = daily ? '上一月' : '上一年';
        $('usageNext').textContent = daily ? '下一月' : '下一年';
        $('usagePrev').disabled = daily ? month.value <= month.min : Number(year.value) <= Number(year.min || 1);
        $('usageNext').disabled = daily ? month.value >= month.max : Number(year.value) >= Number(year.max);
        $('usageCurrent').textContent = daily ? '回到本月' : '回到今年';
        $('usageCurrent').disabled = $('usageNext').disabled;
    }

    function shiftPeriod(by) {
        if (mode.value === 'days') {
            const [y, m] = month.value.split('-').map(Number);
            const next = new Date(0);
            next.setFullYear(y, m - 1 + by, 1);
            month.value = currentMonth(next);
            year.value = next.getFullYear();
        } else {
            year.value = Number(year.value) + by;
            syncMonthToYear();
        }
        followCurrent = false;
        changePeriod();
    }

    function resetPeriod() {
        month.value = currentMonth();
        year.value = new Date().getFullYear();
        followCurrent = true;
    }

    function syncMonthToYear() {
        const candidate = `${String(year.value).padStart(4, '0')}-${month.value.slice(5, 7) || '01'}`;
        month.value = candidate > currentMonth() ? currentMonth() : candidate;
    }

    function queryMonth() {
        if (panel.hidden) return currentMonth();
        return mode.value === 'days' ? month.value : `${String(year.value).padStart(4, '0')}-01`;
    }

    function viewKey() {
        return `${mode.value}-${queryMonth()}`;
    }

    function changePeriod() {
        clearRanking();
        hovered = focused = keyboardPeriod = '';
        periods.clear();
        bars.replaceChildren();
        rows.innerHTML = '<tr><td colspan="5" class="empty">正在读取用量…</td></tr>';
        $('usagePeriodTotal').hidden = true;
        $('usageScale').textContent = '';
        readout.textContent = '正在读取所选时段';
        shownAt = 0;
        updateControls();
        refreshUsage(true);
    }

    async function refreshUsage(force = false) {
        if (!force && (paused || document.hidden)) return;
        if (followCurrent && month.value !== currentMonth()) {
            resetPeriod();
            updateControls();
        }
        const query = queryMonth();
        const key = viewKey();
        const now = Date.now();
        if (!force && now - summaryAt < 30000 && (panel.hidden || (shownKey === key && now - shownAt < 30000))) return;
        if (pendingUsage?.query === query) {
            pendingUsage.force ||= force;
            return;
        }
        pendingUsage?.controller.abort();
        const controller = new AbortController();
        pendingUsage = {controller, query, force};
        try {
            const data = await request('/api/usage?' + new URLSearchParams({month: query}), controller, 10000);
            if (pendingUsage?.controller !== controller) return;
            if (!paused || !summaryAt) {
                renderSummary(data);
                summaryAt = Date.now();
                showError(overviewError, '');
            }
            if (!panel.hidden && queryMonth() === query && (!paused || pendingUsage.force)) {
                renderUsage(data);
                shownKey = viewKey();
                shownAt = Date.now();
                showError(usageError, '');
            }
        } catch {
            if (pendingUsage?.controller !== controller) return;
            showError(overviewError, summaryAt ? '用量刷新失败，仍显示上次记录。' : '暂时无法读取用量。');
            if (!panel.hidden && queryMonth() === query) {
                const retryHint = paused ? '请重新选择时段或恢复刷新。' : '稍后自动重试。';
                const oldDataHint = periods.size ? '仍显示上次记录。' : '';
                showError(usageError, `用量读取失败。${oldDataHint}${retryHint}`);
                if (!periods.size) {
                    rows.innerHTML = '<tr><td colspan="5" class="empty">暂时无法读取用量</td></tr>';
                    readout.textContent = '—';
                }
            }
        } finally {
            if (pendingUsage?.controller === controller) pendingUsage = undefined;
        }
    }

    bars.addEventListener('pointerover', event => {
        const button = event.target.closest('.usageBar');
        if (!button || event.pointerType === 'touch') return;
        hovered = button.dataset.period;
        setReadout();
    });
    bars.addEventListener('pointerleave', () => {
        hovered = '';
        setReadout();
    });
    bars.addEventListener('focusin', event => {
        const button = event.target.closest('.usageBar');
        if (!button) return;
        hovered = '';
        focused = button.dataset.period;
        setKeyboardPeriod(button);
        setReadout();
    });
    bars.addEventListener('focusout', () => {
        focused = '';
        setReadout();
    });
    bars.addEventListener('keydown', event => {
        const button = event.target.closest('.usageBar');
        if (!button) return;
        const target = {
            ArrowLeft: button.previousElementSibling,
            ArrowRight: button.nextElementSibling,
            Home: bars.firstElementChild,
            End: bars.lastElementChild,
        }[event.key];
        if (!target) return;
        event.preventDefault();
        target.focus();
    });
    bars.addEventListener('click', event => {
        const button = event.target.closest('.usageBar');
        if (button) selectPeriod(button.dataset.period);
    });
    rows.addEventListener('click', event => {
        const link = event.target.closest('.usagePeriodLink');
        if (link) selectPeriod(link.closest('tr').dataset.period, true);
    });
    rankingRows.addEventListener('click', event => {
        if (event.target.closest('[data-retry-ranking]') && selected) selectPeriod(selected);
    });

    month.addEventListener('change', () => {
        if (!month.value || !month.reportValidity()) {
            month.value = lastMonth;
            return;
        }
        year.value = Number(month.value.slice(0, 4));
        followCurrent = month.value === currentMonth();
        changePeriod();
    });
    year.addEventListener('change', () => {
        if (!year.value || !year.reportValidity()) {
            year.value = lastYear;
            return;
        }
        syncMonthToYear();
        followCurrent = month.value === currentMonth();
        changePeriod();
    });
    mode.addEventListener('change', changePeriod);
    $('usagePrev').addEventListener('click', () => shiftPeriod(-1));
    $('usageNext').addEventListener('click', () => shiftPeriod(1));
    $('usageCurrent').addEventListener('click', () => {
        resetPeriod();
        changePeriod();
    });
    document.addEventListener('usage-resume', () => refreshUsage(true));
    document.addEventListener('usage-activate', () => {
        updateControls();
        if (shownKey !== viewKey() || Date.now() - shownAt >= 30000) refreshUsage(true);
    });
    document.addEventListener('usage-deactivate', () => {
        hovered = focused = '';
        setReadout();
    });
    updateControls();
    refreshUsage(true);
    setInterval(refreshUsage, 5000);
})();
