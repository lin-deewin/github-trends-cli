const state = { period: '7d', search: '', category: '', language: '', rows: [], selected: null, refreshPoll: null, schedulerPoll: null, lastRefreshFinishedAt: null };
const $ = (id) => document.getElementById(id);
const number = (value) => Number(value || 0).toLocaleString('en-US');
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const periodNames = {'3d':'最近 3 天','7d':'最近 7 天','1m':'最近 1 个月','3m':'最近 3 个月'};
let searchTimer;

function initials(name) { return name.split('/').map(x => x[0] || '').join('').slice(0, 2).toUpperCase(); }
function sparkline(values) {
  if (!Array.isArray(values) || values.length !== 7 || values.some(v => v == null)) return '<span class="language-cell">—</span>';
  const max = Math.max(1, ...values), min = Math.min(0, ...values), range = Math.max(1, max - min);
  const points = values.map((v, i) => `${(i / 6) * 78 + 2},${23 - ((v - min) / range) * 19}`).join(' ');
  return `<svg class="sparkline" viewBox="0 0 82 27" aria-label="最近七天每日新增 Star"><polyline points="${points}" fill="none" stroke="#9a90e9" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

async function loadDashboard() {
  const params = new URLSearchParams({period:state.period, q:state.search, category:state.category, language:state.language, limit:'100'});
  $('repo-table').innerHTML = '<tr><td colspan="8" class="empty-cell"><span class="loader"></span>正在读取数据…</td></tr>';
  try {
    const response = await fetch(`/api/dashboard?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '读取失败');
    state.rows = data.results || [];
    renderDashboard(data);
  } catch (error) {
    $('repo-table').innerHTML = `<tr><td colspan="8" class="empty-cell">${esc(error.message)}。请确认 web.py 正常运行。</td></tr>`;
    $('connection-label').textContent = '数据连接异常';
  }
}

function renderDashboard(data) {
  const s = data.summary;
  $('metric-repos').textContent = number(s.monitored_repositories);
  $('metric-growth').textContent = number(s.period_growth);
  $('metric-daily').textContent = number(s.daily_average);
  $('metric-ranked').textContent = number(s.ranked_repositories);
  $('metric-period-label').textContent = periodNames[state.period];
  $('metric-missing').textContent = `另有 ${number(s.missing_repositories)} 个项目历史数据不足`;
  $('report-date').textContent = data.data_through ? new Date(`${data.data_through}T00:00:00Z`).toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',timeZone:'UTC'}) : '—';
  $('last-updated').textContent = s.latest_update ? `更新于 ${new Date(s.latest_update).toLocaleString('zh-CN',{hour12:false})}` : '尚未采集';
  $('result-count').textContent = `${number(data.results.length)} 个项目`;
  $('data-coverage').textContent = `可排名 ${number(s.ranked_repositories)} / 监控 ${number(s.monitored_repositories)}`;
  fillSelect('category-filter', data.categories || [], '全部分类');
  fillSelect('language-filter', data.languages || [], '全部语言');
  renderRows(data.results || []);
  $('connection-label').textContent = '本地数据已连接';
}

function fillSelect(id, options, label) {
  const select = $(id), current = select.value;
  const values = [`<option value="">${label}</option>`, ...options.map(v => `<option value="${esc(v)}">${esc(v)}</option>`)];
  select.innerHTML = values.join('');
  if (options.includes(current)) select.value = current;
}

function renderRows(rows) {
  if (!rows.length) {
    $('repo-table').innerHTML = `<tr><td colspan="8" class="empty-cell">当前周期暂无完整历史数据。先点击右上角刷新按钮采集项目。</td></tr>`;
    return;
  }
  $('repo-table').innerHTML = rows.map((row, index) => `
    <tr data-repo="${esc(row.repo)}" tabindex="0" aria-label="查看 ${esc(row.repo)} 详情">
      <td class="rank-col"><span class="rank-number ${index < 3 ? 'top-three' : ''}">${String(index + 1).padStart(2,'0')}</span></td>
      <td><div class="project-cell"><span class="repo-avatar">${esc(initials(row.repo))}</span><div class="project-main"><div class="project-name">${esc(row.repo)}</div><div class="project-desc">${esc(row.description || '暂无项目简介')}</div></div></div></td>
      <td><span class="category-pill" title="${esc(row.category)}">${esc(row.category)}</span></td>
      <td class="language-cell"><i class="language-dot"></i>${esc(row.language)}</td>
      <td class="star-number">${number(row.total_stars)}</td>
      <td class="growth-number">+${number(row.gain)}</td>
      <td>${sparkline(row.sparkline)}</td>
      <td class="row-arrow">›</td>
    </tr>`).join('');
  $('repo-table').querySelectorAll('tr[data-repo]').forEach(row => {
    row.addEventListener('click', () => openDetail(row.dataset.repo));
    row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(row.dataset.repo); } });
  });
}

function openDrawer() {
  $('detail-drawer').classList.add('open'); $('drawer-backdrop').classList.add('open');
  $('detail-drawer').setAttribute('aria-hidden','false'); document.body.style.overflow = 'hidden';
}
function closeDrawer() {
  $('detail-drawer').classList.remove('open'); $('drawer-backdrop').classList.remove('open');
  $('detail-drawer').setAttribute('aria-hidden','true'); document.body.style.overflow = '';
}

async function openDetail(name) {
  state.selected = name; openDrawer();
  $('detail-title').textContent = name; $('detail-avatar').textContent = initials(name);
  $('growth-chart').innerHTML = '<div class="chart-loading"><span class="loader"></span>正在加载增长曲线…</div>';
  try {
    const response = await fetch(`/api/repo?${new URLSearchParams({name,period:state.period})}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '无法加载项目详情');
    if (state.selected !== name) return;
    renderDetail(data);
  } catch (error) {
    $('growth-chart').innerHTML = `<div class="chart-loading">${esc(error.message)}</div>`;
  }
}

function renderDetail(data) {
  $('detail-title').textContent = data.repo;
  $('detail-avatar').textContent = initials(data.repo);
  $('detail-url').href = data.url; $('detail-url').textContent = '打开 GitHub 仓库 ↗';
  $('detail-description').textContent = data.description || '项目维护者尚未填写简介。';
  $('detail-tags').innerHTML = `<span>${esc(data.category)}</span><span>${esc(data.language)}</span>`;
  $('detail-stars').textContent = number(data.total_stars);
  $('detail-growth').textContent = data.complete ? `+${number(data.growth)}` : '数据不足';
  $('detail-average').textContent = data.daily_average == null ? '—' : `${number(data.daily_average)} / 天`;
  $('detail-category').textContent = data.category;
  $('detail-language').textContent = data.language;
  $('detail-forks').textContent = number(data.forks);
  $('detail-created').textContent = data.created_at ? new Date(data.created_at).toLocaleDateString('zh-CN',{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}) : '—';
  $('detail-topics').innerHTML = (data.topics || []).length ? data.topics.map(t => `<span>${esc(t)}</span>`).join('') : '<span>暂无 Topics</span>';
  $('chart-caption').textContent = `${periodNames[data.period]} · 每日新增 Star 与 ${data.moving_average_window} 日移动平均`;
  $('chart-start').textContent = data.window_start;
  $('chart-end').textContent = data.window_end;
  $('chart-note').textContent = data.complete ? `该周期累计新增 ${number(data.growth)} Star，日均 ${number(data.daily_average)} Star。${data.best_day ? `最高单日为 ${data.best_day.date}（${number(data.best_day.stars)} Star）。` : ''}` : '该项目的历史数据尚未覆盖完整周期，图表仅展示已采集的数据，不会将缺失日期当作 0。';
  drawGrowthChart(data.series || []);
}

function drawGrowthChart(series) {
  const host = $('growth-chart');
  const W = 480, H = 190, left = 35, right = 8, top = 10, bottom = 27;
  const plotW = W - left - right, plotH = H - top - bottom;
  const values = series.map(x => x.stars).filter(x => x != null);
  if (!values.length) { host.innerHTML = '<div class="chart-loading">没有可展示的历史数据</div>'; return; }
  const max = Math.max(1, ...values), n = series.length, slot = plotW / Math.max(1,n), barW = Math.max(2,Math.min(13,slot*.55));
  const y = v => top + plotH - (v / max) * plotH;
  const x = i => left + i * slot + slot / 2;
  let grid = '', bars = '', lineSegments = [], currentSegment = [];
  for (let i=0;i<=4;i++) {
    const v=max*i/4, yy=y(v);
    grid += `<line x1="${left}" y1="${yy}" x2="${W-right}" y2="${yy}" stroke="#edf0f5" stroke-dasharray="3 4"/><text x="${left-8}" y="${yy+3}" text-anchor="end" fill="#a3a9b6" font-size="9">${Math.round(v)}</text>`;
  }
  series.forEach((point,i) => {
    if (point.stars != null) {
      const yy=y(point.stars), height=Math.max(1,top+plotH-yy);
      bars += `<rect x="${x(i)-barW/2}" y="${yy}" width="${barW}" height="${height}" rx="2" fill="#c9c3f5"><title>${point.date}: ${point.stars} new Stars</title></rect>`;
    }
    if (point.moving_average != null) {
      currentSegment.push(`${x(i)},${y(point.moving_average)}`);
    } else if (currentSegment.length) {
      lineSegments.push(currentSegment); currentSegment = [];
    }
  });
  if (currentSegment.length) lineSegments.push(currentSegment);
  const line = lineSegments.map(segment => `<polyline points="${segment.join(' ')}" fill="none" stroke="#7165dc" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/>`).join('');
  const labels = [0,Math.floor((n-1)/2),n-1].filter((v,i,a)=>a.indexOf(v)===i).map(i=>`<text x="${x(i)}" y="${H-7}" text-anchor="middle" fill="#a3a9b6" font-size="9">${series[i].date.slice(5)}</text>`).join('');
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="每日新增 Star 增长图表">${grid}${bars}${line}${labels}</svg>`;
}

function showToast(message) { const toast=$('toast'); toast.textContent=message; toast.classList.add('show'); setTimeout(()=>toast.classList.remove('show'),2600); }
async function triggerRefresh() {
  const button=$('refresh-button'); button.classList.add('is-loading'); button.disabled=true;
  try {
    const response=await fetch('/api/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({limit:30})});
    const data=await response.json();
    if (!response.ok) throw new Error(data.error || '启动采集失败');
    showToast('已启动采集，完成后会自动更新排行榜'); pollRefresh();
  } catch(error) { showToast(error.message); button.classList.remove('is-loading'); button.disabled=false; }
}
async function pollRefresh() {
  clearInterval(state.refreshPoll);
  state.refreshPoll=setInterval(async()=>{
    try {
      const response=await fetch('/api/refresh-status'); const data=await response.json();
      if (!data.running) {
        clearInterval(state.refreshPoll); $('refresh-button').classList.remove('is-loading'); $('refresh-button').disabled=false;
        if (data.returncode===0) { showToast('采集完成，排行榜已更新'); await loadDashboard(); if (state.selected) await openDetail(state.selected); }
        else showToast('采集结束，请检查终端日志或 Token 配置');
      }
    } catch(_) {}
  },1800);
}


async function loadSchedulerStatus() {
  try {
    const response = await fetch('/api/scheduler-status');
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '定时任务状态读取失败');
    const scheduler = payload.scheduler || {};
    const refresh = payload.refresh || {};
    const label = $('scheduler-label');
    const next = scheduler.next_run_at ? new Date(scheduler.next_run_at) : null;
    const nextText = next && !Number.isNaN(next.getTime())
      ? next.toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit', hour12:false})
      : '待安排';

    if (!scheduler.enabled) label.textContent = '自动采集未启动';
    else if (refresh.running) label.textContent = refresh.trigger === 'scheduled' ? '正在定时采集' : '正在手动采集';
    else label.textContent = `每 ${scheduler.interval_hours || 2} 小时自动采集 · 下次 ${nextText}`;

    const last = scheduler.last_finished_at
      ? new Date(scheduler.last_finished_at).toLocaleString('zh-CN', {hour12:false})
      : '尚无完成记录';
    const outcome = scheduler.last_outcome === 'success' ? '成功'
      : scheduler.last_outcome === 'failed' ? '失败'
      : scheduler.last_outcome === 'skipped_busy' ? '因已有任务运行而跳过'
      : '等待首次运行';
    label.title = `定时任务：${scheduler.enabled ? '已启用' : '未启动'}；间隔：${scheduler.interval_hours || 2} 小时；上次结果：${outcome}；上次完成：${last}`;

    if (refresh.trigger === 'scheduled' && !refresh.running && refresh.finished_at
        && refresh.finished_at !== state.lastRefreshFinishedAt && refresh.returncode === 0) {
      await loadDashboard();
      if (state.selected) await openDetail(state.selected);
      showToast(refresh.trigger === 'scheduled' ? '定时采集完成，排行榜已更新' : '采集完成，排行榜已更新');
    }
    if (refresh.finished_at) state.lastRefreshFinishedAt = refresh.finished_at;
  } catch (_) {
    $('scheduler-label').textContent = '定时任务状态未知';
  }
}

function bindEvents() {
  document.querySelectorAll('.period-button').forEach(button=>button.addEventListener('click',async()=>{
    if (state.period===button.dataset.period) return;
    state.period=button.dataset.period;
    document.querySelectorAll('.period-button').forEach(b=>b.classList.toggle('active',b===button));
    await loadDashboard(); if (state.selected) await openDetail(state.selected);
  }));
  $('search-input').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.search=$('search-input').value;loadDashboard();},220);});
  $('category-filter').addEventListener('change',()=>{state.category=$('category-filter').value;loadDashboard();});
  $('language-filter').addEventListener('change',()=>{state.language=$('language-filter').value;loadDashboard();});
  $('refresh-button').addEventListener('click',triggerRefresh);
  $('close-drawer').addEventListener('click',closeDrawer); $('drawer-backdrop').addEventListener('click',closeDrawer);
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});
}

bindEvents();
loadDashboard();
loadSchedulerStatus();
state.schedulerPoll = setInterval(loadSchedulerStatus, 15000);
