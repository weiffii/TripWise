#!/usr/bin/env python3
"""Optional future re-extraction of the published source HTML.

This tool is included for reproducibility, but the ZIP ALREADY CONTAINS
extracted source_rows.psv and district_sequence.txt; user need not run it.
Never creates random points. Polygon positions are deterministic interior
points and are NEVER labeled confirmed entrances.
"""
import argparse
import hashlib
import html
import json
import re
import urllib.request
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]/'data'/'demo'/'moscow'
URL='https://raw.githubusercontent.com/ArtemIlinn/masters-urban-analytics/main/index.html'
EXPECTED_BLOB='6d011539cdd244603095b389b0cd996ea214c4c5'


def inside(pt, ring):
    ok=False
    for i in range(len(ring)):
        a=ring[i];b=ring[i-1]
        if (a[1]>pt[1])!=(b[1]>pt[1]) and pt[0]<(b[0]-a[0])*(pt[1]-a[1])/(b[1]-a[1])+a[0]:ok=not ok
    return ok


def poly_hit(pt, feature):
    geom=feature['geometry']
    polys=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates'] if geom['type']=='MultiPolygon' else []
    return any(inside(pt,poly[0]) and not any(inside(pt,hole) for hole in poly[1:]) for poly in polys)


def ring_area(ring):
    return abs(sum(ring[i][0]*ring[i-1][1]-ring[i-1][0]*ring[i][1] for i in range(len(ring))))


def polygon_interior(feature):
    coords=feature['geometry']['coordinates']
    polys=[coords] if feature['geometry']['type']=='Polygon' else coords
    poly=max(polys,key=lambda x:ring_area(x[0]))
    bbox=feature['bbox']
    b=[float(x) for x in bbox]
    def valid(point):return inside(point,poly[0]) and not any(inside(point,hole) for hole in poly[1:])
    mid=[(b[0]+b[2])/2,(b[1]+b[3])/2]
    if valid(mid):return mid
    for n in [5,11,21,41]:
        for k in range(n):
            for j in range(n):
                pt=[b[0]+(j+.5)*(b[2]-b[0])/n,b[1]+(k+.5)*(b[3]-b[1])/n]
                if valid(pt):return pt
    return poly[0][0]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--source-html',type=Path,help='Use an already-downloaded original HTML instead of network')
    ap.add_argument('--output-dir',type=Path,default=BASE)
    args=ap.parse_args()
    raw=args.source_html.read_bytes() if args.source_html else urllib.request.urlopen(URL, timeout=180).read()
    blobsha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    if blobsha != EXPECTED_BLOB:
        raise ValueError(f'Original source map differs from the analyzed snapshot: expected {EXPECTED_BLOB}, got {blobsha}. Do not silently mix versions.')
    source=raw.decode('utf-8')
    collections=[json.loads(m.group(1)) for m in re.finditer(r'geo_json_[a-f0-9]+_add\((\{"bbox":.+?\})\);',source,re.S)]
    if len(collections)<3:raise RuntimeError('Source Folium geometry format changed')
    parks=collections[1]['features'];districts=collections[2]['features']
    allrows=[];coords=[]
    markers=list(re.finditer(r'var marker_([a-f0-9]+) = L\.marker\(\s*\[([\d.]+), ([\d.]+)\],',source))
    typemap={'Театр':'T','Музей':'M','Галерея':'G'}
    for i,m in enumerate(markers):
        segment=source[m.start():markers[i+1].start() if i+1<len(markers) else len(source)][:3400]
        info=re.search(r'<b>(.*?)</b><br>(.*?)</div>',segment)
        if info is None or info.group(2) not in typemap:raise RuntimeError(f'Unrecognized marker {i}')
        lat,lon=map(float,[m.group(2),m.group(3)])
        allrows.append(f'{i}|{lat}|{lon}|{typemap[info.group(2)]}|{html.unescape(info.group(1))}')
        coords.append([lon,lat])
    circles=list(re.finditer(r'var circle_marker_([a-f0-9]+) = L\.circleMarker\(\s*\[([\d.]+), ([\d.]+)\]',source))
    for i,m in enumerate(circles):
        segment=source[m.start():circles[i+1].start() if i+1<len(circles) else len(source)][:1200]
        info=re.search(r'bindTooltip\(\s*`<div>\s*(.*?)\s*</div>`',segment,re.S)
        if info is None or not info.group(1).endswith('(Кинотеатр)'):continue
        lat,lon=map(float,[m.group(2),m.group(3)])
        name=info.group(1).strip().removesuffix('(Кинотеатр)').strip()
        allrows.append(f'c{i}|{lat}|{lon}|C|{name}')
        coords.append([lon,lat])
    for feature in parks:
        name=feature['properties'].get('name')
        if not name or name.strip().casefold()=='без названия':continue
        lon,lat=polygon_interior(feature)
        area=feature['properties'].get('area_ha')
        allrows.append(f'p{feature["id"]}|{lat:.5f}|{lon:.5f}|N|{name}|{area}')
        coords.append([round(lon,5),round(lat,5)])
    assert len(allrows)==len(coords)==1281,(len(allrows),len(coords))
    district_names=[];sequence=[]
    for feature in districts:
        district_names.append(feature['properties']['name'])
    for pt in coords:
        d=next((i for i,f in enumerate(districts) if poly_hit(pt,f)),None)
        sequence.append('__' if d is None else format(d,'02x') if d<16 else to_base36_2(d))
    # Base-36 coding ensures 124 district identifiers fit two characters.
    sequence=[('__' if not any(poly_hit(pt, f) for f in districts) else to_base36_2(next(i for i,f in enumerate(districts) if poly_hit(pt,f)))) for pt in coords]
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'source_rows.psv').write_text('\n'.join(allrows)+'\n',encoding='utf-8')
    (args.output_dir/'district_codes.psv').write_text('\n'.join(f'{to_base36_2(i)}|{name}' for i,name in enumerate(district_names))+'\n',encoding='utf-8')
    (args.output_dir/'district_sequence.txt').write_text(''.join(sequence)+'\n',encoding='utf-8')
    print(f'Wrote {len(allrows)} map records, {len(district_names)} districts. Blob {blobsha}.')


def to_base36_2(n):
    abc='0123456789abcdefghijklmnopqrstuvwxyz'
    return abc[n//36]+abc[n%36]

if __name__=='__main__':main()
