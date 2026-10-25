"""Cria (ou recria) uma conta de demonstração com listas e compras.

Uso: python manage.py seed_demo

Serve para testar no celular e para a apresentação. Só roda com DEBUG=True,
porque a senha abaixo é pública (está no repositório).

A demo usa produtos de marca com código de barras real, para a tela
"Produtos com menor preço" mostrar foto e marca (vindas do Open Food Facts).
Sem internet, os produtos são criados com nome e marca, só sem foto.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.lists.models import List, ListItem
from apps.products.models import Category, Product
from apps.products.services.openfoodfacts import buscar_por_codigo

DEMO_EMAIL = 'demo@comprafacil.test'
DEMO_SENHA = 'Demo@CompraFacil2026'
DEMO_NOME = 'Demo'

# chave: (código de barras, nome, marca, embalagem, categoria)
PRODUTOS_DEMO = {
    'arroz': ('7896006711155', 'Arroz tipo 1', 'Camil', '5 kg', 'Alimentos'),
    'feijao': ('7896006744115', 'Feijão carioca', 'Camil', '1 kg', 'Alimentos'),
    'cafe': ('7896089011357', 'Café torrado e moído', 'Pilão', '500 g', 'Bebidas'),
    'leite': ('7891000100103', 'Leite condensado', 'Nestlé', '395 g', 'Alimentos'),
    'lamen': ('7891079000229', 'Lámen galinha caipira', 'Nissin', '85 g', 'Alimentos'),
    'toddy': ('7894321711171', 'Achocolatado em pó', 'Toddy', '200 g', 'Bebidas'),
}

# (nome, orçamento, dias atrás, [(produto, quantidade, preço)])
COMPRAS_CONCLUIDAS = [
    ('Compra do mês passado', '250', 35, [
        ('arroz', 1, '24.90'),
        ('feijao', 2, '8.90'),
        ('cafe', 1, '16.90'),
        ('leite', 2, '8.49'),
        ('toddy', 2, '7.90'),
    ]),
    ('Feira', '100', 10, [
        ('arroz', 1, '23.90'),
        ('feijao', 2, '8.50'),
        ('leite', 1, '7.99'),
        ('lamen', 6, '2.49'),
    ]),
    ('Lanche da semana', '60', 3, [
        ('cafe', 1, '17.50'),
        ('toddy', 1, '8.50'),
        ('lamen', 4, '2.69'),
    ]),
]

# (produto, quantidade, preço, já no carrinho)
LISTA_EM_ANDAMENTO = [
    ('arroz', 1, '21.90', True),
    ('leite', 3, '6.99', False),
    ('cafe', 1, '14.90', False),
    ('feijao', 2, '9.20', False),
    ('lamen', 5, '1.99', False),
    ('toddy', 1, '6.99', False),
]


class Command(BaseCommand):
    help = (
        'Cria a conta de demonstração (listas, compras e produtos de marca '
        'com foto). Só funciona com DEBUG=True.'
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'seed_demo só roda com DEBUG=True: a senha da conta demo é '
                'pública.'
            )

        call_command('seed_products', stdout=self.stdout)

        usuario = self.conta_demo()

        # Recomeça do zero a cada execução.
        List.objects.filter(owner=usuario).delete()

        produtos = {
            chave: self.produto_de_marca(*dados)
            for chave, dados in PRODUTOS_DEMO.items()
        }

        agora = timezone.now()

        for nome, orcamento, dias_atras, itens in COMPRAS_CONCLUIDAS:
            lista = List.objects.create(
                owner=usuario,
                name=nome,
                budget=Decimal(orcamento),
                is_completed=True,
                completed_at=agora - timedelta(days=dias_atras),
            )

            for chave, quantidade, preco in itens:
                ListItem.objects.create(
                    list=lista,
                    product=produtos[chave],
                    quantity=quantidade,
                    price=Decimal(preco),
                    is_completed=True,
                )

        ativa = List.objects.create(
            owner=usuario,
            name='Compra da semana',
            budget=Decimal('150'),
        )

        for chave, quantidade, preco, no_carrinho in LISTA_EM_ANDAMENTO:
            ListItem.objects.create(
                list=ativa,
                product=produtos[chave],
                quantity=quantidade,
                price=Decimal(preco),
                is_completed=no_carrinho,
            )

        com_foto = sum(1 for p in produtos.values() if p.image_url)

        self.stdout.write(self.style.SUCCESS(
            f'Conta demo pronta: {DEMO_EMAIL} '
            '(senha em apps/lists/management/commands/seed_demo.py). '
            f'Produtos com foto: {com_foto} de {len(produtos)}.'
        ))

    def conta_demo(self):
        usuario = User.objects.filter(email=DEMO_EMAIL).first()

        if usuario is None:
            return User.objects.create_user(
                email=DEMO_EMAIL,
                password=DEMO_SENHA,
                first_name=DEMO_NOME,
            )

        usuario.set_password(DEMO_SENHA)
        usuario.first_name = DEMO_NOME
        usuario.save()

        return usuario

    def produto_de_marca(self, codigo, nome, marca, embalagem, categoria):
        produto = Product.objects.filter(barcode=codigo).first()

        if produto is None:
            produto = Product(
                barcode=codigo,
                name=nome,
                brand=marca,
                package_size=embalagem,
                category=Category.objects.filter(name=categoria).first(),
            )

        # Completa só a foto; nome e marca da demo ficam padronizados.
        if not produto.image_url:
            sugestao = buscar_por_codigo(codigo)

            if sugestao and sugestao['image_url']:
                produto.image_url = sugestao['image_url']
                produto.data_source = sugestao['data_source']

        produto.save()

        return produto
