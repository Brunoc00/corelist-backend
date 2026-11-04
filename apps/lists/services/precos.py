"""Preços da cidade, a partir das notas fiscais importadas.

Regras (docs/PLANO_PRECOS_DA_CIDADE.md):
- o preço "atual" de uma loja é o último que apareceu numa nota daquela loja;
- com mais de 7 dias, o preço "pode ter mudado" e não disputa o "mais barato
  agora", mas continua aparecendo com a data;
- só notas fiscais entram aqui (nunca preço digitado à mão);
- ninguém vê quem comprou: só loja, preço e data.
"""

import time
from datetime import timedelta
from urllib.parse import urlencode
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.products.models import ObservedPrice, Product, Store
from apps.products.services.geocodificar import (
    PAUSA_ENTRE_CONSULTAS,
    distancia_km,
    geocodificar_loja,
)
from apps.products.services.redes import rede_da_loja

from .busca import pontuar
from .reconhecer import somente_digitos

# Depois disso, o preço "pode ter mudado".
VALIDADE_DO_PRECO = timedelta(days=7)
PERIODOS = (7, 30, 90)
PERIODO_PADRAO = 30
RAIOS_KM = (2, 5, 10)
# Lojas sem coordenadas consultadas no OpenStreetMap por busca (1 por segundo).
GEOCODIFICAR_POR_BUSCA = 3


def periodo(valor):
    try:
        dias = int(valor)
    except (TypeError, ValueError):
        return PERIODO_PADRAO
    return dias if dias in PERIODOS else PERIODO_PADRAO


# --- Registrar -----------------------------------------------------------

def registrar_preco(loja, nota, item, produto):
    """Guarda o preço de um item da nota (sem duplicar a mesma nota)."""
    if loja is None or produto is None:
        return
    try:
        with transaction.atomic():
            ObservedPrice.objects.get_or_create(
                nfce_key=nota['chave'],
                product=produto,
                store_code=somente_digitos(item.get('codigo_loja')),
                price=item['preco'],
                defaults={
                    'store': loja,
                    'measure': item['medida'],
                    'observed_at': nota['emitida_em'] or timezone.now(),
                },
            )
    except IntegrityError:
        pass


def localizacao(lat, lng):
    """(lat, lng) de parâmetros da URL, ou None se faltar ou for inválido."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def raio(valor):
    try:
        km = int(valor)
    except (TypeError, ValueError):
        return None
    return km if km in RAIOS_KM else None


# --- Consultar -----------------------------------------------------------

def _distancia(loja, origem):
    if origem is None or loja.latitude is None or loja.longitude is None:
        return None
    return round(distancia_km(origem, (loja.latitude, loja.longitude)), 1)


def _loja_json(loja, origem=None):
    marca, icone = rede_da_loja(loja.name)
    dados = {
        'id': loja.id,
        'nome': loja.name,
        # Nome que as pessoas conhecem ("Assaí Atacadista") e o ícone da rede.
        'marca': marca,
        'icone': icone,
        'endereco': loja.address,
    }
    if origem is not None:
        dados['distancia_km'] = _distancia(loja, origem)
    return dados


def link_como_chegar(loja):
    """Rota no Google Maps até a loja (igual ao "Como chegar" do site)."""
    destino = f"{loja['nome']}, {loja['endereco']}" if loja.get('endereco') else f"{loja['nome']}, Manaus, AM"
    return 'https://www.google.com/maps/dir/?' + urlencode({
        'api': '1', 'destination': destino, 'travelmode': 'driving', 'dir_action': 'navigate',
    })


def _geocodificar_faltantes(lojas):
    """Busca as coordenadas de algumas lojas que ainda não têm (as demais
    ficam para as próximas buscas ou para o comando geocodificar_lojas)."""
    faltam = [
        loja for loja in lojas
        if loja.latitude is None and loja.geocoded_at is None and loja.address
    ][:GEOCODIFICAR_POR_BUSCA]
    for indice, loja in enumerate(faltam):
        if indice:
            time.sleep(PAUSA_ENTRE_CONSULTAS)
        geocodificar_loja(loja)


def _preco_json(observado, agora, origem=None):
    return {
        'loja': _loja_json(observado.store, origem),
        'preco': str(observado.price),
        'medida': observado.measure,
        'data': observado.observed_at,
        'desatualizado': agora - observado.observed_at > VALIDADE_DO_PRECO,
    }


def ultimos_por_loja(produto_ids, medida=None):
    """{produto_id: {loja_id: último ObservedPrice}}."""
    consulta = (
        ObservedPrice.objects
        .filter(product_id__in=produto_ids)
        .select_related('store')
        .order_by('product_id', 'store_id', '-observed_at', '-id')
    )
    if medida:
        consulta = consulta.filter(measure=medida)

    ultimos = {}
    for observado in consulta:
        ultimos.setdefault(observado.product_id, {}).setdefault(
            observado.store_id, observado,
        )
    return ultimos


def resumo_do_produto(produto, dias=PERIODO_PADRAO, ultimos=None, origem=None, raio_km=None):
    """Preço atual em cada loja, o mais barato agora e o menor do período.

    Com a localização (origem), cada loja traz a distância; com um raio, só
    ficam as lojas até essa distância (as sem coordenadas ficam de fora)."""
    agora = timezone.now()
    if ultimos is None:
        ultimos = ultimos_por_loja([produto.id]).get(produto.id, {})

    lojas = [_preco_json(observado, agora, origem) for observado in ultimos.values()]
    if origem is not None and raio_km:
        lojas = [
            linha for linha in lojas
            if linha['loja']['distancia_km'] is not None
            and linha['loja']['distancia_km'] <= raio_km
        ]
    # Atuais primeiro, do mais barato ao mais caro; depois os desatualizados.
    lojas.sort(key=lambda linha: (linha['desatualizado'], Decimal(linha['preco'])))

    atuais = [linha for linha in lojas if not linha['desatualizado']]
    mais_barato = atuais[0] if atuais else None

    menor = (
        ObservedPrice.objects
        .filter(product=produto, observed_at__gte=agora - timedelta(days=dias))
        .select_related('store')
        .order_by('price', '-observed_at')
        .first()
    )

    return {
        'produto': {
            'id': produto.id,
            'name': produto.name,
            'brand': produto.brand,
            'embalagem': (produto.package_size or produto.unit or '').strip(),
            'image_url': produto.image_url,
        },
        'mais_barato_agora': mais_barato,
        'menor_no_periodo': (
            None if menor is None else {
                'preco': str(menor.price),
                'medida': menor.measure,
                'loja': _loja_json(menor.store, origem),
                'data': menor.observed_at,
                'dias': dias,
            }
        ),
        'lojas': lojas,
    }


def buscar_precos(texto, dias=PERIODO_PADRAO, limite=10, origem=None, raio_km=None):
    """Produtos com preço da cidade que combinam com o texto (com origem e
    raio: só as lojas perto da pessoa, com a distância)."""
    texto = (texto or '').strip()
    com_preco = Product.objects.filter(observed_prices__isnull=False).distinct()

    if texto:
        candidatos = [(pontuar(texto, produto), produto) for produto in com_preco]
        candidatos = [par for par in candidatos if par[0] > 0]
        candidatos.sort(key=lambda par: (-par[0], par[1].name.lower()))
        produtos = [produto for _, produto in candidatos[:limite]]
    else:
        # Sem texto: os produtos com preço mais recente.
        recentes = (
            ObservedPrice.objects
            .order_by('-observed_at')
            .values_list('product_id', flat=True)
        )
        ids = list(dict.fromkeys(recentes[:200]))[:limite]
        por_id = {produto.id: produto for produto in Product.objects.filter(id__in=ids)}
        produtos = [por_id[pid] for pid in ids if pid in por_id]

    ultimos = ultimos_por_loja([produto.id for produto in produtos])
    if origem is not None:
        _geocodificar_faltantes({
            observado.store_id: observado.store
            for por_loja in ultimos.values()
            for observado in por_loja.values()
        }.values())

    resumos = [
        resumo_do_produto(produto, dias, ultimos.get(produto.id, {}), origem, raio_km)
        for produto in produtos
    ]
    if origem is not None and raio_km:
        # Perto de mim: só produtos com alguma loja dentro do raio.
        resumos = [resumo for resumo in resumos if resumo['lojas']]
    return resumos


def precos_da_lista(lista):
    """Para cada item da lista, onde está mais barato agora; e a melhor loja
    para a lista inteira (por cobertura e depois pelo total)."""
    agora = timezone.now()
    itens = list(lista.items.select_related('product'))
    ultimos = ultimos_por_loja([item.product_id for item in itens])

    por_item = []
    lojas = {}
    for item in itens:
        # Só compara preços da mesma medida (unidade com unidade, kg com kg).
        da_medida = {
            loja_id: observado
            for loja_id, observado in ultimos.get(item.product_id, {}).items()
            if observado.measure == item.measure
        }

        atuais = sorted(
            (o for o in da_medida.values() if agora - o.observed_at <= VALIDADE_DO_PRECO),
            key=lambda o: o.price,
        )
        por_item.append({
            'item': item.id,
            'mais_barato_agora': _preco_json(atuais[0], agora) if atuais else None,
            'lojas_com_preco': len(da_medida),
        })

        for loja_id, observado in da_medida.items():
            resumo = lojas.setdefault(loja_id, {
                'loja': _loja_json(observado.store),
                'total': Decimal('0'),
                'itens': 0,
                'desatualizados': 0,
            })
            resumo['total'] += observado.price * item.quantity
            resumo['itens'] += 1
            if agora - observado.observed_at > VALIDADE_DO_PRECO:
                resumo['desatualizados'] += 1

    melhores = sorted(lojas.values(), key=lambda resumo: (-resumo['itens'], resumo['total']))
    for resumo in melhores:
        resumo['total'] = str(resumo['total'].quantize(Decimal('0.01')))

    return {
        'total_itens': len(itens),
        'itens': por_item,
        'melhor_loja': melhores[:5],
    }


def loja_pela_chave(chave, nome=''):
    """A loja da nota a partir da chave de acesso (CNPJ nos dígitos 7 a 20)."""
    cnpj = (chave or '')[6:20]
    if len(cnpj) != 14 or not cnpj.isdigit():
        return None
    loja, _ = Store.objects.get_or_create(cnpj=cnpj, defaults={'name': nome[:200]})
    return loja
