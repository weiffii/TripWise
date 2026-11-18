/* Экспериментальная Shanghai demo v1. Все оценки — синтетические, без API. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TripWiseShanghai = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const CATEGORIES = ['museums', 'history', 'nature', 'cafes', 'entertainment', 'shopping', 'viewpoints', 'unusual'];
  const POLICY = {relaxed: {fraction: .55, rest: 0}, balanced: {fraction: .70, rest: 0}, intensive: {fraction: .85, rest: 0}};
  const LABELS = {museums:'музеи',history:'история',nature:'природа',cafes:'еда и кафе',entertainment:'развлечения',shopping:'шопинг',viewpoints:'смотровые площадки',unusual:'необычные места'};
  const clone = value => JSON.parse(JSON.stringify(value));
  const cmp = (a,b) => a < b ? -1 : a > b ? 1 : 0;
  const finite = n => typeof n === 'number' && Number.isFinite(n);
  const sum = rows => rows.reduce((a,b) => a+b,0);
  function need(value, message) { if (!value) throw new Error(message); }
  function minute(value) {
    need(typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value) || value === '24:00', 'Время должно иметь формат ЧЧ:ММ.');
    const [h,m] = value.split(':').map(Number); return h*60+m;
  }
  function dates(start, count) {
    need(typeof start === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(start), 'Укажите корректную дату начала.');
    const initial = new Date(start+'T00:00:00Z');
    need(Number.isFinite(initial.getTime()) && initial.toISOString().slice(0,10) === start, 'Укажите существующую дату начала.');
    return Array.from({length:count}, (_,i) => new Date(initial.getTime()+i*86400000).toISOString().slice(0,10));
  }
  function prepare(dataset, options) {
    need(dataset && Array.isArray(dataset.pois) && dataset.pois.length, 'Нет демонстрационного каталога Шанхая.');
    need(options && Array.isArray(options.interests) && options.interests.length > 0, 'Выберите хотя бы один интерес.');
    need(options.interests.every(i => CATEGORIES.includes(i)), 'Неизвестная категория интересов.');
    const interests = [...new Set(options.interests)];
    const pace = options.pace || 'balanced';
    need(Object.prototype.hasOwnProperty.call(POLICY,pace), 'Неизвестный темп путешествия.');
    const count = options.days === undefined ? 1 : options.days;
    need(Number.isInteger(count) && count >= 1 && count <= 5, 'Для демо выберите от одного до пяти дней.');
    const start = minute(options.day_start || '09:00'), end = minute(options.day_end || '19:00');
    need(start < end && end-start >= 60, 'Конец дня должен быть минимум на час позже начала.');
    const budget = options.budget_cny === undefined ? 1000 : options.budget_cny;
    need(finite(budget) && budget >= 0 && budget <= 1000000, 'Бюджет всей поездки должен быть неотрицательным числом в юанях.');
    const idMap = new Map();
    dataset.pois.forEach(p => {
      need(p && typeof p.id === 'string' && p.id && !idMap.has(p.id), 'В каталоге отсутствующий или повторный ID.');
      need(['attraction','restaurant'].includes(p.kind), 'Неизвестный тип места.');
      need(typeof p.name === 'string' && p.name.length, 'В каталоге отсутствует название.');
      need(Array.isArray(p.categories) && p.categories.length && p.categories.every(c => CATEGORIES.includes(c)), 'Некорректные категории места.');
      need(finite(p.lat) && finite(p.lon) && p.lat >= 30 && p.lat <= 32 && p.lon >= 120 && p.lon <= 123, 'Координаты места выходят за границы демонстрационного района Шанхая.');
      need(finite(p.duration_minutes) && p.duration_minutes >= 1 && p.duration_minutes <= 1440, 'Не задана допустимая длительность посещения.');
      need(finite(p.price_cny) && p.price_cny >= 0 && finite(p.rating) && p.rating >= 1 && p.rating <= 5 && Number.isInteger(p.review_count) && p.review_count >= 0, 'Некорректные синтетические цена или рейтинг.');
      need(p.opening && minute(p.opening.open) < minute(p.opening.close) && Array.isArray(p.opening.closed_weekdays) && p.opening.closed_weekdays.every(d => Number.isInteger(d) && d>=1 && d<=7), 'Некорректное демонстрационное расписание места.');
      need(!p.river_bank || ['east','west'].includes(p.river_bank), 'Некорректная модель берега.');
      idMap.set(p.id,p);
    });
    const required = options.required_ids || [];
    need(Array.isArray(required) && required.every(id => idMap.has(id)), 'Обязательное место не найдено в каталоге.');
    return {interests, pace, dates:dates(options.start_date || '2026-11-18',count), start,end,budget,
      required:[...new Set(required)], idMap, pois:dataset.pois};
  }
  function rankPrepared(input) {
    return input.pois.map(p => {
      const matched = input.interests.filter(c => p.categories.includes(c));
      return {poi_id:p.id,match_count:matched.length,matched_interests:matched,
        reasons:matched.length ? ['Совпадает с интересами: '+matched.map(c=>LABELS[c]).join(', ')] : ['Не совпадает с выбранными интересами']};
    }).sort((a,b) => b.match_count-a.match_count || input.idMap.get(b.poi_id).rating-input.idMap.get(a.poi_id).rating ||
      input.idMap.get(b.poi_id).review_count-input.idMap.get(a.poi_id).review_count || cmp(a.poi_id,b.poi_id))
      .map((r,i)=>Object.assign(r,{rank:i+1}));
  }
  function rankPois(dataset, options) { return rankPrepared(prepare(dataset,options)); }
  function distance(a,b) {
    const rad = v => v*Math.PI/180;
    const dLat = rad(b.lat-a.lat), dLon = rad(b.lon-a.lon);
    const h = Math.sin(dLat/2)**2 + Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dLon/2)**2;
    return 6371000*2*Math.atan2(Math.sqrt(h),Math.sqrt(Math.max(0,1-h)));
  }
  function tripLeg(a,b) {
    if (!a || a.id===b.id) return {base_minutes:0,buffer_minutes:0,planned_minutes:0,distance_m:0};
    const straight = distance(a,b), road = Math.round(straight*1.3);
    const cross = a.river_bank && b.river_bank && a.river_bank !== b.river_bank;
    // Ни линия метро, ни дорога не выдумываются: это только временная модель.
    const transfer = cross || straight>3500;
    const base = transfer ? Math.ceil((cross?20:15)+road/300) : Math.max(1,Math.ceil(road/(4000/60)));
    const planned = Math.ceil((base*1.25+5)/5)*5;
    return {base_minutes:base,buffer_minutes:planned-base,planned_minutes:planned,distance_m:road,
      transport_assumption:transfer?'synthetic_urban_transfer':'synthetic_walk_estimate',
      reason:transfer ? (cross?'Синтетическая оценка переезда между берегами: включает условные доступ и ожидание. Это не пеший мост и не реальное расписание ОТ.':'Синтетическая оценка городского переезда с условными доступом и ожиданием, без маршрута ОТ.') : 'Синтетическая оценка пешего времени по приближённым координатам; уличная сеть не проверена.'};
  }
  function slotsFor(input) {
    const slots=[];
    if(input.start<720 && input.end-input.start>=50) {
      slots.push({name:'breakfast',earliest:input.start,target:input.start,
        latest:Math.min(719,input.start+60,input.end-50)});
    }
    for (const [name,early,late,target] of [['lunch',720,840,780],['dinner',1080,1200,1080]]) {
      const earliest=Math.max(input.start,early), latest=Math.min(late,input.end-50);
      if (earliest<=latest) slots.push({name,earliest,latest,target:Math.max(earliest,Math.min(target,latest))});
    }
    return slots;
  }
  function intervalFor(p,date) {
    const dow = new Date(date+'T00:00:00Z').getUTCDay() || 7;
    return p.opening.closed_weekdays.includes(dow) ? null : [minute(p.opening.open),minute(p.opening.close)];
  }
  function planTrip(dataset,options) {
    const input=prepare(dataset,options), ranking=rankPrepared(input), byRank=new Map(ranking.map(r=>[r.poi_id,r]));
    const required=new Set(input.required), used=new Set(), covered=new Set();
    const restaurants=input.pois.filter(p=>p.kind==='restaurant');
    const mealSlots=slotsFor(input), minFood=restaurants.length ? Math.min(...restaurants.map(p=>p.price_cny)) : 0;
    let mealsRemaining=mealSlots.length*input.dates.length, spent=0, mealsMissing=0;
    const attractionRequired=input.required.map(id=>input.idMap.get(id)).filter(p=>p.kind==='attraction');
    const lowerBound=sum(attractionRequired.map(p=>p.price_cny))+minFood*mealsRemaining;
    const budgetImpossible=lowerBound>input.budget;
    const warnings=['Демонстрационные данные: цены, рейтинги, отзывы, часы и переходы синтетические. Это не подтверждение реальной поездки.'];
    const days=[];
    function requiredReserve(excluded) {
      return sum(attractionRequired.filter(p=>!used.has(p.id) && p.id!==excluded).map(p=>p.price_cny));
    }
    function prepareMove(state,p,duration) {
      const open=intervalFor(p,state.date); if (!open) return null;
      const rest=state.lastAction==='visit'?POLICY[input.pace].rest:0;
      const leg=tripLeg(state.position,p), depart=state.now+rest, arrival=depart+leg.planned_minutes;
      const begin=Math.max(arrival,open[0]), finish=begin+duration;
      if(finish>open[1] || finish>input.end) return null;
      return {p,rest,leg,depart,arrival,begin,finish,duration};
    }
    function chooseMeal(state,slot,anticipated,remainingAfter) {
      const source=anticipated ? Object.assign({},state,{now:anticipated.finish,position:anticipated.p,lastAction:'visit',active:state.active+anticipated.duration+anticipated.leg.planned_minutes}) : state;
      const hasFood=input.interests.includes('cafes');
      const options=[];
      for (const p of restaurants) {
        if(used.has(p.id) || anticipated && p.id===anticipated.p.id)continue;
        if(source.position && source.position.river_bank && p.river_bank && source.position.river_bank!==p.river_bank)continue;
        const metres=source.position?distance(source.position,p):0;
        if(metres>(hasFood?3000:1500))continue;
        const duration=Math.max(50,Math.min(60,Math.round(p.duration_minutes)));
        const move=prepareMove(source,p,duration); if(!move)continue;
        move.begin=Math.max(move.begin,slot.earliest);
        move.finish=move.begin+duration;
        const open=intervalFor(p,state.date);
        if(move.begin>slot.latest || move.finish>open[1] || move.finish>input.end)continue;
        if(source.active+move.leg.planned_minutes>state.cap)continue;
        const added=anticipated?anticipated.p.price_cny:0;
        const reserve=requiredReserve(anticipated?anticipated.p.id:null);
        if(spent+added+p.price_cny+minFood*remainingAfter+reserve>input.budget+1e-8)continue;
        options.push(Object.assign(move,{metres,qualityBucket:Math.floor(metres/500)}));
      }
      options.sort((a,b)=>Number(required.has(b.p.id))-Number(required.has(a.p.id)) ||
        (hasFood?0:a.qualityBucket-b.qualityBucket) || b.p.rating-a.p.rating ||
        b.p.review_count-a.p.review_count || a.metres-b.metres || cmp(a.p.id,b.p.id));
      return options[0] || null;
    }
    function append(state,move,type,reason) {
      if(move.rest)state.events.push({type:'rest',start_min:state.now,end_min:move.depart,reason:'Отдых после посещения'});
      if(move.leg.planned_minutes)state.events.push(Object.assign({type:'travel',start_min:move.depart,end_min:move.arrival,from_poi_id:state.position.id,to_poi_id:move.p.id},move.leg));
      if(move.begin>move.arrival)state.events.push({type:'wait',start_min:move.arrival,end_min:move.begin,reason:type==='meal'?'Ожидание времени приёма пищи или открытия заведения':'Ожидание открытия'});
      state.events.push({type,start_min:move.begin,end_min:move.finish,poi_id:move.p.id,reason});
      state.now=move.finish;state.position=move.p;state.lastAction=type;
      state.active+=move.leg.planned_minutes+(type==='visit'?move.duration:0);
      state.cost+=move.p.price_cny;spent+=move.p.price_cny;used.add(move.p.id);
      (type==='meal'?state.meal_ids:state.visit_ids).push(move.p.id);
      if(type==='visit')input.interests.filter(c=>move.p.categories.includes(c)).forEach(c=>covered.add(c));
      if(type==='meal' && input.interests.includes('cafes'))covered.add('cafes');
    }
    for(const date of input.dates) {
      const state={date,now:input.start,position:null,lastAction:null,events:[],visit_ids:[],meal_ids:[],cost:0,active:0,
        cap:Math.floor(Math.max(0,input.end-input.start-mealSlots.length*60)*POLICY[input.pace].fraction),warnings:[]};
      for(let phase=0;phase<=mealSlots.length;phase++) {
        const slot=mealSlots[phase] || null;
        let guard=0;
        while(guard++<input.pois.length+1) {
          if(slot && state.now>=slot.target)break;
          const choices=[];
          for(const p of input.pois) {
            const rank=byRank.get(p.id);
            if(p.kind!=='attraction' || used.has(p.id) || !required.has(p.id) && !rank.match_count)continue;
            const move=prepareMove(state,p,p.duration_minutes);if(!move)continue;
            if(state.active+move.duration+move.leg.planned_minutes>state.cap)continue;
            const reserve=requiredReserve(p.id);
            if(spent+p.price_cny+reserve+minFood*mealsRemaining>input.budget+1e-8)continue;
            // Проверка питания делается ДО выбора места, а не после заполнения дня.
            if(slot && !chooseMeal(state,slot,move,Math.max(0,mealsRemaining-1)))continue;
            const fresh=input.interests.filter(c=>p.categories.includes(c) && !covered.has(c)).length;
            choices.push(Object.assign(move,{fresh,match:rank.match_count}));
          }
          choices.sort((a,b)=>Number(required.has(b.p.id))-Number(required.has(a.p.id)) || b.fresh-a.fresh || b.match-a.match ||
            a.leg.planned_minutes-b.leg.planned_minutes || b.p.rating-a.p.rating || b.p.review_count-a.p.review_count || cmp(a.p.id,b.p.id));
          if(!choices.length)break;
          const selected=choices[0];
          const matches=byRank.get(selected.p.id).matched_interests;
          append(state,selected,'visit',required.has(selected.p.id)?'Обязательное место; '+(matches.length?'совпадает с интересами: '+matches.map(c=>LABELS[c]).join(', '):'выбрано отдельно от интересов'):'Совпадает с интересами: '+matches.map(c=>LABELS[c]).join(', '));
        }
        if(slot) {
          const meal=chooseMeal(state,slot,null,Math.max(0,mealsRemaining-1));
          if(meal) {
            append(state,meal,'meal',input.interests.includes('cafes')?'Еда выбрана как интерес: доступное заведение с высоким демонстрационным рейтингом и отзывами, без смены берега.':'Питание рядом с текущим маршрутом, без смены берега; среди близких вариантов учтены демонстрационные рейтинг и отзывы.');
            state.events[state.events.length-1].meal_kind=slot.name;
          }
          else {
            mealsMissing++;
            const label={breakfast:'завтрак',lunch:'обед',dinner:'ужин'}[slot.name];
            state.warnings.push('Не удалось включить '+label+': нет доступного по времени, бюджету и близости заведения. Увеличьте бюджет/время или измените места.');
            state.now=Math.max(state.now,slot.earliest);
          }
          mealsRemaining--;
        }
      }
      const activeLimit=state.cap;
      days.push({date,events:state.events,visit_ids:state.visit_ids,meal_ids:state.meal_ids,total_cost_cny:Math.round(state.cost*100)/100,
        active_minutes:state.active,active_limit_minutes:activeLimit,rest_minutes:POLICY[input.pace].rest,warnings:state.warnings});
    }
    const unplaced=input.required.filter(id=>!used.has(id));
    const uncovered=input.interests.filter(c=>!covered.has(c));
    if(unplaced.length)warnings.push('Не размещены обязательные места: '+unplaced.map(id=>input.idMap.get(id).name).join(', ')+'. Возможны ограничения часов, бюджета, еды или нагрузки. Неудача эвристики не доказывает отсутствие другого маршрута.');
    if(uncovered.length)warnings.push('Не покрыты интересы: '+uncovered.map(c=>LABELS[c]).join(', ')+'. Добавьте дни, измените бюджет/время или обязательные места.');
    if(budgetImpossible)warnings.push('Бюджет меньше нижней оценки обязательных билетов и необходимых приёмов пищи.');
    days.forEach(d=>warnings.push(...d.warnings));
    const events=days.flatMap(d=>d.events), visits=events.filter(e=>e.type==='visit'), meals=events.filter(e=>e.type==='meal'), legs=events.filter(e=>e.type==='travel');
    const checks={time:days.every(d=>d.events.every((e,i)=>e.start_min>=input.start && e.end_min<=input.end && e.end_min>=e.start_min && (!i || e.start_min>=d.events[i-1].end_min)) && d.active_minutes<=d.active_limit_minutes)?'passed':'failed',
      budget:spent<=input.budget+1e-8 && !budgetImpossible?'passed':'failed',
      opening_hours:days.every(d=>d.events.filter(e=>e.type==='visit'||e.type==='meal').every(e=>{const window=intervalFor(input.idMap.get(e.poi_id),d.date);return window && e.start_min>=window[0] && e.end_min<=window[1];}))?'passed':'failed',
      meals:mealSlots.length?(mealsMissing?'failed':'passed'):'not_applicable',
      required_places:unplaced.length?'failed':'passed',interest_coverage:uncovered.length?'failed':'passed'};
    const status=budgetImpossible?'infeasible':Object.values(checks).includes('failed') || !visits.length && !input.interests.includes('cafes')?'partial':'demo';
    return {schema_version:'shanghai-demo-v1',model_version:'shanghai-interest-meal-greedy-v1',status,days,selected_ids:[...used],ranking,warnings:[...new Set(warnings)],checks,
      unplaced_required_ids:unplaced,uncovered_interests:uncovered,
      summary:{total_cost_cny:Math.round(spent*100)/100,budget_cny:input.budget,visit_count:visits.length,meal_count:meals.length,
        estimated_travel_minutes:sum(legs.map(l=>l.base_minutes)),planned_travel_minutes:sum(legs.map(l=>l.end_min-l.start_min)),days_count:days.length,
        covered_interests:[...covered],synthetic_data:true,transport_basis:'synthetic_estimate',first_last_poi:true}};
  }
  return {planTrip,rankPois};
});
