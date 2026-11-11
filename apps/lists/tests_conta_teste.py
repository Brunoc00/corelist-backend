from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounts.models import User
from apps.products.models import ProdutoDoUsuario, Product, Store

from .models import List
from .tests_precos import _preco


class PrepararContaTesteTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(email='teste@exemplo.test', password='senha-de-teste-123')
        loja = Store.objects.create(cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A')
        self.arroz = Product.objects.create(name='ARROZ TP1 CAMIL 5KG', tipo='Arroz')
        _preco(self.arroz, loja, '23.50')

    def rodar(self, email='teste@exemplo.test'):
        call_command('preparar_conta_teste', email=email, stdout=StringIO())

    def test_cria_as_duas_listas_com_preco_das_notas(self):
        self.rodar()

        semana = List.objects.get(owner=self.usuario, name='Compras da semana')
        churrasco = List.objects.get(owner=self.usuario, name='Churrasco de sábado')
        arroz = semana.items.get(product=self.arroz)
        self.assertEqual(arroz.price, Decimal('23.50'))
        self.assertTrue(arroz.is_completed)
        self.assertFalse(semana.is_completed)
        self.assertLessEqual(semana.total, semana.budget)
        self.assertGreater(churrasco.total, churrasco.budget)  # mostra o aviso de estouro
        self.assertTrue(ProdutoDoUsuario.objects.filter(usuario=self.usuario, produto=self.arroz).exists())

    def test_rodar_de_novo_nao_duplica(self):
        self.rodar()
        self.rodar()
        self.assertEqual(List.objects.filter(owner=self.usuario).count(), 2)

    def test_conta_que_nao_existe(self):
        with self.assertRaises(CommandError):
            self.rodar('ninguem@exemplo.test')
        self.assertFalse(List.objects.exists())
