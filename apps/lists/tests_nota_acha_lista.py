from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product

from .models import List, ListItem
from .tests_nfce import LINK, _resposta


@patch('apps.lists.services.nfce.urlopen')
class NotaAchaAListaTests(TestCase):
    """Ao ler a nota pelo menu, o app sugere a lista em andamento parecida.
    A página de exemplo (tests_nfce.PAGINA) tem ARROZ TP1 5KG, TOMATE KG e
    PRATO FUNDO 2x."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user(email='acha@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)
        self.arroz = Product.objects.create(name='Arroz tipo 1', unit='5 kg')
        self.tomate = Product.objects.create(name='Tomate')
        self.leite = Product.objects.create(name='Leite integral 1 L')
        self.sabao = Product.objects.create(name='Sabão em pó')

    def lista(self, nome, produtos, **extra):
        lista = List.objects.create(owner=self.usuario, name=nome, **extra)
        for produto in produtos:
            ListItem.objects.create(list=lista, product=produto, quantity=1, price=Decimal('5.00'))
        return lista

    def ler(self):
        return self.client.post('/api/lists/nfce/ler/', {'link': LINK}, format='json')

    def test_sugere_a_lista_com_os_produtos_da_nota(self, urlopen):
        urlopen.return_value = _resposta()
        semana = self.lista('Compra da semana', [self.arroz, self.tomate, self.leite])
        self.lista('Limpeza', [self.sabao])

        dados = self.ler().data

        self.assertEqual(dados['listas_parecidas'], [
            {'id': semana.id, 'nome': 'Compra da semana', 'itens_na_nota': 2, 'total_itens': 3},
        ])

    def test_lista_mais_parecida_vem_primeiro(self, urlopen):
        urlopen.return_value = _resposta()
        grande = self.lista('Mês', [self.arroz, self.tomate, self.leite, self.sabao])
        pequena = self.lista('Feira', [self.arroz, self.tomate])

        ids = [lista['id'] for lista in self.ler().data['listas_parecidas']]

        self.assertEqual(ids, [pequena.id, grande.id])

    def test_lista_pequena_com_metade_na_nota_entra(self, urlopen):
        urlopen.return_value = _resposta()
        feira = self.lista('Feira', [self.tomate, self.leite])
        self.assertEqual([l['id'] for l in self.ler().data['listas_parecidas']], [feira.id])

    def test_nao_sugere_lista_concluida_de_outra_pessoa_ou_vazia(self, urlopen):
        urlopen.return_value = _resposta()
        concluida = self.lista('Antiga', [self.arroz, self.tomate])
        concluida.is_completed = True
        concluida.save()
        outra = User.objects.create_user(email='outra-acha@exemplo.test', password='senha-de-teste-123')
        lista_da_outra = List.objects.create(owner=outra, name='Da outra')
        ListItem.objects.create(list=lista_da_outra, product=self.arroz, quantity=1)
        ListItem.objects.create(list=lista_da_outra, product=self.tomate, quantity=1)
        List.objects.create(owner=self.usuario, name='Vazia')

        self.assertEqual(self.ler().data['listas_parecidas'], [])

    def test_nota_ja_importada_nao_sugere(self, urlopen):
        urlopen.return_value = _resposta()
        self.lista('Compra da semana', [self.arroz, self.tomate])
        importar = self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')
        self.assertEqual(importar.status_code, 201)

        dados = self.ler().data

        self.assertIsNotNone(dados['lista_importada'])
        self.assertEqual(dados['listas_parecidas'], [])
