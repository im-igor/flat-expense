'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state = {categories: [], expenses: [], plans: [], loan: [], today: '', moveMonth: '2027-08', view: 'overview', ready: false, report: null, reportSequence: 0, loadSequence: 0, start: '', end: '', expensePage: 0, planPage: 0, ledgerPage: 0, expenseFilters: {search:'', category:'', month:'', deleted:false}, planFilters: {search:'', category:'', month:'', status:'', archived:false}, ledgerFilters: {search:'', category:'', month:'', type:'', inactive:false}};
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
function planById(id) { return state.plans.find(p => p.id === id); }
function planStatus(plan) {
  if (plan.archived) return '<span class="badge gray">В архиве</span>';
  if (plan.remaining === null) return '<span class="badge amber">Без оценки</span>';
  if (cents(plan.remaining) === 0n) return `<span class="badge paid">${cents(plan.overpaid) > 0n ? 'Переплата' : 'Оплачено'}</span>`;
  if (plan.due_month && plan.due_month.slice(0,7) < state.today.slice(0,7)) return '<span class="badge amber">Перенести месяц</span>';
  return `<span class="badge">${cents(plan.paid) > 0n ? 'Частично оплачено' : 'В плане'}</span>`;
}
function navigate(view) {
  if (view === 'expenses' || view === 'plans') view = 'ledger';
  state.view = ['overview','ledger','loan','categories'].includes(view) ? view : 'overview';
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
  const paid = expenses.reduce((sum,e) => sum + cents(e.amount), 0n)+pastLoan.reduce((sum,row)=>sum+cents(row.total),0n);
  const remaining = plans.reduce((sum,p) => sum + cents(p.remaining), 0n)+futureLoan.reduce((sum,row)=>sum+cents(row.total),0n);
  const unpriced = plans.filter(p => p.remaining === null).length;
  const month = state.today.slice(0,7);
  const currentActual = expenses.filter(e => e.paid_on.startsWith(month)).reduce((s,e) => s + cents(e.amount), 0n)+pastLoan.filter(row=>row.payment_date.startsWith(month)).reduce((s,row)=>s+cents(row.total),0n);
  const currentPlanned = plans.filter(p => p.due_month?.startsWith(month)).reduce((s,p) => s + cents(p.remaining), 0n)+futureLoan.filter(row=>row.payment_date.startsWith(month)).reduce((s,row)=>s+cents(row.total),0n);
  return `<div class="metrics">
    <article class="metric featured"><span class="metric-mark" aria-hidden="true">↗</span><p class="metric-label">Уже оплачено</p><p class="metric-value">${rub(paid)}</p><p class="metric-foot">${plural(expenses.length,'платёж','платежа','платежей')} и ${plural(pastLoan.length,'взнос','взноса','взносов')} по займу</p></article>
    <article class="metric"><span class="metric-mark" aria-hidden="true">◷</span><p class="metric-label">Осталось впереди</p><p class="metric-value">${rub(remaining)}</p><p class="metric-foot">Остатки покупок и будущие взносы по займу</p></article>
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
      <div class="chart-wrap">${report ? chartMarkup(report) : '<div class="empty-small">Загружаю прогноз…</div>'}</div><div class="chart-legend"><span><i class="legend-dot"></i>Оплачено</span><span><i class="legend-dot planned"></i>Будущие расходы</span><button class="text-button" data-action="move-period">До переезда</button></div>
      ${report ? `<details class="month-details"><summary>Точные суммы по месяцам</summary><div class="table-scroll"><table><thead><tr><th>Месяц</th><th class="num">Оплачено</th><th class="num">Запланировано</th><th class="num">Известный итог</th><th class="num">Без оценки</th></tr></thead><tbody>${report.months.map(m => `<tr><td>${esc(monthLabel(m.month))}${m.overdue_plan ? ' <span class="badge amber">Месяц прошёл</span>' : ''}</td><td class="num">${rub(m.actual)}</td><td class="num">${rub(m.planned_remaining)}</td><td class="num bold-num">${rub(m.known_total)}</td><td class="num">${m.unpriced || '—'}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
    </article>
    <div class="overview-lower"><article class="card"><div class="card-head"><div><h2>Ближайшие планы</h2><p>Остатки по покупкам и этапам ремонта</p></div><span class="badge">${plans.length} открытых</span></div><div class="compact-list">${upcoming.length ? upcoming.map(p => `<button class="list-row list-click" data-action="edit-plan" data-id="${p.id}"><span class="list-symbol" aria-hidden="true">▤</span><span class="list-body"><strong>${esc(p.title)}</strong><small>${esc(p.category)}</small></span><span class="list-amount">${rub(p.remaining)}<small>${esc(monthLabel(p.due_month))}</small></span></button>`).join('') : '<div class="empty-small">Пока нет открытых планов. Добавьте первую будущую покупку.</div>'}</div><div class="card-tail"><a class="text-button" href="#ledger">Все расходы и планы →</a></div></article>
    <article class="card"><div class="card-head"><div><h2>Последние оплаты</h2><p>Фактические расходы по датам платежей</p></div></div><div class="compact-list">${recent.length ? recent.map(e => `<button class="list-row list-click" data-action="edit-expense" data-id="${e.id}"><span class="list-symbol" aria-hidden="true">↗</span><span class="list-body"><strong>${esc(e.description)}</strong><small>${esc(e.category)}</small></span><span class="list-amount">${rub(e.amount)}<small>${dateLabel(e.paid_on)}</small></span></button>`).join('') : '<div class="empty-small">Здесь появятся ваши платежи. Начните с кнопки «Записать оплату».</div>'}</div><div class="card-tail"><a class="text-button" href="#ledger">Все расходы и планы →</a></div></article></div>`;
}
function pagination(kind, count, page, totalText) {
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  return `<div class="table-foot"><span>${plural(count,'запись','записи','записей')}${totalText ? ' · ' + totalText : ''}</span><div class="pagination"><button class="text-button" data-action="page" data-kind="${kind}" data-page="${page-1}" ${page===0?'disabled':''}>← Назад</button><span>${page+1} / ${pages}</span><button class="text-button" data-action="page" data-kind="${kind}" data-page="${page+1}" ${page+1>=pages?'disabled':''}>Далее →</button></div></div>`;
}
function ledgerRows() {
  const f=state.ledgerFilters, search=f.search.toLocaleLowerCase('ru');
  const expenses=state.expenses.filter(e=>(f.inactive||!e.deleted)&&categoryMatches(e,f.category)&&(!f.month||e.paid_on.startsWith(f.month))&&(!search||[e.description,e.note,e.category,planById(e.plan_id)?.title].join(' ').toLocaleLowerCase('ru').includes(search))).map(e=>({kind:'expense',date:e.paid_on,item:e}));
  const plans=state.plans.filter(p=>(f.inactive||!p.archived)&&categoryMatches(p,f.category)&&(!f.month||p.due_month?.startsWith(f.month))&&(!search||[p.title,p.note,p.category].join(' ').toLocaleLowerCase('ru').includes(search))).map(p=>({kind:'plan',date:p.due_month?.slice(0,10)||'9999-12-31',item:p}));
  const loan=state.loan.filter(row=>!f.category&&(!f.month||row.payment_date.startsWith(f.month))&&(!search||`займ взнос платёж ${row.period_index}`.includes(search))).map(row=>({kind:'loan',date:row.payment_date,item:row}));
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
  if(!children.length||(kind==='plan'&&state.expenses.some(e=>e.plan_id===item.id)))return null;
  if(kind==='expense'&&item.plan_id){
    const planned=state.categories.find(c=>c.id===planById(item.plan_id)?.category_id);
    if(planned?.parent_id===current.id&&!recommendationDismissed(kind,item,planned.id))return planned;
  }
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
function categoryCell(kind,item) {
  const [,second]=categoryParts(item.category_id);
  if(second)return `<td>${esc(second)}</td>`;
  const suggestion=categorySuggestion(kind,item), inactive=item.deleted||item.archived;
  return `<td class="category-missing-cell"><span class="badge amber">Не выбрана</span>${inactive?'':suggestion?`<span class="category-suggestion">Рекомендуется: <strong>${esc(suggestion.name)}</strong><span class="suggestion-actions"><button class="text-button" data-action="accept-category" data-kind="${kind}" data-id="${item.id}" data-category-id="${suggestion.id}">Принять</button><button class="text-button muted-button" data-action="dismiss-category" data-kind="${kind}" data-id="${item.id}" data-category-id="${suggestion.id}">Отклонить</button></span></span>`:`<button class="text-button category-manual" data-action="edit-${kind}" data-id="${item.id}">Назначить вручную</button>`}</td>`;
}
function ledgerMonthKey(row) { return row.date==='9999-12-31'?'':row.date.slice(0,7); }
function ledgerMonthTotals(rows) {
  const totals=new Map();
  rows.forEach(row=>{
    const key=ledgerMonthKey(row), total=totals.get(key)||{actual:0n,planned:0n,unpriced:0};
    if(row.kind==='expense'&&!row.item.deleted)total.actual+=cents(row.item.amount);
    else if(row.kind==='loan'){
      if(row.item.payment_date<=state.today)total.actual+=cents(row.item.total);
      else total.planned+=cents(row.item.total);
    }else if(row.kind==='plan'&&!row.item.archived){
      if(row.item.remaining===null)total.unpriced+=1;
      else total.planned+=cents(row.item.remaining);
    }
    totals.set(key,total);
  });
  return totals;
}
function ledgerMonthHeader(key,total) {
  const unpriced=total.unpriced?` · без оценки: ${total.unpriced}`:'';
  return `<tr class="month-divider"><td colspan="11"><strong>${key?esc(monthLabel(key)):'Без даты'}</strong><span>Факт: ${rub(total.actual)} · План: ${rub(total.planned)}${unpriced}</span></td></tr>`;
}
function ledgerRowMarkup(kind,item) {
  const [first]=categoryParts(item.category_id);
  if(kind==='expense'){
    const plan=planById(item.plan_id);
    return `<tr class="${item.deleted?'inactive-row':'paid-row'}"><td class="date-cell">${dateLabel(item.paid_on)}</td><td class="num">${plan?.expected===null||!plan?'—':rub(plan.expected)}</td><td class="num">${plan?.agreed===null||!plan?'—':rub(plan.agreed)}</td><td class="num bold-num">${rub(item.amount)}</td><td class="num">${plan?.remaining===null||!plan?'—':rub(plan.remaining)}</td><td>${item.deleted?'<span class="badge gray">Удалена</span>':'<span class="badge paid">Оплачено</span>'}</td><td>${esc(first)}</td>${categoryCell('expense',item)}<td><button class="text-button table-main" data-action="edit-expense" data-id="${item.id}">${esc(item.description)}</button>${plan?`<span class="table-note">В счёт: ${esc(plan.title)}</span>`:''}</td><td><span class="table-note">${esc(item.note)||'—'}</span></td><td><button class="text-button" data-action="${item.deleted?'restore-expense':'edit-expense'}" data-id="${item.id}">${item.deleted?'Восстановить':'Изменить'}</button></td></tr>`;
  }
  if(kind==='loan'){
    const paid=item.payment_date<=state.today;
    return `<tr class="${paid?'paid-row':''}"><td class="date-cell">${dateLabel(item.payment_date)}</td><td class="num">${rub(item.total)}</td><td class="num">${paid?rub(item.total):'—'}</td><td class="num bold-num">${paid?rub(item.total):rub(0n)}</td><td class="num">${paid?rub(0n):rub(item.total)}</td><td>${paid?'<span class="badge paid">Оплачено</span>':'<span class="badge plan-badge">Запланировано</span>'}</td><td>Заём</td><td>Взнос</td><td><span class="table-main">Взнос по займу № ${item.period_index}</span></td><td><span class="table-note">Основной долг ${rub(item.principal)}, проценты ${rub(item.interest)}, налог ${item.material_tax===null?'не рассчитан':rub(item.material_tax)}</span></td><td><a class="text-button" href="#loan">График</a></td></tr>`;
  }
  const closed=item.remaining!==null&&cents(item.remaining)===0n;
  return `<tr class="${item.archived?'inactive-row':closed?'paid-row':''}"><td class="date-cell">${esc(monthLabel(item.due_month))}</td><td class="num">${item.expected===null?'—':rub(item.expected)}</td><td class="num">${item.agreed===null?'—':rub(item.agreed)}</td><td class="num">${rub(item.paid)}</td><td class="num bold-num">${item.remaining===null?'Не оценено':rub(item.remaining)}</td><td>${planStatus(item)}</td><td>${esc(first)}</td>${categoryCell('plan',item)}<td><button class="text-button table-main" data-action="edit-plan" data-id="${item.id}">${esc(item.title)}</button></td><td><span class="table-note">${esc(item.note)||'—'}</span></td><td><button class="text-button" data-action="${item.archived?'restore-plan':closed?'edit-plan':'pay-plan'}" data-id="${item.id}">${item.archived?'Вернуть':closed?'Изменить':'Оплатить'}</button></td></tr>`;
}
function renderLedger() {
  const f=state.ledgerFilters, rows=ledgerRows();
  state.ledgerPage=Math.min(state.ledgerPage,Math.max(0,Math.ceil(rows.length/PAGE_SIZE)-1));
  const visible=rows.slice(state.ledgerPage*PAGE_SIZE,(state.ledgerPage+1)*PAGE_SIZE);
  const monthly=ledgerMonthTotals(rows);
  let previousMonth=null;
  const body=visible.map(row=>{
    const key=ledgerMonthKey(row), header=key!==previousMonth?ledgerMonthHeader(key,monthly.get(key)):'';
    previousMonth=key;
    return header+ledgerRowMarkup(row.kind,row.item);
  }).join('');
  const actual=rows.filter(r=>(r.kind==='expense'&&!r.item.deleted)||(r.kind==='loan'&&r.item.payment_date<=state.today)).reduce((sum,r)=>sum+cents(r.kind==='expense'?r.item.amount:r.item.total),0n);
  const remaining=rows.filter(r=>(r.kind==='plan'&&!r.item.archived)||(r.kind==='loan'&&r.item.payment_date>state.today)).reduce((sum,r)=>sum+cents(r.kind==='plan'?r.item.remaining:r.item.total),0n);
  $('#view-ledger').innerHTML=`<article class="card"><div class="toolbar"><label class="screen-reader" for="ledger-search">Поиск</label><input id="ledger-search" type="search" placeholder="Найти оплату или план" value="${esc(f.search)}" maxlength="200"><label class="screen-reader" for="ledger-category">Категория</label><select id="ledger-category"><option value="">Все категории</option>${categoryOptions(f.category)}</select><label class="screen-reader" for="ledger-type">Тип</label><select id="ledger-type"><option value="" ${!f.type?'selected':''}>Все расходы</option><option value="expense" ${f.type==='expense'?'selected':''}>Только оплаты</option><option value="plan" ${f.type==='plan'?'selected':''}>Только планы</option><option value="loan" ${f.type==='loan'?'selected':''}>Только заём</option></select><label class="screen-reader" for="ledger-month">Месяц</label><input id="ledger-month" type="month" value="${f.month}"><label class="filter-label"><input type="checkbox" id="ledger-inactive" ${f.inactive?'checked':''}>Показать удалённые и архивные</label></div>
  ${visible.length?`<div class="table-scroll"><table class="ledger-table"><thead><tr><th>Дата</th><th class="num">План</th><th class="num">Факт, цена</th><th class="num">Оплачено</th><th class="num">Осталось</th><th>Состояние</th><th>Категория 1</th><th>Категория 2</th><th>Описание</th><th>Примечание</th><th></th></tr></thead><tbody>${body}</tbody></table></div>`:empty('Записи не найдены','Измените фильтры или добавьте первую оплату.','new-expense','Записать оплату')}
  ${pagination('ledger',rows.length,state.ledgerPage,'оплачено '+rub(actual)+' · осталось '+rub(remaining))}<p class="small-summary">Все строки отсортированы по возрастанию даты. В заголовке каждого месяца показаны факт и оставшийся план без двойного счёта.</p></article>`;
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
  ${visible.length ? `<div class="table-scroll"><table><thead><tr><th>Покупка / категория</th><th>Месяц остатка</th><th class="num">План</th><th class="num">Факт · цена</th><th class="num">Оплачено</th><th class="num">Осталось</th><th>Состояние</th><th><span class="screen-reader">Действия</span></th></tr></thead><tbody>${visible.map(p=>{const closed=p.remaining!==null&&cents(p.remaining)===0n;return `<tr><td><button class="text-button table-main" data-action="edit-plan" data-id="${p.id}">${esc(p.title)}</button><span class="table-note">${esc(p.category)}</span></td><td class="date-cell">${esc(monthLabel(p.due_month))}</td><td class="num">${p.expected===null?'—':rub(p.expected)}</td><td class="num">${p.agreed===null?'—':rub(p.agreed)}</td><td class="num">${rub(p.paid)}</td><td class="num bold-num">${p.remaining===null?'Не оценено':rub(p.remaining)}</td><td>${planStatus(p)}</td><td><div class="row-actions">${p.archived?`<button class="text-button" data-action="restore-plan" data-id="${p.id}">Вернуть</button>`:`<button class="text-button" data-action="${closed?'edit-plan':'pay-plan'}" data-id="${p.id}">${closed?'Изменить':'Оплатить'}</button>`}</div></td></tr>`;}).join('')}</tbody></table></div>` : empty('Планы не найдены',f.search||f.category||f.status||f.month?'Попробуйте изменить условия поиска.':'Начните с покупки или этапа ремонта. Сумму и месяц можно уточнить позже.',f.archived?'':'new-plan','Добавить план')}
  ${pagination('plan',rows.length,state.planPage,'известный остаток ' + rub(rows.reduce((s,p)=>s+cents(p.remaining),0n)))}<p class="small-summary">«Факт · цена» — полная согласованная стоимость покупки. «Оплачено» — сумма связанных платежей.</p></article>`;
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
  const title=kind==='expense'?(item?'Оплата':'Новая оплата'):kind==='plan'?(item?'Покупка / этап':'Новый план'):(item?.id?'Категория':'Новая категория');
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
    const parent=state.categories.find(c=>c.id===parentId);
    fields=(item?.id?field('category_group','Группа',`<input id="category_group" value="${esc(parent?.name||'Основная категория')}" disabled>`,'Уровень категории после создания не меняется.',true):field('parent_id','Группа',`<select id="parent_id" name="parent_id"><option value="">Новая основная категория</option>${state.categories.filter(c=>!c.parent_id).map(c=>`<option value="${c.id}" ${c.id===parentId?'selected':''}>Подкатегория: ${esc(c.name)}</option>`).join('')}</select>`,'',true))+field('name','Название',`<input id="name" name="name" required maxlength="120" placeholder="Например, Материалы" value="${esc(item?.name||'')}">`,'',true);
  }
  const readOnly=!!item&&(item.deleted||item.archived);
  const payments=kind==='plan'&&item?state.expenses.filter(e=>e.plan_id===item.id&&!e.deleted):[];
  const categoryRemoval=kind==='category'&&item?`<div class="category-danger"><h3>Удаление и замена</h3>${item.child_count?'<p>У этой группы есть подкатегории. Сначала удалите или замените их.</p>':item.expense_count||item.plan_count?`<p>Категория используется: ${plural(item.expense_count,'оплата','оплаты','оплат')} и ${plural(item.plan_count,'план','плана','планов')}. Выберите, куда их перенести.</p><select id="replacement_category"><option value="">Выберите категорию для замены</option>${state.categories.filter(c=>c.id!==item.id&&!c.child_count).map(c=>`<option value="${c.id}">${esc(c.path)}</option>`).join('')}</select><button type="button" class="button danger" data-action="remove-category" data-id="${item.id}">Заменить везде и удалить</button>`:'<p>Категория нигде не используется.</p><button type="button" class="button danger" data-action="remove-category" data-id="'+item.id+'">Удалить категорию</button>'}</div>`:'';
  $('#editor-content').innerHTML=`<form id="record-form" class="editor-form"><div id="form-error" class="form-error hidden" role="alert"></div><div class="form-grid">${fields}</div>
    ${kind==='plan'&&item?`<div class="linked-payments"><h3>Оплаты по этой покупке · ${payments.length}</h3>${payments.length?`<table><tbody>${payments.map(e=>`<tr><td class="date-cell">${dateLabel(e.paid_on)}</td><td>${esc(e.description)}</td><td class="num">${rub(e.amount)}</td><td><button type="button" class="text-button" data-action="linked-expense" data-id="${e.id}">Открыть</button></td></tr>`).join('')}</tbody></table>`:'<p class="muted">Оплат пока нет.</p>'}${!item.archived?`<button type="button" class="text-button" data-action="dialog-pay" data-id="${item.id}">＋ Записать оплату</button>`:''}</div>`:''}
    ${categoryRemoval}
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
    notify(current.kind==='expense'?'Оплата сохранена':current.kind==='plan'?'План сохранён':current.item?'Категория переименована':'Категория добавлена');
    try { await loadData(); } catch(error) { showError(new Error('Запись сохранена, но обновить список не удалось. Нажмите «Обновить список». '+error.message)); }
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
  const data=kind==='expense'?{amount:item.amount,paid_on:item.paid_on,description:item.description,note:item.note,category_id:categoryId,plan_id:item.plan_id,version:item.version}:{title:item.title,expected:item.expected,agreed:item.agreed,due_month:item.due_month,category_id:categoryId,note:item.note,version:item.version};
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
  state.ledgerFilters={search:'',category:'',month:'',type:'plan',inactive:false};
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
    else if (action==='edit-category') openEditor('category',state.categories.find(c=>c.id===id));
    else if (action==='delete-category') {openEditor('category',state.categories.find(c=>c.id===id));setTimeout(()=>$('.category-danger')?.scrollIntoView({behavior:'smooth',block:'center'}));}
    else if (action==='remove-category') {try{await removeCategory(id);}catch(error){showFormError(error);}}
    else if (action==='accept-category') await applyCategorySuggestion(target.dataset.kind,id,target.dataset.categoryId);
    else if (action==='dismiss-category') dismissCategorySuggestion(target.dataset.kind,id,target.dataset.categoryId);
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
    } else if (action==='refresh-loan-rates') {
      if(!await confirmAction('Обновить ставки?','Прошедшие периоды получат ставку ЦБ на дату платежа, будущие — последнюю известную ставку как прогноз.','Обновить'))return;
      target.disabled=true;
      try{const result=await mutation('/v1/loan/key-rates','POST',{});await loadData();notify(`Ставки обновлены: ${result.actual} фактических, ${result.forecast} прогнозных`);}finally{target.disabled=false;}
    } else if (action==='move-period') {
      state.start=addMonths(state.today,-1);state.end=state.moveMonth>state.start?state.moveMonth:addMonths(state.start,11);
      state.report=null;renderOverview();await loadReport();
    }
  } catch(error) {showError(error);}
}
let searchTimer;
function updateFilter(event) {
  const id=event.target.id;
  const ledger=id.match(/^ledger-(search|category|month|type|inactive)$/);
  if(ledger){const key=ledger[1];state.ledgerFilters[key]=event.target.type==='checkbox'?event.target.checked:event.target.value;state.ledgerPage=0;const restore=()=>{const selection=event.target.selectionStart,active=document.activeElement===event.target;renderLedger();if(active){const replacement=document.getElementById(id);replacement.focus();if(replacement.type==='search')replacement.setSelectionRange(selection,selection);}};clearTimeout(searchTimer);if(key==='search')searchTimer=setTimeout(restore,220);else restore();return true;}
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
