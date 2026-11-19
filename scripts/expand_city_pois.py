#!/usr/bin/env python3
"""Add (never regenerate) POIs using a seeded transit/district spatial intensity model.

This is explicitly a DEMO-POINT generator. Without OSM building, land-use,
water and routing geometries, generated locations are NOT verified addresses.

Run from an arbitrary folder:
 python scripts/expand_city_pois.py --config config/shanghai.json \
   --base /path/to/original_2000_pois.json --output data/demo/shanghai/pois.json

With an already-complete catalogue it copies it unchanged (idempotent).
All parameters for another city are in its config, station CSV and local-name
lexicon. The original POI objects are never modified by this script.
"""
from __future__ import annotations
import argparse, csv, copy, hashlib, json, math, random, re
from collections import Counter, defaultdict
from pathlib import Path

# A small Chinese/English/Russian fictitious brand vocabulary. Translate
# descriptors semantically; do NOT imply that these businesses actually exist.

def geo_km(a,b,c,d):
    return math.hypot((a-c)*111.2, (b-d)*111.2*math.cos(math.radians((a+c)/2)))

def radius_km(lat,lon,center): return geo_km(lat,lon,center['lat'],center['lon'])

def nearest_segment_m(lat,lon,points):
    # Conservative approximate line only, NOT an OSM water polygon.
    x,y=lon*95300,lat*111200; best=1e12
    for a,b in zip(points,points[1:]):
        ax,ay=a['lon']*95300,a['lat']*111200
        bx,by=b['lon']*95300,b['lat']*111200
        t=max(0,min(1,((x-ax)*(bx-ax)+(y-ay)*(by-ay))/max(1,(bx-ax)**2+(by-ay)**2)))
        best=min(best,math.hypot(x-ax-t*(bx-ax),y-ay-t*(by-ay)))
    return best

def allocate_counts(cat_add,band_add,prefs):
    cats=list(cat_add);bands=list(band_add)
    assert sum(cat_add.values())==sum(band_add.values())
    mat=[[max(1e-12,cat_add[c]*prefs[c][i]) for i in range(len(bands))] for c in cats]
    for _ in range(150):
        for i,c in enumerate(cats):
            k=cat_add[c]/sum(mat[i]);mat[i]=[v*k for v in mat[i]]
        for j,b in enumerate(bands):
            k=band_add[b]/sum(mat[i][j] for i in range(len(cats)))
            for i in range(len(cats)):mat[i][j]*=k
    out=[[int(math.floor(v)) for v in row] for row in mat]
    remainrow=[cat_add[cats[i]]-sum(row) for i,row in enumerate(out)]
    remaincol=[band_add[bands[j]]-sum(out[i][j] for i in range(len(cats))) for j in range(len(bands))]
    while sum(remainrow):
        candidates=[(mat[i][j]-out[i][j],i,j) for i in range(len(cats)) for j in range(len(bands)) if remainrow[i]>0 and remaincol[j]>0]
        _,i,j=max(candidates,key=lambda x:(x[0],-x[1],-x[2]));out[i][j]+=1;remainrow[i]-=1;remaincol[j]-=1
    assert not any(remaincol)
    return {c:{b:out[i][j] for j,b in enumerate(bands)} for i,c in enumerate(cats)}

class SpaceSampler:
    def __init__(self,config,stations,existing,rng):
        self.cf=config;self.stations=stations;self.rng=rng;self.center=config['center'];self.hubs=config['district_centers']
        self.bounds=config['conservative_sampling_box_approx'];self.used=defaultdict(list);self.station_count=0;self.district_count=0;self.weight_cache={}
        self.metros_by_band={b:[(idx,st) for idx,st in enumerate(stations) if lo<=radius_km(st['lat'],st['lon'],self.center)<hi] for b,(lo,hi) in ((b,config['radial_bands_km'][b]) for b in config['radial_bands_km'])}
        for poi in existing:self.add(poi['lat'],poi['lon'])
        self.attraction_centers=[(p['lat'],p['lon']) for p in existing if p['kind']!='restaurant']
        self.cafe_biased_hubs=[]
    def xy(self,lat,lon):return round(lat*111200/18),round(lon*95300/18)
    def add(self,lat,lon):
        self.used[self.xy(lat,lon)].append((lat,lon))
    def free(self,lat,lon):
        a,b=self.xy(lat,lon)
        for i in range(a-1,a+2):
            for j in range(b-1,b+2):
                for la,lo in self.used.get((i,j),[]):
                    if geo_km(lat,lon,la,lo)<0.012:return False
        return True
    def nearest_metro(self,lat,lon):
        if not self.stations:return None,None
        idx=min(range(len(self.stations)),key=lambda j:geo_km(lat,lon,self.stations[j]['lat'],self.stations[j]['lon']))
        st=self.stations[idx]
        return idx,round(geo_km(lat,lon,st['lat'],st['lon'])*1000,1)
    def zone(self,lat,lon):
        return min(self.hubs,key=lambda z:geo_km(lat,lon,z['lat'],z['lon'])/max(1.0, math.sqrt(z['weight'])))
    def affinity(self,lat,lon,category):
        key='nature_hub_proxies' if category=='nature' else 'cultural_hub_proxies' if category in ('history','museums','viewpoints','unusual') else 'commercial_hub_proxies'
        hubs=self.cf.get(key,[])
        if not hubs:return 1.0
        d=min(geo_km(lat,lon,a['lat'],a['lon']) for a in hubs)
        # Accept mixed neighborhoods even far from the listed proxies.
        return 0.68+1.65*math.exp(-(d/3.0)**2)
    def sample(self,category,band):
        lo,hi=self.cf['radial_bands_km'][band]
        opt=self.metros_by_band[band]
        regions=[z for z in self.hubs if lo-5 <= radius_km(z['lat'],z['lon'],self.center) < hi+4]
        for attempt in range(15000):
            by_station=bool(opt) and (not regions or self.rng.random()<self.cf['transit_component_share'])
            if by_station:
                cache_key=(category,band)
                if cache_key not in self.weight_cache:
                    options=[];weights=[]
                    for i,st in opt:
                        r=radius_km(st['lat'],st['lon'],self.center)
                        x=(0.58+0.42*math.exp(-r/13.0))*((1+0.28*(max(1,st['lines'])-1)))
                        x*=self.affinity(st['lat'],st['lon'],category)
                        if category=='cafes' and self.attraction_centers:
                            local=sum(1 for a,b in self.attraction_centers if geo_km(st['lat'],st['lon'],a,b)<1.35)
                            x*=0.66+min(2.5,local/5)
                        options.append((i,st));weights.append(max(.02,x))
                    self.weight_cache[cache_key]=(options,weights)
                options,weights=self.weight_cache[cache_key]
                i,st=self.rng.choices(options,weights=weights,k=1)[0]
                sigma=(0.45+0.015*max(0,radius_km(st['lat'],st['lon'],self.center)-4))
                if category=='nature':sigma*=1.65
                if category=='history':sigma*=1.1
                ang=self.rng.random()*2*math.pi
                distance=abs(self.rng.gauss(0,sigma))*math.sqrt(self.rng.uniform(.75,1.65))
                lat=st['lat']+distance*math.sin(ang)/111.2
                lon=st['lon']+distance*math.cos(ang)/(111.2*math.cos(math.radians(lat)))
                component='metro_station_kernel'
            else:
                if not regions:continue
                rw=[(0.8+z['weight']**.5)*self.affinity(z['lat'],z['lon'],category) for z in regions]
                z=self.rng.choices(regions,weights=rw,k=1)[0]
                sig=0.50+0.25*min(6,math.sqrt(z['weight']))
                if category=='nature':sig*=1.45
                dx=self.rng.gauss(0,sig*1.18);dy=self.rng.gauss(0,sig*.87)
                lat=z['lat']+dy/111.2
                lon=z['lon']+dx/(111.2*math.cos(math.radians(lat)))
                component='district_activity_kernel'
            r=radius_km(lat,lon,self.center)
            if not (lo<=r<hi):continue
            if not (self.bounds['min_lat']<lat<self.bounds['max_lat'] and self.bounds['min_lon']<lon<self.bounds['max_lon']):continue
            if nearest_segment_m(lat,lon,self.cf.get('river_centreline_approximation', [])) < (95 if category=='viewpoints' else 190):continue
            # The main algorithm remains a continuous density process; the only
            # minimum distance is a *technical* 12m check for new simulated points.
            lat=round(lat,7);lon=round(lon,7)
            if not self.free(lat,lon):continue
            i,dist=self.nearest_metro(lat,lon)
            if dist is not None and dist>3300 and component=='metro_station_kernel':continue
            z=self.zone(lat,lon)
            self.add(lat,lon)
            if category!='cafes':self.attraction_centers.append((lat,lon))
            if component=='metro_station_kernel':self.station_count+=1
            else:self.district_count+=1
            return lat,lon,i,dist,z,component
        raise RuntimeError('No valid model coordinate after 15000 attempts: '+category+' '+band)

def create_name(category,area,rng,seen,lexicon):
    zh_area=area['name_zh'];en_area=area['name_en'];ru_area=area['name_ru']
    for attempt in range(50000):
        a,b=rng.sample(lexicon['roots'],2);t=rng.choice(lexicon['types'][category])
        pattern=rng.randrange(4)
        if pattern==0: zh=a[0]+b[0]+t[0]; en=a[1]+' '+b[1]+' '+t[1]; ru=t[2].capitalize()+' «'+a[2]+' — '+b[2]+'»'
        elif pattern==1: zh=a[0]+'与'+b[0]+t[0]; en=a[1]+' and '+b[1]+' '+t[1]; ru=t[2].capitalize()+' «'+a[2]+' и '+b[2]+'»'
        elif pattern==2: zh=a[0]+'·'+b[0]+t[0]; en=a[1]+' / '+b[1]+' '+t[1]; ru=t[2].capitalize()+' «'+a[2]+' / '+b[2]+'»'
        else: zh=a[0]+'小'+b[0]+t[0]; en='Little '+a[1]+' '+b[1]+' '+t[1]; ru=t[2].capitalize()+' «Маленький '+a[2]+' и '+b[2]+'»'
        zh+='（'+zh_area+'）';en+=' — '+en_area;ru+=' — '+ru_area
        key=zh.casefold()
        if key not in seen:
            seen.add(key);return {'zh':zh,'en':en,'ru':ru}
    raise RuntimeError('Name space exhausted: '+category)

def demo_properties(cat,lat,lon,rng,center):
    prices={
       'museums':[0,20,30,45,60,80,100,130],'history':[0,0,0,15,25,45,65],
       'nature':[0,0,0,10,20,35,50],'cafes':[20,28,35,45,55,68,85,110,130,180],
       'entertainment':[20,40,60,80,110,140,180,220], 'shopping':[0,0,0,0],
       'viewpoints':[0,0,20,45,70,100,150], 'unusual':[0,10,25,45,60,90,130]}
    durations={
       'museums':[45,60,75,90,105,120,150], 'history':[35,45,60,75,90,110],
       'nature':[35,50,60,75,90,120,150], 'cafes':[25,35,45,55,65,75,90],
       'entertainment':[60,75,90,120,150,180], 'shopping':[30,45,60,75,90,120],
       'viewpoints':[20,30,40,50,60], 'unusual':[30,45,60,75,100,120]}
    hours={
       'museums':[('09:00','17:00'),('09:30','17:30'),('10:00','18:00'),('10:00','19:00')],
       'history':[('09:00','18:00'),('10:00','19:00'),('08:30','18:30'),('10:00','20:00')],
       'nature':[('06:00','19:00'),('07:00','20:00'),('08:00','18:30'),('06:30','21:00')],
       'cafes':[('07:00','20:00'),('08:00','21:30'),('09:00','22:00'),('10:00','22:30'),('06:30','17:30'),('11:00','23:00')],
       'entertainment':[('10:00','20:00'),('11:00','22:00'),('12:00','21:30'),('09:00','18:00')],
       'shopping':[('09:30','20:00'),('10:00','22:00'),('10:30','21:30'),('09:00','19:00')],
       'viewpoints':[('09:00','21:00'),('10:00','22:00'),('08:00','18:30')],
       'unusual':[('09:30','18:30'),('10:00','20:00'),('11:00','19:00'),('12:00','22:00')]}
    closed=[]
    if cat=='museums':closed=[1] if rng.random()<.58 else [2] if rng.random()<.09 else []
    elif cat in ('history','unusual','entertainment') and rng.random()<.20:closed=[rng.choice([1,2,3])]
    elif cat=='cafes' and rng.random()<.065:closed=[rng.choice([1,2,3])]
    price=rng.choice(prices[cat]);op,cl=rng.choice(hours[cat])
    if cat=='cafes' and price>=110 and op<'09:00':op='09:00'
    review_base=rng.lognormvariate(4.8,1.07)
    reviews=max(4,min(4500,int(review_base*(1.1 if radius_km(lat,lon,center)<7 else .65))))
    family=rng.random()<{'museums':.83,'history':.81,'nature':.93,'cafes':.87,'entertainment':.73,'shopping':.92,'viewpoints':.86,'unusual':.77}[cat]
    senior=rng.random()<{'museums':.91,'history':.86,'nature':.88,'cafes':.96,'entertainment':.79,'shopping':.94,'viewpoints':.75,'unusual':.83}[cat]
    pet=rng.random()<{'museums':.07,'history':.15,'nature':.58,'cafes':.31,'entertainment':.13,'shopping':.24,'viewpoints':.43,'unusual':.20}[cat]
    price_mult={'adult':1.0,'child':rng.choice([.4,.5,.6,.7]),'senior':rng.choice([.5,.6,.7,.8])}
    return {'duration_minutes':rng.choice(durations[cat]),'price_cny':price,'rating':round(rng.uniform(3.65,4.88),2),'review_count':reviews,
            'opening':{'open':op,'close':cl,'closed_weekdays':closed},
            'season_months':rng.choice([[3,4,5,6,9,10],[4,5,6,7,8,9],[]]) if cat=='nature' and rng.random()<.22 else [],
            'weekday_crowd_level':rng.choices(['low','medium','high'],weights=[3,6,1],k=1)[0],
            'weekend_crowd_level':rng.choices(['low','medium','high'],weights=[1,5,4],k=1)[0],
            'pet_friendly':pet,'family_friendly':family,'senior_friendly':senior,'price_multipliers':price_mult,
            'pet_fee_cny':rng.choice([0,0,10,20]) if pet else 0}

def run(config_path,base_path,output_path):
    cf=json.loads(config_path.read_text(encoding='utf8'))
    stations=[]
    if cf.get('metro_source'):
        with (config_path.parent.parent/cf['metro_source']).open(encoding='utf8',newline='') as f:
            stations=[{'lat':float(r['lat']),'lon':float(r['lon']),'lines':int(r['transfer_line_count_proxy'])} for r in csv.DictReader(f)]
    lexicon=json.loads((config_path.parent.parent/cf['name_lexicon']).read_text(encoding='utf8'))
    old=json.loads(base_path.read_text(encoding='utf8'));existing=old['pois']
    if len(existing)==cf['target_total']:
        assert len(set(p['id'] for p in existing))==cf['target_total']
        output_path.parent.mkdir(parents=True,exist_ok=True)
        output_path.write_bytes(base_path.read_bytes())
        print('No change: target size already reached; originals preserved');return
    assert len(existing)==cf['existing_count'],f'Expected {cf["existing_count"]} originals, got {len(existing)}'
    assert len(set(p['id'] for p in existing))==len(existing)
    base_cats=Counter((p.get('primary_category') or p['categories'][0]) for p in existing)
    target={c:base_cats[c]+cf['category_additions'][c] for c in cf['category_additions']}
    assert all(base_cats[c]+cf['category_additions'][c]==target[c] for c in target),base_cats
    rng=random.Random(cf['seed']);sampler=SpaceSampler(cf,stations,existing,rng)
    allocation=allocate_counts(cf['category_additions'],cf['radial_additions'],cf['category_ring_preferences'])
    seen={str(p.get('name_zh') or p['name']).casefold() for p in existing}
    existing_ids={p['id'] for p in existing}
    generated=[]
    prior_indexes=[int(m.group(1)) for p in existing if (m:=re.fullmatch(re.escape(cf['city_slug'])+r'-demo:synthetic-ext-v2-(\d+)',p['id']))]
    next_idx=max(prior_indexes,default=0)+1
    # Draw places to visit first; cafe intensity is then conditioned on the
    # resulting district-level attraction density without shifting any POIs.
    for cat in [c for c in cf['category_additions'] if c!='cafes']+['cafes']:
        for band,n in allocation[cat].items():
            for _ in range(n):
                lat,lon,idx,dist,area,component=sampler.sample(cat,band)
                names=create_name(cat,area,rng,seen,lexicon)
                ident=f'{cf["city_slug"]}-demo:synthetic-ext-v2-{next_idx:04d}'
                assert ident not in existing_ids
                loc={'model':'transit_district_inhomogeneous_point_process_v2','sample_component':component,
                     'model_station_index':idx,'distance_to_nearest_model_station_m_straight_line':dist,
                     'district_proxy':area['name_en'],'building_footprint_verified':False,
                     'landuse_polygon_verified':False,'water_polygon_verified':False,
                     'administrative_boundary_polygon_verified':False,'road_clearance_verified':False,
                     'entry_access_verified':False,'physical_site_verified':False}
                rec={'id':ident,'name':names['zh'],'name_zh':names['zh'],'name_en':names['en'],'name_ru':names['ru'],'names':names,
                     'kind':'restaurant' if cat=='cafes' else 'attraction','primary_category':cat,'categories':[cat],
                     'lat':lat,'lon':lon,'area':area['name_ru'],
                     'river_bank':('east' if lon>min(cf['river_centreline_approximation'],key=lambda z:abs(z['lat']-lat))['lon'] else 'west') if cf.get('river_centreline_approximation') else 'unknown',
                     'description':'Вымышленное демонстрационное место TripWise. Название, адрес, здание, вход и пешеходный подход не проверены.',
                     'source_url':'','language_note':'Invented localized brand and category descriptor; no claim about an existing business',
                     'provenance':{'identity':'synthetic_scenario_poi','coordinates':'synthetic_transit_district_density_unverified',
                                   'rating':'synthetic','review_count':'synthetic','price':'synthetic','opening':'synthetic',
                                   'duration':'planning_estimate','river_bank':'approximate','seasonality':'synthetic',
                                   'crowd':'synthetic','group_prices':'synthetic','osm_building_landuse_water_roads':'NOT_VERIFIED'},
                     'synthetic_location':loc}
                rec.update(demo_properties(cat,lat,lon,rng,cf['center']));generated.append(rec);next_idx+=1
    assert len(generated)==cf['new_count'] and len(existing)+len(generated)==cf['target_total']
    data=copy.deepcopy(old)
    data['pois']=copy.deepcopy(existing)+generated
    data['description']=f'{cf["city_name_en"]}: {len(existing)} unchanged original POIs + {len(generated)} added synthetic test POIs; synthetic coordinates are NOT verified sites.'
    provenance=copy.deepcopy(data.get('catalog_provenance',{}))
    provenance['extension_v2']={'model':'inhomogeneous_transit_and_district_intensity_v2','seed':cf['seed'],
       'original_unchanged':len(existing),'new_synthetic':len(generated),'station_centres_in_archived_model':len(stations),
       'new_station_component':sampler.station_count,'new_district_component':sampler.district_count,
       'osm_pbf_downloaded':False,'verified_new_building_geometry':0,'verified_new_road_entries':0,
       'input_base_sha256':hashlib.sha256(base_path.read_bytes()).hexdigest()}
    data['catalog_provenance']=provenance
    previous_synthetic=sum(p.get('provenance',{}).get('identity') in ('synthetic_place','synthetic_scenario_poi','synthetic_venue_near_real_place') for p in existing)
    data['synthetic_placement_notice']=f'{previous_synthetic+len(generated)} fictional POIs in the final catalog ({previous_synthetic} previous + {len(generated)} new); station/district model is not checked against buildings, landuse, water polygons, entrances or street network. Straight-line metrics only.'
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps({'base':len(existing),'new':len(generated),'total':len(data['pois']),
      'new_by_category':dict(Counter(p['primary_category'] for p in generated)),
      'new_by_ring':dict(Counter('0_7' if radius_km(p['lat'],p['lon'],cf['center'])<7 else '7_20' if radius_km(p['lat'],p['lon'],cf['center'])<20 else '20_45' for p in generated)),
      'new_sampling':{'metro':sampler.station_count,'district':sampler.district_count}},ensure_ascii=False,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',type=Path,default=Path(__file__).resolve().parents[1]/'config/shanghai.json')
    ap.add_argument('--base',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'data/demo/shanghai/pois.json')
    a=ap.parse_args();run(a.config,a.base,a.output)
