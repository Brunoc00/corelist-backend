from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product, Store, StoreProductCode

from .models import List, ListItem
from .services.reconhecer import normalizar, produto_certo, semelhanca
from .tests_nfce import LINK, _resposta


class ReconhecerTests(TestCase):
    def test_normaliza_unidades_e_acentos(self):
        self.assertEqual(normalizar('Flocão de milho 500 g'), ['flocao', 'milho', '500g'])
        self.assertEqual(normalizar('Leite 1,5 L'), ['leite', '1.5l'])

    def test_abreviacao_da_nota_parece_com_o_nome_completo(self):
        certo = semelhanca('IOG VIG 800G BATIDO', 'Iogurte Vigor Batido 800 g')
        errado = semelhanca('IOG VIG 800G BATIDO', 'Iogurte Nestle 170 g')

        self.assertGreater(certo, 0.8)
        self.assertLess(errado, 0.3)

    def test_tamanho_diferente_pesa_contra(self):
        cinco = semelhanca('ARROZ CAMIL 5KG', 'Arroz Camil 5 kg')
        um = semelhanca('ARROZ CAMIL 5KG', 'Arroz Camil 1 kg')

        self.assertGreater(cinco, um)

    def test_codigo_da_loja_que_e_codigo_de_barras(self):
        produto = Product.objects.create(name='Leite Ninho', barcode='7891000100103')

        achado, motivo = produto_certo({'codigo_loja': '7891000100103', 'nome': 'LT NINHO'})

        self.assertEqual(achado, produto)
        self.assertEqual(motivo, 'codigo_de_barras')

    def test_codigo_curto_nao_e_codigo_de_barras(self):
        achado, _ = produto_certo({'codigo_loja': '111', 'nome': 'ARROZ'})

        self.assertIsNone(achado)


@patch('apps.lists.services.nfce.urlopen')
class NotaNaListaTests(TestCase):
    """A página de exemplo (tests_nfce.PAGINA) tem: ARROZ TP1 5KG (cód. 111),
    TOMATE KG (cód. 222, por peso) e PRATO FUNDO 2x (cód. 333)."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user(
            email='lista@exemplo.test', password='senha-de-teste-123', first_name='Lista',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

        self.arroz = Product.objects.create(name='Arroz tipo 1', unit='5 kg')
        self.tomate = Product.objects.create(name='Tomate')
        self.leite = Product.objects.create(name='Leite integral 1 L')

        self.lista = List.objects.create(owner=self.usuario, name='Semana', budget=Decimal('100'))
        self.item_arroz = ListItem.objects.create(
            list=self.lista, product=self.arroz, quantity=1, price=Decimal('22.00'),
        )
        self.item_tomate = ListItem.objects.create(
            list=self.lista, product=self.tomate, quantity=1, measure='kg',
        )
        self.item_leite = ListItem.objects.create(
            list=self.lista, product=self.leite, quantity=2, price=Decimal('5.00'),
        )

    def ler(self, lista=None):
        lista = lista or self.lista
        return self.client.post(f'/api/lists/{lista.id}/nfce/ler/', {'link': LINK}, format='json')

    def concluir(self, itens, lista=None):
        lista = lista or self.lista
        return self.client.post(
            f'/api/lists/{lista.id}/nfce/concluir/', {'link': LINK, 'itens': itens}, format='json',
        )

    def test_ler_cruza_a_nota_com_a_lista(self, urlopen):
        urlopen.return_value = _resposta()

        resposta = self.ler()

        self.assertEqual(resposta.status_code, 200)
        itens = resposta.data['itens']
        self.assertEqual(itens[0]['list_item'], self.item_arroz.id)
        self.assertEqual(itens[1]['list_item'], self.item_tomate.id)
        self.assertIsNone(itens[2]['list_item'])  # prato: fora da lista
        self.assertEqual(resposta.data['esquecidos'], [self.item_leite.id])
        self.assertEqual(resposta.data['orcamento'], '100.00')
        self.assertEqual(resposta.data['endereco'], 'Rua Exemplo, 100, Centro, Manaus, AM')
        self.assertTrue(Store.objects.filter(cnpj='12345678000190').exists())

    def test_concluir_aplica_a_nota_na_lista(self, urlopen):
        urlopen.return_value = _resposta()

        resposta = self.concluir([
            {'indice': 0, 'list_item': self.item_arroz.id},
            {'indice': 1, 'list_item': self.item_tomate.id},
            {'indice': 2, 'list_item': None, 'nome': 'Prato fundo'},
        ])

        self.assertEqual(resposta.status_code, 200)
        self.lista.refresh_from_db()
        self.assertTrue(self.lista.is_completed)
        self.assertEqual(self.lista.store, 'MERCADO EXEMPLO LTDA')
        self.assertEqual(self.lista.completed_at.strftime('%d/%m/%Y'), '23/10/2026')

        self.item_arroz.refresh_from_db()
        self.assertEqual(self.item_arroz.price, Decimal('24.90'))  # preço da nota
        self.assertTrue(self.item_arroz.is_completed)

        self.item_tomate.refresh_from_db()
        self.assertEqual(self.item_tomate.measure, 'kg')
        self.assertEqual(self.item_tomate.price, Decimal('7.99'))

        prato = self.lista.items.get(product__name='Prato fundo')
        self.assertEqual(prato.quantity, Decimal('2'))
        self.assertTrue(prato.is_completed)

        # Esquecido: fica como "não comprado", sem preço, fora do total.
        self.item_leite.refresh_from_db()
        self.assertFalse(self.item_leite.is_completed)
        self.assertIsNone(self.item_leite.price)
        self.assertEqual(resposta.data['esquecidos'], [self.item_leite.id])
        self.assertAlmostEqual(self.lista.total, Decimal('49.47'), delta=Decimal('0.01'))

    def test_concluir_aprende_o_codigo_da_loja(self, urlopen):
        urlopen.return_value = _resposta()
        self.concluir([{'indice': 0, 'list_item': self.item_arroz.id}])

        vinculo = StoreProductCode.objects.get(store__cnpj='12345678000190', store_code='111')
        self.assertEqual(vinculo.product, self.arroz)

    def test_vinculo_aprendido_liga_mesmo_com_nome_diferente(self, urlopen):
        urlopen.return_value = _resposta()
        loja = Store.objects.create(cnpj='12345678000190', name='MERCADO EXEMPLO LTDA')
        feijao = Product.objects.create(name='Feijão carioca')
        StoreProductCode.objects.create(store=loja, store_code='111', product=feijao)
        lista = List.objects.create(owner=self.usuario, name='Outra')
        item = ListItem.objects.create(list=lista, product=feijao, quantity=1)

        resposta = self.ler(lista)

        self.assertEqual(resposta.data['itens'][0]['list_item'], item.id)
        self.assertEqual(resposta.data['itens'][0]['motivo'], 'aprendido')

    def test_lista_vazia_pode_ser_concluida_pela_nota(self, urlopen):
        urlopen.return_value = _resposta()
        vazia = List.objects.create(owner=self.usuario, name='Fim de semana', budget=Decimal('200'))

        lida = self.ler(vazia)
        self.assertTrue(all(item['list_item'] is None for item in lida.data['itens']))

        resposta = self.concluir([{'indice': i} for i in range(3)], vazia)

        self.assertEqual(resposta.status_code, 200)
        vazia.refresh_from_db()
        self.assertTrue(vazia.is_completed)
        self.assertEqual(vazia.items.count(), 3)

    def test_mesma_nota_nao_entra_duas_vezes(self, urlopen):
        urlopen.return_value = _resposta()
        self.concluir([{'indice': 0, 'list_item': self.item_arroz.id}])
        outra = List.objects.create(owner=self.usuario, name='Outra')

        resposta = self.concluir([{'indice': 0}], outra)

        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(resposta.data['list_id'], self.lista.id)

    def test_nao_aceita_item_de_outra_lista(self, urlopen):
        urlopen.return_value = _resposta()
        outra = List.objects.create(owner=self.usuario, name='Outra')
        intruso = ListItem.objects.create(list=outra, product=self.leite, quantity=1)

        resposta = self.concluir([{'indice': 0, 'list_item': intruso.id}])

        self.assertEqual(resposta.status_code, 400)
        self.lista.refresh_from_db()
        self.assertFalse(self.lista.is_completed)

    def test_nao_aceita_o_mesmo_item_da_lista_duas_vezes(self, urlopen):
        urlopen.return_value = _resposta()

        resposta = self.concluir([
            {'indice': 0, 'list_item': self.item_arroz.id},
            {'indice': 2, 'list_item': self.item_arroz.id},
        ])

        self.assertEqual(resposta.status_code, 400)

    def test_lista_de_outra_pessoa_da_404(self, urlopen):
        outro = User.objects.create_user(
            email='outro@exemplo.test', password='senha-de-teste-123', first_name='Outro',
        )
        self.client.force_authenticate(outro)

        self.assertEqual(self.ler().status_code, 404)
        self.assertEqual(self.concluir([{'indice': 0}]).status_code, 404)
        urlopen.assert_not_called()

    def test_lista_concluida_nao_recebe_nota(self, urlopen):
        self.lista.is_completed = True
        self.lista.save()

        self.assertEqual(self.ler().status_code, 404)
