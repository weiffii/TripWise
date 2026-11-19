/* Контракт текущего неизменяемого каталога. Не подтверждает геометрию и покрытие едой. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const file=path.join(__dirname,'../data/demo/shanghai/pois.json');
const bytes=fs.readFileSync(file);
assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),'72184b2b6a221be96535529fb70e78fb3146b1707c4411ec1b4bcd34d240380d','Исходный каталог не должен изменяться');
const data=JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/,'')),pois=data.pois;
const categories=new Set(['museums','history','nature','cafes','entertainment','shopping','viewpoints','unusual']);
const time=value=>{assert.equal(typeof value,'string');assert(/^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$/.test(value),value);const [h,m]=value.split(':').map(Number);return h*60+m;};
assert.equal(data.is_demo,true);
assert.equal(pois.length,5000);
assert.equal(pois.filter(p=>p.kind==='restaurant').length,2000);
const ids=new Set();let fictional=0;
for(const p of pois){
 assert.equal(typeof p.id,'string');assert(p.id.trim());assert(!ids.has(p.id),p.id);ids.add(p.id);
 assert(['restaurant','attraction'].includes(p.kind),p.id);
 assert(Array.isArray(p.categories)&&p.categories.length>0,p.id);
 assert.equal(new Set(p.categories).size,p.categories.length,p.id);
 assert(p.categories.every(c=>categories.has(c)),p.id);
 assert.equal(p.kind==='restaurant',p.categories.includes('cafes'),p.id);
 assert(Number.isFinite(p.lat)&&p.lat>=-90&&p.lat<=90,p.id);
 assert(Number.isFinite(p.lon)&&p.lon>=-180&&p.lon<=180,p.id);
 assert(Number.isFinite(p.price_cny)&&p.price_cny>=0,p.id);
 assert(Number.isFinite(p.duration_minutes)&&p.duration_minutes>0,p.id);
 if(p.avg_check_cny!=null)assert(Number.isFinite(p.avg_check_cny)&&p.avg_check_cny>=0,p.id);
 assert(p.opening&&time(p.opening.open)<time(p.opening.close),p.id);
 assert(Array.isArray(p.opening.closed_weekdays),p.id);
 assert(p.opening.closed_weekdays.every(d=>Number.isInteger(d)&&d>=1&&d<=7),p.id);
 for(const language of ['ru','en','zh'])assert(typeof p.names?.[language]==='string'&&p.names[language].trim(),p.id+':'+language);
 assert(p.provenance&&typeof p.provenance.identity==='string'&&typeof p.provenance.coordinates==='string',p.id);
 if(p.provenance.identity.startsWith('synthetic')){
  fictional++;
  assert(p.provenance.coordinates.startsWith('synthetic'),p.id);
  assert(p.coordinate_source_url==null,p.id);
  assert(p.synthetic_location&&typeof p.synthetic_location.model==='string',p.id);
  for(const [name,value] of Object.entries(p.synthetic_location))if(name.endsWith('_verified'))assert.equal(value,false,p.id+':'+name);
 }
}
assert(fictional>0,'Синтетические координаты должны иметь явное происхождение');
console.log(`Shanghai current data: ${pois.length} POI, 2000 restaurants, ${fictional} explicitly fictional; schema/provenance/SHA passed`);
