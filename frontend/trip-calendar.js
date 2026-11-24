/* Календарь прогрессивно улучшает исходные datetime-local поля.
 * Черновики и questionnaire.js по-прежнему используют #first-walk / #last-walk.
 */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const first = get('first-walk'), last = get('last-walk'), dialog = get('trip-calendar-dialog');
  if (!first || !last || !dialog || !dialog.showModal) return;
  const trigger = get('trip-calendar-open'), startTime = get('trip-calendar-start-time'), endTime = get('trip-calendar-end-time');
  const months = get('trip-calendar-months'), apply = get('trip-calendar-apply');
  const DAY = 86400000;
  const parse = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? new Date(value + 'T12:00:00Z') : null;
  const iso = date => date.toISOString().slice(0, 10);
  const monthStart = date => new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1, 12));
  const addDays = (date, count) => new Date(date.getTime() + count * DAY);
  const addMonths = (date, count) => new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + count, 1, 12));
  const daysBetween = (start, end) => Math.round((parse(end) - parse(start)) / DAY) + 1;
  const format = (date, options) => new Intl.DateTimeFormat('ru-RU', {timeZone: 'UTC', ...options}).format(date);
  const label = value => parse(value) ? format(parse(value), {day: 'numeric', month: 'short', year: 'numeric'}) : 'Выберите дату';
  let start = '', end = '', selection = 'start', view = monthStart(new Date()), focusDate = '', previousFocus = null;

  function updateTrigger() {
    const startValue = first.value.slice(0, 10), endValue = last.value.slice(0, 10);
    get('trip-calendar-range').textContent = startValue && endValue ? `${label(startValue)} — ${label(endValue)}` : startValue ? `${label(startValue)} — выберите окончание` : 'Выберите даты';
    const count = startValue && endValue ? daysBetween(startValue, endValue) : 0;
    get('trip-calendar-duration').textContent = count > 0 && count <= 14 ? `${count} ${count === 1 ? 'день' : count < 5 ? 'дня' : 'дней'} · время по городу назначения` : 'До 14 дней в вашем ритме';
  }
  function errorMessage() {
    if (!start) return 'Выберите дату начала.';
    if (!end) return 'Теперь выберите дату окончания.';
    if (daysBetween(start, end) > 14) return 'Поездка может длиться не больше 14 дней включительно.';
    if (!startTime.value || !endTime.value) return 'Укажите время начала и окончания прогулок.';
    if (`${start}T${startTime.value}` >= `${end}T${endTime.value}`) return 'Окончание должно быть позже начала прогулки.';
    if (start === end && (parseInt(endTime.value.slice(0,2),10) * 60 + parseInt(endTime.value.slice(3),10) - parseInt(startTime.value.slice(0,2),10) * 60 - parseInt(startTime.value.slice(3),10)) < 60) return 'Оставьте хотя бы час на прогулку.';
    if (start !== end && startTime.value > '20:00') return 'В первый день оставьте хотя бы час до 21:00.';
    if (start !== end && endTime.value < '10:00') return 'В последний день оставьте хотя бы час после 09:00.';
    return '';
  }
  function updateState() {
    get('trip-calendar-start-label').textContent = label(start);
    get('trip-calendar-end-label').textContent = label(end);
    get('trip-calendar-start').setAttribute('aria-pressed', String(selection === 'start'));
    get('trip-calendar-end').setAttribute('aria-pressed', String(selection === 'end'));
    const message = errorMessage();
    apply.disabled = Boolean(message);
    get('trip-calendar-message').textContent = message || `${daysBetween(start, end)} ${daysBetween(start, end) === 1 ? 'день' : daysBetween(start, end) < 5 ? 'дня' : 'дней'} для новых впечатлений. Даты готовы к выбору.`;
    get('trip-calendar-message').dataset.error = String(Boolean(start && end && message));
  }
  function choose(value) {
    if (selection === 'start') {
      start = value; end = ''; selection = 'end';
    } else if (!start || value < start) {
      start = value; end = ''; selection = 'end';
    } else {
      if (daysBetween(start, value) > 14) return;
      end = value; selection = 'start';
    }
    focusDate = value; render(); focusCell(value);
  }
  function focusCell(value) {
    months.querySelector(`[data-date="${value}"]`)?.focus({preventScroll: true});
  }
  function moveFocus(value) {
    let date = parse(value);
    if (!date) return;
    const maxEnd = start && selection === 'end' ? iso(addDays(parse(start),13)) : null;
    if (maxEnd && value > maxEnd) { value = maxEnd; date = parse(value); }
    view = monthStart(date);
    focusDate = value; render(); focusCell(value);
  }
  function keyDay(event, value) {
    const date = parse(value); let next;
    if (event.key === 'ArrowRight') next = addDays(date,1);
    else if (event.key === 'ArrowLeft') next = addDays(date,-1);
    else if (event.key === 'ArrowDown') next = addDays(date,7);
    else if (event.key === 'ArrowUp') next = addDays(date,-7);
    else if (event.key === 'Home') next = addDays(date,-((date.getUTCDay()+6)%7));
    else if (event.key === 'End') next = addDays(date,6-((date.getUTCDay()+6)%7));
    else if (event.key === 'PageUp' || event.key === 'PageDown') {
      const shift = (event.key === 'PageUp' ? -1 : 1) * (event.shiftKey ? 12 : 1);
      const destination = addMonths(date,shift);
      const max = new Date(Date.UTC(destination.getUTCFullYear(),destination.getUTCMonth()+1,0,12)).getUTCDate();
      next = new Date(Date.UTC(destination.getUTCFullYear(),destination.getUTCMonth(),Math.min(date.getUTCDate(),max),12));
    }
    if (next) { event.preventDefault(); moveFocus(iso(next)); }
  }
  function render() {
    updateState(); months.replaceChildren();
    const monthLabel = get('trip-calendar-month-label');
    monthLabel.textContent = format(view,{month:'long',year:'numeric'});
    const today = iso(new Date());
    const visibleStart = iso(view), visibleEnd = iso(addMonths(view,1));
    if (!focusDate || focusDate < visibleStart || focusDate >= visibleEnd) focusDate = start && start >= visibleStart && start < visibleEnd ? start : visibleStart;
    const date = view, section = document.createElement('section'); section.className = 'trip-calendar-month';
    const table = document.createElement('table'); table.setAttribute('role','grid'); table.setAttribute('aria-labelledby',monthLabel.id);
    const thead = document.createElement('thead'), headRow = document.createElement('tr');
    ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].forEach((day, dayIndex) => {
      const th = document.createElement('th'); th.scope='col'; th.textContent=day;
      th.setAttribute('aria-label',['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'][dayIndex]); headRow.append(th);
    }); thead.append(headRow); table.append(thead);
    const body = document.createElement('tbody');
    const offset = (date.getUTCDay()+6)%7;
    const length = new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0,12)).getUTCDate();
    for (let week = 0; week < Math.ceil((offset+length)/7); week++) {
      const tr = document.createElement('tr');
      for (let weekday = 0; weekday < 7; weekday++) {
        const td = document.createElement('td'), day = week*7+weekday-offset+1;
        if (day > 0 && day <= length) {
          const current = new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),day,12)), value = iso(current);
          const button = document.createElement('button'); button.type='button'; button.className='trip-calendar-day'; button.textContent=String(day); button.dataset.date=value;
          button.tabIndex=value===focusDate ? 0 : -1;
          const selected = value===start || value===end;
          button.classList.toggle('is-selected',selected); button.classList.toggle('is-today',value===today);
          td.classList.toggle('is-between',Boolean(start && end && value>start && value<end));
          td.setAttribute('aria-selected',String(selected || Boolean(start && end && value>start && value<end)));
          button.setAttribute('aria-label',format(current,{weekday:'long',day:'numeric',month:'long',year:'numeric'})+(value===start?' — начало':value===end?' — окончание':''));
          if (value===today) button.setAttribute('aria-current','date');
          button.disabled=Boolean(selection==='end' && start && value>start && daysBetween(start,value)>14);
          button.addEventListener('click',()=>choose(value)); button.addEventListener('keydown',event=>keyDay(event,value));
          button.addEventListener('focus',()=>{focusDate=value; months.querySelectorAll('.trip-calendar-day').forEach(item=>{item.tabIndex=item===button?0:-1;});});
          td.append(button);
        }
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(body); section.append(table); months.append(section);
  }
  trigger.addEventListener('click',()=>{
    previousFocus=document.activeElement;
    start=first.value.slice(0,10); end=last.value.slice(0,10);
    startTime.value=first.value.slice(11,16)||'09:00'; endTime.value=last.value.slice(11,16)||'19:00';
    selection=start&&!end?'end':'start';
    view=monthStart(parse(start)||new Date()); focusDate=start||iso(new Date());
    get('trip-calendar-zone').textContent=get('timezone-note')?.textContent||'Время местное — для выбранного города.';
    render(); dialog.showModal(); focusCell(focusDate);
  });
  function close() { dialog.close(); previousFocus?.focus({preventScroll:true}); }
  get('trip-calendar-close').addEventListener('click',close);
  dialog.addEventListener('cancel',()=>{setTimeout(()=>previousFocus?.focus({preventScroll:true}),0);});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)close();}});
  get('trip-calendar-start').addEventListener('click',()=>{selection='start';render();});
  get('trip-calendar-end').addEventListener('click',()=>{selection='end';render();});
  get('trip-calendar-prev').addEventListener('click',()=>{view=addMonths(view,-1);focusDate='';render();});
  get('trip-calendar-next').addEventListener('click',()=>{view=addMonths(view,1);focusDate='';render();});
  get('trip-calendar-today').addEventListener('click',()=>{view=monthStart(new Date());focusDate=iso(new Date());render();focusCell(focusDate);});
  for (const field of [startTime,endTime]) {
    field.addEventListener('input',event=>{event.stopPropagation();updateState();});
    field.addEventListener('change',event=>{event.stopPropagation();updateState();});
  }
  apply.addEventListener('click',()=>{
    if(errorMessage())return;
    first.value=`${start}T${startTime.value}`; last.value=`${end}T${endTime.value}`;
    for(const field of [first,last]){field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));}
    updateTrigger();close();
    get('middle-days').open = true;
  });
  for(const field of [first,last]){field.addEventListener('input',updateTrigger);field.addEventListener('change',updateTrigger);}
  // Загрузка черновика присваивает value без событий; наблюдаем окончание загрузки.
  new MutationObserver(updateTrigger).observe(get('trip-fields'),{attributes:true,attributeFilter:['disabled']});
  window.addEventListener('pageshow',updateTrigger);updateTrigger();
})();
