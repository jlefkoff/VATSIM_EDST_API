import itertools
import re
import random
from collections import defaultdict
from typing import Optional

import geopandas
import geopy.distance
from shapely.geometry import Point, shape

from flask import g
from pymongo import MongoClient
from datetime import datetime, timedelta

import mongo_client
import libs.lib
import libs.adr_lib
import libs.adar_lib

CID_OVERFLOW_RANGE = list('CFNPTVWY')  # list(string.ascii_lowercase)
NM_CONVERSION_FACTOR = 0.86898
KM_NM_CONVERSION_FACTOR = 0.53996

time_mask = '%Y-%m-%d %H:%M:%S.%f'
cid_list = set(f'{a}{b}{c}' for a, b, c in itertools.product(range(10), range(10), range(10)))
cid_overflow_list = set(f'{a}{b}{c}' for a, b, c in itertools.product(range(10), range(10), CID_OVERFLOW_RANGE))


def get_cid(used_cid_list) -> str:
    candidates = list(cid_list - set(used_cid_list))
    if not candidates:
        candidates = list(cid_overflow_list - set(used_cid_list))
    return random.choice(candidates)


def get_edst_data():
    client: MongoClient = g.mongo_edst_client
    return list(client.edst.data.find({}, {'_id': False}))


def get_boundary_data(artcc):
    client: MongoClient = g.mongo_reader_client
    boundary_data = client[artcc.lower()].boundary_data.find_one({}, {'_id': False})
    return boundary_data


def get_artcc_edst_data(artcc):
    client: MongoClient = g.mongo_reader_client
    edst_data = client.edst.data.find({}, {'_id': False})
    boundary_data = client[artcc.lower()].boundary_data.find_one({}, {'_id': False})
    geometry = geopandas.GeoSeries(shape(boundary_data['geometry'])).set_crs(epsg=4326).to_crs("EPSG:3857")
    artcc_data = []
    # geometry.plot()
    # plt.savefig(f'{artcc}_boundary_plot.jpg')
    flightplans = libs.lib.get_all_flightplans().keys()
    for e in edst_data:
        if e['callsign'] not in flightplans:
            continue
        pos = geopandas.GeoSeries([Point(e['flightplan']['lon'], e['flightplan']['lat'])]) \
            .set_crs(epsg=4326).to_crs("EPSG:3857")
        dist = (float(geometry.distance(pos)) / 1000) * KM_NM_CONVERSION_FACTOR
        if dist < 150:
            artcc_data.append(e)
    return artcc_data


def format_remaining_route(entry, remaining_route_data):
    split_route = re.sub(r'\.+', ' ', entry['route']).strip().split()
    if remaining_route_data:
        remaining_fixes = [e['fix'] for e in remaining_route_data]
        if first_common_fix := next(iter([fix for fix in remaining_fixes if fix in split_route]), None):
            index = split_route.index(first_common_fix)
            split_route = split_route[index:]
            if remaining_fixes[0] not in split_route:
                split_route.insert(0, remaining_fixes[0])

    return libs.lib.format_route(' '.join(split_route))


def update_edst_data():
    client: MongoClient = mongo_client.get_edst_client()
    reader_client: MongoClient = mongo_client.get_reader_client()
    data = {d['callsign']: d for d in client.edst.data.find({}, {'_id': False})}
    used_cid_list = [d['cid'] for d in data.values()]
    prefroutes = defaultdict(None)
    for callsign, fp in libs.lib.get_all_flightplans().items():
        if not ((20 < float(fp.lat) < 55) and (-135 < float(fp.lon) < -40)):
            continue
        pos = (float(fp.lat), float(fp.lon))
        dep = fp.departure
        dest = fp.arrival
        if callsign in data.keys():
            entry = data[callsign]
            update_time = entry['update_time']
            if datetime.strptime(update_time, time_mask) < datetime.utcnow() + timedelta(minutes=30) \
                    and entry['dep'] == dep and entry['dest'] == dest:
                if entry['departing'] is not False:
                    dep_info = reader_client.navdata.airports.find_one({'icao': dep.upper()}, {'_id': False})
                    entry['departing'] = geopy.distance.distance(
                        (float(dep_info['lat']), float(dep_info['lon'])), pos).miles * NM_CONVERSION_FACTOR < 20 \
                        if dep_info else False
                remaining_route_data = get_remaining_route_data(callsign)
                entry['flightplan'] = vars(fp)
                entry['update_time'] = datetime.utcnow().strftime(time_mask)
                entry['remaining_route_data'] = remaining_route_data
                entry['remaining_route'] = format_remaining_route(entry, remaining_route_data)
                client.edst.data.update_one({'callsign': callsign}, {'$set': entry})
                continue
        dep_info = reader_client.navdata.airports.find_one({'icao': dep.upper()}, {'_id': False})
        if not (dep_info or reader_client.navdata.airports.find_one({'icao': dest.upper()}, {'_id': False})):
            continue
        departing = geopy.distance.distance((float(dep_info['lat']), float(dep_info['lon'])),
                                            pos).miles * NM_CONVERSION_FACTOR < 20 if dep_info else False
        dep_artcc = dep_info['artcc'].lower() if dep_info else None
        cid = get_cid(used_cid_list)
        used_cid_list.append(cid)
        route = fp.route
        aircraft_faa = fp.aircraft_faa.split('/')
        try:
            equipment = (aircraft_faa[-1])[0] if len(aircraft_faa) > 1 else ''
        except IndexError:
            equipment = ''
        # airways = libs.lib.get_airways_on_route(fp.route)
        expanded_route = libs.lib.expand_route(route)
        entry = {
            'callsign': callsign,
            'type': fp.aircraft_short,
            'equipment': equipment,
            'beacon': fp.assigned_transponder,
            'dep': dep,
            'dep_artcc': dep_artcc,
            'dest': dest,
            'route': libs.lib.format_route(route),
            'route_data': get_route_data(expanded_route),
            'altitude': str(int(fp.altitude)).zfill(3),
            'interim': None,
            'hdg': None,
            'spd': None,
            'hold_fix': None,
            'hold_hdg': None,
            'hold_spd': None,
            'remarks': fp.remarks,
            'cid': cid,
            'free_text': '',
            'departing': departing,
            'flightplan': vars(fp)
        }
        route_key = f'{dep}_{dest}'
        if route_key not in prefroutes.keys():
            local_dep = re.sub(r'^K?', '', dep)
            local_dest = re.sub(r'^K?', '', dest)
            cdr = list(reader_client.flightdata.faa_cdr.find({'dep': dep, 'dest': dest}, {'_id': False}))
            pdr = list(reader_client.flightdata.faa_prd.find({'dep': local_dep, 'dest': local_dest}, {'_id': False}))
            for r in cdr:
                r['route_data'] = get_route_data(libs.lib.expand_route(r['route']))
                r['route'] = libs.lib.format_route(re.sub(rf'{dep}|{dest}', '', r['route']))
            for r in pdr:
                r['route_data'] = get_route_data(libs.lib.expand_route(r['route']))
                r['route'] = libs.lib.format_route(r['route'])
            prefroutes[route_key] = cdr + pdr
        adr = libs.adr_lib.get_eligible_adr(fp)
        for a in adr:
            a['route'] = libs.lib.format_route(a['route'])
        adar = libs.adar_lib.get_eligible_adar(fp)
        for a in adar:
            a['route_data'] = get_route_data(libs.lib.expand_route(a['route']))
            a['route'] = libs.lib.format_route(a['route'])
        entry['adr'] = adr
        entry['adar'] = adar
        entry['routes'] = prefroutes[route_key]
        entry['update_time'] = datetime.utcnow().strftime(time_mask)
        client.edst.data.update_one({'callsign': callsign}, {'$set': entry}, upsert=True)
    for callsign, entry in data.items():
        update_time = entry['update_time']
        if datetime.strptime(update_time, time_mask) + timedelta(minutes=30) < datetime.utcnow():
            client.edst.data.delete_one({'callsign': callsign})
    client.close()


def get_edst_entry(callsign: str) -> Optional[dict]:
    client: MongoClient = mongo_client.get_edst_client()
    return client.edst.data.find_one({'callsign': callsign.upper()}, {'_id': False})


def update_edst_entry(callsign, data):
    client: MongoClient = g.mongo_edst_client
    if 'route' in data.keys():
        data['remaining_route'] = data['route']
        if 'route_data' not in data.keys():
            expanded_route = libs.lib.expand_route(re.sub(r'\.+', ' ', data['route']).strip())
            data['route_data'] = get_route_data(expanded_route)
        data['remaining_route_data'] = data['route_data']
    client.edst.data.update_one({'callsign': callsign}, {'$set': data})
    return client.edst.data.find_one({'callsign': callsign}, {'_id': False})


def get_route_data(expanded_route) -> list:
    client: MongoClient = mongo_client.get_reader_client()
    points = []
    for fix in expanded_route.split():
        if fix_data := client.navdata.waypoints.find_one({'waypoint_id': fix}, {'_id': False}):
            points.append({'fix': fix, 'pos': (float(fix_data['lat']), float(fix_data['lon']))})
    return points


def get_remaining_route_data(callsign: str) -> Optional[list]:
    client: MongoClient = mongo_client.get_reader_client()
    if entry := get_edst_entry(callsign):
        route_data = entry['route_data']
        if route_data:
            dest = entry['dest']
            if dest_data := client.navdata.airports.find_one({'icao': dest}, {'_id': False}):
                route_data.append({'fix': dest, 'pos': (float(dest_data['lat']), float(dest_data['lon']))})
            if (fp := libs.lib.get_flightplan(callsign)) is None:
                return []
            pos = (float(fp.lat), float(fp.lon))
            fixes_sorted = sorted(
                [{'fix': e['fix'], 'distance': geopy.distance.distance(e['pos'], pos).miles * NM_CONVERSION_FACTOR}
                 for e in route_data],
                key=lambda x: x['distance'])
            fix_distances = {e['fix']: e['distance'] for e in fixes_sorted}
            fixes = [e['fix'] for e in fixes_sorted]
            next_fix = None
            if len(fixes) == 1:
                next_fix = fixes_sorted[0]
            else:
                next_fix = fixes_sorted[0] \
                    if fixes.index(fixes_sorted[0]['fix']) > fixes.index(fixes_sorted[1]['fix']) \
                    else fixes_sorted[1]
            if next_fix is None:
                return []
            for e in list(route_data):
                if e['fix'] == next_fix['fix']:
                    break
                else:
                    route_data.remove(e)
            return [{'fix': e['fix'], 'pos': e['pos'], 'distance': fix_distances[e['fix']]} for e in route_data]
    return []
