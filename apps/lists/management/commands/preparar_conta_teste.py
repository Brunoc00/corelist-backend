"""Monta listas em andamento numa conta que já existe, para a apresentação.

Uso (no servidor):
    docker compose exec api python manage.py preparar_conta_teste --email conta@exemplo.com

Não cria conta nem mexe em senha: a pessoa cria a conta pelo site e depois
roda este comando com o e-mail dela. Os itens usam produtos e preços reais das
notas fiscais lidas no app (o menor preço visto); sem nota daquele produto,
usa um produto com o nome simples e o preço de referência abaixo. Rodar de novo
refaz as duas listas (não duplica).
"""

import math
import unicodedata
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Min

from apps.accounts.models import User
from apps.lists.models import List, ListItem
from apps.lists.services.enriquecer import tipo_do_produto
from apps.products.models import ObservedPrice, Product
from apps.products.services.categorizar import categoria_pelo_nome

# (nome, folga do orçamento, [(tipo do produto, quantidade, preço de referência, medida)])
# O orçamento sai do total: a primeira lista cabe nele (15% de folga) e a
# segunda passa dele (orçamento de 80% do total), para mostrar o aviso.
LISTAS = [
    ('Compras da semana', Decimal('1.15'), [
        ('Arroz', 1, '24.90', 'unit'),
        ('Feijão', 2, '8.49', 'unit'),
        ('Café', 1, '16.90', 'unit'),
        ('Leite', 6, '5.49', 'unit'),
        ('Açúcar', 1, '4.99', 'unit'),
        ('Óleo', 2, '7.99', 'unit'),
        ('Sabonete', 4, '1.99', 'unit'),
        ('Detergente', 2, '2.49', 'unit'),
        ('Banana', 1, '5.99', 'kg'),
    ]),
    ('Churrasco de sábado', Decimal('0.80'), [
        ('Carne', 2, '45.90', 'kg'),
        ('Linguiça', 1, '24.90', 'kg'),
        ('Carvão', 1, '18.90', 'unit'),
        ('Refrigerante', 2, '9.99', 'unit'),
        ('Pão', 1, '13.99', 'unit'),
        ('Farofa', 1, '6.49', 'unit'),
    ]),
]
# Itens já marcados como "no carrinho" na primeira lista (modo de compra).
NO_CARRINHO = {'Arroz', 'Feijão', 'Café'}


def _chave(texto):
    texto = unicodedata.normalize('NFKD', texto or '')
    return ''.join(c for c in texto if not unicodedata.combining(c)).strip().lower()


def produto_com_preco(tipo, por_tipo):
    """(produto, menor preço, medida) do tipo nas notas, ou None: o produto
    mais barato daquele tipo, como quem usa o app escolheria."""
    candidatos = por_tipo.get(_chave(tipo), [])
    if not candidatos:
        return None
    menor = (
        ObservedPrice.objects.filter(product__in=candidatos)
        .values('product_id', 'measure').annotate(preco=Min('price')).order_by('preco', 'product_id').first()
    )
    produto = next(p for p in candidatos if p.id == menor['product_id'])
    return produto, menor['preco'], menor['measure']


class Command(BaseCommand):
    help = 'Monta duas listas em andamento numa conta que já existe (para a apresentação).'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True, help='E-mail da conta (criada antes pelo site).')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        usuario = User.objects.filter(email__iexact=email).first()
        if usuario is None:
            raise CommandError('Não existe conta com esse e-mail. Crie a conta pelo site primeiro.')

        por_tipo = {}
        for produto in Product.objects.filter(observed_prices__isnull=False).distinct():
            por_tipo.setdefault(_chave(tipo_do_produto(produto)), []).append(produto)

        with transaction.atomic():
            for nome, folga, itens in LISTAS:
                List.objects.filter(owner=usuario, name=nome, is_completed=False).delete()
                lista = List.objects.create(owner=usuario, name=nome)
                das_notas = 0
                for tipo, quantidade, referencia, medida in itens:
                    achado = produto_com_preco(tipo, por_tipo)
                    if achado:
                        produto, preco, medida = achado
                        das_notas += 1
                    else:
                        produto = (
                            Product.objects.filter(name__iexact=tipo).order_by('id').first()
                            or Product.objects.create(
                                name=tipo, created_by=usuario, category=categoria_pelo_nome(tipo),
                            )
                        )
                        preco = Decimal(referencia)
                    # O post_save do ListItem põe o produto em "Meus produtos".
                    ListItem.objects.create(
                        list=lista, product=produto, quantity=quantidade, price=preco, measure=medida,
                        is_completed=nome == 'Compras da semana' and tipo in NO_CARRINHO,
                    )
                # Orçamento redondo (múltiplo de 10) a partir do total.
                total = lista.total
                arredondar = math.ceil if folga > 1 else math.floor
                lista.budget = Decimal(arredondar(total * folga / 10) * 10)
                lista.save(update_fields=['budget', 'updated_at'])
                self.stdout.write(
                    f'{nome}: {len(itens)} itens ({das_notas} com preço das notas), '
                    f'total R$ {total:.2f}, orçamento R$ {lista.budget:.2f}.'
                )
        self.stdout.write(self.style.SUCCESS(f'Listas prontas na conta {usuario.email}.'))
