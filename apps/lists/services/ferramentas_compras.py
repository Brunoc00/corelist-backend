"""Ferramentas gratuitas do assistente de compras.

- mercados_perto: supermercados e atacarejos ao redor de uma coordenada, no
  OpenStreetMap (busca do Nominatim, sem chave; o Overpass público falhava
  muito). Política de uso: no máximo 1 consulta por segundo, com User-Agent;
  por isso o resultado fica 1 hora em cache por região.
- buscar_promocoes: promoções e encartes na internet pela Tavily (plano
  gratuito de ~1.000 buscas/mês, TAVILY_API_KEY). Cache de 6 horas por busca.

As duas devolvem dados para a IA e acrescentam em `fontes` os links que o app
mostra abaixo da resposta.
"""

import hashlib
import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from tavily import TavilyClient

from apps.products.services.geocodificar import distancia_km
from apps.products.services.redes import rede_pelo_nome

logger = logging.getLogger(__name__)

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
TIMEOUT_SEGUNDOS = 10
KM_POR_GRAU = 111.0
MERCADOS_NO_MAXIMO = 8
RAIO_PADRAO_KM = 3
PROMOCOES_NO_MAXIMO = 5


def _link_mapa(nome, lat, lng):
    return 'https://www.google.com/maps/search/?' + urlencode({'api': 1, 'query': f'{lat},{lng}'})


def _endereco(endereco):
    rua = ' '.join(p for p in (endereco.get('road'), endereco.get('house_number')) if p)
    partes = [rua, endereco.get('suburb') or endereco.get('neighbourhood') or endereco.get('city_district')]
    return ', '.join(p for p in partes if p) or None


def _consultar_nominatim(lat, lng, raio_km):
    """Supermercados (tag shop=supermarket) dentro do quadrado do raio."""
    delta = raio_km / KM_POR_GRAU
    endereco = NOMINATIM_URL + '?' + urlencode({
        'q': 'supermarket',
        'format': 'jsonv2',
        'limit': 40,
        'bounded': 1,
        'addressdetails': 1,
        'viewbox': f'{lng - delta},{lat + delta},{lng + delta},{lat - delta}',
    })
    pedido = Request(endereco, headers={'User-Agent': settings.GEOCODER_USER_AGENT})
    with urlopen(pedido, timeout=TIMEOUT_SEGUNDOS) as resposta:
        return json.loads(resposta.read().decode('utf-8'))


def mercados_perto(origem, raio_km=RAIO_PADRAO_KM, fontes=None, lojas=None):
    """Supermercados e atacarejos até raio_km da origem (lat, lng)."""
    if origem is None:
        return {'erro': 'A pessoa não liberou a localização: peça para tocar em "Usar minha localização".'}
    try:
        raio_km = max(1, min(int(raio_km), 10))
    except (TypeError, ValueError):
        raio_km = RAIO_PADRAO_KM

    chave = f'mercados:{origem[0]:.2f}:{origem[1]:.2f}:{raio_km}'
    lugares = cache.get(chave)
    if lugares is None:
        try:
            lugares = _consultar_nominatim(origem[0], origem[1], raio_km)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as erro:
            logger.warning('Nominatim (mercados perto) falhou: %s', erro)
            return {'erro': 'O mapa de mercados não respondeu agora.'}
        cache.set(chave, lugares, 60 * 60)

    mercados, vistos = [], set()
    for lugar in lugares:
        nome = (lugar.get('name') or '').strip()
        try:
            lat, lng = float(lugar['lat']), float(lugar['lon'])
        except (KeyError, TypeError, ValueError):
            continue
        distancia = round(distancia_km(origem, (lat, lng)), 1)
        identidade = (nome.lower(), round(lat, 3), round(lng, 3))
        if not nome or distancia > raio_km or identidade in vistos:
            continue
        vistos.add(identidade)
        atacarejo = any(p in nome.lower() for p in ('atacad', 'assaí', 'assai', 'makro', 'atacarejo'))
        mercados.append({
            'nome': nome,
            'tipo': 'atacarejo' if atacarejo else 'supermercado',
            'distancia_km': distancia,
            'endereco': _endereco(lugar.get('address') or {}),
            '_lat': lat,
            '_lng': lng,
        })
    mercados.sort(key=lambda m: m['distancia_km'])
    mercados = mercados[:MERCADOS_NO_MAXIMO]

    for mercado in mercados[:4]:
        url = _link_mapa(mercado['nome'], mercado['_lat'], mercado['_lng'])
        if fontes is not None:
            fontes.append({'tipo': 'maps', 'titulo': mercado['nome'], 'url': url})
        if lojas is not None:
            # O nome do mapa já é o popular ("Supermercado DB"): fica ele, com
            # o logo da rede quando a rede é conhecida.
            _, icone = rede_pelo_nome(mercado['nome'])
            lojas.append({
                'nome': mercado['nome'], 'marca': None, 'icone': icone,
                'distancia_km': mercado['distancia_km'], 'url': url,
            })
    for mercado in mercados:
        del mercado['_lat'], mercado['_lng']
    return {'raio_km': raio_km, 'mercados': mercados, 'fonte': 'OpenStreetMap'}


def _cliente_tavily():
    return TavilyClient(api_key=os.getenv('TAVILY_API_KEY'))


def buscar_promocoes(produto, fontes=None):
    """Promoções e preços publicados na internet para o produto em Manaus."""
    produto = (produto or '').strip()[:80]
    if not produto:
        return {'erro': 'Diga o nome do produto.'}
    if not os.getenv('TAVILY_API_KEY'):
        return {'erro': 'Busca na internet desligada (sem TAVILY_API_KEY).'}

    consulta = f'{produto} preço promoção supermercado Manaus'
    chave = 'promocoes:' + hashlib.sha256(consulta.lower().encode()).hexdigest()
    resultados = cache.get(chave)
    if resultados is None:
        try:
            resposta = _cliente_tavily().search(
                consulta,
                max_results=PROMOCOES_NO_MAXIMO,
                search_depth='basic',
                country='brazil',
                time_range='year',  # promoções antigas não servem
                timeout=TIMEOUT_SEGUNDOS,
            )
        except Exception as erro:  # rede, limite do plano grátis, chave inválida
            logger.warning('Tavily falhou: %s', erro)
            return {'erro': 'A busca na internet não respondeu agora.'}
        resultados = [
            {
                'titulo': item.get('title') or item.get('url'),
                'url': item.get('url'),
                'trecho': (item.get('content') or '')[:400],
            }
            for item in (resposta or {}).get('results', [])
            if item.get('url')
        ]
        cache.set(chave, resultados, 6 * 60 * 60)

    if fontes is not None:
        for item in resultados[:3]:
            fontes.append({'tipo': 'web', 'titulo': item['titulo'], 'url': item['url']})
    return {'busca': consulta, 'resultados': resultados}
