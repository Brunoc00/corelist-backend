"""Endereço da loja -> coordenadas, pelo Nominatim do OpenStreetMap.

Regras de uso do Nominatim: no máximo 1 consulta por segundo, User-Agent que
identifique o app e guardar o resultado (não consultar de novo o mesmo
endereço). Por isso cada loja é consultada uma vez só (Store.geocoded_at).
"""

import json
import logging
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

URL = 'https://nominatim.openstreetmap.org/search'
TIMEOUT_SEGUNDOS = 5
PAUSA_ENTRE_CONSULTAS = 1.1  # segundos (limite de 1 por segundo)


def _consultar(texto):
    endereco = URL + '?' + urlencode({
        'q': texto, 'format': 'json', 'limit': 1, 'countrycodes': 'br',
    })
    pedido = Request(endereco, headers={'User-Agent': settings.GEOCODER_USER_AGENT})
    try:
        with urlopen(pedido, timeout=TIMEOUT_SEGUNDOS) as resposta:
            dados = json.loads(resposta.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as erro:
        logger.warning('Nominatim falhou para %r: %s', texto, erro)
        return None
    if not dados:
        return None
    try:
        return float(dados[0]['lat']), float(dados[0]['lon'])
    except (KeyError, TypeError, ValueError):
        return None


def tentativas(endereco):
    """O endereço completo e, se não achar, sem o número (o OpenStreetMap
    muitas vezes não tem a numeração de Manaus)."""
    endereco = (endereco or '').strip(' ,')
    if not endereco:
        return []
    completo = endereco if 'brasil' in endereco.lower() else f'{endereco}, Brasil'
    sem_numero = re.sub(r',\s*(\d+[A-Za-z]?|S/?N)\s*(?=,)', '', completo, flags=re.IGNORECASE)
    return list(dict.fromkeys([completo, sem_numero]))


def coordenadas_do_endereco(endereco):
    for indice, texto in enumerate(tentativas(endereco)):
        if indice:
            time.sleep(PAUSA_ENTRE_CONSULTAS)
        achou = _consultar(texto)
        if achou:
            return achou
    return None


def geocodificar_loja(loja):
    """Preenche latitude/longitude da loja (uma tentativa só por loja)."""
    coordenadas = coordenadas_do_endereco(loja.address)
    if coordenadas:
        loja.latitude, loja.longitude = coordenadas
    loja.geocoded_at = timezone.now()
    loja.save(update_fields=['latitude', 'longitude', 'geocoded_at', 'updated_at'])
    return coordenadas


def distancia_km(origem, destino):
    """Distância em linha reta (fórmula de haversine), em km."""
    lat1, lon1 = map(math.radians, origem)
    lat2, lon2 = map(math.radians, destino)
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))


URL_REVERSO = 'https://nominatim.openstreetmap.org/reverse'


def endereco_da_coordenada(lat, lng):
    """{'rua', 'bairro', 'cidade', 'texto'} perto da coordenada, ou None.

    Para mostrar "você está perto de ..." (uma consulta por região de ~100 m,
    guardada 1 dia)."""
    chave = f'onde-estou:{lat:.3f}:{lng:.3f}'
    guardado = cache.get(chave)
    if guardado is not None:
        return guardado or None

    endereco = URL_REVERSO + '?' + urlencode({
        'format': 'jsonv2', 'lat': f'{lat:.5f}', 'lon': f'{lng:.5f}', 'zoom': 17, 'addressdetails': 1,
    })
    pedido = Request(endereco, headers={'User-Agent': settings.GEOCODER_USER_AGENT})
    try:
        with urlopen(pedido, timeout=8) as resposta:
            dados = json.loads(resposta.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as erro:
        logger.warning('Nominatim (onde estou) falhou: %s', erro)
        return None

    partes = dados.get('address') or {}
    rua = partes.get('road') or partes.get('pedestrian')
    bairro = partes.get('suburb') or partes.get('neighbourhood') or partes.get('quarter')
    cidade = partes.get('city') or partes.get('town') or partes.get('municipality')
    resultado = {
        'rua': rua,
        'bairro': bairro,
        'cidade': cidade,
        'texto': ', '.join(p for p in (rua, bairro, cidade) if p) or None,
    }
    if not resultado['texto']:
        resultado = {}
    cache.set(chave, resultado, 24 * 60 * 60)
    return resultado or None
