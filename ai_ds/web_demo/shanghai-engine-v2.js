/* TripWise Shanghai v2: deterministic, offline, synthetic demonstration. No external APIs. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TripWiseShanghai = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const CATS = ['museums','history','nature','cafes','entertainment','shopping','viewpoints','unusual'];
  const LABELS = {museums:'музеи',history:'история',nature:'природа',cafes:'еда и кафе',entertainment:'развлечения',shopping:'шопинг',viewpoints:'смотровые',unusual:'необычные места'};
  const PACE = {relaxed:{scale:1.70,max:3},balanced:{scale:1.33,max:4},intensive:{scale:0.95,max:6}};
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const sum = a => a.reduce((x,y)=>x+y,0);
  const mins = s => {if(typeof s!=='string'||!(/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(s)||s==='24:00'))throw Error('Некорректное время.');const [h,m]=s.split(':').map(Number);return h*60+m;};
  const unique = a => [...new Set(a)];
  function assert(ok,message){if(!ok)throw Error(message);}
  function dates(s,count){
    assert(/^\d{4}-\d{2}-\d{2}$/.test(s),'Укажите дату начала.');
    const d=new Date(s+'T00:00:00Z');assert(!Number.isNaN(d.getTime())&&d.toISOString().slice(0,10)===s,'Несуществующая дата.');
    return Array.from({length:count},(_,i)=>new Date(d.getTime()+i*86400000).toISOString().slice(0,10));
  }
  function normalize(dataset, opts){
    assert(dataset&&Array.isArray(dataset.pois)&&dataset.pois.length>0,'Нет объектов Шанхая.');
    assert(opts&&typeof opts==='object','Не задана анкета.');
    const primary=unique(opts.primary_interests||opts.interests||[]), secondary=unique(opts.secondary_interests||[]).filter(c=>!primary.includes(c));
    const interests=primary.concat(secondary);
    assert(interests.length>0&&interests.every(c=>CATS.includes(c)),'Выберите хотя бы один корректный интерес.');
    const pace=opts.pace||'balanced';assert(!!PACE[pace],'Некорректный темп.');
    const days=opts.days===undefined?1:opts.days;assert(Number.isInteger(days)&&days>=1&&days<=5,'Длительность: от 1 до 5 дней.');
    const start=mins(opts.day_start||'09:00'),end=mins(opts.day_end||'19:00');assert(end-start>=60,'День должен длиться минимум час.');
    const budget=opts.budget_cny===undefined?1000:opts.budget_cny;
    assert(finite(budget)&&budget>=0&&budget<=10000000,'Некорректный бюджет в CNY.');
    const group=Object.assign({adult:1,child:0,senior:0,pet:0},opts.group||{});
    assert(['adult','child','senior','pet'].every(k=>Number.isInteger(group[k])&&group[k]>=0&&group[k]<=30),'Некорректное количество участников.');
    assert(group.adult+group.child+group.senior>=1,'Нужен хотя бы один человек.');
    const mobility=opts.transport||'mixed';assert(['walk','taxi','transit','mixed'].includes(mobility),'Неизвестный способ передвижения.');
    const map=new Map();
    for (const p of dataset.pois){
      assert(p&&typeof p.id==='string'&&p.id&&!map.has(p.id),'Повтор ID объекта.');
      assert(['restaurant','attraction'].includes(p.kind),'Неизвестный тип объекта.');
      assert(Array.isArray(p.categories)&&p.categories.every(c=>CATS.includes(c))&&p.categories.length,'Неверные категории объекта.');
      assert(finite(p.lat)&&finite(p.lon)&&p.lat>=30&&p.lat<=32&&p.lon>=120&&p.lon<=123,'Некорректные координаты.');
      assert(finite(p.price_cny)&&p.price_cny>=0&&finite(p.duration_minutes)&&p.duration_minutes>0,'Некорректные цена/длительность.');
      assert(p.opening&&mins(p.opening.open)<mins(p.opening.close)&&Array.isArray(p.opening.closed_weekdays),'Некорректные часы работы.');
      if(p.season_months!==undefined)assert(Array.isArray(p.season_months)&&p.season_months.every(m=>Number.isInteger(m)&&m>=1&&m<=12),'Некорректный сезон.');
      map.set(p.id,p);
    }
    const required=unique(opts.required_ids||[]);assert(required.every(id=>map.has(id)),'Неизвестное обязательное место.');
    return {primary,secondary,interests,pace,dates:dates(opts.start_date||'2026-11-18',days),start,end,budget,group,mobility,required,map,pois:dataset.pois};
  }
  function distance(a,b){
    const r=x=>x*Math.PI/180,dLat=r(b.lat-a.lat),dLon=r(b.lon-a.lon);
    const h=Math.sin(dLat/2)**2+Math.cos(r(a.lat))*Math.cos(r(b.lat))*Math.sin(dLon/2)**2;
    return 12742000*Math.atan2(Math.sqrt(h),Math.sqrt(Math.max(0,1-h)));
  }
  function groupPrice(p,g){
    const a=p.price_cny;
    const tickets=p.kind==='restaurant'?{adult:1,child:0.65,senior:0.90}:{adult:1,child:0.50,senior:0.70};
    const multipliers=Object.assign({},tickets,p.price_multipliers||{});
    return Math.round(100*(a*(g.adult*multipliers.adult+g.child*multipliers.child+g.senior*multipliers.senior)+(p.pet_fee_cny||0)*g.pet))/100;
  }
  function transportLeg(a,b,mode,g,date){
    if(!a||a.id===b.id)return {base_minutes:0,buffer_minutes:0,planned_minutes:0,distance_m:0,transport_assumption:'start',transport_cost_cny:0};
    const raw=distance(a,b),road=raw*1.28;
    const cross=a.river_bank&&b.river_bank&&a.river_bank!==b.river_bank;
    const people=g.adult+g.child+g.senior;
    let type=mode;
    if(mode==='mixed')type=!cross&&raw<=1700?'walk':g.pet>0||people>=4?'taxi':'transit';
    if(type==='walk'&&(cross||raw>6000))return null; // No imaginary bridge across the Huangpu.
    if(type==='transit'&&g.pet>0)return null; // Conservative offline assumption, not an actual operator policy.
    const dow=new Date(date+'T00:00:00Z').getUTCDay(),weekend=dow===0||dow===6;
    let base,cost=0,description;
    if(type==='walk'){
      const speed=g.senior?2700:g.child||g.pet?3200:4200;
      base=Math.max(1,Math.ceil(road/speed*60));description='Синтетическая пешая оценка (не уличный маршрут).';
    }else if(type==='taxi'){
      base=Math.ceil((cross?9:4)+road/(weekend?230:300));
      const cars=Math.ceil((people+Math.ceil(g.pet/2))/4);
      cost=Math.round((16+Math.max(0,road/1000-3)*2.8)*cars*100)/100;
      description='Синтетическое такси: условная дорожная скорость/тариф, без реального заказа.';
    }else{
      base=Math.ceil((cross?21:14)+road/380);
      cost=Math.round((g.adult*4+g.child*2+g.senior*2)*100)/100;
      description='Синтетическая оценка общественного транспорта: ожидание и доступ учтены, расписание/линии не проверены.';
    }
    const planned=Math.ceil((base*1.20+3)/5)*5;
    return {base_minutes:base,buffer_minutes:planned-base,planned_minutes:planned,distance_m:Math.round(road),
      transport_assumption:type==='walk'?'synthetic_walk_estimate':type==='taxi'?'synthetic_taxi_estimate':'synthetic_transit_estimate',transport_cost_cny:cost,reason:description};
  }
  function dateAttributes(p,date){
    const month=Number(date.slice(5,7));const dow=new Date(date+'T00:00:00Z').getUTCDay();const weekend=dow===0||dow===6;
    const seasonal=p.season_months&&p.season_months.length>0;
    const available=!seasonal||p.season_months.includes(month);
    const crowd=weekend?(p.weekend_crowd_level||((p.review_count||0)>10000?'high':'medium')):(p.weekday_crowd_level||'low');
    const crowded=weekend&&crowd==='high';
    const opening=p.opening;
    return {available:available&&!opening.closed_weekdays.includes(dow||7)&&!(opening.closed_dates||[]).includes(date),
      opens:mins(opening.open),closes:mins(opening.close),weekend,crowded,seasonal:!!seasonal,seasonMatch:!!seasonal&&available};
  }
  function suitability(p,g){
    if(g.pet>0&&p.pet_friendly!==true)return false;
    return true;
  }
  function rankPois(dataset,options){
    const input=normalize(dataset,options),d=input.dates[0],req=new Set(input.required);
    return input.pois.map(p=>{
      const a=dateAttributes(p,d),matched=input.interests.filter(c=>p.categories.includes(c));
      const priority=matched.reduce((s,c)=>s+(input.primary.includes(c)?4:1.5),0);
      const bonus=a.seasonMatch?2:0,penalty=a.crowded?8:0;
      return {poi_id:p.id,match_count:matched.length,matched_interests:matched,priority_score:priority+bonus-penalty,
        reasons:[matched.length?'Главные/дополнительные интересы: '+matched.map(c=>LABELS[c]).join(', '):'Вне интересов',a.seasonMatch?'Сезонное совпадение':'',a.crowded?'Штраф за выходные и многолюдность':''].filter(Boolean),
        _score:priority+bonus-penalty+(req.has(p.id)?100:0)+(p.rating||4)*0.02,
      };
    }).sort((a,b)=>b._score-a._score||a.poi_id.localeCompare(b.poi_id)).map((r,i)=>{delete r._score;r.rank=i+1;return r;});
  }
  function mealSlots(start,end){
    const result=[];
    if(start<720&&end-start>=50)result.push({kind:'breakfast',earliest:start,latest:Math.min(start+60,719),target:start});
    if(start<=840&&end>=770)result.push({kind:'lunch',earliest:Math.max(start,720),latest:Math.min(840,end-50),target:780});
    if(start<=1200&&end>=1070)result.push({kind:'dinner',earliest:Math.max(start,1020),latest:Math.min(1200,end-50),target:1080});
    return result.filter(s=>s.latest>=s.earliest);
  }
  function planTrip(dataset,options){
    const x=normalize(dataset,options), rankings=rankPois(dataset,options),rank=new Map(rankings.map(a=>[a.poi_id,a]));
    const used=new Set(),covered=new Set(),required=new Set(x.required),warnings=[
      'Демонстрационные расчёты: цены, скидки, валюта, загруженность, сезонность и транспорт — синтетические допущения, не проверенные условия поездки.'
    ],days=[],restaurants=x.pois.filter(p=>p.kind==='restaurant');
    const countMeals=mealSlots(x.start,x.end).length*x.dates.length;
    const minMeal=restaurants.length?Math.min(...restaurants.filter(p=>suitability(p,x.group)).map(p=>groupPrice(p,x.group))):Infinity;
    const lowerBound=sum(x.required.map(id=>groupPrice(x.map.get(id),x.group)))+countMeals*minMeal;
    const impossible=lowerBound>x.budget;let spent=0,missingMeals=0;
    const availableBudget=(price,excludeId,remainingMeals)=>spent+price+
      sum(x.required.filter(id=>!used.has(id)&&id!==excludeId).map(id=>groupPrice(x.map.get(id),x.group)))+
      (Number.isFinite(minMeal)?minMeal*remainingMeals:0)<=x.budget+1e-7;
    const plannedMeals=mealSlots(x.start,x.end);
    for(const date of x.dates){
      const d={date,events:[],visit_ids:[],meal_ids:[],total_cost_cny:0,active_minutes:0,active_limit_minutes:x.end-x.start,rest_minutes:0,warnings:[]};
      let now=x.start,position=null,visitsToday=0;
      const remainingMeals=()=>plannedMeals.length*(x.dates.length-days.length)-d.meal_ids.length;
      function attach(p,type,leg,length,why,kind){
        if(leg&&leg.planned_minutes){d.events.push({type:'travel',start_min:now,end_min:now+leg.planned_minutes,
          from_poi_id:position.id,to_poi_id:p.id,...leg});now+=leg.planned_minutes;d.active_minutes+=leg.planned_minutes;}
        const price=groupPrice(p,x.group)+(leg?.transport_cost_cny||0);
        const ev={type,start_min:now,end_min:now+length,poi_id:p.id,price_cny:groupPrice(p,x.group),reason:why};
        if(kind)ev.meal_kind=kind;
        d.events.push(ev);now+=length;position=p;used.add(p.id);spent+=price;d.total_cost_cny+=price;
        if(type==='visit'){visitsToday++;d.visit_ids.push(p.id);d.active_minutes+=length;
          x.interests.filter(c=>p.categories.includes(c)).forEach(c=>covered.add(c));}
        else {d.meal_ids.push(p.id);if(x.interests.includes('cafes'))covered.add('cafes');}
      }
      function feasible(p,type,length){
        if(used.has(p.id)||!suitability(p,x.group))return null;
        const info=dateAttributes(p,date);if(!info.available||info.crowded&&!required.has(p.id))return null;
        const leg=transportLeg(position,p,x.mobility,x.group,date);if(!leg)return null;
        const begin=now+leg.planned_minutes;
        if(begin<info.opens||begin+length>info.closes||begin+length>x.end)return null;
        const price=groupPrice(p,x.group)+leg.transport_cost_cny;
        if(!availableBudget(price,p.id,Math.max(0,remainingMeals()-(type==='meal'?1:0))))return null;
        return {p,info,leg,begin,length,price};
      }
      function eat(slot){
        const alternatives=[];
        for(const p of restaurants){
          if(position&&position.river_bank&&p.river_bank&&position.river_bank!==p.river_bank)continue;
          const length=Math.max(50,Math.min(60,p.duration_minutes));const item=feasible(p,'meal',length);if(!item)continue;
          if(item.begin<slot.earliest||item.begin>slot.latest)continue;
          const metres=position?distance(position,p):0;
          if(position&&metres>(x.interests.includes('cafes')?4000:2700))continue;
          item.quality=(x.interests.includes('cafes')?(p.rating||4)*15:0)-metres/220-(item.info.crowded?20:0)-item.price/100;
          alternatives.push(item);
        }
        alternatives.sort((a,b)=>b.quality-a.quality||a.p.id.localeCompare(b.p.id));
        if(!alternatives.length)return false;
        const a=alternatives[0];attach(a.p,'meal',a.leg,a.length,'Питание: учтены время, близость, участники и стоимость.',slot.kind);return true;
      }
      function candidates(slot){
        const possible=[];
        for(const p of x.pois){
          if(p.kind!=='attraction')continue;
          const interest=x.interests.filter(c=>p.categories.includes(c));
          if(!required.has(p.id)&&!interest.length)continue;
          const normal=Math.max(60,Math.round(p.duration_minutes*PACE[x.pace].scale/5)*5);
          // A different pace changes time AT places and the daily number of visits.
          const duration=Math.min(x.pace==='relaxed'?270: x.pace==='balanced'?230:180,normal);
          const v=feasible(p,'visit',duration);if(!v)continue;
          if(slot&&v.begin+duration>slot.latest)continue;
          const fresh=interest.filter(c=>!covered.has(c));
          const novelty=sum(fresh.map(c=>x.primary.includes(c)?110:85));
          const recurring=sum(interest.map(c=>x.primary.includes(c)?12:4));
          const seasonal=v.info.seasonMatch?35:0;
          const groupPenalty=(x.group.senior>0&&p.senior_friendly===false?45:0)+(x.group.child>0&&p.family_friendly===false?30:0);
          const requiredBoost=required.has(p.id)?400:0;
          const score=requiredBoost+novelty+recurring+seasonal+rank.get(p.id).match_count*5+
            (p.rating||4)*2-v.leg.planned_minutes*0.8-groupPenalty-v.price*0.06;
          possible.push({...v,score,required:required.has(p.id),fresh});
        }
        possible.sort((a,b)=>b.score-a.score||a.p.id.localeCompare(b.p.id));return possible;
      }
      function visit(slot){
        if(visitsToday>=PACE[x.pace].max)return false;
        const items=candidates(slot);if(!items.length)return false;
        // Test next meal viability before choosing a visit, where possible.
        for(const v of items){
          if(slot){
            const afterward=v.begin+v.length;
            const potential=restaurants.some(p=>{
              if(used.has(p.id)||p.id===v.p.id||!suitability(p,x.group))return false;
              if(v.p.river_bank&&p.river_bank&&v.p.river_bank!==p.river_bank)return false;
              const info=dateAttributes(p,date),leg=transportLeg(v.p,p,x.mobility,x.group,date);
              if(!info.available||info.crowded||!leg)return false;
              const mealStart=afterward+leg.planned_minutes,mealEnd=mealStart+Math.max(50,Math.min(60,p.duration_minutes));
              return mealStart>=slot.earliest&&mealStart<=slot.latest&&mealEnd<=x.end&&mealEnd<=info.closes&&
                mealStart>=info.opens&&availableBudget(v.price+groupPrice(p,x.group)+leg.transport_cost_cny,v.p.id,Math.max(0,remainingMeals()-1));
            });
            if(!potential)continue;
          }
          const reason=(v.required?'Обязательное место. ':'')+
            (v.fresh.length?'Покрывает ещё не посещённые категории: '+v.fresh.map(c=>LABELS[c]).join(', '):
            'Соответствует выбранным интересам.')+(v.info.seasonMatch?' Сезонное совпадение.':'');
          attach(v.p,'visit',v.leg,v.length,reason);return true;
        }
        return false;
      }
      if(!impossible){
        for(const slot of plannedMeals){
          let loop=0;
          if(slot.kind!=='breakfast'){
            while(now<slot.earliest&&loop++<8&&visitsToday<PACE[x.pace].max&&visit(slot)){}
          }
          if(now<=slot.latest){
            if(!eat(slot)){
              // An attraction can be extended instead of inserting a blank wait event.
              const last=d.events[d.events.length-1],p=last&&last.type==='visit'?x.map.get(last.poi_id):null;
              if(p){
                const candidates=restaurants.filter(r=>!used.has(r.id)&&suitability(r,x.group));
                const soon=candidates.map(r=>({r,leg:transportLeg(p,r,x.mobility,x.group,date)})).filter(e=>e.leg);
                const needed=soon.length?Math.max(0,slot.earliest-now-Math.min(...soon.map(e=>e.leg.planned_minutes))):0;
                const info=dateAttributes(p,date);
                if(needed>0&&needed<=45&&now+needed<=info.closes&&now+needed<=x.end){
                  last.end_min+=needed;d.active_minutes+=needed;now+=needed;
                }
              }
              if(!eat(slot)){missingMeals++;d.warnings.push('Не удалось разместить '+({breakfast:'завтрак',lunch:'обед',dinner:'ужин'}[slot.kind])+ ' без свободного ожидания с учётом всех ограничений.');}
            }
          }else {missingMeals++;d.warnings.push('Время для приёма пищи «'+slot.kind+'» уже прошло.');}
        }
        while(visitsToday<PACE[x.pace].max&&visit(null)){}
      }
      if(x.pace==='balanced'&&visitsToday===1)d.warnings.push('Только одно посещение: строгие ограничения даты, транспорта, часов или бюджета не позволили добавить второе.');
      d.total_cost_cny=Math.round(d.total_cost_cny*100)/100;
      days.push(d);warnings.push(...d.warnings);
    }
    const unplaced=x.required.filter(id=>!used.has(id)),uncovered=x.interests.filter(c=>!covered.has(c));
    if(unplaced.length)warnings.push('Не размещены обязательные места: '+unplaced.map(id=>x.map.get(id).name).join(', ')+'.');
    if(uncovered.length)warnings.push('Не покрыты выбранные категории: '+uncovered.map(c=>LABELS[c]).join(', ')+'. При текущих жёстких ограничениях жадный алгоритм не смог найти допустимый маршрут.');
    if(impossible)warnings.push('Нижняя оценка обязательных расходов превышает бюджет.');
    const events=days.flatMap(d=>d.events),visits=events.filter(e=>e.type==='visit'),meals=events.filter(e=>e.type==='meal'),legs=events.filter(e=>e.type==='travel');
    const checks={
      time:days.every(d=>d.events.every((e,i)=>e.start_min>=x.start&&e.end_min<=x.end&&e.end_min>e.start_min&&(!i||e.start_min===d.events[i-1].end_min)))?'passed':'failed',
      budget:spent<=x.budget+0.000001&&!impossible?'passed':'failed',
      opening_hours:days.every(d=>d.events.filter(e=>['visit','meal'].includes(e.type)).every(e=>{const info=dateAttributes(x.map.get(e.poi_id),d.date);return info.available&&e.start_min>=info.opens&&e.end_min<=info.closes;}))?'passed':'failed',
      meals:missingMeals?'failed':'passed',required_places:unplaced.length?'failed':'passed',interest_coverage:uncovered.length?'failed':'passed',
      no_gaps:days.every(d=>!d.events.some(e=>e.type==='wait'||e.type==='rest'))?'passed':'failed'
    };
    const status=impossible?'infeasible':Object.values(checks).includes('failed')||!visits.length&& !x.interests.includes('cafes')?'partial':'demo';
    return {schema_version:'shanghai-demo-v1',model_version:'shanghai-group-season-interests-v2',status,days,
      selected_ids:[...used],ranking:rankings,unplaced_required_ids:unplaced,uncovered_interests:uncovered,
      checks,warnings:unique(warnings),summary:{total_cost_cny:Math.round(spent*100)/100,budget_cny:x.budget,
        visit_count:visits.length,meal_count:meals.length,estimated_travel_minutes:sum(legs.map(e=>e.base_minutes)),
        planned_travel_minutes:sum(legs.map(e=>e.planned_minutes)),transport_cost_cny:sum(legs.map(e=>e.transport_cost_cny||0)),
        covered_interests:[...covered],days_count:days.length,synthetic_data:true,transport_basis:'synthetic_estimate',group:x.group,
        transport_mode:x.mobility,first_last_poi:true}};
  }
  return {planTrip,rankPois,groupPrice,transportLeg};
});
