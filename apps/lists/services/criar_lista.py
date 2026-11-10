"""Cria uma lista de compras a pedido do assistente ("crie uma lista com
arroz, feijão e café").

Cada item vira um produto do catálogo da pessoa: o que ela já usa (mesma
busca do campo "adicionar item"), um produto com o mesmo nome no catálogo
geral ou, se não houver, um produto novo. O preço planejado é o último que a
pessoa pagou por aquele produto, quando houver.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.products.models import Product
from apps.products.services.categorizar import categoria_pelo_nome

from ..models import List, ListItem
from .busca import buscar
from .reconhecer import nome_completo, normalizar

MAX_ITENS = 30
MAX_QUANTIDADE = Decimal('999')
# Se a IA pedir a mesma lista de novo logo em seguida (outra IA assumiu a
# resposta, por exemplo), devolve a que já foi criada.
JANELA_SEM_REPETIR = timedelta(minutes=2)


def _quantidade(valor):
    try:
        quantidade = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('1')
    if not quantidade.is_finite() or quantidade <= 0:
        return Decimal('1')
    return min(quantidade, MAX_QUANTIDADE).quantize(Decimal('0.001'))


def _orcamento(valor):
    if valor in (None, ''):
        return None
    try:
        orcamento = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not orcamento.is_finite() or orcamento <= 0:
        return None
    return min(orcamento, Decimal('99999999')).quantize(Decimal('0.01'))


def _produto(usuario, nome):
    """(produto, último preço, última medida) para o nome do item."""
    # Regra mais rígida que a da busca: cada palavra pedida tem de ser o começo
    # de uma palavra do produto ("arroz" acha "ARROZ TP1 CAMIL"). A busca também
    # aceita abreviações da nota, e aí "cerveja" acharia "CER MAT..." (cereal).
    pedidas = normalizar(nome)
    for achado in buscar(usuario, nome) if pedidas else []:
        produto = Product.objects.get(id=achado['id'])
        palavras = normalizar(nome_completo(produto))
        if all(any(p.startswith(pedida) for p in palavras) for pedida in pedidas):
            return produto, achado['ultimo_preco'], achado['ultima_medida']

    existente = Product.objects.filter(name__iexact=nome).order_by('id').first()
    if existente:
        return existente, None, None

    novo = Product.objects.create(
        name=nome[:1].upper() + nome[1:],
        data_source=Product.SOURCE_MANUAL,
        category=categoria_pelo_nome(nome),
        created_by=usuario,
    )
    return novo, None, None


def criar_lista(usuario, nome, itens, orcamento=None):
    """Cria a lista (ativa) e devolve um resumo para a IA e para o site."""
    limpos = []
    for item in (itens if isinstance(itens, list) else [])[:MAX_ITENS]:
        if isinstance(item, str):
            item = {'produto': item}
        if not isinstance(item, dict):
            continue
        produto = ' '.join(str(item.get('produto') or '').split())[:200]
        if produto:
            limpos.append((produto, _quantidade(item.get('quantidade', 1))))
    if not limpos:
        return {'erro': 'Diga quais produtos vão na lista.'}

    nome = ' '.join(str(nome or '').split())[:200] or f'Lista de {timezone.localdate():%d/%m}'
    recente = List.objects.filter(
        owner=usuario, name=nome, is_completed=False,
        created_at__gte=timezone.now() - JANELA_SEM_REPETIR,
    ).order_by('-id').first()
    if recente:
        return resumo_da_lista(recente)

    with transaction.atomic():
        lista = List.objects.create(owner=usuario, name=nome, budget=_orcamento(orcamento))
        criados = []
        for produto_nome, quantidade in limpos:
            produto, preco, medida = _produto(usuario, produto_nome)
            existente = next((c for c in criados if c.product_id == produto.id), None)
            if existente:  # mesmo produto duas vezes: soma a quantidade
                existente.quantity = min(existente.quantity + quantidade, MAX_QUANTIDADE)
                existente.save(update_fields=['quantity', 'updated_at'])
                continue
            # O post_save do ListItem põe o produto em "Meus produtos".
            criados.append(ListItem.objects.create(
                list=lista,
                product=produto,
                quantity=quantidade,
                measure=medida or ListItem.MEASURE_UNIT,
                price=preco,
            ))

    return resumo_da_lista(lista)


def resumo_da_lista(lista):
    itens = list(lista.items.select_related('product').order_by('id'))
    return {
        'lista_criada': {
            'id': lista.id,
            'nome': lista.name,
            'orcamento': None if lista.budget is None else str(lista.budget),
            'total_planejado': str(
                sum((item.subtotal or Decimal('0') for item in itens), Decimal('0')).quantize(Decimal('0.01'))
            ),
            # O total só soma os itens com preço: a IA avisa quantos faltam.
            'itens_sem_preco': sum(1 for item in itens if item.price is None),
            'itens': [
                {
                    'produto': item.product.name,
                    'quantidade': str(item.quantity.normalize()),
                    'medida': item.measure,
                    'preco_planejado': None if item.price is None else str(item.price),
                }
                for item in itens
            ],
        },
    }


# --- Sem IA: "crie uma lista com arroz, 2 feijão e café" ---------------------

PEDIDO_DE_LISTA = re.compile(
    r'\b(cri[ae]r?|cria|fa[cz]a|fazer|monte|montar|monta|nova|gere|gerar|anote|anota)\b.*\blista\b'
)


def _simples(texto):
    texto = unicodedata.normalize('NFKD', texto.lower())
    return ''.join(c for c in texto if not unicodedata.combining(c))


def e_pedido_de_lista(pergunta):
    return bool(PEDIDO_DE_LISTA.search(_simples(pergunta)))


def itens_do_pedido(pergunta):
    """Itens depois de "com" ou ":" (separados por vírgula, ";" ou " e ")."""
    partes = re.split(r'\bcom\b|:', pergunta, maxsplit=1, flags=re.IGNORECASE)
    if len(partes) < 2:
        return []
    itens = []
    for parte in re.split(r',|;|\n|\s+e\s+', partes[1]):
        parte = parte.strip(' .!?')
        if not parte:
            continue
        numero = re.match(r'^(\d+(?:[.,]\d+)?)\s*(?:x\s+|unidades?\s+(?:de\s+)?|pacotes?\s+(?:de\s+)?)?(.+)$', parte)
        if numero:
            itens.append({'produto': numero.group(2), 'quantidade': numero.group(1).replace(',', '.')})
        else:
            itens.append({'produto': parte, 'quantidade': 1})
    return itens
