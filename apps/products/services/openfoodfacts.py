"""Consulta de produtos no Open Food Facts pelo código de barras.

Regras do Open Food Facts que este módulo respeita:
- no máximo 15 consultas de produto por minuto por IP: por isso cada código
  fica 24 h em cache (encontrado ou não) e o produto salvo no catálogo não é
  consultado de novo;
- toda chamada leva um User-Agent que identifica o app;
- dados sob ODbL e fotos sob CC BY-SA: o frontend mostra o crédito quando
  `data_source` é `openfoodfacts`.
"""

import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

API_URL = 'https://world.openfoodfacts.org/api/v2/product/{codigo}'

CAMPOS = ','.join([
    'product_name_pt',
    'product_name',
    'brands',
    'quantity',
    'image_front_small_url',
    'image_front_url',
])

TIMEOUT_SEGUNDOS = 5

CACHE_SEGUNDOS = 60 * 60 * 24

# Marca guardada no cache para "não encontrado", diferente de "sem cache".
_NAO_ENCONTRADO = 'nao-encontrado'

TAMANHOS_VALIDOS = {8, 12, 13, 14}


def normalizar_codigo(codigo):
    return (codigo or '').strip()


def codigo_valido(codigo):
    """EAN-8, UPC-A, EAN-13 ou GTIN-14 com dígito verificador correto."""
    codigo = normalizar_codigo(codigo)

    if not re.fullmatch(r'[0-9]+', codigo):
        return False

    if len(codigo) not in TAMANHOS_VALIDOS:
        return False

    digitos = [int(d) for d in codigo]
    corpo = digitos[:-1]

    # Da direita para a esquerda (sem o verificador), pesos 3, 1, 3, 1...
    soma = sum(
        digito * (3 if indice % 2 == 0 else 1)
        for indice, digito in enumerate(reversed(corpo))
    )

    return (10 - soma % 10) % 10 == digitos[-1]


def _primeira_marca(marcas):
    return (marcas or '').split(',')[0].strip()


def _url_https(url):
    url = (url or '').strip()
    return url if url.startswith('https://') else ''


def _converter(produto):
    nome = (
        (produto.get('product_name_pt') or '').strip()
        or (produto.get('product_name') or '').strip()
    )

    if not nome:
        return None

    return {
        'name': nome[:200],
        'brand': _primeira_marca(produto.get('brands'))[:100],
        'package_size': (produto.get('quantity') or '').strip()[:50],
        'image_url': _url_https(
            produto.get('image_front_small_url')
            or produto.get('image_front_url')
        )[:500],
    }


def _consultar(codigo):
    request = Request(
        API_URL.format(codigo=quote(codigo)) + f'?fields={CAMPOS}',
        headers={
            'User-Agent': settings.OPEN_FOOD_FACTS_USER_AGENT,
            'Accept': 'application/json',
        },
    )

    try:
        with urlopen(request, timeout=TIMEOUT_SEGUNDOS) as response:
            dados = json.loads(response.read().decode('utf-8'))
    except HTTPError as erro:
        # 404 é o "produto não existe" do Open Food Facts; o resto é falha.
        if erro.code != 404:
            logger.warning(
                'Open Food Facts respondeu %s para %s', erro.code, codigo,
            )
        return None
    except (URLError, TimeoutError, ValueError, OSError):
        logger.exception('Erro ao consultar o Open Food Facts (%s)', codigo)
        return None

    if dados.get('status') != 1 or not isinstance(
        dados.get('product'), dict,
    ):
        return None

    return _converter(dados['product'])


def buscar_por_codigo(codigo):
    """Dados do produto no Open Food Facts, ou None.

    Nunca lança exceção: erro de rede, timeout ou resposta estranha viram
    None, como acontece com o Gemini.
    """
    codigo = normalizar_codigo(codigo)

    if not codigo_valido(codigo):
        return None

    chave = f'openfoodfacts:{codigo}'
    em_cache = cache.get(chave)

    if em_cache == _NAO_ENCONTRADO:
        return None

    if em_cache is not None:
        return em_cache

    resultado = _consultar(codigo)

    cache.set(
        chave,
        resultado if resultado is not None else _NAO_ENCONTRADO,
        CACHE_SEGUNDOS,
    )

    return resultado
