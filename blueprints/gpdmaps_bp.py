import logging
import re
from typing import Optional

import requests
from lxml import etree
from flask import Blueprint, jsonify

gpdmaps_blueprint = Blueprint('maps', __name__)

maps_base_url = 'https://data-api.vnas.vatsim.net/Files/VideoMaps'
vnas_api_base_url = 'https://data-api.vnas.vatsim.net/api'

def get_artcc_object(artcc):
    response = requests.get(f'{vnas_api_base_url}/artccs/{artcc}')
    artcc_object = response.json()
    return artcc_object

@gpdmaps_blueprint.route('/tracons/<artcc>')
def _get_tracon_maps(artcc):
    artccObject = get_artcc_object(artcc)
    videoMaps = artccObject['videoMaps']
    traconBoundaryMaps = []
    for videoMapNumber in videoMaps:
        if 'EDST_TRACON_BOUNDARY' in videoMapNumber['tags']:
            traconBoundaryMaps.append(videoMapNumber['id'])
    return jsonify(traconBoundaryMaps)

@gpdmaps_blueprint.route('/sectors/high/<artcc>')
def _get_high_sector_maps(artcc):
    artccObject = get_artcc_object(artcc)
    videoMaps = artccObject['videoMaps']
    traconBoundaryMaps = []
    for videoMapNumber in videoMaps:
        if 'EDST_SECTOR_HIGH' in videoMapNumber['tags']:
            traconBoundaryMaps.append(videoMapNumber['id'])
    return jsonify(traconBoundaryMaps)

@gpdmaps_blueprint.route('/sectors/low/<artcc>')
def _get_low_sector_maps(artcc):
    artccObject = get_artcc_object(artcc)
    videoMaps = artccObject['videoMaps']
    traconBoundaryMaps = []
    for videoMapNumber in videoMaps:
        if 'EDST_SECTOR_LOW' in videoMapNumber['tags']:
            traconBoundaryMaps.append(videoMapNumber['id'])
    return jsonify(traconBoundaryMaps)