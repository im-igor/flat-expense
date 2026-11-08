'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state = {categories: [], expenses: [], plans: [], loan: [], today: '', moveMonth: '2027-08', view: 'overview', ready: false, report: null, reportSequence: 0, loadSequence: 0, chartLoanMode: 'with', start: '', end: '', expensePage: 0, planPage: 0, ledgerPage: 0, ledgerAutoPosition: false, ledgerSelectedPlanIds: new Set(), expenseFilters: {search:'', category:'', month:'', deleted:false}, planFilters: {search:'', category:'', month:'', status:'', archived:false}, ledgerFilters: {search:'', category1:'', category2:'', month:'', type:'', inactive:false}};
let editing = null;
let pendingRequest = null;
let toastTimer;
const PAGE_SIZE = 15;
const monthFormatter = new Intl.DateTimeFormat('ru-RU', {month:'short', year:'2-digit', timeZone:'UTC'});
const numberFormatter = new Intl.NumberFormat('ru-RU');

function initSidebar() {
  const storageKey = 'flat-expense.sidebar-collapsed';
  const toggle = $('#sidebar-toggle');
  const apply = collapsed => {
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    const label = collapsed ? 'Развернуть меню' : 'Свернуть меню';
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
  };
  let collapsed = true;
  try { collapsed = localStorage.getItem(storageKey) !== 'false'; } catch { /* Storage may be disabled. */ }
  apply(collapsed);
  toggle.addEventListener('click', () => {
    const next = !document.body.classList.contains('sidebar-collapsed');
    apply(next);
    try { localStorage.setItem(storageKey, String(next)); } catch { /* Keep the control usable without storage. */ }
  });
}

function initTheme() {
  const storageKey = 'flat-expense.theme';
  const toggle = $('#theme-toggle');
  const stylesheet = $('#theme-stylesheet');
  const label = $('#theme-toggle-label');
  const icon = $('#theme-toggle-icon');
  const apply = theme => {
    const dark = theme === 'dark';
    stylesheet.disabled = !dark;
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
    toggle.setAttribute('aria-pressed', String(dark));
    const text = dark ? 'Светлая тема' : 'Тёмная тема';
    label.textContent = text;
    icon.textContent = dark ? '☀' : '◐';
    toggle.title = 'Включить ' + text.toLocaleLowerCase('ru');
  };
  let theme = 'dark';
  try { theme = localStorage.getItem(storageKey) === 'light' ? 'light' : 'dark'; } catch { /* Default to dark. */ }
  apply(theme);
  toggle.addEventListener('click', () => {
    theme = theme === 'dark' ? 'light' : 'dark';
    apply(theme);
    try { localStorage.setItem(storageKey, theme); } catch { /* Keep the switch usable without storage. */ }
  });
}

function cents(value) {
  if (value === null || value === undefined || value === '') return 0n;
  const [whole, fraction = ''] = String(value).split('.');
  return BigInt(whole) * 100n + BigInt((fraction + '00').slice(0, 2));
}
function requestKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (globalThis.crypto?.getRandomValues) {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  return `fallback-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
function rub(value, wholeOnly = false) {
  if (value === null || value === undefined) return 'Не оценено';
  const amount = typeof value === 'bigint' ? value : cents(value);
  const whole = amount / 100n;
  const fraction = (amount % 100n).toString().padStart(2, '0');
  return numberFormatter.format(whole) + (wholeOnly || fraction === '00' ? '' : ',' + fraction) + ' ₽';
}
function plural(count, one, few, many) {
  const n = count % 100, digit = count % 10;
  return count + ' ' + (n >= 11 && n <= 14 ? many : digit === 1 ? one : digit >= 2 && digit <= 4 ? few : many);
}
function dateLabel(value) { return value ? value.split('-').reverse().join('.') : '—'; }
function monthLabel(value) { return value ? monthFormatter.format(new Date(value.slice(0,7) + '-01T12:00:00Z')) : 'Без даты'; }
function addMonths(value, count) {
  const [year, month] = value.slice(0,7).split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1 + count, 1));
  return date.toISOString().slice(0,7);
}
function moneyInput(value, required = false) {
  const clean = value.trim().replace(/[\s\u00a0\u202f]/g, '').replace(',', '.');
  if (!clean && !required) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) throw new Error('Введите сумму числом, максимум с двумя знаками после запятой.');
  if (required && cents(clean) <= 0n) throw new Error('Сумма оплаты должна быть больше нуля.');
  return clean;
}
function empty(title, text, action, label) {
  return `<div class="empty"><div class="empty-icon" aria-hidden="true">◷</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${action ? `<button class="button primary" data-action="${esc(action)}">${esc(label)}</button>` : ''}</div>`;
}
function notify(text) {
  clearTimeout(toastTimer);
  $('#toast').textContent = text;
  $('#toast').classList.remove('hidden');
  toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 4200);
}
function showError(error) {
  const box = $('#global-error');
  box.textContent = error.message || 'Не удалось загрузить данные. Попробуйте обновить страницу.';
  box.classList.remove('hidden');
}
async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(path, {credentials:'same-origin', ...options, signal:controller.signal, headers:{'X-Flat-Expense':'web', ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers}});
    if (response.status === 401) { location.assign('/login'); throw new Error('Сессия завершена. Войдите снова.'); }
    let result;
    try { result = await response.json(); } catch { throw new Error('Сервер вернул непонятный ответ. Обновите страницу.'); }
    if (!response.ok) {
      if (Array.isArray(result.detail)) {
        const names = {amount:'сумму оплаты', planned_amount:'плановую сумму', expected:'плановую сумму', agreed:'фактическую цену', description:'описание', title:'название', paid_on:'дату оплаты', note:'примечание'};
        const field = result.detail[0]?.loc?.at(-1);
        throw new Error('Проверьте ' + (names[field] || 'обязательные поля') + '. Суммы — до 9 999 999 999,99 ₽, описание — до 500 символов.');
      }
      throw new Error(result.detail || 'Не удалось выполнить запрос.');
    }
    return result;
  } catch (error) {
    if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('Нет ответа от сервера. Проверьте подключение и повторите действие; повторная отправка той же оплаты не создаст дубликат.');
    throw error;
  } finally { clearTimeout(timer); }
}
async function pages(path) {
  const items = [];
  for (let offset = 0; ; offset += 100) {
    const batch = await request(path + (path.includes('?') ? '&' : '?') + `limit=100&offset=${offset}`);
    items.push(...batch);
    if (batch.length < 100) return items;
  }
}
async function loadData() {
  const sequence = ++state.loadSequence;
  const [categories, expenses, deleted, plans, archived, loan] = await Promise.all([
    request('/v1/categories'), pages('/v1/expenses'), pages('/v1/expenses?deleted=true'), pages('/v1/plans'), pages('/v1/plans?archived=true'), request('/v1/loan')
  ]);
  if (sequence !== state.loadSequence) return;
  state.categories = categories;
  state.expenses = [...expenses, ...deleted];
  state.plans = [...plans, ...archived];
  state.loan = loan;
  state.ready = true;
  $('#loading').classList.add('hidden');
  $('#global-error').classList.add('hidden');
  render();
  await loadReport();
}
async function loadReport() {
  const sequence = ++state.reportSequence;
  try {
    const result = await request(`/v1/summary?start=${state.start}-01&end=${state.end}-01`);
    if (sequence !== state.reportSequence) return;
    state.report = result;
    if (state.view === 'overview') renderOverview();
  } catch (error) { if (sequence === state.reportSequence) showError(error); }
}
function categoryOptions(selected = '', rootsOnly = false) {
  return state.categories.filter(c => !rootsOnly || !c.parent_id).map(c => `<option value="${esc(c.id)}" ${c.id === selected ? 'selected' : ''}>${esc(c.path)}</option>`).join('');
}
function categoryMatches(item, category) {
  if (!category) return true;
  const child = state.categories.find(c => c.id === item.category_id);
  return item.category_id === category || child?.parent_id === category;
}
function categoryPath(id) { return state.categories.find(c => c.id === id)?.path || ''; }
function planAmount(plan) { return plan.planned_amount ?? plan.agreed ?? plan.expected; }
function planStatus(plan) {
  if (plan.archived) return '<span class="badge gray">В архиве</span>';
  if (plan.payment_status === 'paid') return '<span class="badge paid">Оплачено</span>';
  if (plan.payment_status === 'finalized') return '<span class="badge gray">Финализировано</span>';
  if (plan.payment_status === 'partial') return '<span class="badge amber">Частично оплачено</span>';
  if (planAmount(plan) === null) return '<span class="badge amber">Без оценки</span>';
  if (plan.due_month && plan.due_month.slice(0,7) < state.today.slice(0,7)) return '<span class="badge amber">Перенести месяц</span>';
  return '<span class="badge plan-badge">Запланировано</span>';
}
function navigate(view) {
  if (view === 'expenses' || view === 'plans') view = 'ledger';
  const nextView=['overview','ledger','loan','categories'].includes(view) ? view : 'overview';
  if(nextView==='ledger'&&state.view!=='ledger')state.ledgerAutoPosition=true;
  state.view = nextView;
  $('.page-header').classList.toggle('compact',state.view==='ledger');
  document.querySelectorAll('nav a').forEach(a => {
    const active = a.dataset.view === state.view;
    a.classList.toggle('active', active);
    if (active) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  const titles = {overview:['Обзор','Понимать, сколько уже потрачено и что ещё впереди.'], ledger:['Расходы и планы','Оплаты и будущие расходы в одной хронологии.'], loan:['Займ','График платежей, материальная выгода и налог по ставке Банка России.'], categories:['Категории','Два уровня, чтобы расходы всегда были на своём месте.']};
  $('#page-title').textContent = titles[state.view][0];
  $('#page-subtitle').textContent = titles[state.view][1];
  render();
}
function render() {
  document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
  if (!state.ready) return;
  $(`#view-${state.view}`).classList.remove('hidden');
  ({overview:renderOverview, ledger:renderLedger, loan:renderLoan, categories:renderCategories})[state.view]();
}
function metrics() {
  const expenses = state.expenses.filter(e => !e.deleted);
  const plans = state.plans.filter(p => !p.archived);
  const pastLoan=state.loan.filter(row=>row.payment_date<=state.today), futureLoan=state.loan.filter(row=>row.payment_date>state.today);
  const paidWithoutLoan=expenses.reduce((sum,e)=>sum+cents(e.amount),0n)+plans.reduce((sum,p)=>sum+cents(p.paid_amount),0n), paidLoan=pastLoan.reduce((sum,row)=>sum+cents(row.total),0n), paid=paidWithoutLoan+paidLoan;
  const plannedWithoutLoan=plans.reduce((sum,p)=>sum+cents(p.remaining_amount),0n), plannedLoan=futureLoan.reduce((sum,row)=>sum+cents(row.total),0n), remaining=plannedWithoutLoan+plannedLoan;
  const unpriced = plans.filter(p => planAmount(p) === null).length;
  const month = state.today.slice(0,7);
  const currentActualWithoutLoan=expenses.filter(e=>e.paid_on.startsWith(month)).reduce((s,e)=>s+cents(e.amount),0n)+plans.filter(p=>p.paid_on?.startsWith(month)).reduce((s,p)=>s+cents(p.paid_amount),0n), currentActualLoan=pastLoan.filter(row=>row.payment_date.startsWith(month)).reduce((s,row)=>s+cents(row.total),0n);
  const currentPlannedWithoutLoan=plans.filter(p=>p.due_month?.startsWith(month)).reduce((s,p)=>s+cents(p.remaining_amount),0n), currentPlannedLoan=futureLoan.filter(row=>row.payment_date.startsWith(month)).reduce((s,row)=>s+cents(row.total),0n);
  const currentWithoutLoan=currentActualWithoutLoan+currentPlannedWithoutLoan, currentLoan=currentActualLoan+currentPlannedLoan;
  return `<div class="metrics">
    <article class="metric featured"><span class="metric-mark" aria-hidden="true">↗</span><p class="metric-label">Факт · с займом</p><p class="metric-value">${rub(paid)}</p><p class="metric-foot">Без займа ${rub(paidWithoutLoan)} · заём ${rub(paidLoan)}</p></article>
    <article class="metric"><span class="metric-mark" aria-hidden="true">◷</span><p class="metric-label">План · с займом</p><p class="metric-value">${rub(remaining)}</p><p class="metric-foot">Без займа ${rub(plannedWithoutLoan)} · заём ${rub(plannedLoan)}</p></article>
    <article class="metric"><span class="metric-mark" aria-hidden="true">◫</span><p class="metric-label">${esc(monthLabel(month))} · факт + план</p><p class="metric-value">${rub(currentWithoutLoan+currentLoan)}</p><p class="metric-foot">Без займа ${rub(currentWithoutLoan)} · заём ${rub(currentLoan)}</p></article>
    <article class="metric"><span class="metric-mark" aria-hidden="true">?</span><p class="metric-label">Покупки без оценки</p><p class="metric-value">${unpriced}</p><p class="metric-foot">Пока не включены в денежные итоги</p></article>
  </div>`;
}
function chartMonthValues(month) {
  if (state.chartLoanMode==='without') return {actual:month.actual_without_loan,planned:month.planned_without_loan,total:month.known_total_without_loan};
  return {actual:month.actual,planned:month.planned_remaining,total:month.known_total};
}
function chartMarkup(report) {
  const months = report.months;
  const width = Math.max(720, months.length * 60 + 70), height = 245, left = 62, top = 15, bottom = 42, plot = height - top - bottom;
  const max = Math.max(1, ...months.map(m => Number(cents(chartMonthValues(m).total)) / 100));
  const scale = max * 1.15;
  const step = (width - left - 18) / months.length;
  const scope=state.chartLoanMode==='with'?'с займом':'без займа';
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Оплачено и запланировано по месяцам ${scope}. Точные суммы доступны в таблице ниже.">`;
  for (let i = 0; i <= 3; i++) {
    const value = scale * i / 3, y = height - bottom - plot * i / 3;
    const label = value >= 1e6 ? (value / 1e6).toLocaleString('ru-RU',{maximumFractionDigits:1}) + ' млн' : value >= 1e3 ? Math.round(value / 1e3).toLocaleString('ru-RU') + ' тыс' : Math.round(value).toLocaleString('ru-RU');
    svg += `<line class="tick" x1="${left}" y1="${y}" x2="${width - 10}" y2="${y}"/><text x="${left - 12}" y="${y + 3}" text-anchor="end">${label}</text>`;
  }
  months.forEach((m,i) => {
    const values=chartMonthValues(m), actual=Number(cents(values.actual))/100, planned=Number(cents(values.planned))/100;
    const bar = Math.min(32, step * .52), x = left + step * i + (step - bar) / 2, ah = plot * actual / scale, ph = plot * planned / scale;
    const separator=ah>0&&ph>0?`<line class="bar-separator" x1="${x}" y1="${height-bottom-ah}" x2="${x+bar}" y2="${height-bottom-ah}"/>`:'';
    svg += `<g class="period-bar" aria-label="${esc(monthLabel(m.month))}: всего ${rub(values.total)}, оплачено ${rub(values.actual)}, запланировано ${rub(values.planned)}" data-month="${esc(m.month)}" data-actual="${esc(values.actual)}" data-planned="${esc(values.planned)}" data-total="${esc(values.total)}" data-scope="${scope}"><rect class="bar-hit" x="${x-step*.18}" y="${top}" width="${bar+step*.36}" height="${plot}"/><rect class="planned-bar" x="${x}" y="${height-bottom-ah-ph}" width="${bar}" height="${ph}" rx="3"/><rect class="actual-bar" x="${x}" y="${height-bottom-ah}" width="${bar}" height="${ah}" rx="3"/>${separator}<text x="${x+bar/2}" y="${height-18}" text-anchor="middle">${esc(monthLabel(m.month).replace(' г.',''))}</text></g>`;
  });
  return svg + '</svg>';
}
function renderOverview() {
  const plans = state.plans.filter(p => !p.archived);
  const undated = plans.filter(p => !p.due_month);
  const overdue = plans.filter(p => p.due_month && p.due_month.slice(0,7) < state.today.slice(0,7));
  const currentMonth=state.today.slice(0,7), nextMonth=addMonths(currentMonth,1);
  const upcomingMonths=[currentMonth,nextMonth].map(month=>({month,items:plans.filter(p=>p.due_month?.startsWith(month)).sort((a,b)=>a.title.localeCompare(b.title,'ru'))}));
  const report = state.report;
  const notices = [];
  if (undated.length) notices.push(`<span>${plural(undated.length,'покупка','покупки','покупок')} без месяца оплаты. Распределите их, чтобы увидеть полный прогноз.</span><button class="text-button" data-action="undated">Распределить →</button>`);
  if (overdue.length) notices.push(`<span>У ${overdue.length} планов указанный месяц уже прошёл.</span><button class="text-button" data-action="overdue">Проверить →</button>`);
  $('#view-overview').innerHTML = metrics() + notices.map(n => `<div class="notice">${n}</div>`).join('') + `
    <article class="card"><div class="card-head"><div><h2>Расходы по месяцам</h2><p>Нижний уровень столбика — оплачено, верхний — запланировано</p></div><div class="chart-head-tools"><div class="chart-scope" role="group" aria-label="Учитывать заём в графике"><button class="chart-scope-button ${state.chartLoanMode==='with'?'active':''}" data-action="chart-loan-mode" data-mode="with" aria-pressed="${state.chartLoanMode==='with'}">С займом</button><button class="chart-scope-button ${state.chartLoanMode==='without'?'active':''}" data-action="chart-loan-mode" data-mode="without" aria-pressed="${state.chartLoanMode==='without'}">Без займа</button></div><div class="chart-tools"><label class="screen-reader" for="report-start">Начало периода</label><input type="month" id="report-start" value="${state.start}" min="2000-01" max="2100-12"><span>—</span><label class="screen-reader" for="report-end">Конец периода</label><input type="month" id="report-end" value="${state.end}" min="2000-01" max="2100-12"></div></div></div>
      <div class="chart-wrap">${report ? chartMarkup(report)+'<div id="chart-tooltip" class="chart-tooltip hidden" role="status"></div>' : '<div class="empty-small">Загружаю прогноз…</div>'}</div><div class="chart-legend"><span><i class="legend-dot"></i>Оплачено</span><span><i class="legend-dot planned"></i>Запланировано</span><span class="chart-scope-label">График ${state.chartLoanMode==='with'?'с займом':'без займа'}</span><button class="text-button" data-action="move-period">До переезда</button></div>
      ${report ? `<details class="month-details"><summary>Точные суммы по месяцам · с займом и без</summary><div class="table-scroll"><table><thead><tr><th>Месяц</th><th class="num">Факт без займа</th><th class="num">План без займа</th><th class="num">Факт с займом</th><th class="num">План с займом</th><th class="num">Без оценки</th></tr></thead><tbody>${report.months.map(m => `<tr><td>${esc(monthLabel(m.month))}${m.overdue_plan ? ' <span class="badge amber">Месяц прошёл</span>' : ''}</td><td class="num">${rub(m.actual_without_loan)}</td><td class="num">${rub(m.planned_without_loan)}</td><td class="num bold-num">${rub(m.actual)}</td><td class="num bold-num">${rub(m.planned_remaining)}</td><td class="num">${m.unpriced || '—'}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
    </article>
    <article class="card upcoming-card"><div class="card-head"><div><h2>Ближайшие планы</h2><p>Плановые затраты на незаконченный текущий и следующий полный месяц</p></div><span class="badge">${upcomingMonths.reduce((sum,value)=>sum+value.items.length,0)} позиций</span></div><div class="upcoming-grid">${upcomingMonths.map(({month,items})=>{const known=items.reduce((sum,item)=>sum+cents(planAmount(item)),0n),unpriced=items.filter(item=>planAmount(item)===null).length;return `<section class="upcoming-month"><div class="upcoming-month-head"><div><strong>${esc(monthLabel(month))}</strong><small>${month===currentMonth?'Текущий незаконченный месяц':'Следующий полный месяц'}</small></div><div><b>${rub(known)}</b>${unpriced?`<small>＋ ${plural(unpriced,'позиция','позиции','позиций')} без оценки</small>`:''}</div></div><div class="compact-list">${items.length?items.map(p=>`<button class="list-row list-click" data-action="edit-plan" data-id="${p.id}"><span class="list-symbol" aria-hidden="true">▤</span><span class="list-body"><strong>${esc(p.title)}</strong><small>${esc(p.category)}</small></span><span class="list-amount">${rub(planAmount(p))}</span></button>`).join(''):'<div class="empty-small">На этот месяц плановых расходов нет.</div>'}</div></section>`;}).join('')}</div><div class="card-tail"><a class="text-button" href="#ledger">Все расходы и планы →</a></div></article>`;
}
function updateChartTooltip(event) {
  const tooltip=$('#chart-tooltip');
  if(!tooltip)return;
  const bar=event.target.closest?.('.period-bar');
  if(!bar){tooltip.classList.add('hidden');return;}
  tooltip.innerHTML=`<strong>${esc(monthLabel(bar.dataset.month))}</strong><small>${esc(bar.dataset.scope)}</small><div><span>Всего</span><b>${rub(bar.dataset.total)}</b></div><div><span><i class="legend-dot"></i>Оплачено</span><b>${rub(bar.dataset.actual)}</b></div><div><span><i class="legend-dot planned"></i>Запланировано</span><b>${rub(bar.dataset.planned)}</b></div>`;
  tooltip.classList.remove('hidden');
  const bounds=tooltip.getBoundingClientRect(), margin=12;
  tooltip.style.left=Math.max(margin,Math.min(event.clientX+14,window.innerWidth-bounds.width-margin))+'px';
  tooltip.style.top=Math.max(margin,Math.min(event.clientY+14,window.innerHeight-bounds.height-margin))+'px';
}
function hideChartTooltip(){ $('#chart-tooltip')?.classList.add('hidden'); }
function pagination(kind, count, page, totalText, pageCount = null) {
  const pages = pageCount ?? Math.max(1, Math.ceil(count / PAGE_SIZE));
  return `<div class="table-foot"><span>${plural(count,'запись','записи','записей')}${totalText ? ' · ' + totalText : ''}</span><div class="pagination"><button class="text-button" data-action="page" data-kind="${kind}" data-page="${page-1}" ${page===0?'disabled':''}>← Назад</button><span>${page+1} / ${pages}</span><button class="text-button" data-action="page" data-kind="${kind}" data-page="${page+1}" ${page+1>=pages?'disabled':''}>Далее →</button></div></div>`;
}
function ledgerRows(filters=state.ledgerFilters) {
  const f=filters, search=f.search.toLocaleLowerCase('ru'), category=f.category2||f.category1;
  const expenses=state.expenses.filter(e=>(f.inactive||!e.deleted)&&categoryMatches(e,category)&&(!f.month||e.paid_on.startsWith(f.month))&&(!search||[e.description,e.note,e.category].join(' ').toLocaleLowerCase('ru').includes(search))).map(e=>({kind:'expense',date:e.paid_on,item:e}));
  const plans=state.plans.filter(p=>(f.inactive||!p.archived)&&categoryMatches(p,category)&&(!f.month||p.due_month?.startsWith(f.month))&&(!search||[p.title,p.note,p.category].join(' ').toLocaleLowerCase('ru').includes(search))).map(p=>({kind:'plan',date:p.due_month?.slice(0,10)||'9999-12-31',item:p}));
  const loan=state.loan.filter(row=>!category&&(!f.month||row.payment_date.startsWith(f.month))&&(!search||`займ взнос платёж ${row.period_index}`.includes(search))).map(row=>({kind:'loan',date:row.payment_date,item:row}));
  const rows=f.type==='expense'?expenses:f.type==='plan'?plans:f.type==='loan'?loan:[...expenses,...loan,...plans];
  const order={expense:0,loan:1,plan:2};
  return rows.sort((a,b)=>a.date.localeCompare(b.date)||order[a.kind]-order[b.kind]||a.item.id.localeCompare(b.item.id));
}
function categoryParts(id) {
  const category=state.categories.find(c=>c.id===id);
  if(!category)return ['',''];
  if(!category.parent_id)return [category.name,''];
  return [state.categories.find(c=>c.id===category.parent_id)?.name||'',category.name];
}
function normalizedText(value) {
  return String(value||'').toLocaleLowerCase('ru').replaceAll('ё','е').replace(/[^а-яa-z0-9]+/g,' ').trim();
}
function recommendationKey(kind,item,categoryId) { return `flat-expense:category-dismissed:${kind}:${item.id}:${categoryId}`; }
function recommendationDismissed(kind,item,categoryId) {
  try { return localStorage.getItem(recommendationKey(kind,item,categoryId))==='1'; } catch { return false; }
}
function categorySuggestion(kind,item) {
  const current=state.categories.find(c=>c.id===item.category_id);
  if(!current||current.parent_id)return null;
  const children=state.categories.filter(c=>c.parent_id===current.id);
  if(!children.length)return null;
  const main=normalizedText(kind==='expense'?item.description:item.title);
  const peers=[...state.expenses.map(value=>({kind:'expense',item:value})),...state.plans.map(value=>({kind:'plan',item:value}))];
  const exact=peers.map(value=>value.item).filter(peer=>peer.id!==item.id&&normalizedText(peer.description||peer.title)===main).map(peer=>state.categories.find(c=>c.id===peer.category_id)).filter(c=>c?.parent_id===current.id);
  if(exact.length){
    const counts=new Map();exact.forEach(c=>counts.set(c.id,(counts.get(c.id)||0)+1));
    const best=[...counts].sort((a,b)=>b[1]-a[1])[0];
    const suggested=children.find(c=>c.id===best[0]);
    if(suggested&&!recommendationDismissed(kind,item,suggested.id))return suggested;
  }
  const text=normalizedText([item.description||item.title,item.note].join(' '));
  const hints={
    'работы':['этап','работ','монтаж','демонтаж'],
    'черновые материалы':['материал','штукатур','шпаклев','грунтов','стяжк','кварцвинил','плитк','керамогранит'],
    'дизайн':['дизайн','проект'],
    'климат':['климат','вентиляц','кондиц','увлажнен'],
    'перепланировка':['переплан'],
    'приемка':['приемк'],
    'авторский надзор':['авторск','надзор'],
    'страховка':['страхов'],
    'кап ремонт':['капремонт','кап ремонт'],
    'коммуналка от ук':['коммунал','управляющ'],
    'покупки':['двер','потол','светиль','сантехник','кухн','шкаф','кроват','матрас','диван','стол','стул','штор'],
  };
  const scored=children.map(child=>{
    const name=normalizedText(child.name), words=name.split(' ').filter(word=>word.length>=4);
    const patterns=[...words.map(word=>word.slice(0,Math.max(4,word.length-2))),...(hints[name]||[])];
    return {child,score:new Set(patterns.filter(pattern=>text.includes(pattern))).size};
  }).sort((a,b)=>b.score-a.score);
  const suggested=scored[0]?.score>0&&scored[0].score>=(scored[1]?.score||0)+1?scored[0].child:children.length===1?children[0]:null;
  return suggested&&!recommendationDismissed(kind,item,suggested.id)?suggested:null;
}
const editableFieldLabels={
  amount:'оплаченную сумму',planned_amount:'плановую сумму',paid_on:'дату оплаты',description:'описание',note:'примечание',
  title:'название',expected:'плановую стоимость',agreed:'фактическую цену',due_month:'месяц оплаты',
  category1:'категорию 1',category2:'категорию 2'
};
function editableValue(kind,item,field,value,classes='') {
  const text=value===null||value===undefined||value===''?'—':String(value);
  if(item.deleted||item.archived)return `<span class="${classes}">${esc(text)}</span>`;
  const label=editableFieldLabels[field]||'значение';
  return `<button type="button" class="inline-edit ${classes}" data-action="edit-${kind}" data-id="${item.id}" data-field="${field}" aria-label="Изменить ${label}: ${esc(text)}" title="Нажмите, чтобы изменить">${esc(text)}</button>`;
}
function categoryCell(kind,item) {
  const [,second]=categoryParts(item.category_id);
  if(second)return `<td>${editableValue(kind,item,'category2',second)}</td>`;
  const suggestion=categorySuggestion(kind,item), inactive=item.deleted||item.archived;
  const missing=inactive?'<span class="badge amber">Не выбрана</span>':`<button type="button" class="inline-edit inline-edit-badge badge amber" data-action="edit-${kind}" data-id="${item.id}" data-field="category2" aria-label="Выбрать категорию 2" title="Нажмите, чтобы выбрать">Не выбрана</button>`;
  return `<td class="category-missing-cell">${missing}${inactive?'':suggestion?`<span class="category-suggestion">Рекомендуется: <strong>${esc(suggestion.name)}</strong><span class="suggestion-actions"><button class="text-button" data-action="accept-category" data-kind="${kind}" data-id="${item.id}" data-category-id="${suggestion.id}">Принять</button><button class="text-button muted-button" data-action="dismiss-category" data-kind="${kind}" data-id="${item.id}" data-category-id="${suggestion.id}">Отклонить</button></span></span>`:`<button class="text-button category-manual" data-action="edit-${kind}" data-id="${item.id}" data-field="category2">Назначить вручную</button>`}</td>`;
}
function ledgerMonthKey(row) { return row.date==='9999-12-31'?'':row.date.slice(0,7); }
function ledgerPages(rows) {
  const monthGroups=[];
  rows.forEach(row=>{
    const key=ledgerMonthKey(row), current=monthGroups.at(-1);
    if(current?.key===key)current.rows.push(row);
    else monthGroups.push({key,rows:[row]});
  });
  const pages=[];
  let page=[];
  monthGroups.forEach(group=>{
    if(page.length&&page.length+group.rows.length>PAGE_SIZE){pages.push(page);page=[];}
    page.push(...group.rows);
  });
  if(page.length||!pages.length)pages.push(page);
  return pages;
}
function ledgerHasDefaultFilters() {
  const f=state.ledgerFilters;
  return !f.search&&!f.category1&&!f.category2&&!f.month&&!f.type&&!f.inactive;
}
function ledgerCurrentMonthPage(pages) {
  const month=state.today.slice(0,7);
  const exact=pages.findIndex(page=>page.some(row=>ledgerMonthKey(row)===month));
  if(exact>=0)return exact;
  const upcoming=pages.findIndex(page=>page.some(row=>ledgerMonthKey(row)>=month));
  return upcoming>=0?upcoming:Math.max(0,pages.length-1);
}
function selectablePlanRows(rows) {
  return rows.filter(row=>row.kind==='plan'&&!row.item.archived);
}
function reconcileLedgerSelection(rows) {
  const available=new Set(selectablePlanRows(rows).map(row=>row.item.id));
  state.ledgerSelectedPlanIds=new Set([...state.ledgerSelectedPlanIds].filter(id=>available.has(id)));
}
function ledgerMonthTotals(rows) {
  const totals=new Map();
  rows.forEach(row=>{
    const key=ledgerMonthKey(row), total=totals.get(key)||{plan:0n,fact:0n,paid:0n,remaining:0n,unpriced:0};
    if(row.kind==='expense'&&!row.item.deleted){const amount=cents(row.item.amount);total.plan+=cents(row.item.planned_amount);total.fact+=amount;total.paid+=amount;}
    else if(row.kind==='loan'){
      const amount=cents(row.item.total), paid=row.item.payment_date<=state.today;
      total.plan+=amount;
      if(paid){total.fact+=amount;total.paid+=amount;}
      else total.remaining+=amount;
    }else if(row.kind==='plan'&&!row.item.archived){
      if(row.item.planned_amount!==null)total.plan+=cents(row.item.planned_amount);
      if(row.item.paid_amount!==null){total.fact+=cents(row.item.paid_amount);total.paid+=cents(row.item.paid_amount);}
      if(planAmount(row.item)===null)total.unpriced+=1;
      else total.remaining+=cents(row.item.remaining_amount);
    }
    totals.set(key,total);
  });
  return totals;
}
function ledgerCumulativeTotals(rows) {
  const monthly=ledgerMonthTotals(rows), cumulative=new Map();
  let running={plan:0n,fact:0n,paid:0n,remaining:0n};
  [...monthly.keys()].filter(Boolean).sort().forEach(key=>{
    const total=monthly.get(key);
    cumulative.set(key,running);
    running={
      plan:running.plan+total.plan,
      fact:running.fact+total.fact,
      paid:running.paid+total.paid,
      remaining:running.remaining+total.remaining,
    };
  });
  return cumulative;
}
function monthTotal(value,cumulative) {
  return `<strong>${rub(value)}</strong>${cumulative===undefined?'':`<small class="month-cumulative">Σ ${rub(cumulative)}</small>`}`;
}
function ledgerMonthHeader(key,total,cumulative) {
  const unpriced=total.unpriced?`${plural(total.unpriced,'позиция','позиции','позиций')} без оценки`:'';
  const cumulativeLabel=key?'<small class="month-cumulative-label">На начало месяца</small>':'';
  return `<tr class="month-divider"><td></td><td><strong>${key?esc(monthLabel(key)):'Без даты'}</strong>${cumulativeLabel}</td><td colspan="3"></td><td class="num month-total">${monthTotal(total.plan,cumulative?.plan)}</td><td class="num month-total">${monthTotal(total.fact,cumulative?.fact)}</td><td class="num month-total">${monthTotal(total.paid,cumulative?.paid)}</td><td class="num month-total">${monthTotal(total.remaining,cumulative?.remaining)}</td><td class="month-status">Итого</td><td colspan="2" class="month-extra">${unpriced}</td></tr>`;
}
function ledgerRowMarkup(kind,item) {
  const [first]=categoryParts(item.category_id);
  if(kind==='expense'){
    return `<tr class="${item.deleted?'inactive-row':'paid-row'}"><td class="ledger-select-cell"></td><td class="date-cell">${editableValue('expense',item,'paid_on',dateLabel(item.paid_on))}</td><td>${editableValue('expense',item,'category1',first)}</td>${categoryCell('expense',item)}<td>${editableValue('expense',item,'description',item.description,'table-main')}</td><td class="num">${editableValue('expense',item,'planned_amount',rub(item.planned_amount))}</td><td class="num">${editableValue('expense',item,'amount',rub(item.amount))}</td><td class="num bold-num">${editableValue('expense',item,'amount',rub(item.amount))}</td><td class="num">${rub(0n)}</td><td>${item.deleted?'<span class="badge gray">Удалена</span>':'<span class="badge paid">Оплачено</span>'}</td><td>${editableValue('expense',item,'note',item.note||'—','table-note')}</td><td><button class="text-button" data-action="${item.deleted?'restore-expense':'edit-expense'}" data-id="${item.id}">${item.deleted?'Восстановить':'Изменить'}</button></td></tr>`;
  }
  if(kind==='loan'){
    const paid=item.payment_date<=state.today;
    return `<tr class="${paid?'paid-row':''}"><td class="ledger-select-cell"></td><td class="date-cell">${dateLabel(item.payment_date)}</td><td>Заём</td><td>Взнос</td><td><span class="table-main">Взнос по займу № ${item.period_index}</span></td><td class="num">${rub(item.total)}</td><td class="num">${paid?rub(item.total):'—'}</td><td class="num bold-num">${paid?rub(item.total):rub(0n)}</td><td class="num">${paid?rub(0n):rub(item.total)}</td><td>${paid?'<span class="badge paid">Оплачено</span>':'<span class="badge plan-badge">Запланировано</span>'}</td><td><span class="table-note">Основной долг ${rub(item.principal)}, проценты ${rub(item.interest)}, налог ${item.material_tax===null?'не рассчитан':rub(item.material_tax)}</span></td><td><a class="text-button" href="#loan">График</a></td></tr>`;
  }
  const planCell=item.planned_amount===null?`<td class="num budget-missing-cell">${editableValue('plan',item,'expected','Не заложено','inline-edit-warning')}</td>`:`<td class="num">${editableValue('plan',item,'expected',rub(item.planned_amount))}</td>`;
  const select=item.archived?'':`<input class="ledger-plan-select" type="checkbox" data-plan-id="${item.id}" aria-label="Выбрать план: ${esc(item.title)}" ${state.ledgerSelectedPlanIds.has(item.id)?'checked':''}>`;
  const actions=item.archived?`<button class="text-button" data-action="restore-plan" data-id="${item.id}">Вернуть</button>`:`<button class="text-button" data-action="record-plan-payment" data-id="${item.id}">Внести оплату</button><button class="text-button" data-action="edit-plan" data-id="${item.id}">Изменить</button>`;
  return `<tr class="${item.archived?'inactive-row':''}"><td class="ledger-select-cell">${select}</td><td class="date-cell">${editableValue('plan',item,'due_month',monthLabel(item.due_month))}</td><td>${editableValue('plan',item,'category1',first)}</td>${categoryCell('plan',item)}<td>${editableValue('plan',item,'title',item.title,'table-main')}</td>${planCell}<td class="num">${item.paid_amount===null?'—':rub(item.paid_amount)}</td><td class="num bold-num">${item.paid_amount===null?'—':rub(item.paid_amount)}</td><td class="num bold-num">${item.remaining_amount===null?'Не оценено':rub(item.remaining_amount)}</td><td>${planStatus(item)}</td><td>${editableValue('plan',item,'note',item.note||'—','table-note')}</td><td>${actions}</td></tr>`;
}
function ledgerBalance(rows) {
  let plan=0n,fact=0n,count=0;
  rows.forEach(row=>{
    if(row.kind==='expense'&&!row.item.deleted){plan+=cents(row.item.planned_amount);fact+=cents(row.item.amount);count+=1;}
    else if(row.kind==='loan'&&row.item.payment_date<=state.today){const amount=cents(row.item.total);plan+=amount;fact+=amount;count+=1;}
    else if(row.kind==='plan'&&!row.item.archived&&['paid','finalized'].includes(row.item.payment_status)&&row.item.planned_amount!==null){plan+=cents(row.item.planned_amount);fact+=cents(row.item.paid_amount);count+=1;}
  });
  return {plan,fact,difference:plan-fact,count};
}
function ledgerBulkControls(rows) {
  const plans=selectablePlanRows(rows), selected=state.ledgerSelectedPlanIds.size;
  if(!plans.length)return '';
  return `<div class="ledger-bulk"><span>${selected?`Выбрано: ${plural(selected,'план','плана','планов')}`:`Выберите планы для переноса срока`}</span><button class="text-button" data-action="select-filtered-plans">Выбрать все ${plans.length}</button>${selected?`<label>Новый месяц <input id="bulk-due-month" type="month" min="2000-01" max="2100-12" value="${esc(state.today.slice(0,7))}"></label><button class="button secondary" data-action="bulk-move-plans">Перенести срок</button><button class="text-button" data-action="clear-selected-plans">Снять выбор</button>`:''}</div>`;
}
function recordPlanPayment(item) {
  if(!item)throw new Error('План не найден. Обновите данные.');
  if(['paid','finalized'].includes(item.payment_status))throw new Error('Этот план уже закрыт.');
  openEditor('plan-payment',item);
}
async function bulkMovePlans() {
  const selected=[...state.ledgerSelectedPlanIds];
  const month=$('#bulk-due-month')?.value;
  if(!selected.length)return;
  if(!month)throw new Error('Выберите новый месяц оплаты.');
  if(!await confirmAction('Перенести сроки планов?',`У ${plural(selected.length,'выбранного плана','выбранных планов','выбранных планов')} месяц оплаты изменится на ${monthLabel(month)}.`,'Перенести'))return;
  const result=await mutation('/v1/plans/bulk-due-month','POST',{ids:selected,due_month:month+'-01'});
  state.ledgerSelectedPlanIds.clear();
  await loadData();
  notify(`Срок перенесён у ${plural(result.updated,'плана','планов','планов')}`);
}
function renderLedger() {
  const f=state.ledgerFilters, rows=ledgerRows();
  const roots=state.categories.filter(category=>!category.parent_id);
  const children=state.categories.filter(category=>category.parent_id===f.category1);
  const pages=ledgerPages(rows);
  reconcileLedgerSelection(rows);
  if(state.ledgerAutoPosition&&ledgerHasDefaultFilters())state.ledgerPage=ledgerCurrentMonthPage(pages);
  state.ledgerAutoPosition=false;
  state.ledgerPage=Math.min(state.ledgerPage,pages.length-1);
  const visible=pages[state.ledgerPage];
  const monthly=ledgerMonthTotals(rows);
  const cumulative=ledgerCumulativeTotals(ledgerRows({...f,month:''}));
  let previousMonth=null;
  const body=visible.map(row=>{
    const key=ledgerMonthKey(row), header=key!==previousMonth?ledgerMonthHeader(key,monthly.get(key),cumulative.get(key)):'';
    previousMonth=key;
    return header+ledgerRowMarkup(row.kind,row.item);
  }).join('');
  const actual=rows.filter(r=>(r.kind==='expense'&&!r.item.deleted)||(r.kind==='plan'&&!r.item.archived)||(r.kind==='loan'&&r.item.payment_date<=state.today)).reduce((sum,r)=>sum+cents(r.kind==='expense'?r.item.amount:r.kind==='plan'?r.item.paid_amount:r.item.total),0n);
  const remaining=rows.filter(r=>(r.kind==='plan'&&!r.item.archived)||(r.kind==='loan'&&r.item.payment_date>state.today)).reduce((sum,r)=>sum+cents(r.kind==='plan'?r.item.remaining_amount:r.item.total),0n);
  const balance=ledgerBalance(rows),balanceClass=balance.difference>0n?'saving':balance.difference<0n?'overrun':'neutral',balanceText=balance.difference>0n?`Экономия ${rub(balance.difference)}`:balance.difference<0n?`Перерасход ${rub(-balance.difference)}`:'Точно по плану';
  $('#view-ledger').innerHTML=`<article class="card"><div class="toolbar"><label class="screen-reader" for="ledger-search">Поиск</label><input id="ledger-search" type="search" placeholder="Найти оплату или план" value="${esc(f.search)}" maxlength="200"><label class="screen-reader" for="ledger-category1">Категория 1</label><select id="ledger-category1" aria-label="Категория 1"><option value="">Все категории 1</option>${roots.map(category=>`<option value="${esc(category.id)}" ${category.id===f.category1?'selected':''}>${esc(category.name)}</option>`).join('')}</select><label class="screen-reader" for="ledger-category2">Категория 2</label><select id="ledger-category2" aria-label="Категория 2" ${f.category1?'':'disabled'}><option value="">Все категории 2</option>${children.map(category=>`<option value="${esc(category.id)}" ${category.id===f.category2?'selected':''}>${esc(category.name)}</option>`).join('')}</select><label class="screen-reader" for="ledger-type">Тип</label><select id="ledger-type"><option value="" ${!f.type?'selected':''}>Все расходы</option><option value="expense" ${f.type==='expense'?'selected':''}>Только оплаты</option><option value="plan" ${f.type==='plan'?'selected':''}>Только планы</option><option value="loan" ${f.type==='loan'?'selected':''}>Только заём</option></select><label class="screen-reader" for="ledger-month">Месяц</label><input id="ledger-month" type="month" value="${f.month}"><label class="filter-label"><input type="checkbox" id="ledger-inactive" ${f.inactive?'checked':''}>Показать удалённые и архивные</label></div>
  ${ledgerBulkControls(rows)}
  <div class="ledger-balance ${balanceClass}"><span>Баланс завершённых позиций</span>${balance.count?`<strong>${balanceText}</strong><small>План ${rub(balance.plan)} − Факт ${rub(balance.fact)} · ${plural(balance.count,'сопоставимая позиция','сопоставимые позиции','сопоставимых позиций')}</small>`:'<strong>Пока нет завершённых позиций</strong><small>Открытые и частично оплаченные планы в экономию не включаются.</small>'}</div>
  ${visible.length?`<div class="table-scroll"><table class="ledger-table"><thead><tr><th><span class="screen-reader">Выбрать план</span></th><th>Дата</th><th>Категория 1</th><th>Категория 2</th><th>Описание</th><th class="num">План</th><th class="num">Факт, цена</th><th class="num">Оплачено</th><th class="num">Осталось</th><th>Состояние</th><th>Примечание</th><th></th></tr></thead><tbody>${body}</tbody></table></div>`:empty('Записи не найдены','Измените фильтры или добавьте первую оплату.','new-expense','Записать оплату')}
  ${pagination('ledger',rows.length,state.ledgerPage,'оплачено '+rub(actual)+' · осталось запланировать '+rub(remaining),pages.length)}<p class="small-summary">Все строки отсортированы по возрастанию даты. Страница заканчивается только между месяцами, поэтому число строк на странице может отличаться. В заголовке каждого месяца показаны суммы оплат и остатка открытых планов. Оплата выбранного плана обновляет его строку.</p></article>`;
}
function rateBadge(row) {
  const labels={actual:'Факт ЦБ',forecast:'Прогноз',manual:'Вручную',imported:'Из Excel',missing:'Нет ставки'};
  return `<span class="badge ${row.rate_kind==='forecast'?'amber':row.rate_kind==='actual'?'paid':'gray'}">${labels[row.rate_kind]||esc(row.rate_kind)}</span>`;
}
function renderLoan() {
  const rows=state.loan;
  if(!rows.length){$('#view-loan').innerHTML=empty('График займа ещё не загружен','После импорта здесь появятся все столбцы A–N из исходной таблицы.','','');return;}
  const latest=[...rows].reverse().find(r=>r.key_rate!==null), forecast=rows.filter(r=>r.rate_kind==='forecast').length, needsRefresh=rows.some(r=>r.rate_kind==='imported'||r.rate_kind==='missing');
  const paidRows=rows.filter(r=>r.payment_date<=state.today), futureRows=rows.filter(r=>r.payment_date>state.today);
  const paidTotal=paidRows.reduce((sum,r)=>sum+cents(r.total),0n), remainingTotal=futureRows.reduce((sum,r)=>sum+cents(r.total),0n);
  const totalPayments=rows.reduce((sum,r)=>sum+cents(r.payment),0n), totalTax=rows.reduce((sum,r)=>sum+cents(r.material_tax),0n);
  $('#view-loan').innerHTML=`<div class="metrics loan-metrics"><article class="metric featured"><p class="metric-label">Исходная сумма займа</p><p class="metric-value">${rub(rows[0].start_balance)}</p><p class="metric-foot">${rows.length} период · ${monthLabel(rows[0].usage_month)}–${monthLabel(rows.at(-1).usage_month)}</p></article><article class="metric"><p class="metric-label">Уже уплачено</p><p class="metric-value">${rub(paidTotal)}</p><p class="metric-foot">${plural(paidRows.length,'взнос','взноса','взносов')} по прошедшим датам · итог N</p></article><article class="metric"><p class="metric-label">Осталось по графику</p><p class="metric-value">${rub(remainingTotal)}</p><p class="metric-foot">${plural(futureRows.length,'будущий взнос','будущих взноса','будущих взносов')} · итог N</p></article><article class="metric"><p class="metric-label">Платежи по графику</p><p class="metric-value">${rub(totalPayments)}</p><p class="metric-foot">Без налога на материальную выгоду</p></article><article class="metric"><p class="metric-label">Налог на матвыгоду</p><p class="metric-value">${rub(totalTax)}</p><p class="metric-foot">Расчёт по ставкам в таблице</p></article><article class="metric"><p class="metric-label">Актуальная ставка</p><p class="metric-value">${latest?Number(latest.key_rate).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:4})+' %':'—'}</p><p class="metric-foot">${needsRefresh?'Данные из Excel · обновите ставки ЦБ':forecast?forecast+' будущих периодов с прогнозом':'Все доступные ставки фактические'}</p></article></div>
  <div class="notice"><span>Для прошедших дат берётся ставка Банка России на дату платежа. Для будущих — последняя известная ставка как прогноз. <a href="https://www.cbr.ru/hd_base/KeyRate/" target="_blank" rel="noreferrer">Источник</a></span><button class="text-button" data-action="refresh-loan-rates">Обновить ставки ЦБ</button></div>
  <article class="card"><div class="table-scroll loan-scroll"><table class="loan-table"><thead><tr><th>A · Месяц займа</th><th class="num">B · Дней в году</th><th class="num">C · Дней в месяце</th><th class="num">D · №</th><th>E · Дата платежа</th><th class="num">F · Ставка ЦБ</th><th class="num">G · Остаток на начало</th><th class="num">H · Проценты</th><th class="num">I · Основной долг</th><th class="num">J · Всего платёж</th><th class="num">K · Остаток на конец</th><th class="num">L · Матвыгода</th><th class="num">M · Налог</th><th class="num">N · Итого</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${monthLabel(r.usage_month)}</td><td class="num">${r.days_in_year}</td><td class="num">${r.days_in_month}</td><td class="num">${r.period_index}</td><td class="date-cell">${dateLabel(r.payment_date)}</td><td class="num"><strong>${r.key_rate===null?'—':Number(r.key_rate).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:4})+' %'}</strong><span class="table-note">${rateBadge(r)}${r.rate_effective_on?' · '+dateLabel(r.rate_effective_on):''}</span></td><td class="num">${rub(r.start_balance)}</td><td class="num">${rub(r.interest)}</td><td class="num">${rub(r.principal)}</td><td class="num bold-num">${rub(r.payment)}</td><td class="num">${rub(r.end_balance)}</td><td class="num">${r.material_benefit===null?'—':rub(r.material_benefit)}</td><td class="num">${r.material_tax===null?'—':rub(r.material_tax)}</td><td class="num bold-num">${rub(r.total)}</td></tr>`).join('')}</tbody></table></div><p class="small-summary">Формулы как в Excel: L = K × ((2/3 × F − 3%) / B × C), M = 35% × L, N = J + M. Итог N входит в общий анализ расходов по дате платежа.</p></article>`;
}
function expenseRows() {
  const f = state.expenseFilters, search = f.search.toLocaleLowerCase('ru');
  return state.expenses.filter(e => e.deleted === f.deleted && categoryMatches(e,f.category) && (!f.month || e.paid_on.startsWith(f.month)) && (!search || [e.description,e.note,e.category].join(' ').toLocaleLowerCase('ru').includes(search)));
}
function renderExpenses() {
  const f = state.expenseFilters, rows = expenseRows();
  state.expensePage = Math.min(state.expensePage, Math.max(0,Math.ceil(rows.length/PAGE_SIZE)-1));
  const visible = rows.slice(state.expensePage*PAGE_SIZE,(state.expensePage+1)*PAGE_SIZE);
  $('#view-expenses').innerHTML = `<article class="card"><div class="toolbar"><label class="screen-reader" for="expense-search">Поиск оплат</label><input id="expense-search" type="search" placeholder="Найти оплату, покупку или примечание" value="${esc(f.search)}" maxlength="200"><label class="screen-reader" for="expense-category">Категория оплат</label><select id="expense-category"><option value="">Все категории</option>${categoryOptions(f.category)}</select><label class="screen-reader" for="expense-month">Месяц оплат</label><input id="expense-month" type="month" value="${f.month}" aria-label="Месяц оплат"><label class="filter-label"><input type="checkbox" id="expense-deleted" ${f.deleted?'checked':''}>Удалённые</label></div>
    ${visible.length ? `<div class="table-scroll"><table><thead><tr><th>Дата</th><th>Описание</th><th>Категория</th><th class="num">Сумма оплаты</th><th><span class="screen-reader">Действия</span></th></tr></thead><tbody>${visible.map(e => `<tr><td class="date-cell">${editableValue('expense',e,'paid_on',dateLabel(e.paid_on))}</td><td>${editableValue('expense',e,'description',e.description,'table-main')}${e.note ? editableValue('expense',e,'note',e.note,'table-note'):''}</td><td>${editableValue('expense',e,'category1',e.category,'badge')}</td><td class="num bold-num">${editableValue('expense',e,'amount',rub(e.amount))}</td><td><div class="row-actions">${e.deleted ? `<button class="text-button" data-action="restore-expense" data-id="${e.id}">Восстановить</button>` : `<button class="text-button" data-action="edit-expense" data-id="${e.id}">Изменить</button>`}</div></td></tr>`).join('')}</tbody></table></div>` : empty(f.deleted?'Нет удалённых оплат':'Оплаты не найдены',f.search||f.month||f.category?'Попробуйте изменить условия поиска.':'Добавьте фактический платёж.',f.deleted?'':'new-expense','Записать оплату')}
    ${pagination('expense',rows.length,state.expensePage,'сумма ' + rub(rows.reduce((s,e)=>s+cents(e.amount),0n)))}</article>`;
}
function planRows() {
  const f=state.planFilters, search=f.search.toLocaleLowerCase('ru');
  return state.plans.filter(p => p.archived===f.archived && categoryMatches(p,f.category) && (!f.month||p.due_month?.startsWith(f.month)) && (!search||[p.title,p.note,p.category].join(' ').toLocaleLowerCase('ru').includes(search)) && (!f.status || (f.status==='undated'&&!p.due_month) || (f.status==='unpriced'&&planAmount(p)===null) || (f.status==='priced'&&planAmount(p)!==null) || (f.status==='overdue'&&p.due_month?.slice(0,7)<state.today.slice(0,7))));
}
function renderPlans() {
  const f=state.planFilters, rows=planRows();
  state.planPage=Math.min(state.planPage,Math.max(0,Math.ceil(rows.length/PAGE_SIZE)-1));
  const visible=rows.slice(state.planPage*PAGE_SIZE,(state.planPage+1)*PAGE_SIZE);
  const statuses=[['','Все состояния'],['priced','С оценкой'],['undated','Без месяца'],['unpriced','Без оценки'],['overdue','Месяц прошёл']];
  $('#view-plans').innerHTML=`<article class="card"><div class="toolbar"><label class="screen-reader" for="plan-search">Поиск планов</label><input id="plan-search" type="search" placeholder="Найти покупку или этап ремонта" value="${esc(f.search)}" maxlength="200"><label class="screen-reader" for="plan-category">Категория планов</label><select id="plan-category"><option value="">Все категории</option>${categoryOptions(f.category)}</select><label class="screen-reader" for="plan-status">Состояние планов</label><select id="plan-status">${statuses.map(([v,l])=>`<option value="${v}" ${f.status===v?'selected':''}>${l}</option>`).join('')}</select><label class="screen-reader" for="plan-month">Месяц планов</label><input id="plan-month" type="month" value="${f.month}" aria-label="Месяц планов"><label class="filter-label"><input type="checkbox" id="plan-archived" ${f.archived?'checked':''}>Архив</label></div>
  ${visible.length ? `<div class="table-scroll"><table><thead><tr><th>Покупка / категория</th><th>Месяц расхода</th><th class="num">План</th><th class="num">Оплачено</th><th class="num">Осталось</th><th>Состояние</th><th><span class="screen-reader">Действия</span></th></tr></thead><tbody>${visible.map(p=>`<tr><td>${editableValue('plan',p,'title',p.title,'table-main')}${editableValue('plan',p,'category1',p.category,'table-note')}</td><td class="date-cell">${editableValue('plan',p,'due_month',monthLabel(p.due_month))}</td><td class="num">${editableValue('plan',p,'expected',p.planned_amount===null?'—':rub(p.planned_amount))}</td><td class="num">${rub(p.paid_amount)}</td><td class="num bold-num">${p.remaining_amount===null?'Не оценено':rub(p.remaining_amount)}</td><td>${planStatus(p)}</td><td><div class="row-actions">${p.archived?'':p.payment_status==='paid'||p.payment_status==='finalized'?'':`<button class="text-button" data-action="record-plan-payment" data-id="${p.id}">Внести сумму</button>`}<button class="text-button" data-action="${p.archived?'restore-plan':'edit-plan'}" data-id="${p.id}">${p.archived?'Вернуть':'Изменить'}</button></div></td></tr>`).join('')}</tbody></table></div>` : empty('Планы не найдены',f.search||f.category||f.status||f.month?'Попробуйте изменить условия поиска.':'Начните с покупки или этапа ремонта. Сумму и месяц можно уточнить позже.',f.archived?'':'new-plan','Добавить план')}
  ${pagination('plan',rows.length,state.planPage,'известный план ' + rub(rows.reduce((s,p)=>s+cents(planAmount(p)),0n)))}<p class="small-summary">Оплата выбранного плана обновляет его строку. Самостоятельные расходы остаются отдельными строками.</p></article>`;
}
function renderCategories() {
  const roots=state.categories.filter(c=>!c.parent_id);
  const usage=c=>`${plural(c.expense_count,'оплата','оплаты','оплат')} · ${plural(c.plan_count,'план','плана','планов')}`;
  $('#view-categories').innerHTML=`<div class="notice"><span>Категорию можно переименовать или удалить. Используемую категорию приложение сначала предложит заменить во всех оплатах и планах.</span><button class="text-button" data-action="new-category">＋ Категория</button></div><div class="category-grid">${roots.map(c=>{const children=state.categories.filter(s=>s.parent_id===c.id);return `<article class="category-card"><h3><button class="text-button category-title" data-action="edit-category" data-id="${c.id}">${esc(c.name)}</button><span>${children.length}</span></h3><p class="category-usage">${usage(c)}</p><ul>${children.length?children.map(s=>`<li><span><strong>${esc(s.name)}</strong><small>${usage(s)}</small></span><span class="category-item-actions"><button class="text-button" data-action="edit-category" data-id="${s.id}">Изменить</button><button class="text-button danger-text" data-action="delete-category" data-id="${s.id}">Удалить</button></span></li>`).join(''):'<li>Можно использовать без подкатегории</li>'}</ul><div class="category-actions"><button class="text-button" data-action="new-category" data-id="${c.id}">＋ Подкатегория</button><button class="text-button" data-action="edit-category" data-id="${c.id}">Изменить</button><button class="text-button danger-text" data-action="delete-category" data-id="${c.id}">Удалить</button></div></article>`;}).join('')}</div>${roots.length?'':empty('Пока нет категорий','Создайте основную категорию, например «Ремонт», а затем добавьте «Работы» и «Черновые материалы».','new-category','Добавить категорию')}`;
}

function field(id, label, input, hint = '', full = false) {
  return `<div class="field${full?' full':''}"><label for="${id}">${label}</label>${input}${hint?`<small>${hint}</small>`:''}</div>`;
}
function categoryFields(categoryId = '') {
  const category=state.categories.find(c=>c.id===categoryId), rootId=category?.parent_id||category?.id||'';
  const roots=state.categories.filter(c=>!c.parent_id);
  const children=state.categories.filter(c=>c.parent_id===rootId);
  return field('category1','Категория 1',`<select id="category1" name="category1" required><option value="">Выберите категорию</option>${roots.map(c=>`<option value="${c.id}" ${c.id===rootId?'selected':''}>${esc(c.name)}</option>`).join('')}</select><button type="button" class="text-button category-create-toggle" data-action="inline-category-toggle" data-level="1">＋ Создать категорию 1</button><div class="inline-category hidden" id="inline-category-1"><input id="inline-category-name-1" maxlength="120" placeholder="Название новой категории"><div><button type="button" class="button primary" data-action="inline-category-save" data-level="1">Создать</button><button type="button" class="text-button" data-action="inline-category-cancel" data-level="1">Отмена</button></div></div>`) + field('category2','Категория 2',`<select id="category2" name="category2" ${children.length?'':'disabled'}><option value="">Без подкатегории</option>${children.map(c=>`<option value="${c.id}" ${c.id===categoryId?'selected':''}>${esc(c.name)}</option>`).join('')}</select><button type="button" class="text-button category-create-toggle" data-action="inline-category-toggle" data-level="2">＋ Создать категорию 2</button><div class="inline-category hidden" id="inline-category-2"><input id="inline-category-name-2" maxlength="120" placeholder="Название новой подкатегории"><div><button type="button" class="button primary" data-action="inline-category-save" data-level="2">Создать</button><button type="button" class="text-button" data-action="inline-category-cancel" data-level="2">Отмена</button></div></div>`);
}
function refreshSubcategories(selected = '') {
  const parent=$('#category1')?.value;
  const child=$('#category2');
  if (!child) return;
  const children=state.categories.filter(c=>c.parent_id===parent);
  child.innerHTML='<option value="">Без подкатегории</option>'+children.map(c=>`<option value="${c.id}" ${c.id===selected?'selected':''}>${esc(c.name)}</option>`).join('');
  child.disabled=!children.length;
}
function selectCategory(id) {
  const category=state.categories.find(c=>c.id===id);
  if (!category) return;
  $('#category1').value=category.parent_id||category.id;
  refreshSubcategories(category.parent_id?category.id:'');
}
function toggleInlineCategory(level, show) {
  const container=$(`#inline-category-${level}`), input=$(`#inline-category-name-${level}`);
  if (!container||!input) return;
  container.classList.toggle('hidden',!show);
  if (show) input.focus(); else input.value='';
}
async function createInlineCategory(level, button) {
  const input=$(`#inline-category-name-${level}`);
  const name=input?.value.trim();
  if (!name) throw new Error('Введите название новой категории.');
  const parentId=level==='2'?$('#category1')?.value:'';
  if (level==='2'&&!parentId) throw new Error('Сначала выберите или создайте категорию 1.');
  button.disabled=true;button.textContent='Создаю…';
  try {
    const created=await mutation('/v1/categories','POST',{name,parent_id:parentId||null});
    state.categories=await request('/v1/categories');
    const root=$('#category1');
    const previousRoot=level==='1'?created.id:parentId;
    root.innerHTML='<option value="">Выберите категорию</option>'+state.categories.filter(c=>!c.parent_id).map(c=>`<option value="${c.id}" ${c.id===previousRoot?'selected':''}>${esc(c.name)}</option>`).join('');
    refreshSubcategories(level==='2'?created.id:'');
    toggleInlineCategory(level,false);
    notify(level==='1'?'Категория 1 создана':'Категория 2 создана');
  } finally {button.disabled=false;button.textContent='Создать';}
}
function formSnapshot() {
  const form=$('#record-form');
  return form ? JSON.stringify([...new FormData(form).entries()]) : '';
}
function dirtyForm() { return !!editing && editing.initial !== formSnapshot(); }
function confirmAction(title,text,label='Подтвердить') {
  return new Promise(resolve=>{
    const dialog=$('#confirm-dialog');
    $('#confirm-title').textContent=title;
    $('#confirm-text').textContent=text;
    $('#confirm-ok').textContent=label;
    const finish=value=>{dialog.close();resolve(value);};
    $('#confirm-ok').onclick=()=>finish(true);
    $('#confirm-cancel').onclick=()=>finish(false);
    dialog.oncancel=event=>{event.preventDefault();finish(false);};
    dialog.showModal();
  });
}
async function closeEditor(force=false) {
  if (!force && dirtyForm() && !await confirmAction('Закрыть без сохранения?','В форме есть изменения, которые ещё не сохранены.','Закрыть')) return false;
  $('#editor').close();
  editing=null;
  return true;
}
function openEditor(kind, item=null, focusField='') {
  if (!state.ready) return;
  editing={kind,item,initial:''};
  const title=kind==='expense'?(item?'Оплата':'Новая оплата'):kind==='plan'?(item?'Покупка / этап':'Новый план'):kind==='plan-payment'?'Оплата по плану':(item?.id?'Категория':'Новая категория');
  $('#editor-title').textContent=title;
  $('#editor-eyebrow').textContent=kind==='expense'?'ФАКТИЧЕСКИЙ РАСХОД':kind==='plan'?'ПЛАНИРОВАНИЕ':kind==='plan-payment'?'ОПЛАТА ПО ПЛАНУ':'СПРАВОЧНИК';
  let fields='';
  if (kind==='expense') {
    const v=item||{amount:'',planned_amount:'',paid_on:state.today,description:'',category_id:'',note:''};
    fields=field('planned_amount','Плановая сумма, ₽',`<input id="planned_amount" name="planned_amount" inputmode="decimal" placeholder="По умолчанию равна оплате" value="${esc(v.planned_amount)}">`,'Сколько планировали потратить на эту позицию.')+
      field('amount','Оплачено, ₽',`<input id="amount" name="amount" inputmode="decimal" required placeholder="Например, 25 000" value="${esc(v.amount)}">`,'Сколько фактически перечислили.')+
      field('paid_on','Точная дата оплаты',`<input id="paid_on" name="paid_on" type="date" required min="2000-01-01" max="2100-12-31" value="${v.paid_on}">`,'Укажите день, когда была перечислена эта часть оплаты.')+
      field('description','Описание расхода',`<input id="description" name="description" required maxlength="500" placeholder="Например, аванс за межкомнатные двери" value="${esc(v.description)}">`,'',true)+categoryFields(v.category_id)+
      field('note','Примечание',`<textarea id="note" name="note" maxlength="4000" placeholder="Магазин, подрядчик или детали платежа">${esc(v.note)}</textarea>`,'',true);
  } else if (kind==='plan') {
    const v=item||{title:'',expected:null,agreed:null,due_month:null,category_id:'',note:''};
    fields=field('title','Покупка или этап ремонта',`<input id="title" name="title" required maxlength="500" placeholder="Например, межкомнатные двери" value="${esc(v.title)}">`,'',true)+
      field('expected','Плановая стоимость, ₽',`<input id="expected" name="expected" inputmode="decimal" placeholder="Пока неизвестно" value="${esc(v.expected)}">`,'Предварительная оценка всей покупки.')+
      field('agreed','Фактическая цена, ₽',`<input id="agreed" name="agreed" inputmode="decimal" placeholder="Ещё не согласована" value="${esc(v.agreed)}">`,'Полная цена по счёту или договорённости.')+
      field('due_month','Месяц оплаты',`<input id="due_month" name="due_month" type="month" min="2000-01" max="2100-12" value="${v.due_month?.slice(0,7)||''}">`,'Можно оставить пустым и распределить позже.',true)+categoryFields(v.category_id)+
      field('note','Примечание',`<textarea id="note" name="note" maxlength="4000" placeholder="Что входит в стоимость, сроки, поставщик">${esc(v.note)}</textarea>`,'',true);
  } else if (kind==='plan-payment') {
    fields=field('plan_title','План',`<input id="plan_title" value="${esc(item.title)}" disabled>`,'',true)+
      field('amount','Новая оплата, ₽',`<input id="amount" name="amount" inputmode="decimal" required placeholder="Например, 25 000">`,'Сумма будет добавлена к уже оплаченному: '+rub(item.paid_amount)+'.')+
      field('paid_on','Дата оплаты',`<input id="paid_on" name="paid_on" type="date" required min="2000-01-01" max="2100-12-31" value="${state.today}">`,'Сохраняется как последняя дата оплаты.')+
      field('finalize','',`<label class="filter-label"><input id="finalize" type="checkbox"> Больше оплат не будет — финализировать</label>`,'После финализации остаток не попадёт в прогноз.',true);
  } else {
    const parentId=item?.parent_id||'';
    const parent=state.categories.find(c=>c.id===parentId);
    fields=(item?.id?field('category_group','Группа',`<input id="category_group" value="${esc(parent?.name||'Основная категория')}" disabled>`,'Уровень категории после создания не меняется.',true):field('parent_id','Группа',`<select id="parent_id" name="parent_id"><option value="">Новая основная категория</option>${state.categories.filter(c=>!c.parent_id).map(c=>`<option value="${c.id}" ${c.id===parentId?'selected':''}>Подкатегория: ${esc(c.name)}</option>`).join('')}</select>`,'',true))+field('name','Название',`<input id="name" name="name" required maxlength="120" placeholder="Например, Материалы" value="${esc(item?.name||'')}">`,'',true);
  }
  const readOnly=!!item&&(item.deleted||item.archived);
  const categoryRemoval=kind==='category'&&item?`<div class="category-danger"><h3>Удаление и замена</h3>${item.child_count?'<p>У этой группы есть подкатегории. Сначала удалите или замените их.</p>':item.expense_count||item.plan_count?`<p>Категория используется: ${plural(item.expense_count,'оплата','оплаты','оплат')} и ${plural(item.plan_count,'план','плана','планов')}. Выберите, куда их перенести.</p><select id="replacement_category"><option value="">Выберите категорию для замены</option>${state.categories.filter(c=>c.id!==item.id&&!c.child_count).map(c=>`<option value="${c.id}">${esc(c.path)}</option>`).join('')}</select><button type="button" class="button danger" data-action="remove-category" data-id="${item.id}">Заменить везде и удалить</button>`:'<p>Категория нигде не используется.</p><button type="button" class="button danger" data-action="remove-category" data-id="'+item.id+'">Удалить категорию</button>'}</div>`:'';
  $('#editor-content').innerHTML=`<form id="record-form" class="editor-form"><div id="form-error" class="form-error hidden" role="alert"></div><div class="form-grid">${fields}</div>
    ${categoryRemoval}
    <div class="form-actions">${item?.id&&kind!=='category'&&kind!=='plan-payment'?`<button type="button" class="text-button danger-text delete-button" data-action="${kind==='expense'?(item.deleted?'restore-expense':'delete-expense'):'delete-plan'}" data-id="${item.id}">${kind==='expense'?(item.deleted?'Восстановить':'Удалить'):'Удалить'}</button>`:''}<button type="button" class="button secondary" data-action="close-dialog">${readOnly?'Закрыть':'Отмена'}</button>${readOnly?'':'<button type="submit" class="button primary" id="save-record">Сохранить</button>'}</div></form>`;
  if (readOnly) $('#record-form').querySelectorAll('input,textarea,select,.category-create-toggle,.inline-category button').forEach(el=>el.disabled=true);
  if(kind==='expense'&&!item){
    const planned=$('#planned_amount'),amount=$('#amount');let plannedTouched=false;
    planned.addEventListener('input',()=>{plannedTouched=true;});
    amount.addEventListener('input',()=>{if(!plannedTouched)planned.value=amount.value;});
  }
  editing.initial=formSnapshot();
  $('#editor').showModal();
  if(focusField&&!readOnly)requestAnimationFrame(()=>{
    const control=document.getElementById(focusField);
    if(!control||control.disabled)return;
    control.focus({preventScroll:true});
    control.scrollIntoView({block:'center'});
    if(control instanceof HTMLInputElement&&['text','search'].includes(control.type))control.select();
  });
}
function showFormError(error) {
  const box=$('#form-error');
  if (!box) {showError(error);return;}
  box.textContent=error.message;
  box.classList.remove('hidden');
  box.scrollIntoView({block:'nearest'});
}
async function mutation(path,method,data) {
  const body=JSON.stringify(data), fingerprint=method+path+body;
  if (!pendingRequest||pendingRequest.fingerprint!==fingerprint) pendingRequest={fingerprint,key:requestKey()};
  const result=await request(path,{method,body,headers:{'Idempotency-Key':pendingRequest.key}});
  pendingRequest=null;
  return result;
}
async function saveForm(event) {
  event.preventDefault();
  if (!editing) return;
  const current=editing, button=$('#save-record');
  if (button.disabled) return;
  button.disabled=true;button.textContent='Сохраняю…';
  $('#form-error').classList.add('hidden');
  try {
    let data,path,method=current.item?.id?'PUT':'POST';
    if (current.kind==='category') {
      data={name:$('#name').value.trim()};
      if(current.item?.id){path='/v1/categories/'+current.item.id;method='PUT';}
      else{data.parent_id=$('#parent_id')?.value||current.item?.parent_id||null;path='/v1/categories';method='POST';}
    } else if (current.kind==='plan-payment') {
      data={amount:moneyInput($('#amount').value,true),paid_on:$('#paid_on').value,version:current.item.version,finalize:$('#finalize').checked};
      path='/v1/plans/'+current.item.id+'/payment';method='POST';
    } else {
      const category=$('#category2').value||$('#category1').value;
      if (!category) throw new Error('Выберите категорию расхода.');
      data=current.kind==='expense'?{
        amount:moneyInput($('#amount').value,true),planned_amount:moneyInput($('#planned_amount').value)||moneyInput($('#amount').value,true),paid_on:$('#paid_on').value,
        description:$('#description').value.trim(),note:$('#note').value.trim(),
        category_id:category,
      }:{
        title:$('#title').value.trim(),expected:moneyInput($('#expected').value),agreed:moneyInput($('#agreed').value),
        due_month:$('#due_month').value?$('#due_month').value+'-01':null,category_id:category,note:$('#note').value.trim(),
      };
      path='/v1/'+(current.kind==='expense'?'expenses':'plans')+(current.item?.id?'/'+current.item.id:'');
      if (current.item?.id) data.version=current.item.version;
    }
    await mutation(path,method,data);
    await closeEditor(true);
    notify(current.kind==='expense'||current.kind==='plan-payment'?'Оплата сохранена':current.kind==='plan'?'План сохранён':current.item?'Категория переименована':'Категория добавлена');
    try { await loadData(); } catch(error) { showError(new Error('Запись сохранена, но обновить список не удалось. Нажмите «Обновить список». '+error.message)); }
  } catch(error) {showFormError(error);}
  finally {button.disabled=false;button.textContent='Сохранить';}
}
async function toggleRecord(action,id) {
  const isExpense=action.endsWith('expense'), item=(isExpense?state.expenses:state.plans).find(i=>i.id===id);
  if (!item) throw new Error('Запись не найдена. Обновите данные.');
  const restoring=action.startsWith('restore');
  if (!restoring&&!await confirmAction(isExpense?'Удалить оплату?':'Перенести план в архив?',isExpense?'Оплата исчезнет из итогов. Её можно будет восстановить.':'План исчезнет из прогноза. Фактические оплаты останутся в истории.',isExpense?'Удалить':'В архив')) return;
  const endpoint=isExpense?(restoring?'restore':'delete'):(restoring?'restore':'archive');
  try {
    await mutation(`/v1/${isExpense?'expenses':'plans'}/${id}/${endpoint}`,'POST',{version:item.version});
    if ($('#editor').open) await closeEditor(true);
    notify(restoring?'Запись восстановлена':isExpense?'Оплата удалена':'План в архиве');
    await loadData();
  } catch(error) {if($('#editor').open)showFormError(error);else showError(error);}
}
async function deletePlan(id) {
  const item=state.plans.find(plan=>plan.id===id);
  if(!item)throw new Error('План не найден. Обновите данные.');
  if(!await confirmAction('Удалить план?','План будет удалён без возможности восстановления. Фактические оплаты не изменятся.','Удалить'))return;
  try{
    await mutation(`/v1/plans/${id}`,'DELETE',{version:item.version});
    if($('#editor').open)await closeEditor(true);
    notify('План удалён');
    await loadData();
  }catch(error){if($('#editor').open)showFormError(error);else showError(error);}
}
async function removeCategory(id) {
  const item=state.categories.find(c=>c.id===id);
  if(!item)throw new Error('Категория не найдена. Обновите данные.');
  if(item.child_count)throw new Error('Сначала удалите или замените подкатегории.');
  const used=item.expense_count||item.plan_count;
  if(used){
    const target=$('#replacement_category')?.value;
    if(!target)throw new Error('Выберите категорию, на которую нужно заменить старую.');
    const replacement=state.categories.find(c=>c.id===target);
    if(!await confirmAction('Заменить категорию?',`Во всех ${item.expense_count} оплатах и ${item.plan_count} планах «${item.path}» будет заменена на «${replacement.path}». Старая категория будет удалена.`,'Заменить'))return;
    await mutation(`/v1/categories/${id}/replace`,'POST',{target_id:target});
    notify('Категория заменена во всех записях');
  }else{
    if(!await confirmAction('Удалить категорию?',`Категория «${item.path}» нигде не используется и будет удалена.`,'Удалить'))return;
    await mutation(`/v1/categories/${id}`,'DELETE',{});
    notify('Категория удалена');
  }
  await closeEditor(true);await loadData();
}
async function applyCategorySuggestion(kind,id,categoryId) {
  const item=(kind==='expense'?state.expenses:state.plans).find(value=>value.id===id);
  if(!item)throw new Error('Запись не найдена. Обновите список.');
  const data=kind==='expense'?{amount:item.amount,planned_amount:item.planned_amount,paid_on:item.paid_on,description:item.description,note:item.note,category_id:categoryId,version:item.version}:{title:item.title,expected:item.expected,agreed:item.agreed,due_month:item.due_month,category_id:categoryId,note:item.note,version:item.version};
  await mutation(`/v1/${kind==='expense'?'expenses':'plans'}/${id}`,'PUT',data);
  try{localStorage.removeItem(recommendationKey(kind,item,categoryId));}catch{}
  notify('Категория назначена');
  await loadData();
}
function dismissCategorySuggestion(kind,id,categoryId) {
  const item=(kind==='expense'?state.expenses:state.plans).find(value=>value.id===id);
  if(!item)return;
  try{localStorage.setItem(recommendationKey(kind,item,categoryId),'1');}catch{}
  renderLedger();
  notify('Рекомендация скрыта');
}
function setPlanFilter(status) {
  state.ledgerFilters={search:'',category1:'',category2:'',month:'',type:'plan',inactive:false};
  state.ledgerPage=0;
  location.hash='ledger';
  navigate('ledger');
}
async function handleAction(event) {
  const target=event.target.closest('[data-action]');
  if (!target||target.disabled) return;
  const {action,id}=target.dataset;
  if (!state.ready&&action!=='refresh') return;
  try {
    if (action==='new-expense') openEditor('expense');
    else if (action==='new-plan') openEditor('plan');
    else if (action==='new-category') openEditor('category',id?{parent_id:id}:null);
    else if (action==='inline-category-toggle') toggleInlineCategory(target.dataset.level,true);
    else if (action==='inline-category-cancel') toggleInlineCategory(target.dataset.level,false);
    else if (action==='inline-category-save') {try{await createInlineCategory(target.dataset.level,target);}catch(error){showFormError(error);}}
    else if (action==='edit-category') openEditor('category',state.categories.find(c=>c.id===id));
    else if (action==='delete-category') {openEditor('category',state.categories.find(c=>c.id===id));setTimeout(()=>$('.category-danger')?.scrollIntoView({behavior:'smooth',block:'center'}));}
    else if (action==='remove-category') {try{await removeCategory(id);}catch(error){showFormError(error);}}
    else if (action==='accept-category') await applyCategorySuggestion(target.dataset.kind,id,target.dataset.categoryId);
    else if (action==='dismiss-category') dismissCategorySuggestion(target.dataset.kind,id,target.dataset.categoryId);
    else if (action==='edit-expense') openEditor('expense',state.expenses.find(e=>e.id===id),target.dataset.field);
    else if (action==='edit-plan') openEditor('plan',state.plans.find(p=>p.id===id),target.dataset.field);
    else if (action==='record-plan-payment') recordPlanPayment(state.plans.find(p=>p.id===id));
    else if (action==='select-filtered-plans') {state.ledgerSelectedPlanIds=new Set(selectablePlanRows(ledgerRows()).map(row=>row.item.id));renderLedger();}
    else if (action==='clear-selected-plans') {state.ledgerSelectedPlanIds.clear();renderLedger();}
    else if (action==='bulk-move-plans') await bulkMovePlans();
    else if (action==='close-dialog') await closeEditor();
    else if (action==='delete-plan') await deletePlan(id);
    else if (['delete-expense','restore-expense','archive-plan','restore-plan'].includes(action)) await toggleRecord(action,id);
    else if (action==='page') {
      state[target.dataset.kind+'Page']=Number(target.dataset.page);
      render();
    } else if (action==='undated'||action==='overdue') setPlanFilter(action);
    else if (action==='logout') {
      await request('/web/logout',{method:'POST'}); location.assign('/login');
    }
    else if (action==='refresh') {
      target.disabled=true;
      try {if(state.today)await loadData();else await start();notify('Данные обновлены');} finally {target.disabled=false;}
    } else if (action==='refresh-loan-rates') {
      if(!await confirmAction('Обновить ставки?','Прошедшие периоды получат ставку ЦБ на дату платежа, будущие — последнюю известную ставку как прогноз.','Обновить'))return;
      target.disabled=true;
      try{const result=await mutation('/v1/loan/key-rates','POST',{});await loadData();notify(`Ставки обновлены: ${result.actual} фактических, ${result.forecast} прогнозных`);}finally{target.disabled=false;}
    } else if (action==='chart-loan-mode') {
      state.chartLoanMode=target.dataset.mode==='without'?'without':'with';
      renderOverview();
    } else if (action==='move-period') {
      state.start=addMonths(state.today,-1);state.end=state.moveMonth>state.start?state.moveMonth:addMonths(state.start,11);
      state.report=null;renderOverview();await loadReport();
    }
  } catch(error) {showError(error);}
}
let searchTimer;
function updateFilter(event) {
  const id=event.target.id;
  const ledger=id.match(/^ledger-(search|category1|category2|month|type|inactive)$/);
  if(ledger){const key=ledger[1];state.ledgerFilters[key]=event.target.type==='checkbox'?event.target.checked:event.target.value;if(key==='category1')state.ledgerFilters.category2='';state.ledgerSelectedPlanIds.clear();state.ledgerPage=0;const restore=()=>{const selection=event.target.selectionStart,active=document.activeElement===event.target;renderLedger();if(active){const replacement=document.getElementById(id);replacement.focus();if(replacement.type==='search')replacement.setSelectionRange(selection,selection);}};clearTimeout(searchTimer);if(key==='search')searchTimer=setTimeout(restore,220);else restore();return true;}
  const matches=id.match(/^(expense|plan)-(search|category|month|deleted|archived|status)$/);
  if (!matches) return false;
  const [,kind,key]=matches;
  state[kind+'Filters'][key]=event.target.type==='checkbox'?event.target.checked:event.target.value;
  state[kind+'Page']=0;
  const restore=()=>{
    const selection=event.target.selectionStart;
    const active=document.activeElement===event.target;
    (kind==='expense'?renderExpenses:renderPlans)();
    if(active){const replacement=document.getElementById(id);replacement.focus();if(replacement.type==='search')replacement.setSelectionRange(selection,selection);}
  };
  clearTimeout(searchTimer);
  if(key==='search') searchTimer=setTimeout(restore,220);else restore();
  return true;
}
document.addEventListener('click',handleAction);
document.addEventListener('pointermove',updateChartTooltip);
document.addEventListener('scroll',hideChartTooltip,true);
document.addEventListener('submit',event=>{if(event.target.id==='record-form')saveForm(event);});
document.addEventListener('input',event=>{if(event.target.type==='search')updateFilter(event);});
document.addEventListener('change',async event=>{
  if(event.target.matches('.ledger-plan-select')){
    const id=event.target.dataset.planId;
    if(event.target.checked)state.ledgerSelectedPlanIds.add(id);else state.ledgerSelectedPlanIds.delete(id);
    renderLedger();
    return;
  }
  if(updateFilter(event))return;
  if(event.target.id==='category1')refreshSubcategories();
  if(event.target.id==='report-start'||event.target.id==='report-end'){
    const start=$('#report-start').value,end=$('#report-end').value;
    if(!start||!end||start>end){showError(new Error('Выберите период: начало должно быть не позже конца.'));return;}
    const span=(Number(end.slice(0,4))-Number(start.slice(0,4)))*12+Number(end.slice(5))-Number(start.slice(5));
    if(span>35){showError(new Error('Для графика выберите период до 36 месяцев.'));return;}
    state.start=start;state.end=end;state.report=null;$('#global-error').classList.add('hidden');renderOverview();await loadReport();
  }
});
$('#editor').addEventListener('cancel',event=>{event.preventDefault();closeEditor();});
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
window.addEventListener('beforeunload',event=>{if(dirtyForm()){event.preventDefault();event.returnValue='';}});
async function start() {
  navigate(location.hash.slice(1));
  try {
    const data=await request('/web/bootstrap');
    state.today=data.today;state.moveMonth=data.move_month;
    state.start=addMonths(state.today,-1);state.end=state.moveMonth>state.start?state.moveMonth:addMonths(state.start,11);
    const existing=await request('/v1/categories');
    if(!existing.length)await request('/v1/categories/seed',{method:'POST'});
    await loadData();
  } catch(error){$('#loading').classList.add('hidden');showError(error);}
}
initSidebar();
initTheme();
start();
