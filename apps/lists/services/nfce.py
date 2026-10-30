"""Leitura da NFC-e (nota fiscal do consumidor) a partir do link do QR code.

O QR code da nota é um link para a página pública da Sefaz que mostra a
compra. Este módulo:

1. confere se o link é da Sefaz-AM (nenhum outro endereço é acessado);
2. baixa a página, que não tem captcha;
3. lê loja, data, itens e total do HTML (layout nacional da NFC-e).

A página não traz o código de barras dos produtos, só o código interno da
loja, e os nomes vêm abreviados ("IOG VIG 800G BATIDO").
"""

import logging
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import unescape
from urllib.error import URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Só estes endereços são aceitos: evita que o servidor seja usado para
# acessar outros sites a pedido de quem chama a API.
HOSTS_PERMITIDOS = {'sistemas.sefaz.am.gov.br'}
CAMINHO = '/nfceweb/consultarNFCe.jsp'

TIMEOUT_SEGUNDOS = 10
TAMANHO_MAXIMO = 2 * 1024 * 1024
CACHE_SEGUNDOS = 60 * 60

FUSO_DA_NOTA = ZoneInfo('America/Manaus')

CENTAVO = Decimal('0.01')

VAZIOS_DO_ENDERECO = {'NAO INFORMADO', 'NÃO INFORMADO', 'SEM COMPLEMENTO'}

# Unidades da nota que significam venda por peso.
UNIDADES_PESO = {'KG', 'KGS', 'QUILO'}


class NfceErro(Exception):
    """Erro com mensagem que pode ser mostrada ao usuário."""


class LinkInvalido(NfceErro):
    """O link não é de uma nota da Sefaz-AM (erro de quem chamou)."""


def validar_link(link):
    """Devolve (link limpo, chave de acesso de 44 dígitos) ou levanta NfceErro.

    O QR code impresso usa http:// e o "|" sem codificar; o navegador
    costuma trocar para https:// e %7C. Os dois formatos são aceitos, e a
    consulta é sempre feita por https.
    """
    link = (link or '').strip()
    partes = urlparse(link)

    if partes.scheme not in ('http', 'https') or partes.hostname not in HOSTS_PERMITIDOS:
        raise LinkInvalido('O link não é de uma nota fiscal da Sefaz do Amazonas.')

    if partes.path != CAMINHO or partes.port is not None:
        raise LinkInvalido('O link não é de uma nota fiscal da Sefaz do Amazonas.')

    parametro = parse_qs(partes.query).get('p', [''])[0]
    chave = parametro.split('|')[0]

    if not re.fullmatch(r'\d{44}', chave):
        raise LinkInvalido('Não encontrei a chave da nota no link.')

    # Só o parâmetro p segue para a Sefaz, codificado ("|" vira %7C).
    return f'https://{partes.hostname}{CAMINHO}?p={quote(parametro, safe="")}', chave


def baixar_pagina(link):
    request = Request(link, headers={
        'User-Agent': settings.OPEN_FOOD_FACTS_USER_AGENT,
        'Accept': 'text/html',
    })

    try:
        with urlopen(request, timeout=TIMEOUT_SEGUNDOS) as resposta:
            # Um redirecionamento não pode levar para fora da Sefaz-AM.
            if urlparse(resposta.geturl()).hostname not in HOSTS_PERMITIDOS:
                raise NfceErro('A Sefaz redirecionou para um endereço inesperado.')
            conteudo = resposta.read(TAMANHO_MAXIMO + 1)
    except (URLError, TimeoutError, OSError) as erro:
        logger.warning('Falha ao consultar a NFC-e: %s', erro)
        raise NfceErro(
            'Não foi possível consultar a Sefaz agora. Tente de novo em instantes.'
        ) from erro

    if len(conteudo) > TAMANHO_MAXIMO:
        raise NfceErro('A página da nota veio grande demais.')

    # A Sefaz-AM responde em ISO-8859-1.
    return conteudo.decode('iso-8859-1')


def _texto(html):
    sem_tags = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', unescape(sem_tags)).strip()


def _decimal(texto):
    """'1.234,56' -> Decimal('1234.56')."""
    limpo = _texto(texto).replace('.', '').replace(',', '.')

    try:
        return Decimal(limpo)
    except InvalidOperation as erro:
        raise NfceErro('Não consegui ler um valor da nota.') from erro


def _span(html, classe):
    achado = re.search(
        r'<span class="%s[^"]*">(.*?)</span>' % classe, html, re.S,
    )
    return achado.group(1) if achado else ''


def _depois_do_rotulo(html):
    """'<strong>Qtde.:</strong>2' -> '2'."""
    return html.split('</strong>')[-1]


def _endereco(html):
    """'Avenida - Rodrigo Otavio , 2595 , , Crespo , Manaus , AM'
    -> 'Avenida Rodrigo Otavio, 2595, Crespo, Manaus, AM'."""
    topo = re.search(r'class="txtCenter">(.*?)</table>', html, re.S)
    blocos = re.findall(r'<div class="text">(.*?)</div>', topo.group(1) if topo else '', re.S)

    for bloco in blocos:
        texto = _texto(bloco)
        if texto and not texto.startswith('CNPJ'):
            # A nota preenche campo vazio com "NAO INFORMADO": não vai para o mapa.
            partes = [
                parte.strip() for parte in texto.split(',')
                if parte.strip() and parte.strip().upper() not in VAZIOS_DO_ENDERECO
            ]
            return re.sub(r'\s+-\s+', ' ', ', '.join(partes))[:300]

    return ''


def ler_pagina(html):
    """Lê a página da nota. Itens iguais (mesmo código e preço) são somados."""
    if 'tabResult' not in html:
        raise NfceErro(
            'A Sefaz não mostrou os itens desta nota. Confira se o link está completo.'
        )

    loja = re.search(r'class="txtTopo"[^>]*>(.*?)</div>', html, re.S)
    cnpj = re.search(r'CNPJ:\s*([\d./-]+)', _texto(html))
    endereco = _endereco(html)
    emissao = re.search(
        r'Emiss\S*o:\s*(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})', _texto(html),
    )
    total = re.search(
        r'Valor a pagar R\$:</label>\s*<span class="totalNumb[^"]*">(.*?)</span>',
        html,
    )

    itens = []
    por_chave = {}

    for linha in re.findall(r'<tr id="Item \+ \d+">(.*?)</tr>', html, re.S):
        nome = _texto(_span(linha, 'txtTit'))
        codigo = re.search(r'\d+', _texto(_span(linha, 'RCod')))
        unidade = _texto(_depois_do_rotulo(_span(linha, 'RUN'))).upper()
        quantidade = _decimal(_depois_do_rotulo(_span(linha, 'Rqtd')))
        # Em item por peso, a Sefaz mostra o preço do kg com 10 casas
        # (total ÷ peso): arredonda para centavos, como na etiqueta.
        preco = _decimal(_depois_do_rotulo(_span(linha, 'RvlUnit'))).quantize(
            CENTAVO, rounding=ROUND_HALF_UP,
        )
        valor = _decimal(_span(linha, 'valor'))

        if not nome:
            continue

        item = {
            'nome': nome,
            'codigo_loja': codigo.group(0) if codigo else '',
            'unidade': unidade,
            'medida': 'kg' if unidade in UNIDADES_PESO else 'unit',
            'quantidade': quantidade,
            'preco': preco,
            'total': valor,
        }

        chave_item = (item['codigo_loja'] or nome, preco, item['medida'])

        if chave_item in por_chave:
            existente = por_chave[chave_item]
            existente['quantidade'] += quantidade
            existente['total'] += valor
        else:
            por_chave[chave_item] = item
            itens.append(item)

    if not itens:
        raise NfceErro('Não encontrei itens nesta nota.')

    data = None
    if emissao:
        data = datetime.strptime(
            emissao.group(1), '%d/%m/%Y %H:%M:%S',
        ).replace(tzinfo=FUSO_DA_NOTA)

    return {
        'loja': _texto(loja.group(1)) if loja else '',
        'cnpj': cnpj.group(1) if cnpj else '',
        'endereco': endereco,
        'emitida_em': data,
        'total': _decimal(total.group(1)) if total else sum(
            (item['total'] for item in itens), Decimal('0'),
        ),
        'itens': itens,
    }


def ler_nota(link):
    """Valida o link, baixa a página (com cache de 1 h) e devolve a nota lida."""
    link, chave = validar_link(link)
    cache_key = f'nfce:{chave}'

    html = cache.get(cache_key)
    if html is None:
        html = baixar_pagina(link)

    nota = ler_pagina(html)
    cache.set(cache_key, html, CACHE_SEGUNDOS)

    nota['chave'] = chave
    return nota
