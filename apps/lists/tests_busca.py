from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product

from .models import List, ListItem


class BuscaDeProdutosTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            email='busca@exemplo.test', password='senha-de-teste-123', first_name='Busca',
        )
        self.outro = User.objects.create_user(
            email='outro@exemplo.test', password='senha-de-teste-123', first_name='Outro',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

        self.arroz = Product.objects.create(name='Arroz', unit='5 kg')
        self.arroz_camil = Product.objects.create(name='Arroz Camil tipo 1', package_size='5 kg')
        self.iogurte_nota = Product.objects.create(name='IOG VIG 800G BATIDO', data_source='nfce')
        self.feijao = Product.objects.create(name='Feijão carioca', package_size='1 kg')
        self.cafe = Product.objects.create(name='Café Pilão', package_size='500 g')

    def buscar(self, texto):
        return self.client.get('/api/lists/produtos/', {'q': texto})

    def nomes(self, resposta):
        return [produto['name'] for produto in resposta.data]

    def test_comeco_da_palavra_acha_o_produto(self):
        resposta = self.buscar('arr')

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(set(self.nomes(resposta)), {'Arroz', 'Arroz Camil tipo 1'})

    def test_ignora_acento_e_maiuscula(self):
        self.assertEqual(self.nomes(self.buscar('FEIJAO')), ['Feijão carioca'])
        self.assertEqual(self.nomes(self.buscar('cafe pil')), ['Café Pilão'])

    def test_acha_o_nome_abreviado_da_nota(self):
        self.assertIn('IOG VIG 800G BATIDO', self.nomes(self.buscar('iogurte')))

    def test_mostra_embalagem_e_ultimo_preco_pago(self):
        lista = List.objects.create(owner=self.usuario, name='Mês', is_completed=True)
        ListItem.objects.create(
            list=lista, product=self.arroz_camil, quantity=1, price=Decimal('26.49'),
        )

        camil = next(p for p in self.buscar('camil').data if p['id'] == self.arroz_camil.id)

        self.assertEqual(camil['embalagem'], '5 kg')
        self.assertEqual(camil['ultimo_preco'], '26.49')
        self.assertEqual(camil['ultima_medida'], 'unit')

    def test_preco_de_outra_pessoa_nao_aparece(self):
        lista = List.objects.create(owner=self.outro, name='Dele')
        ListItem.objects.create(list=lista, product=self.cafe, quantity=1, price=Decimal('15'))

        cafe = self.buscar('cafe').data[0]

        self.assertIsNone(cafe['ultimo_preco'])

    def test_o_que_a_pessoa_mais_compra_vem_primeiro_no_empate(self):
        lista = List.objects.create(owner=self.usuario, name='Mês')
        for _ in range(3):
            ListItem.objects.create(list=lista, product=self.arroz_camil, quantity=1)

        self.assertEqual(self.nomes(self.buscar('arroz'))[0], 'Arroz Camil tipo 1')

    def test_sem_texto_mostra_os_mais_comprados(self):
        lista = List.objects.create(owner=self.usuario, name='Mês')
        ListItem.objects.create(list=lista, product=self.cafe, quantity=1)
        ListItem.objects.create(list=lista, product=self.cafe, quantity=1)
        ListItem.objects.create(list=lista, product=self.feijao, quantity=1)

        self.assertEqual(self.nomes(self.buscar('')), ['Café Pilão', 'Feijão carioca'])

    def test_nada_parecido_devolve_vazio(self):
        self.assertEqual(self.buscar('xyzw').data, [])

    def test_exige_login(self):
        self.assertEqual(APIClient().get('/api/lists/produtos/', {'q': 'arr'}).status_code, 401)
