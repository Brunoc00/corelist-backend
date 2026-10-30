"""Preços da cidade, a partir das notas fiscais importadas.

Regras (docs/PLANO_PRECOS_DA_CIDADE.md):
- o preço "atual" de uma loja é o último que apareceu numa nota daquela loja;
- com mais de 1 dia, o preço "pode ter mudado" e não disputa o "mais barato
  agora", mas continua aparecendo com a data;
- só notas fiscais entram aqui (nunca preço digitado à mão);
- ninguém vê quem comprou: só loja, preço e data.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.products.models import ObservedPrice, Product, Store
from apps.products.services.redes import rede_da_loja

from .busca import pontuar
from .reconhecer import somente_digitos

# Depois disso, o preço "pode ter mudado".
VALIDADE_DO_PRECO = timedelta(days=1)
PERIODOS = (7, 30, 90)
PERIODO_PADRAO = 30


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


# --- Consultar -----------------------------------------------------------

def _loja_json(loja):
    marca, icone = rede_da_loja(loja.name)
    return {
        'id': loja.id,
        'nome': loja.name,
        # Nome que as pessoas conhecem ("Assaí Atacadista") e o ícone da rede.
        'marca': marca,
        'icone': icone,
        'endereco': loja.address,
    }


def _preco_json(observado, agora):
    return {
        'loja': _loja_json(observado.store),
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


def resumo_do_produto(produto, dias=PERIODO_PADRAO, ultimos=None):
    """Preço atual em cada loja, o mais barato agora e o menor do período."""
    agora = timezone.now()
    if ultimos is None:
        ultimos = ultimos_por_loja([produto.id]).get(produto.id, {})

    lojas = [_preco_json(observado, agora) for observado in ultimos.values()]
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
                'loja': _loja_json(menor.store),
                'data': menor.observed_at,
                'dias': dias,
            }
        ),
        'lojas': lojas,
    }


def buscar_precos(texto, dias=PERIODO_PADRAO, limite=10):
    """Produtos com preço da cidade que combinam com o texto."""
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
    return [
        resumo_do_produto(produto, dias, ultimos.get(produto.id, {}))
        for produto in produtos
    ]


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
