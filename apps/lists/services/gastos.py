"""Dados da própria pessoa para o assistente: as listas ativas e os gastos
das compras concluídas. Só leitura, sempre filtrado pelo dono."""

from datetime import datetime
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from ..models import List, ListItem
from .precos import precos_da_lista

MAX_LISTAS = 5
MAX_ITENS = 30
ZERO = Decimal('0.00')
VALOR = DecimalField(max_digits=14, decimal_places=2)


def _dinheiro(valor):
    return str(Decimal(valor or 0).quantize(Decimal('0.01')))


def _nome_da_loja(loja):
    return loja['marca'] or loja['nome']


def listas_ativas(usuario):
    """As listas em andamento: itens, total planejado, saldo do orçamento e a
    loja mais barata para a lista pelas notas fiscais."""
    listas = (
        List.objects
        .filter(owner=usuario, is_completed=False)
        .order_by('-updated_at')[:MAX_LISTAS]
    )
    resultado = []
    for lista in listas:
        itens = list(lista.items.select_related('product').order_by('id'))
        precos = precos_da_lista(lista)
        por_item = {linha['item']: linha for linha in precos['itens']}

        planejado = sum(
            (item.quantity * item.price for item in itens if item.price is not None), ZERO,
        )
        dados_itens = []
        for item in itens[:MAX_ITENS]:
            barato = por_item.get(item.id, {}).get('mais_barato_agora')
            dados_itens.append({
                'produto': item.product.name,
                'quantidade': str(item.quantity),
                'medida': item.measure,
                'preco_planejado': None if item.price is None else str(item.price),
                'comprado': item.is_completed,
                'mais_barato_agora': None if barato is None else {
                    'loja': _nome_da_loja(barato['loja']),
                    'preco': barato['preco'],
                },
            })

        melhor = precos['melhor_loja'][0] if precos['melhor_loja'] else None
        resultado.append({
            'nome': lista.name,
            'orcamento': None if lista.budget is None else _dinheiro(lista.budget),
            'total_planejado': _dinheiro(planejado),
            'saldo': None if lista.budget is None else _dinheiro(lista.budget - planejado),
            'quantidade_de_itens': len(itens),
            'itens': dados_itens,
            'melhor_loja_para_a_lista': None if melhor is None else {
                'loja': _nome_da_loja(melhor['loja']),
                'itens_com_preco': melhor['itens'],
                'total': melhor['total'],
                'precos_que_podem_ter_mudado': melhor['desatualizados'],
            },
        })
    return {'listas': resultado}


def _inicio_do_periodo(meses):
    hoje = timezone.localdate()
    ano, mes = hoje.year, hoje.month - (meses - 1)
    while mes <= 0:
        mes += 12
        ano -= 1
    return timezone.make_aware(datetime(ano, mes, 1))


def resumo_de_gastos(usuario, meses=3):
    """Gastos das compras concluídas nos últimos meses (o mês atual conta)."""
    try:
        meses = max(1, min(int(meses), 12))
    except (TypeError, ValueError):
        meses = 3
    inicio = _inicio_do_periodo(meses)
    valor = ExpressionWrapper(F('quantity') * F('price'), output_field=VALOR)

    itens = ListItem.objects.filter(
        list__owner=usuario,
        list__is_completed=True,
        list__completed_at__gte=inicio,
        price__isnull=False,
    )
    total = itens.aggregate(total=Coalesce(Sum(valor), ZERO))['total']

    por_mes = (
        itens.annotate(mes=TruncMonth('list__completed_at'))
        .values('mes').annotate(total=Sum(valor)).order_by('mes')
    )
    por_categoria = (
        itens.values('product__category__name')
        .annotate(total=Sum(valor)).order_by('-total')[:8]
    )
    mais_comprados = (
        itens.values('product__name')
        .annotate(vezes=Count('id'), gasto=Sum(valor))
        .order_by('-vezes', '-gasto')[:5]
    )

    compras = (
        List.objects
        .filter(owner=usuario, is_completed=True, completed_at__gte=inicio)
        .annotate(gasto=Coalesce(
            Sum(
                ExpressionWrapper(F('items__quantity') * F('items__price'), output_field=VALOR),
            ),
            ZERO,
        ))
    )
    compras = list(compras)
    com_orcamento = [compra for compra in compras if compra.budget is not None]

    return {
        'desde': inicio.strftime('%d/%m/%Y'),
        'compras_concluidas': len(compras),
        'total_gasto': _dinheiro(total),
        'por_mes': [
            {'mes': linha['mes'].strftime('%m/%Y'), 'total': _dinheiro(linha['total'])}
            for linha in por_mes
        ],
        'por_categoria': [
            {'categoria': linha['product__category__name'] or 'Sem categoria', 'total': _dinheiro(linha['total'])}
            for linha in por_categoria
        ],
        'mais_comprados': [
            {'produto': linha['product__name'], 'vezes': linha['vezes'], 'gasto': _dinheiro(linha['gasto'])}
            for linha in mais_comprados
        ],
        'compras_com_orcamento': len(com_orcamento),
        'compras_dentro_do_orcamento': sum(1 for compra in com_orcamento if compra.gasto <= compra.budget),
    }
