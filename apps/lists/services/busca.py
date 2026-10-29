"""Busca de produtos para adicionar à lista.

Ordena pelo que a pessoa digitou (cada palavra pode ser o começo de uma
palavra do produto: "arr" acha "Arroz") e, em empate, pelo que ela mais
compra. Cada resultado traz o último preço que a pessoa pagou.
"""

from django.db.models import Count

from apps.products.models import Product

from ..models import ListItem
from .reconhecer import nome_completo, normalizar, semelhanca

LIMITE = 10


def _combina(palavra_digitada, palavras_do_produto):
    for palavra in palavras_do_produto:
        if palavra.startswith(palavra_digitada):
            return True
        # Nome abreviado da nota: "iogurte" acha "IOG".
        if len(palavra) >= 3 and not palavra[0].isdigit() and palavra_digitada.startswith(palavra):
            return True
    return False


def pontuar(texto, produto):
    digitadas = normalizar(texto)
    if not digitadas:
        return 0.0

    palavras = normalizar(nome_completo(produto))
    if all(_combina(digitada, palavras) for digitada in digitadas):
        bonus = 0.2 if palavras and palavras[0].startswith(digitadas[0]) else 0.0
        return 1.0 + bonus

    parecido = semelhanca(texto, nome_completo(produto))
    return parecido if parecido >= 0.5 else 0.0


def historico_do_usuario(usuario, ids):
    """{produto_id: {'preco', 'medida', 'vezes'}} das listas do usuário."""
    ultimos = {}
    itens = (
        ListItem.objects
        .filter(list__owner=usuario, product_id__in=ids, price__isnull=False)
        .order_by('-updated_at')
        .values('product_id', 'price', 'measure')
    )
    for item in itens:
        ultimos.setdefault(item['product_id'], item)

    vezes = dict(
        ListItem.objects
        .filter(list__owner=usuario, product_id__in=ids)
        .values('product_id')
        .annotate(total=Count('id'))
        .values_list('product_id', 'total')
    )

    return {
        produto_id: {
            'preco': ultimos.get(produto_id, {}).get('price'),
            'medida': ultimos.get(produto_id, {}).get('measure'),
            'vezes': vezes.get(produto_id, 0),
        }
        for produto_id in ids
    }


def _resultado(produto, historico):
    dados = historico.get(produto.id, {})
    preco = dados.get('preco')
    return {
        'id': produto.id,
        'name': produto.name,
        'brand': produto.brand,
        'embalagem': (produto.package_size or produto.unit or '').strip(),
        'ultimo_preco': None if preco is None else str(preco),
        'ultima_medida': dados.get('medida'),
        'vezes': dados.get('vezes', 0),
    }


def buscar(usuario, texto):
    """Produtos para o texto digitado; sem texto, os que a pessoa mais compra."""
    texto = (texto or '').strip()

    if not texto:
        mais_comprados = list(
            ListItem.objects
            .filter(list__owner=usuario)
            .values('product_id')
            .annotate(total=Count('id'))
            .order_by('-total', 'product_id')
            .values_list('product_id', flat=True)[:8]
        )
        produtos = {p.id: p for p in Product.objects.filter(id__in=mais_comprados)}
        historico = historico_do_usuario(usuario, mais_comprados)
        return [
            _resultado(produtos[pid], historico)
            for pid in mais_comprados if pid in produtos
        ]

    candidatos = [
        (pontuar(texto, produto), produto)
        for produto in Product.objects.only(
            'id', 'name', 'brand', 'package_size', 'unit',
        )
    ]
    candidatos = [(nota, produto) for nota, produto in candidatos if nota > 0]

    historico = historico_do_usuario(usuario, [produto.id for _, produto in candidatos])
    candidatos.sort(
        key=lambda par: (
            -par[0],
            -historico.get(par[1].id, {}).get('vezes', 0),
            par[1].name.lower(),
        )
    )

    return [_resultado(produto, historico) for _, produto in candidatos[:LIMITE]]
