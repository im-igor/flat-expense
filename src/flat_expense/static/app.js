'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state = {categories: [], expenses: [], plans: [], today: '', moveMonth: '2027-08', view: 'overview', ready: false, report: null, reportSequence: 0, loadSequence: 0, start: '', end: '', expensePage: 0, planPage: 0, expenseFilters: {search:'', category:'', month:'', deleted:false}, planFilters: {search:'', category:'', month:'', status:'', archived:false}};
let editing = null;
let pendingRequest = null;
let toastTimer;
const PAGE_SIZE = 15;
const monthFormatter = new Intl.DateTimeFormat('ru-RU', {month:'short', year:'2-digit', timeZone:'UTC'});
const numberFormatter = new Intl.NumberFormat('ru-RU');

function cents(value) {
  if (value === null || value === undefined || value === '') return 0n;
  const [whole, fraction = ''] = String(value).split('.');
  return BigInt(whole) * 100n + BigInt((fraction + '00').slice(0, 2));
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
        const names = {amount:'сумму оплаты', expected:'плановую сумму', agreed:'фактическую цену', description:'описание', title:'название', paid_on:'дату оплаты', note:'примечание'};
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
  const [categories, expenses, deleted, plans, archived] = await Promise.all([
    request('/v1/categories'), pages('/v1/expenses'), pages('/v1/expenses?deleted=true'), pages('/v1/plans'), pages('/v1/plans?archived=true')
  ]);
  if (sequence !== state.loadSequence) return;
  state.categories = categories;
  state.expenses = [...expenses, ...deleted];
  state.plans = [...plans, ...archived];
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
function planById(id) { return state.plans.find(p => p.id === id); }
function planStatus(plan) {
  if (plan.archived) return '<span class="badge gray">В архиве</span>';
  if (plan.remaining === null) return '<span class="badge amber">Без оценки</span>';
  if (cents(plan.remaining) === 0n) return `<span class="badge paid">${cents(plan.overpaid) > 0n ? 'Переплата' : 'Оплачено'}</span>`;
  if (plan.due_month && plan.due_month.slice(0,7) < state.today.slice(0,7)) return '<span class="badge amber">Перенести месяц</span>';
  return `<span class="badge">${cents(plan.paid) > 0n ? 'Частично оплачено' : 'В плане'}</span>`;
}
function navigate(view) {
  state.view = ['overview','expenses','plans','categories'].includes(view) ? view : 'overview';
  document.querySelectorAll('nav a').forEach(a => {
    const active = a.dataset.view === state.view;
    a.classList.toggle('active', active);
    if (active) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  const titles = {overview:['Обзор','Понимать, сколько уже потрачено и что ещё впереди.'], expenses:['Оплаты','Каждый платёж — со своей датой, суммой и покупкой.'], plans:['Планы покупок','Оценка, согласованная цена, оплаченная часть и остаток.'], categories:['Категории','Два уровня, чтобы расходы всегда были на своём месте.']};
  $('#page-title').textContent = titles[state.view][0];
  $('#page-subtitle').textContent = titles[state.view][1];
  render();
}
function render() {
  document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
  if (!state.ready) return;
  $(`#view-${state.view}`).classList.remove('hidden');
  ({overview:renderOverview, expenses:renderExpenses, plans:renderPlans, categories:renderCategories})[state.view]();
}
function metrics() {
  const expenses = state.expenses.filter(e => !e.deleted);
  const plans = state.plans.filter(p => !p.archived);
  const paid = expenses.reduce((sum,e) => sum + cents(e.amount), 0n);
  const remaining = plans.reduce((sum,p) => sum + cents(p.remaining), 0n);
  const unpriced = plans.filter(p => p.remaining === null).length;
  const month = state.today.slice(0,7);
  const currentActual = expenses.filter(e => e.paid_on.startsWith(month)).reduce((s,e) => s + cents(e.amount), 0n);
  const currentPlanned = plans.filter(p => p.due_month?.startsWith(month)).reduce((s,p) => s + cents(p.remaining), 0n);
  return `<div class="metrics">
    <article class="metric featured"><span class="metric-mark" aria-hidden="true">↗</span><p class="metric-label">Уже оплачено</p><p class="metric-value">${rub(paid)}</p><p class="metric-foot">${plural(expenses.length,'платёж','платежа','платежей')} за всё время</p></article>
    <article class="metric"><span class="metric-mark" aria-hidden="true">◷</span><p class="metric-label">Осталось по планам</p><p class="metric-value">${rub(remaining)}</p><p class="metric-foot">Все известные остатки, включая без даты</p></article>
    <article class="metric"><span class="metric-mark" aria-hidden="true">◫</span><p class="metric-label">${esc(monthLabel(month))} · факт + остаток</p><p class="metric-value">${rub(currentActual + currentPlanned)}</p><p class="metric-foot">Из них оплачено ${rub(currentActual)}</p></article>
    <article class="metric"><span class="metric-mark" aria-hidden="true">?</span><p class="metric-label">Покупки без оценки</p><p class="metric-value">${unpriced}</p><p class="metric-foot">Пока не включены в денежные итоги</p></article>
  </div>`;
}
function chartMarkup(report) {
  const months = report.months;
  const width = Math.max(720, months.length * 60 + 70), height = 245, left = 62, top = 15, bottom = 42, plot = height - top - bottom;
  const max = Math.max(1, ...months.map(m => Number(cents(m.known_total)) / 100));
  const scale = max * 1.15;
  const step = (width - left - 18) / months.length;
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Оплачено и оставшиеся расходы по месяцам. Точные суммы доступны в таблице ниже.">`;
  for (let i = 0; i <= 3; i++) {
    const value = scale * i / 3, y = height - bottom - plot * i / 3;
    const label = value >= 1e6 ? (value / 1e6).toLocaleString('ru-RU',{maximumFractionDigits:1}) + ' млн' : value >= 1e3 ? Math.round(value / 1e3).toLocaleString('ru-RU') + ' тыс' : Math.round(value).toLocaleString('ru-RU');
    svg += `<line class="tick" x1="${left}" y1="${y}" x2="${width - 10}" y2="${y}"/><text x="${left - 12}" y="${y + 3}" text-anchor="end">${label}</text>`;
  }
  months.forEach((m,i) => {
    const actual = Number(cents(m.actual)) / 100, planned = Number(cents(m.planned_remaining)) / 100;
    const bar = Math.min(32, step * .52), x = left + step * i + (step - bar) / 2, ah = plot * actual / scale, ph = plot * planned / scale;
    svg += `<g class="period-bar"><title>${esc(monthLabel(m.month))}: оплачено ${rub(m.actual)}, осталось ${rub(m.planned_remaining)}${m.unpriced ? ', без оценки: ' + m.unpriced : ''}</title><rect class="actual-bar" x="${x}" y="${height-bottom-ah}" width="${bar}" height="${ah}" rx="3"/><rect class="planned-bar" x="${x}" y="${height-bottom-ah-ph}" width="${bar}" height="${ph}" rx="3"/><text x="${x+bar/2}" y="${height-18}" text-anchor="middle">${esc(monthLabel(m.month).replace(' г.',''))}</text></g>`;
  });
  return svg + '</svg>';
}
function renderOverview() {
  const plans = state.plans.filter(p => !p.archived && p.remaining !== '0.00');
  const undated = plans.filter(p => !p.due_month);
  const overdue = plans.filter(p => p.due_month && p.due_month.slice(0,7) < state.today.slice(0,7));
  const upcoming = [...plans].sort((a,b) => (a.due_month || '9999').localeCompare(b.due_month || '9999')).slice(0,4);
  const recent = state.expenses.filter(e => !e.deleted).slice(0,4);
  const report = state.report;
  const notices = [];
  if (undated.length) notices.push(`<span>${plural(undated.length,'покупка','покупки','покупок')} без месяца оплаты. Распределите их, чтобы увидеть полный прогноз.</span><button class="text-button" data-action="undated">Распределить →</button>`);
  if (overdue.length) notices.push(`<span>У ${overdue.length} планов месяц уже прошёл, но остался неоплаченный остаток или неизвестная цена.</span><button class="text-button" data-action="overdue">Проверить →</button>`);
  $('#view-overview').innerHTML = metrics() + notices.map(n => `<div class="notice">${n}</div>`).join('') + `
    <article class="card"><div class="card-head"><div><h2>Расходы по месяцам</h2><p>Оплаченная часть и то, что ещё предстоит оплатить</p></div><div class="chart-tools"><label class="screen-reader" for="report-start">Начало периода</label><input type="month" id="report-start" value="${state.start}" min="2000-01" max="2100-12"><span>—</span><label class="screen-reader" for="report-end">Конец периода</label><input type="month" id="report-end" value="${state.end}" min="2000-01" max="2100-12"></div></div>
      <div class="chart-wrap">${report ? chartMarkup(report) : '<div class="empty-small">Загружаю прогноз…</div>'}</div><div class="chart-legend"><span><i class="legend-dot"></i>Оплачено</span><span><i class="legend-dot planned"></i>Осталось по плану</span><button class="text-button" data-action="move-period">До переезда</button></div>
      ${report ? `<details class="month-details"><summary>Точные суммы по месяцам</summary><div class="table-scroll"><table><thead><tr><th>Месяц</th><th class="num">Оплачено</th><th class="num">Остаток плана</th><th class="num">Известный итог</th><th class="num">Без оценки</th></tr></thead><tbody>${report.months.map(m => `<tr><td>${esc(monthLabel(m.month))}${m.overdue_plan ? ' <span class="badge amber">Месяц прошёл</span>' : ''}</td><td class="num">${rub(m.actual)}</td><td class="num">${rub(m.planned_remaining)}</td><td class="num bold-num">${rub(m.known_total)}</td><td class="num">${m.unpriced || '—'}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
    </article>
    <div class="overview-lower"><article class="card"><div class="card-head"><div><h2>Ближайшие планы</h2><p>Остатки по покупкам и этапам ремонта</p></div><span class="badge">${plans.length} открытых</span></div><div class="compact-list">${upcoming.length ? upcoming.map(p => `<button class="list-row list-click" data-action="edit-plan" data-id="${p.id}"><span class="list-symbol" aria-hidden="true">▤</span><span class="list-body"><strong>${esc(p.title)}</strong><small>${esc(p.category)}</small></span><span class="list-amount">${rub(p.remaining)}<small>${esc(monthLabel(p.due_month))}</small></span></button>`).join('') : '<div class="empty-small">Пока нет открытых планов. Добавьте первую будущую покупку.</div>'}</div><div class="card-tail"><a class="text-button" href="#plans">Все планы →</a></div></article>
    <article class="card"><div class="card-head"><div><h2>Последние оплаты</h2><p>Фактические расходы по датам платежей</p></div></div><div class="compact-list">${recent.length ? recent.map(e => `<button class="list-row list-click" data-action="edit-expense" data-id="${e.id}"><span class="list-symbol" aria-hidden="true">↗</span><span class="list-body"><strong>${esc(e.description)}</strong><small>${esc(e.category)}</small></span><span class="list-amount">${rub(e.amount)}<small>${dateLabel(e.paid_on)}</small></span></button>`).join('') : '<div class="empty-small">Здесь появятся ваши платежи. Начните с кнопки «Записать оплату».</div>'}</div><div class="card-tail"><a class="text-button" href="#expenses">Все оплаты →</a></div></article></div>`;
}
function pagination(kind, count, page, totalText) {
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  return `<div class="table-foot"><span>${plural(count,'запись','записи','записей')}${totalText ? ' · ' + totalText : ''}</span><div class="pagination"><button class="text-button" data-action="page" data-kind="${kind}" data-page="${page-1}" ${page===0?'disabled':''}>← Назад</button><span>${page+1} / ${pages}</span><button class="text-button" data-action="page" data-kind="${kind}" data-page="${page+1}" ${page+1>=pages?'disabled':''}>Далее →</button></div></div>`;
}
function expenseRows() {
  const f = state.expenseFilters, search = f.search.toLocaleLowerCase('ru');
  return state.expenses.filter(e => e.deleted === f.deleted && categoryMatches(e,f.category) && (!f.month || e.paid_on.startsWith(f.month)) && (!search || [e.description,e.note,e.category,planById(e.plan_id)?.title].join(' ').toLocaleLowerCase('ru').includes(search)));
}
function renderExpenses() {
  const f = state.expenseFilters, rows = expenseRows();
  state.expensePage = Math.min(state.expensePage, Math.max(0,Math.ceil(rows.length/PAGE_SIZE)-1));
  const visible = rows.slice(state.expensePage*PAGE_SIZE,(state.expensePage+1)*PAGE_SIZE);
  $('#view-expenses').innerHTML = `<article class="card"><div class="toolbar"><label class="screen-reader" for="expense-search">Поиск оплат</label><input id="expense-search" type="search" placeholder="Найти оплату, покупку или примечание" value="${esc(f.search)}" maxlength="200"><label class="screen-reader" for="expense-category">Категория оплат</label><select id="expense-category"><option value="">Все категории</option>${categoryOptions(f.category)}</select><label class="screen-reader" for="expense-month">Месяц оплат</label><input id="expense-month" type="month" value="${f.month}" aria-label="Месяц оплат"><label class="filter-label"><input type="checkbox" id="expense-deleted" ${f.deleted?'checked':''}>Удалённые</label></div>
    ${visible.length ? `<div class="table-scroll"><table><thead><tr><th>Дата</th><th>Описание / покупка</th><th>Категория</th><th class="num">Сумма оплаты</th><th><span class="screen-reader">Действия</span></th></tr></thead><tbody>${visible.map(e => `<tr><td class="date-cell">${dateLabel(e.paid_on)}</td><td><button class="text-button table-main" data-action="edit-expense" data-id="${e.id}">${esc(e.description)}</button>${e.plan_id ? `<span class="table-note">В счёт: ${esc(planById(e.plan_id)?.title || 'покупки')}</span>` : '<span class="table-note">Самостоятельный расход</span>'}${e.note ? `<span class="table-note">${esc(e.note)}</span>`:''}</td><td><span class="badge">${esc(e.category)}</span></td><td class="num bold-num">${rub(e.amount)}</td><td><div class="row-actions">${e.deleted ? `<button class="text-button" data-action="restore-expense" data-id="${e.id}">Восстановить</button>` : `<button class="text-button" data-action="edit-expense" data-id="${e.id}">Изменить</button>`}</div></td></tr>`).join('')}</tbody></table></div>` : empty(f.deleted?'Нет удалённых оплат':'Оплаты не найдены',f.search||f.month||f.category?'Попробуйте изменить условия поиска.':'Добавьте платёж. Его можно связать с покупкой и автоматически уменьшить остаток.',f.deleted?'':'new-expense','Записать оплату')}
    ${pagination('expense',rows.length,state.expensePage,'сумма ' + rub(rows.reduce((s,e)=>s+cents(e.amount),0n)))}</article>`;
}
function planRows() {
  const f=state.planFilters, search=f.search.toLocaleLowerCase('ru');
  return state.plans.filter(p => p.archived===f.archived && categoryMatches(p,f.category) && (!f.month||p.due_month?.startsWith(f.month)) && (!search||[p.title,p.note,p.category].join(' ').toLocaleLowerCase('ru').includes(search)) && (!f.status || (f.status==='undated'&&!p.due_month&&p.remaining!=='0.00') || (f.status==='unpriced'&&p.remaining===null) || (f.status==='open'&&p.remaining!=='0.00') || (f.status==='paid'&&p.remaining==='0.00') || (f.status==='overdue'&&p.due_month?.slice(0,7)<state.today.slice(0,7)&&p.remaining!=='0.00')));
}
function renderPlans() {
  const f=state.planFilters, rows=planRows();
  state.planPage=Math.min(state.planPage,Math.max(0,Math.ceil(rows.length/PAGE_SIZE)-1));
  const visible=rows.slice(state.planPage*PAGE_SIZE,(state.planPage+1)*PAGE_SIZE);
  const statuses=[['','Все состояния'],['open','Есть остаток'],['undated','Без месяца'],['unpriced','Без оценки'],['overdue','Месяц прошёл'],['paid','Оплачено']];
  $('#view-plans').innerHTML=`<article class="card"><div class="toolbar"><label class="screen-reader" for="plan-search">Поиск планов</label><input id="plan-search" type="search" placeholder="Найти покупку или этап ремонта" value="${esc(f.search)}" maxlength="200"><label class="screen-reader" for="plan-category">Категория планов</label><select id="plan-category"><option value="">Все категории</option>${categoryOptions(f.category)}</select><label class="screen-reader" for="plan-status">Состояние планов</label><select id="plan-status">${statuses.map(([v,l])=>`<option value="${v}" ${f.status===v?'selected':''}>${l}</option>`).join('')}</select><label class="screen-reader" for="plan-month">Месяц планов</label><input id="plan-month" type="month" value="${f.month}" aria-label="Месяц планов"><label class="filter-label"><input type="checkbox" id="plan-archived" ${f.archived?'checked':''}>Архив</label></div>
  ${visible.length ? `<div class="table-scroll"><table><thead><tr><th>Покупка / категория</th><th>Месяц остатка</th><th class="num">План</th><th class="num">Факт · цена</th><th class="num">Оплачено</th><th class="num">Осталось</th><th>Состояние</th><th><span class="screen-reader">Действия</span></th></tr></thead><tbody>${visible.map(p=>`<tr><td><button class="text-button table-main" data-action="edit-plan" data-id="${p.id}">${esc(p.title)}</button><span class="table-note">${esc(p.category)}</span></td><td class="date-cell">${esc(monthLabel(p.due_month))}</td><td class="num">${p.expected===null?'—':rub(p.expected)}</td><td class="num">${p.agreed===null?'—':rub(p.agreed)}</td><td class="num">${rub(p.paid)}</td><td class="num bold-num">${p.remaining===null?'Не оценено':rub(p.remaining)}</td><td>${planStatus(p)}</td><td><div class="row-actions">${p.archived?`<button class="text-button" data-action="restore-plan" data-id="${p.id}">Вернуть</button>`:`<button class="text-button" data-action="pay-plan" data-id="${p.id}">Оплатить</button>`}</div></td></tr>`).join('')}</tbody></table></div>` : empty('Планы не найдены',f.search||f.category||f.status||f.month?'Попробуйте изменить условия поиска.':'Начните с покупки или этапа ремонта. Сумму и месяц можно уточнить позже.',f.archived?'':'new-plan','Добавить план')}
  ${pagination('plan',rows.length,state.planPage,'известный остаток ' + rub(rows.reduce((s,p)=>s+cents(p.remaining),0n)))}<p class="small-summary">«Факт · цена» — полная согласованная стоимость покупки. «Оплачено» — сумма связанных платежей.</p></article>`;
}
function renderCategories() {
  const roots=state.categories.filter(c=>!c.parent_id);
  $('#view-categories').innerHTML=`<div class="notice"><span>Сначала выбирается основная категория, затем — только её подкатегории. Новые названия сразу появятся в формах оплат и планов.</span><button class="text-button" data-action="new-category">＋ Категория</button></div><div class="category-grid">${roots.map(c=>{const children=state.categories.filter(s=>s.parent_id===c.id);return `<article class="category-card"><h3>${esc(c.name)}<span>${children.length}</span></h3><ul>${children.length?children.map(s=>`<li>${esc(s.name)}</li>`).join(''):'<li>Можно использовать без подкатегории</li>'}</ul><button class="text-button" data-action="new-category" data-id="${c.id}">＋ Добавить подкатегорию</button></article>`;}).join('')}</div>${roots.length?'':empty('Пока нет категорий','Создайте основную категорию, например «Ремонт», а затем добавьте «Работы» и «Материалы».','new-category','Добавить категорию')}`;
}

function field(id, label, input, hint = '', full = false) {
  return `<div class="field${full?' full':''}"><label for="${id}">${label}</label>${input}${hint?`<small>${hint}</small>`:''}</div>`;
}
function categoryFields(categoryId = '') {
  const category=state.categories.find(c=>c.id===categoryId), rootId=category?.parent_id||category?.id||'';
  const roots=state.categories.filter(c=>!c.parent_id);
  const children=state.categories.filter(c=>c.parent_id===rootId);
  return field('category1','Категория 1',`<select id="category1" name="category1" required><option value="">Выберите категорию</option>${roots.map(c=>`<option value="${c.id}" ${c.id===rootId?'selected':''}>${esc(c.name)}</option>`).join('')}</select>`) + field('category2','Категория 2',`<select id="category2" name="category2" ${children.length?'':'disabled'}><option value="">Без подкатегории</option>${children.map(c=>`<option value="${c.id}" ${c.id===categoryId?'selected':''}>${esc(c.name)}</option>`).join('')}</select>`);
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
function openEditor(kind, item=null, plan=null) {
  if (!state.ready) return;
  editing={kind,item,initial:''};
  const title=kind==='expense'?(item?'Оплата':'Новая оплата'):kind==='plan'?(item?'Покупка / этап':'Новый план'):'Новая категория';
  $('#editor-title').textContent=title;
  $('#editor-eyebrow').textContent=kind==='expense'?'ФАКТИЧЕСКИЙ РАСХОД':kind==='plan'?'ПЛАНИРОВАНИЕ':'СПРАВОЧНИК';
  let fields='';
  if (kind==='expense') {
    const v=item||{amount:'',paid_on:state.today,description:plan?.title||'',category_id:plan?.category_id||'',plan_id:plan?.id||'',note:''};
    const selectable=state.plans.filter(p=>!p.archived||p.id===v.plan_id);
    fields=field('amount','Сумма оплаты, ₽',`<input id="amount" name="amount" inputmode="decimal" required placeholder="Например, 25 000" value="${esc(v.amount)}">`,'Сколько перечислили этим платежом.')+
      field('paid_on','Дата оплаты',`<input id="paid_on" name="paid_on" type="date" required min="2000-01-01" max="2100-12-31" value="${v.paid_on}">`)+
      field('plan_id','Связать с покупкой',`<select id="plan_id" name="plan_id"><option value="">Самостоятельный расход</option>${selectable.map(p=>`<option value="${p.id}" ${p.id===v.plan_id?'selected':''}>${esc(p.title)}${p.archived?' · архив':''}</option>`).join('')}</select>`,'Аванс и доплата одной покупки автоматически складываются в «Оплачено».',true)+
      field('description','Описание расхода',`<input id="description" name="description" required maxlength="500" placeholder="Например, аванс за межкомнатные двери" value="${esc(v.description)}">`,'',true)+categoryFields(v.category_id)+
      field('note','Примечание',`<textarea id="note" name="note" maxlength="4000" placeholder="Магазин, подрядчик или детали платежа">${esc(v.note)}</textarea>`,'',true);
  } else if (kind==='plan') {
    const v=item||{title:'',expected:null,agreed:null,due_month:null,category_id:'',note:''};
    fields=field('title','Покупка или этап ремонта',`<input id="title" name="title" required maxlength="500" placeholder="Например, межкомнатные двери" value="${esc(v.title)}">`,'',true)+
      field('expected','Плановая стоимость, ₽',`<input id="expected" name="expected" inputmode="decimal" placeholder="Пока неизвестно" value="${esc(v.expected)}">`,'Предварительная оценка всей покупки.')+
      field('agreed','Фактическая цена, ₽',`<input id="agreed" name="agreed" inputmode="decimal" placeholder="Ещё не согласована" value="${esc(v.agreed)}">`,'Полная цена по счёту или договорённости.')+
      (item?`<div class="plan-totals"><div><small>Уже оплачено</small><strong>${rub(item.paid)}</strong></div><div><small>Текущий остаток</small><strong>${rub(item.remaining)}</strong></div>${cents(item.overpaid)>0n?`<div><small>Сверх цены</small><strong>${rub(item.overpaid)}</strong></div>`:''}</div>`:'')+
      field('due_month','Месяц оставшейся оплаты',`<input id="due_month" name="due_month" type="month" min="2000-01" max="2100-12" value="${v.due_month?.slice(0,7)||''}">`,'Можно оставить пустым и распределить позже.',true)+categoryFields(v.category_id)+
      field('note','Примечание',`<textarea id="note" name="note" maxlength="4000" placeholder="Что входит в стоимость, сроки, поставщик">${esc(v.note)}</textarea>`,'',true);
  } else {
    const parentId=item?.parent_id||'';
    fields=field('parent_id','Группа',`<select id="parent_id" name="parent_id"><option value="">Новая основная категория</option>${state.categories.filter(c=>!c.parent_id).map(c=>`<option value="${c.id}" ${c.id===parentId?'selected':''}>Подкатегория: ${esc(c.name)}</option>`).join('')}</select>`,'',true)+field('name','Название',`<input id="name" name="name" required maxlength="120" placeholder="Например, Материалы">`,'',true);
  }
  const readOnly=!!item&&(item.deleted||item.archived);
  const payments=kind==='plan'&&item?state.expenses.filter(e=>e.plan_id===item.id&&!e.deleted):[];
  $('#editor-content').innerHTML=`<form id="record-form" class="editor-form"><div id="form-error" class="form-error hidden" role="alert"></div><div class="form-grid">${fields}</div>
    ${kind==='plan'&&item?`<div class="linked-payments"><h3>Оплаты по этой покупке · ${payments.length}</h3>${payments.length?`<table><tbody>${payments.map(e=>`<tr><td class="date-cell">${dateLabel(e.paid_on)}</td><td>${esc(e.description)}</td><td class="num">${rub(e.amount)}</td><td><button type="button" class="text-button" data-action="linked-expense" data-id="${e.id}">Открыть</button></td></tr>`).join('')}</tbody></table>`:'<p class="muted">Оплат пока нет.</p>'}${!item.archived?`<button type="button" class="text-button" data-action="dialog-pay" data-id="${item.id}">＋ Записать оплату</button>`:''}</div>`:''}
    <div class="form-actions">${item?.id&&kind!=='category'?`<button type="button" class="text-button danger-text delete-button" data-action="${kind==='expense'?(item.deleted?'restore-expense':'delete-expense'):(item.archived?'restore-plan':'archive-plan')}" data-id="${item.id}">${kind==='expense'?(item.deleted?'Восстановить':'Удалить'):(item.archived?'Вернуть из архива':'В архив')}</button>`:''}<button type="button" class="button secondary" data-action="close-dialog">${readOnly?'Закрыть':'Отмена'}</button>${readOnly?'':'<button type="submit" class="button primary" id="save-record">Сохранить</button>'}</div></form>`;
  if (readOnly) $('#record-form').querySelectorAll('input,textarea,select').forEach(el=>el.disabled=true);
  if (kind==='plan'&&item&&state.expenses.some(e=>e.plan_id===item.id)) {
    $('#category1').disabled=true;$('#category2').disabled=true;
  }
  editing.initial=formSnapshot();
  $('#editor').showModal();
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
  if (!pendingRequest||pendingRequest.fingerprint!==fingerprint) pendingRequest={fingerprint,key:crypto.randomUUID()};
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
      data={name:$('#name').value.trim(),parent_id:$('#parent_id').value||null};
      path='/v1/categories';method='POST';
    } else {
      const category=$('#category2').value||$('#category1').value;
      if (!category) throw new Error('Выберите категорию расхода.');
      data=current.kind==='expense'?{
        amount:moneyInput($('#amount').value,true),paid_on:$('#paid_on').value,
        description:$('#description').value.trim(),note:$('#note').value.trim(),
        category_id:category,plan_id:$('#plan_id').value||null,
      }:{
        title:$('#title').value.trim(),expected:moneyInput($('#expected').value),agreed:moneyInput($('#agreed').value),
        due_month:$('#due_month').value?$('#due_month').value+'-01':null,category_id:category,note:$('#note').value.trim(),
      };
      path='/v1/'+(current.kind==='expense'?'expenses':'plans')+(current.item?.id?'/'+current.item.id:'');
      if (current.item?.id) data.version=current.item.version;
    }
    await mutation(path,method,data);
    await closeEditor(true);
    notify(current.kind==='expense'?'Оплата сохранена':current.kind==='plan'?'План сохранён':'Категория добавлена');
    try { await loadData(); } catch(error) { showError(new Error('Запись сохранена, но обновить список не удалось. Нажмите «Обновить данные». '+error.message)); }
  } catch(error) {showFormError(error);}
  finally {button.disabled=false;button.textContent='Сохранить';}
}
async function toggleRecord(action,id) {
  const isExpense=action.endsWith('expense'), item=(isExpense?state.expenses:state.plans).find(i=>i.id===id);
  if (!item) throw new Error('Запись не найдена. Обновите данные.');
  const restoring=action.startsWith('restore');
  if (!restoring&&!await confirmAction(isExpense?'Удалить оплату?':'Перенести план в архив?',isExpense?'Оплата исчезнет из итогов, а остаток связанной покупки пересчитается. Её можно будет восстановить.':'Остаток покупки исчезнет из прогноза. Уже сделанные оплаты останутся в истории.',isExpense?'Удалить':'В архив')) return;
  const endpoint=isExpense?(restoring?'restore':'delete'):(restoring?'restore':'archive');
  try {
    await mutation(`/v1/${isExpense?'expenses':'plans'}/${id}/${endpoint}`,'POST',{version:item.version});
    if ($('#editor').open) await closeEditor(true);
    notify(restoring?'Запись восстановлена':isExpense?'Оплата удалена':'План в архиве');
    await loadData();
  } catch(error) {if($('#editor').open)showFormError(error);else showError(error);}
}
function setPlanFilter(status) {
  state.planFilters={search:'',category:'',month:'',status,archived:false};
  state.planPage=0;
  location.hash='plans';
  navigate('plans');
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
    else if (action==='edit-expense') openEditor('expense',state.expenses.find(e=>e.id===id));
    else if (action==='edit-plan') openEditor('plan',planById(id));
    else if (action==='pay-plan') openEditor('expense',null,planById(id));
    else if (action==='close-dialog') await closeEditor();
    else if (['delete-expense','restore-expense','archive-plan','restore-plan'].includes(action)) await toggleRecord(action,id);
    else if (action==='dialog-pay'||action==='linked-expense') {
      if (dirtyForm()) {showFormError(new Error('Сначала сохраните изменения покупки или отмените их.'));return;}
      await closeEditor(true);
      if (action==='dialog-pay') openEditor('expense',null,planById(id));
      else openEditor('expense',state.expenses.find(e=>e.id===id));
    } else if (action==='page') {
      state[target.dataset.kind+'Page']=Number(target.dataset.page);
      render();
    } else if (action==='undated'||action==='overdue') setPlanFilter(action);
    else if (action==='logout') {
      await request('/web/logout',{method:'POST'}); location.assign('/login');
    }
    else if (action==='refresh') {
      target.disabled=true;
      try {if(state.today)await loadData();else await start();notify('Данные обновлены');} finally {target.disabled=false;}
    } else if (action==='move-period') {
      state.start=addMonths(state.today,-1);state.end=state.moveMonth>state.start?state.moveMonth:addMonths(state.start,11);
      state.report=null;renderOverview();await loadReport();
    }
  } catch(error) {showError(error);}
}
let searchTimer;
function updateFilter(event) {
  const id=event.target.id;
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
document.addEventListener('submit',event=>{if(event.target.id==='record-form')saveForm(event);});
document.addEventListener('input',event=>{if(event.target.type==='search')updateFilter(event);});
document.addEventListener('change',async event=>{
  if(updateFilter(event))return;
  if(event.target.id==='category1')refreshSubcategories();
  if(event.target.id==='plan_id'&&event.target.value){const p=planById(event.target.value);if(p){selectCategory(p.category_id);if(!$('#description').value.trim())$('#description').value=p.title;}}
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
start();
