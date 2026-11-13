const state = { period: '7d', search: '', category: '', language: '', selected: null, data: null };
const $ = (id) => document.getElementById(id);
const number = (value) => Number(value || 0).toLocaleString('en-US');
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const periodNames = {'3d':'最近 3 天','7d':'最近 7 天','1m':'最近 1 个月','3m':'最近 3 个月'};
const ACTIONS_URL = 'https://github.com/lin-deewin/github-trends-cli/actions/workflows/daily.yml';
let searchTimer;
let previousFocus = null;

function initials(name) { return name.split('/').map(x => x[0] || '').join('').slice(0, 2).toUpperCase(); }

function sparkline(values) {
  if (!Array.isArray(values) || values.length !== 7 || values.some(v => v == null)) return '<span class="language-cell">—</span>';
  const max = Math.max(1, ...values), min = Math.min(0, ...values), range = Math.max(1, max - min);
  const points = values.map((v, i) => ((i / 6) * 78 + 2) + ',' + (23 - ((v - min) / range) * 19)).join(' ');
  return '<svg class="sparkline" viewBox="0 0 82 27" role="img" aria-label="最近七天每日新增 Star"><polyline points="' + points + '" fill="none" stroke="#8f87dc" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
}

function loadDashboard() {
  if (!state.data) return;
  const source = state.data.dashboards[state.period];
  if (!source) {
    $('repo-table').innerHTML = '<tr><td colspan="8" class="empty-cell">当前周期的数据不存在。</td></tr>';
    return;
  }
  const q = state.search.trim().toLocaleLowerCase();
  const results = (source.results || []).filter(row => {
    if (state.category && row.category !== state.category) return false;
    if (state.language && row.language.toLocaleLowerCase() !== state.language.toLocaleLowerCase()) return false;
    if (q) {
      const haystack = [row.repo, row.description, row.category, row.language, ...(row.topics || [])].join(' ').toLocaleLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
  state.rows = results;
  renderDashboard({ ...source, results });
}

function renderDashboard(data) {
  const s = data.summary;
  $('metric-repos').textContent = number(s.monitored_repositories);
  $('metric-growth').textContent = number(s.period_growth);
  $('metric-daily').textContent = number(s.daily_average);
  $('metric-ranked').textContent = number(s.ranked_repositories);
  $('metric-period-label').textContent = periodNames[state.period];
  $('metric-missing').textContent = '另有 ' + number(s.missing_repositories) + ' 个项目历史数据不足';
  $('report-date').textContent = data.data_through
    ? new Date(data.data_through + 'T00:00:00Z').toLocaleDateString('zh-CN', {year:'numeric',month:'long',day:'numeric',timeZone:'UTC'})
    : '—';
  $('last-updated').textContent = state.data.generated_at
    ? '数据更新于 ' + new Date(state.data.generated_at).toLocaleString('zh-CN', {hour12:false,timeZone:'Asia/Shanghai'})
    : '等待数据';
  $('result-count').textContent = number(data.results.length) + ' 个项目';
  $('data-coverage').textContent = '可排名 ' + number(s.ranked_repositories) + ' / 监控 ' + number(s.monitored_repositories);
  fillSelect('category-filter', data.categories || [], '全部分类');
  fillSelect('language-filter', data.languages || [], '全部语言');
  renderRows(data.results || []);
  $('connection-label').textContent = 'GitHub Actions 数据已连接';
}

function fillSelect(id, options, label) {
  const select = $(id), current = select.value;
  const values = ['<option value="">' + label + '</option>', ...options.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>')];
  select.innerHTML = values.join('');
  if (options.includes(current)) select.value = current;
  else {
    select.value = '';
    if (id === 'category-filter') state.category = '';
    if (id === 'language-filter') state.language = '';
  }
}

function renderRows(rows) {
  if (!rows.length) {
    $('repo-table').innerHTML = '<tr><td colspan="8" class="empty-cell">当前周期暂无完整历史数据。下一轮 GitHub Actions 采集后会自动更新。</td></tr>';
    return;
  }
  $('repo-table').innerHTML = rows.map((row, index) =>
    '<tr data-repo="' + esc(row.repo) + '" tabindex="0" aria-label="查看 ' + esc(row.repo) + ' 详情">' +
      '<td class="rank-col"><span class="rank-number ' + (index < 3 ? 'top-three' : '') + '">' + String(index + 1).padStart(2, '0') + '</span></td>' +
      '<td><div class="project-cell"><span class="repo-avatar">' + esc(initials(row.repo)) + '</span><div class="project-main"><div class="project-name">' + esc(row.repo) + '</div><div class="project-desc">' + esc(row.description || '暂无项目简介') + '</div></div></div></td>' +
      '<td><span class="category-pill" title="' + esc(row.category) + '">' + esc(row.category) + '</span></td>' +
      '<td class="language-cell"><i class="language-dot"></i>' + esc(row.language) + '</td>' +
      '<td class="star-number">' + number(row.total_stars) + '</td>' +
      '<td class="growth-number">+' + number(row.gain) + '</td>' +
      '<td>' + sparkline(row.sparkline) + '</td><td><svg class="row-arrow" viewBox="0 0 20 20" aria-hidden="true"><path d="m7 4 6 6-6 6"/></svg></td></tr>'
  ).join('');
  $('repo-table').querySelectorAll('tr[data-repo]').forEach(row => {
    row.addEventListener('click', () => openDetail(row.dataset.repo));
    row.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(row.dataset.repo); }
    });
  });
}

function openDrawer() {
  previousFocus = document.activeElement;
  $('detail-drawer').classList.add('open');
  $('drawer-backdrop').classList.add('open');
  $('detail-drawer').setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  $('close-drawer').focus();
}
function closeDrawer() {
  $('detail-drawer').classList.remove('open');
  $('drawer-backdrop').classList.remove('open');
  $('detail-drawer').setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
  const target = previousFocus;
  previousFocus = null;
  if (target && typeof target.focus === 'function') target.focus();
}

function openDetail(name) {
  state.selected = name;
  openDrawer();
  $('detail-title').textContent = name;
  $('detail-avatar').textContent = initials(name);
  $('growth-chart').innerHTML = '<div class="chart-loading">正在加载增长曲线…</div>';
  const periods = state.data && state.data.details && state.data.details[name];
  const data = periods && periods[state.period];
  if (!data) {
    $('growth-chart').innerHTML = '<div class="chart-loading">没有找到该项目的静态历史数据。</div>';
    return;
  }
  renderDetail(data);
}

function renderDetail(data) {
  $('detail-title').textContent = data.repo;
  $('detail-avatar').textContent = initials(data.repo);
  $('detail-url').href = data.url;
  $('detail-url').textContent = '打开 GitHub 仓库 ↗';
  $('detail-description').textContent = data.description || '项目维护者尚未填写简介。';
  $('detail-tags').innerHTML = '<span>' + esc(data.category) + '</span><span>' + esc(data.language) + '</span>';
  $('detail-stars').textContent = number(data.total_stars);
  $('detail-growth').textContent = data.complete ? '+' + number(data.growth) : '数据不足';
  $('detail-average').textContent = data.daily_average == null ? '—' : number(data.daily_average) + ' / 天';
  $('detail-category').textContent = data.category;
  $('detail-language').textContent = data.language;
  $('detail-forks').textContent = number(data.forks);
  $('detail-created').textContent = data.created_at
    ? new Date(data.created_at).toLocaleDateString('zh-CN', {year:'numeric',month:'short',day:'numeric',timeZone:'UTC'})
    : '—';
  $('detail-topics').innerHTML = (data.topics || []).length
    ? data.topics.map(t => '<span>' + esc(t) + '</span>').join('')
    : '<span>暂无 Topics</span>';
  $('chart-caption').textContent = periodNames[data.period] + ' · 每日新增 Star 与 ' + data.moving_average_window + ' 日移动平均';
  $('chart-start').textContent = data.window_start;
  $('chart-end').textContent = data.window_end;
  $('chart-note').textContent = data.complete
    ? '该周期累计新增 ' + number(data.growth) + ' Star，日均 ' + number(data.daily_average) + ' Star。' +
      (data.best_day ? '最高单日为 ' + data.best_day.date + '（' + number(data.best_day.stars) + ' Star）。' : '')
    : '该项目的历史数据尚未覆盖完整周期，图表仅展示已采集的数据，不会将缺失日期当作 0。';
  drawGrowthChart(data.series || []);
}

function drawGrowthChart(series) {
  const host = $('growth-chart');
  const W = 480, H = 190, left = 35, right = 8, top = 10, bottom = 27;
  const plotW = W - left - right, plotH = H - top - bottom;
  const values = series.map(x => x.stars).filter(x => x != null);
  if (!values.length) { host.innerHTML = '<div class="chart-loading">没有可展示的历史数据</div>'; return; }
  const max = Math.max(1, ...values), n = series.length, slot = plotW / Math.max(1, n), barW = Math.max(2, Math.min(13, slot * .55));
  const y = v => top + plotH - (v / max) * plotH;
  const x = i => left + i * slot + slot / 2;
  let grid = '', bars = '', lineSegments = [], currentSegment = [];
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4, yy = y(v);
    grid += '<line x1="' + left + '" y1="' + yy + '" x2="' + (W-right) + '" y2="' + yy + '" stroke="#edf0f5" stroke-dasharray="3 4"/>' +
      '<text x="' + (left-8) + '" y="' + (yy+3) + '" text-anchor="end" fill="#a3a9b6" font-size="9">' + Math.round(v) + '</text>';
  }
  series.forEach((point, i) => {
    if (point.stars != null) {
      const yy = y(point.stars), height = Math.max(1, top + plotH - yy);
      bars += '<rect x="' + (x(i)-barW/2) + '" y="' + yy + '" width="' + barW + '" height="' + height + '" rx="2" fill="#c9c3f5"><title>' + point.date + ': ' + point.stars + ' new Stars</title></rect>';
    }
    if (point.moving_average != null) currentSegment.push(x(i) + ',' + y(point.moving_average));
    else if (currentSegment.length) { lineSegments.push(currentSegment); currentSegment = []; }
  });
  if (currentSegment.length) lineSegments.push(currentSegment);
  const line = lineSegments.map(segment =>
    '<polyline points="' + segment.join(' ') + '" fill="none" stroke="#7165dc" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/>'
  ).join('');
  const labels = [0, Math.floor((n-1)/2), n-1].filter((v, i, a) => a.indexOf(v) === i).map(i =>
    '<text x="' + x(i) + '" y="' + (H-7) + '" text-anchor="middle" fill="#a3a9b6" font-size="9">' + series[i].date.slice(5) + '</text>'
  ).join('');
  host.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="每日新增 Star 增长图表">' + grid + bars + line + labels + '</svg>';
}

function bindEvents() {
  document.querySelectorAll('.period-button').forEach(button => button.addEventListener('click', () => {
    if (state.period === button.dataset.period) return;
    state.period = button.dataset.period;
    document.querySelectorAll('.period-button').forEach(b => {
      const active = b === button;
      b.classList.toggle('active', active);
      b.setAttribute('aria-pressed', String(active));
    });
    loadDashboard();
    if (state.selected) openDetail(state.selected);
  }));
  $('search-input').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.search = $('search-input').value; loadDashboard(); }, 180);
  });
  $('category-filter').addEventListener('change', () => { state.category = $('category-filter').value; loadDashboard(); });
  $('language-filter').addEventListener('change', () => { state.language = $('language-filter').value; loadDashboard(); });
  $('refresh-button').addEventListener('click', () => {
    window.open(ACTIONS_URL, '_blank', 'noopener,noreferrer');
  });
  $('close-drawer').addEventListener('click', closeDrawer);
  $('drawer-backdrop').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => {
    const drawer = $('detail-drawer');
    if (!drawer.classList.contains('open')) return;
    if (e.key === 'Escape') { closeDrawer(); return; }
    if (e.key !== 'Tab') return;
    const focusable = Array.from(drawer.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'));
    if (!focusable.length) { e.preventDefault(); drawer.focus(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

async function init() {
  try {
    const response = await fetch('./data/site-data.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('静态统计数据加载失败（HTTP ' + response.status + '）');
    state.data = await response.json();
    if (state.data.schema_version !== 1) throw new Error('网站数据版本不兼容，请等待下一次部署');
    $('connection-label').textContent = 'GitHub Actions 数据已连接';
    $('scheduler-label').textContent = 'Actions 自动更新 · 每 2 小时';
    $('scheduler-label').title = '数据由 GitHub Actions 每 2 小时采集并部署到 GitHub Pages';
    $('last-updated').textContent = state.data.generated_at
      ? '数据更新于 ' + new Date(state.data.generated_at).toLocaleString('zh-CN', {hour12:false,timeZone:'Asia/Shanghai'})
      : '等待数据';
    loadDashboard();
  } catch (error) {
    $('connection-label').textContent = '数据加载失败';
    $('scheduler-label').textContent = '暂时无法连接数据';
    $('scheduler-label').title = error.message;
    $('repo-table').innerHTML = '<tr><td colspan="8" class="empty-cell">' + esc(error.message) + '</td></tr>';
  }
}

bindEvents();
init();
