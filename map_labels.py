"""Rasterize configurable OpenMapTiles labels for shared preview/export tiles."""
from functools import lru_cache
from pathlib import Path
import math

LANGUAGES = ('local','zh-Hans','zh-Hant','en','ja','ko','de','fr','es','ru')


POI_CATEGORIES = {'transit': ['bus', 'railway', 'entrance', 'aerialway', 'ferry_terminal', 'bus_stop', 'bus_station', 'station', 'subway', 'tram_stop', 'halt', 'platform'], 'shopping': ['shop', 'clothing_store', 'alcohol_shop', 'music', 'grocery', 'supermarket', 'marketplace', 'department_store', 'bakery', 'butcher', 'seafood'], 'food': ['restaurant', 'fast_food', 'food_court'], 'cafe': ['cafe', 'ice_cream', 'confectionery', 'chocolate'], 'nightlife': ['bar', 'beer', 'pub', 'nightclub', 'biergarten'], 'lodging': ['lodging', 'hotel', 'motel', 'hostel', 'guest_house', 'dormitory'], 'residential': ['residential', 'apartments', 'house', 'housing', 'neighbourhood', 'quarter', 'suburb'], 'office': ['office', 'company', 'coworking', 'industrial', 'commercial'], 'health': ['hospital', 'clinic', 'doctors', 'dentist', 'nursing_home', 'veterinary', 'healthcare'], 'pharmacy': ['pharmacy'], 'education': ['school', 'college', 'university', 'kindergarten', 'educational_institution'], 'library': ['library', 'books'], 'finance': ['bank', 'atm', 'bureau_de_change'], 'government': ['town_hall', 'townhall', 'public_building', 'courthouse', 'community_centre', 'government', 'embassy', 'diplomatic'], 'emergency': ['police', 'fire_station', 'ambulance_station', 'emergency', 'lifeguard'], 'post': ['post', 'post_office', 'post_box', 'parcel_locker', 'courier'], 'culture': ['museum', 'art_gallery', 'gallery', 'artwork', 'arts_centre', 'theatre', 'cinema', 'music_venue'], 'attraction': ['attraction', 'castle', 'ruins', 'monument', 'memorial', 'archaeological_site', 'historic', 'viewpoint'], 'religion': ['place_of_worship', 'cemetery', 'grave_yard', 'church', 'mosque', 'temple', 'synagogue', 'religion'], 'parks': ['park', 'garden', 'nature_reserve', 'wood', 'forest', 'peak', 'spring', 'waterfall', 'beach'], 'sports': ['stadium', 'pitch', 'sports_centre', 'fitness_centre', 'fitness_station', 'golf', 'swimming', 'swimming_pool', 'track', 'tennis', 'basketball', 'soccer', 'sports'], 'recreation': ['playground', 'zoo', 'theme_park', 'amusement_arcade', 'casino', 'aquarium', 'recreation_ground'], 'camping': ['campsite', 'camp_site', 'caravan_site', 'picnic_site', 'shelter', 'wilderness_hut', 'alpine_hut'], 'parking': ['parking', 'parking_entrance', 'motorcycle_parking', 'bicycle_parking'], 'cycling': ['bicycle', 'bicycle_rental', 'bicycle_repair_station'], 'motoring': ['car', 'car_rental', 'car_sharing', 'car_wash', 'car_repair', 'car_parts', 'fuel', 'charging_station', 'taxi'], 'harbor': ['harbor', 'marina', 'dock', 'aerodrome', 'airport', 'helipad', 'terminal'], 'personal': ['laundry', 'hairdresser', 'beauty', 'dry_cleaning', 'massage', 'tailor', 'tattoo'], 'amenities': ['toilets', 'drinking_water', 'fountain', 'bench', 'telephone', 'shower', 'information', 'public_bookcase'], 'utilities': ['recycling', 'waste_basket', 'waste_disposal', 'water_works', 'wastewater_plant', 'power', 'substation', 'water_tower'], 'barriers': ['gate', 'bollard', 'lift_gate', 'barrier', 'stile', 'cycle_barrier', 'toll_booth'], 'other': []}

def poi_category(properties):
    # Specific subclass wins over broader classes such as shop and office.
    for field in ('subclass','class'):
        value=properties.get(field,'')
        for category,values in POI_CATEGORIES.items():
            if value in values:return category
    return 'other'


def label_options(config):
    language=config.get('label_language','local')
    if language not in LANGUAGES: raise ValueError('不支持的标注语言')
    excluded=config.get('poi_excluded',[name for name in POI_CATEGORIES if name not in ('transit','government','parks','attraction')])
    if not isinstance(excluded,list) or any(name not in POI_CATEGORIES for name in excluded):raise ValueError('Invalid POI categories')
    sizes={name:max(6,min(48,int(config.get(name,13)))) for name in ('street_font_size','place_font_size','poi_font_size')}
    return dict(**sizes,poi_excluded=sorted(set(excluded)),label_language=language,show_streets=bool(config.get('show_streets',True)),
                show_places=bool(config.get('show_places',True)),show_pois=bool(config.get('show_pois',True)))


def label_name(properties, language):
    keys=[]
    if language!='local':
        keys=[f'name:{language}']
        if language.startswith('zh'):keys+=['name:zh']
        keys += [f'name_{language}']
    keys+=['name','name_int','name:latin']
    return next((str(properties[k]) for k in keys if properties.get(k)), '')


@lru_cache(maxsize=32)
def font_for(language,size):
    from PIL import ImageFont
    candidates=['/System/Library/Fonts/Hiragino Sans GB.ttc','/System/Library/Fonts/STHeiti Medium.ttc',
                '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                'C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    if language=='ja':
        japanese=next(Path('/System/Library/Fonts').glob('ヒラ* W3.ttc'),None)
        if japanese:candidates.insert(0,str(japanese))
    if language=='ko': candidates.insert(0,'/System/Library/Fonts/AppleSDGothicNeo.ttc')
    for path in candidates:
        if Path(path).exists(): return ImageFont.truetype(path,size)
    return ImageFont.load_default(size=size)


def draw_labels(base, layers, z, x, y, options):
    from PIL import Image,ImageDraw
    image=base.convert('RGBA')
    dark=options.get('_dark',sum(image.getpixel((128,128))[:3])<380)
    halo='#20252b' if dark else '#ffffff'
    colors={'place':'#f0f0ef' if dark else '#344852','transportation_name':'#d7dde4' if dark else '#596671',
            'poi':'#f8d3a4' if dark else '#805629'}
    factor=2**max(0,z-14)
    child_x=x%factor;child_y=y%factor
    occupied=[];seen=set()
    def coords(p,extent):return (p[0]/extent*256*factor-child_x*256,p[1]/extent*256*factor-child_y*256)
    flags={'place':options['show_places'],'transportation_name':options['show_streets'],'poi':options['show_pois']}
    for layer_name in ['place','transportation_name','poi']:
        if not flags[layer_name] or (layer_name=='poi' and z<14) or (layer_name=='transportation_name' and z<12):continue
        layer=layers.get(layer_name,{})
        features=sorted(layer.get('features',[]),key=lambda f:f.get('properties',{}).get('rank',999))
        extent=layer.get('extent',4096)
        for feature in features:
            props=feature.get('properties',{});text=label_name(props,options['label_language'])
            if layer_name=='poi' and poi_category(props) in options.get('poi_excluded',[]):continue
            if layer_name=='place':
                kind_class=props.get('class','')
                if kind_class in ('neighbourhood','quarter','suburb','residential') and 'residential' in options.get('poi_excluded',[]):continue
                if (z>=13 and kind_class in ('country','state','city','town')) or (z>=16 and kind_class=='suburb'):continue
            if not text or len(text)>100:continue
            geometry=feature.get('geometry',{});kind=geometry.get('type');points=geometry.get('coordinates',[])
            angle=0
            if kind=='Point': px,py=coords(points,extent)
            elif kind=='MultiPoint' and points:px,py=coords(points[0],extent)
            elif kind in ('LineString','MultiLineString'):
                lines=[points] if kind=='LineString' else points
                candidates=[]
                for line in lines:
                    converted=[coords(p,extent) for p in line]
                    for a,b in zip(converted,converted[1:]):
                        # Clip segments to a padded viewport before choosing a label anchor.
                        dx,dy=b[0]-a[0],b[1]-a[1];lo,hi=0.,1.
                        for p,q in [(-dx,a[0]+32),(dx,288-a[0]),(-dy,a[1]+32),(dy,288-a[1])]:
                            if p==0:
                                if q<0:hi=-1;break
                            elif p<0:lo=max(lo,q/p)
                            else:hi=min(hi,q/p)
                        if lo>hi:continue
                        aa=(a[0]+lo*dx,a[1]+lo*dy);bb=(a[0]+hi*dx,a[1]+hi*dy)
                        candidates.append((math.dist(aa,bb),aa,bb))
                if not candidates:continue
                length,a,b=max(candidates,key=lambda item:item[0]);px,py=(a[0]+b[0])/2,(a[1]+b[1])/2
                angle=math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))
                if angle>90:angle-=180
                if angle< -90:angle+=180
            else:continue
            if not -80<px<336 or not -40<py<296:continue
            size=options[{'place':'place_font_size','transportation_name':'street_font_size','poi':'poi_font_size'}[layer_name]]
            font=font_for(options['label_language'],size)
            box=font.getbbox(text);w,h=box[2]-box[0],box[3]-box[1]
            if layer_name=='transportation_name' and length<w*.65:continue
            key=(layer_name,text) if layer_name!='transportation_name' else (text,round(px/80),round(py/60))
            if key in seen:continue
            canvas=Image.new('RGBA',(w+10,h+10));draw=ImageDraw.Draw(canvas)
            draw.text((5-box[0],5-box[1]),text,font=font,fill=colors[layer_name],stroke_width=2,stroke_fill=halo)
            if angle:canvas=canvas.rotate(-angle,resample=Image.Resampling.BICUBIC,expand=True)
            if layer_name=='poi':py+=9
            rect=(px-canvas.width/2,py-canvas.height/2,px+canvas.width/2,py+canvas.height/2)
            if any(rect[0]<r[2] and rect[2]>r[0] and rect[1]<r[3] and rect[3]>r[1] for r in occupied):continue
            occupied.append(rect);seen.add(key)
            image.alpha_composite(canvas,(round(rect[0]),round(rect[1])))
            if layer_name=='poi':
                ImageDraw.Draw(image).ellipse((px-2,py-11,px+2,py-7),fill=colors[layer_name],outline=halo)
    return image.convert('RGB')
