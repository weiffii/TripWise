#!/usr/bin/env python3
"""Standalone PNG overview + central zoom, based on the full unfiltered 5000 POIs.
NO basemap/verified streets are used, because OSM extract was not available.
"""
import json,math,argparse
from collections import Counter
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from matplotlib.lines import Line2D

CATEGORIES=[('museums','Музеи','#8C65BD'),('history','История','#C27A54'),('nature','Природа','#3B9673'),('cafes','Кафе и рестораны','#D35F6D'),('entertainment','Развлечения','#5876C5'),('shopping','Шопинг','#BA8F2C'),('viewpoints','Смотровые','#2A9DAC'),('unusual','Необычные места','#916CAD')]

def main(root):
    data=json.loads((root/'data/demo/shanghai/pois.json').read_text())['pois']
    cfg=json.loads((root/'config/shanghai.json').read_text())
    x=np.array([(p['lon']-cfg['center']['lon'])*111.2*math.cos(math.radians(cfg['center']['lat'])) for p in data]); y=np.array([(p['lat']-cfg['center']['lat'])*111.2 for p in data])
    kinds=np.array([p.get('primary_category') or p['categories'][0] for p in data]);
    plt.rcParams['font.family']='DejaVu Sans'
    fig=plt.figure(figsize=(19,10.8),dpi=150,facecolor='#FFFFFF')
    gs=fig.add_gridspec(1,2,left=.047,right=.979,top=.825,bottom=.205,wspace=.14,width_ratios=[1.65,1.05])
    ax=fig.add_subplot(gs[0]);az=fig.add_subplot(gs[1])
    for a in (ax,az):
        a.set_facecolor('#FBFBFC');a.grid(color='#D7DEE5',alpha=.52,linestyle='--',linewidth=.52)
        a.axhline(0,color='#B8C4CD',alpha=.4,linewidth=.8)
        a.axvline(0,color='#B8C4CD',alpha=.4,linewidth=.8)
        a.set_aspect('equal');a.set_xlabel('Расстояние от центра по долготе, км',fontsize=10,color='#566676')
        a.set_ylabel('Расстояние от центра по широте, км',fontsize=10,color='#566676')
        a.tick_params(labelsize=9,labelcolor='#516479')
        for spine in a.spines.values():spine.set_color('#D4DCE3')
    for label,_,col in CATEGORIES:
        mask=kinds==label
        ax.scatter(x[mask],y[mask],s=13.0,c=col,edgecolors='none',alpha=.72,zorder=3,rasterized=True)
        az.scatter(x[mask],y[mask],s=17.5,c=col,edgecolors='none',alpha=.76,zorder=3,rasterized=True)
    for r,ls in [(7,(0,(4,3))),(20,(0,(6,5))),(45,(0,(6,5)))]:
        ax.add_patch(Circle((0,0),r,fill=False,color='#44546A',alpha=.63,linestyle=ls,linewidth=1.2,zorder=4))
        if r<=20:az.add_patch(Circle((0,0),r,fill=False,color='#44546A',alpha=.63,linestyle=ls,linewidth=1.2,zorder=4))
    ax.scatter([0],[0],marker='*',s=140,c='#26354A',edgecolors='white',linewidth=1.0,zorder=7)
    az.scatter([0],[0],marker='*',s=130,c='#26354A',edgecolors='white',linewidth=1.0,zorder=7)
    ax.set_xlim(-48,48);ax.set_ylim(-47,48)
    az.set_xlim(-11,11);az.set_ylim(-11,11)
    ax.set_title('Шанхай и пригороды · все 5 000 точек',loc='left',fontsize=15,color='#26334B',weight='bold',pad=16)
    az.set_title('Центр · масштаб крупнее',loc='left',fontsize=15,color='#26334B',weight='bold',pad=16)
    ax.text(-45.8,44.0,'Внешнее кольцо: 45 км от центра',fontsize=9,color='#475569',ha='left')
    az.text(-10.5,9.7,'Пунктир: радиус 7 км',fontsize=9,color='#475569')
    # Label only a few suburban proxies to aid orientation; NOT admin borders.
    district_labels=[('Цзядин',31.337,121.249,(-10,8)),('Сунцзян',31.045,121.226,(5,-14)),
      ('Цинпу',31.151,121.117,(-25,9)),('Пудун',31.240,121.506,(8,6)),
      ('Баошань',31.354,121.434,(6,10)),('Цибао',31.158,121.350,(-20,-10))]
    for title,lat,lon,offset in district_labels:
        px=(lon-cfg['center']['lon'])*111.2*math.cos(math.radians(cfg['center']['lat']));py=(lat-cfg['center']['lat'])*111.2
        ax.annotate(title,(px,py),xytext=offset,textcoords='offset points',fontsize=9,color='#405168',alpha=.95,zorder=10,
          bbox=dict(facecolor='white',alpha=.67,edgecolor='none',boxstyle='round,pad=.14'))
    fig.text(.047,.95,'TripWise  /  SHANGHAI DATA PILOT',fontsize=12,weight='bold',color='#6A5796')
    fig.text(.047,.902,'Каталог Шанхая: 5 000 туристических POI',fontsize=26,weight='bold',color='#17253C')
    fig.text(.978,.952,'19.11.2026',fontsize=11,color='#657286',ha='right')
    fig.text(.979,.905,'2 000 сохранены  ·  3 000 добавлены',fontsize=15,ha='right',color='#46566B')
    fig.text(.047,.148,'РАСПРЕДЕЛЕНИЕ КАТЕГОРИЙ',fontsize=11,weight='bold',color='#344255')
    handles=[Line2D([0],[0],marker='o',markersize=8,linestyle='None',color=color,label=f'{name}  ·  {int(np.sum(kinds==c)):,}'.replace(',',' ')) for c,name,color in CATEGORIES]
    fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.047,.054),ncol=4,frameon=False,fontsize=10.7,labelspacing=1.15,columnspacing=2.1)
    fig.text(.978,.069,'Цвет = основная категория   ·   * = центр города',ha='right',fontsize=9.6,color='#566377')
    fig.text(.047,.021,'Важно: визуализация модельных координат. Без OSM-проверки зданий, воды, административных границ и реальных пешеходных подходов.',fontsize=9.2,color='#A04C53')
    out=root/'artifacts/TripWise_Shanghai_5000_POI_COVERAGE.png'
    out.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(out,dpi=150,facecolor='white')
    plt.close(fig)
    print('Rendered',out,'points',len(data),'dimensions',2850,1620)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);arg=parser.parse_args()
    main(arg.root)
