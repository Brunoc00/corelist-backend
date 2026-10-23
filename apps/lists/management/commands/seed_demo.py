"""Cria (ou recria) uma conta de demonstração com listas e compras.

Uso: python manage.py seed_demo

Serve para testar no celular e para a apresentação. Só roda com DEBUG=True,
porque a senha abaixo é pública (está no repositório).
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

# Produto que tem foto e marca no Open Food Facts.
CODIGO_COM_FOTO = '7891000100103'


class Command(BaseCommand):
    help = (
        'Cria a conta de demonstração (listas, compras e produto com foto). '
        'Só funciona com DEBUG=True.'
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'seed_demo só roda com DEBUG=True: a senha da conta demo é '
                'pública.'
            )

        call_command('seed_products', stdout=self.stdout)

        usuario = User.objects.filter(email=DEMO_EMAIL).first()

        if usuario is None:
            usuario = User.objects.create_user(
                email=DEMO_EMAIL,
                password=DEMO_SENHA,
                first_name=DEMO_NOME,
            )
        else:
            usuario.set_password(DEMO_SENHA)
            usuario.first_name = DEMO_NOME
            usuario.save()

        # Recomeça do zero a cada execução.
        List.objects.filter(owner=usuario).delete()

        produtos = {
            nome: Product.objects.get(name=nome)
            for nome in ['Arroz', 'Feijão', 'Café', 'Suco', 'Detergente']
        }
        produtos['Leite condensado'] = self.produto_com_foto()

        agora = timezone.now()

        self.compra_concluida(
            usuario,
            'Compra do mês passado',
            '250',
            agora - timedelta(days=35),
            [
                ('Arroz', 1, '24.90'),
                ('Feijão', 2, '8.90'),
                ('Café', 1, '16.90'),
                ('Leite condensado', 2, '8.49'),
                ('Suco', 4, '6.50'),
            ],
        )
        self.compra_concluida(
            usuario,
            'Feira',
            '100',
            agora - timedelta(days=10),
            [
                ('Arroz', 1, '23.90'),
                ('Feijão', 2, '8.50'),
                ('Leite condensado', 1, '7.99'),
            ],
        )
        self.compra_concluida(
            usuario,
            'Churrasco',
            '120',
            agora - timedelta(days=3),
            [
                ('Detergente', 2, '3.20'),
                ('Suco', 6, '6.90'),
                ('Café', 1, '17.50'),
            ],
        )

        ativa = List.objects.create(
            owner=usuario,
            name='Compra da semana',
            budget=Decimal('150'),
        )

        for nome, quantidade, preco, no_carrinho in [
            ('Arroz', 1, '21.90', True),
            ('Leite condensado', 3, '6.99', False),
            ('Café', 1, '14.90', False),
            ('Feijão', 2, '9.20', False),
            ('Detergente', 2, '2.79', False),
        ]:
            ListItem.objects.create(
                list=ativa,
                product=produtos[nome],
                quantity=quantidade,
                price=Decimal(preco),
                is_completed=no_carrinho,
            )

        self.stdout.write(self.style.SUCCESS(
            f'Conta demo pronta: {DEMO_EMAIL} '
            '(senha em apps/lists/management/commands/seed_demo.py).'
        ))

    def produto_com_foto(self):
        produto = Product.objects.filter(barcode=CODIGO_COM_FOTO).first()

        if produto is not None:
            return produto

        sugestao = buscar_por_codigo(CODIGO_COM_FOTO)
        categoria = Category.objects.filter(name='Alimentos').first()

        if sugestao is None:
            # Sem internet: cria sem foto, o resto da demo funciona igual.
            return Product.objects.create(
                name='Leite condensado',
                package_size='395 g',
                barcode=CODIGO_COM_FOTO,
                category=categoria,
            )

        return Product.objects.create(
            name=sugestao['name'],
            brand=sugestao['brand'],
            package_size=sugestao['package_size'],
            image_url=sugestao['image_url'],
            barcode=CODIGO_COM_FOTO,
            data_source=Product.SOURCE_OPEN_FOOD_FACTS,
            category=categoria,
        )

    def compra_concluida(self, usuario, nome, orcamento, quando, itens):
        lista = List.objects.create(
            owner=usuario,
            name=nome,
            budget=Decimal(orcamento),
            is_completed=True,
            completed_at=quando,
        )

        for nome_produto, quantidade, preco in itens:
            ListItem.objects.create(
                list=lista,
                product=self.produto(nome_produto),
                quantity=quantidade,
                price=Decimal(preco),
                is_completed=True,
            )

    def produto(self, nome):
        if nome == 'Leite condensado':
            return self.produto_com_foto()

        return Product.objects.get(name=nome)
