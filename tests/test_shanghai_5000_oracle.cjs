/* Независимый полный перебор малых направленных графов, без еды/API.
   Проверяет обязательные POI, даты, окна и ровно те buffered времена,
   которые определяет предоставленная матрица дорожных длительностей. */
'use strict';
const assert=require('node:assert/strict');
const api=require('../ai_ds/web_demo/shanghai-engine.js');
function permutations(a){if(!a.length)return [[]];return a.flatMap((v,i)=>permutations(a.filter((_,j)=>i!==j)).map(p=>[v,...p]));}
const buffered=seconds=>Math.ceil((Math.max(1,Math.ceil(seconds/60))*1.1+2)/5)*5;
function exact(nodes,matrix,days,start,end){const dayValid=(seq,day)=>{let now=start,last=null;for(const p of seq){if(p.opening.closed_weekdays.includes(4+day))return false;if(last!==null)now+=buffered(matrix[last][p.index]);now=Math.max(now,p.oracle_open);now+=p.duration_minutes;if(now>Math.min(end,p.oracle_close))return false;last=p.index;}return true;};
 for(const order of permutations(nodes)){const split=(index,day)=>{if(day===days)return index===order.length;for(let stop=index;stop<=order.length;stop++)if(dayValid(order.slice(index,stop),day)&&split(stop,day+1))return true;return false;};if(split(0,0))return true;}return false;
}
let state=719;function random(n){state=(Math.imul(state,1664525)+1013904223)>>>0;return state%n;}
let cases=0,feasible=0,impossible=0;
for(let n=2;n<=5;n++)for(let trial=0;trial<12;trial++){
 const days=trial%3?1:2,start=720,end=1020,matrix=Array.from({length:n},(_,i)=>Array.from({length:n},(_,j)=>i===j?0:(2+random(23))*60));
 const nodes=Array.from({length:n},(_,index)=>{const open=start+random(9)*10,close=Math.min(end,open+70+random(16)*10),minutes=25+random(8)*5;const hh=t=>String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0');return {id:'p'+index,index,name:'Synthetic '+index,kind:'attraction',categories:['museums'],lat:31.23,lon:121.47+index*.0001,river_bank:'west',duration_minutes:minutes,price_cny:0,rating:4,review_count:10,opening:{open:hh(open),close:hh(close),closed_weekdays:days===2&&random(4)===0?[4]:[]},oracle_open:open,oracle_close:close};});
 const exists=exact(nodes,matrix,days,start,end),plan=api.planTrip({pois:nodes},{primary_interests:['museums'],days,start_date:'2026-11-19',day_start:'12:00',day_end:'17:00',budget_cny:0,transport:'walk',required_ids:nodes.map(p=>p.id),osm_tables:{foot:{ids:nodes.map(p=>p.id),durations:matrix,distances:matrix.map(row=>row.map(s=>s))}}});
 cases++;for(const k of ['time','opening_hours','budget','no_duplicates'])assert.equal(plan.checks[k],'passed');if(exists){feasible++;assert.equal(plan.summary.required_placed,n,'Oracle feasible, mandatory lost: n='+n+', trial='+trial+', unplaced='+plan.unplaced_required_ids);}else{impossible++;assert(plan.summary.required_placed<n,'Planner claims impossible graph feasible: n='+n+', trial='+trial);}
}
console.log(JSON.stringify({cases,feasible,impossible,missed_feasible:0,network_requests:0}));
