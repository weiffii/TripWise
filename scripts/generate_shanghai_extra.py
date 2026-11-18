"""Create 54 explicitly synthetic scenario POIs for Shanghai v2 demo.

Original 46 POIs remain untouched. Use these only for offline model experiments.
"""
import json
from pathlib import Path

DISTRICTS = [
    ('Народная площадь', 31.2304, 121.4737, 'west'),
    ('Сюйцзяхуэй', 31.1938, 121.4374, 'west'),
    ('Тяньцзыфан', 31.2085, 121.4709, 'west'),
    ('Хунцяо', 31.2000, 121.3400, 'west'),
    ('Чжанцзян', 31.2011, 121.5930, 'east'),
    ('Луцзяцзуй', 31.2348, 121.5036, 'east'),
    ('Сэнчури-парк', 31.2185, 121.5444, 'east'),
    ('Янпу', 31.2709, 121.5244, 'east'),
    ('Цзинъань', 31.2245, 121.4457, 'west'),
]
TYPES = [
    ('Учебный музейный объект', 'attraction', ['museums', 'history'], 95, 75),
    ('Учебная парковая прогулка', 'attraction', ['nature', 'viewpoints'], 100, 0),
    ('Учебная необычная игровая зона', 'attraction', ['shopping', 'unusual', 'entertainment'], 85, 35),
    ('Учебное кафе В', 'restaurant', ['cafes'], 55, 55),
    ('Учебное кафе А', 'restaurant', ['cafes'], 55, 50),
    ('Учебное кафе Б', 'restaurant', ['cafes'], 55, 60),
]


def extras():
    pois=[]
    for di,(area,lat,lon,bank) in enumerate(DISTRICTS):
        for ti,(name,kind,categories,duration,price) in enumerate(TYPES):
            # Tiny deterministic coordinate offsets. NOT verified entrances or venues.
            north=lat + (ti%3-1)*0.0021 + (di%2)*0.0002
            east=lon + (ti//3-0.5)*0.0027
            seasonal=ti==1 and di%2==0
            pet_friendly=ti==1 or (ti>=3 and di%2==0)
            poi={
                'id':f'shanghai-demo:synthetic-v2-{di+1:02d}-{ti+1:02d}',
                'name':f'{name} — {area} (синтетика)',
                'kind':kind, 'categories':categories,
                'lat':round(north,6),'lon':round(east,6),
                'duration_minutes':duration+(di%3)*5,
                'price_cny':price+(di%4)*8,
                'rating':round(4.1+(di%4)*0.14+(ti%2)*0.08,2),
                'review_count':800+di*250+ti*60,
                'opening':{'open':'08:00' if ti==1 else ('09:00' if kind=='attraction' else '08:00'),
                           'close':'21:00' if kind=='restaurant' else '20:00','closed_weekdays':[]},
                'area':area,'river_bank':bank,
                'season_months':[3,4,5,6,9,10] if seasonal else [],
                'weekend_crowd_level':'high' if ti==2 and di%2==0 else 'medium',
                'weekday_crowd_level':'low',
                'pet_friendly':pet_friendly,
                'family_friendly':ti!=2,
                'senior_friendly':ti!=3,
                'price_multipliers':{'adult':1.0,'child':0.5 if kind=='attraction' else 0.65,
                                     'senior':0.7 if kind=='attraction' else 0.9},
                'pet_fee_cny':0,
                'description':'Полностью синтетический тестовый объект в районе '+area+
                   '. Название не означает существование реального заведения или достопримечательности.',
                'source_url':'https://english.shanghai.gov.cn/',
                'provenance':{'identity':'synthetic_scenario_poi','coordinates':'synthetic_approximation',
                              'rating':'synthetic','review_count':'synthetic','price':'synthetic',
                              'opening':'synthetic','duration':'planning_estimate',
                              'river_bank':'planning_assumption','seasonality':'synthetic',
                              'crowd':'synthetic','group_prices':'synthetic'},
            }
            pois.append(poi)
    assert len(pois)==54 and len({p['id'] for p in pois})==54
    return pois


if __name__=='__main__':
    target=Path(__file__).resolve().parents[1]/'data/demo/shanghai/pois-extra.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'schema_version':'shanghai-demo-v1', 'is_demo':True,
          'description':'54 полностью синтетических тестовых POI; не реальные места.',
          'pois':extras()},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(target)
