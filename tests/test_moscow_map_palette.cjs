'use strict';
/* This is a source contract test, not a browser screenshot/E2E test. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const map=fs.readFileSync(path.join(root,'ai_ds/web_demo/shanghai-map.js'),'utf8');
const result=fs.readFileSync(path.join(root,'frontend/trip-result.js'),'utf8');
const page=fs.readFileSync(path.join(root,'backend/pages/result.html'),'utf8');
const main=fs.readFileSync(path.join(root,'backend/main.py'),'utf8');
test('original result page still serves actual shared route-map.js',()=>{
 assert.match(main,/['"]\/route-map\.js['"]/);
 assert.match(page,/<script src="\/route-map\.js" defer>/);
 assert.match(map,/root\.TripWiseMap=\{create\}/);
});
test('markers are colored by scheduled meal, not catalogue primary category',()=>{
 assert.match(map,/__tripMeal:e\.type==='meal'/);
 assert.match(map,/meal\?'#FACC15':'#7C3AED'/);
 assert.match(map,/__tripRequired:mandatory\.has\(e\.poi_id\)/);
 assert.match(map,/mandatory\?'#111827':'white'/);
});
test('drawn leg mode chooses green walk, blue transit, orange taxi',()=>{
 for(const hex of ['#16A34A','#2563EB','#F97316'])assert(map.includes(hex));
 assert.match(map,/seg\.mode\)\?'walk'/);
 assert.match(map,/hasStreetGeometry\?'':'8 5'/);
 assert.match(result,/step\.geometry_verified === true/);
});
test('day selection passes required ids and produces matching stop order',()=>{
 assert.match(result,/required_ids:trip\.preferences\?\.required_ids/);
 assert.match(map,/filter\(e=>e\.type==='visit'\|\|e\.type==='meal'\)/);
 assert.match(page,/Пунктир — приблизительная геометрия/);
});
