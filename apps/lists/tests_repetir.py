from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product

from .models import List, ListItem


class RepetirCompraTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            email='repetir@exemplo.test', password='senha-de-teste-123', first_name='Repetir',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

        self.compra = List.objects.create(
            owner=self.usuario, name='Nota do mercado', budget=Decimal('200'),
            is_completed=True, completed_at=timezone.now(),
        )
        arroz = Product.objects.create(name='Arroz')
        banana = Product.objects.create(name='Banana')
        sabao = Product.objects.create(name='Sabão')
        self.item_arroz = ListItem.objects.create(
            list=self.compra, product=arroz, quantity=2, price=Decimal('25.90'), is_completed=True,
        )
        self.item_banana = ListItem.objects.create(
            list=self.compra, product=banana, quantity=Decimal('1.235'), measure='kg',
            price=Decimal('6.99'), is_completed=True,
        )
        # Item esquecido (não comprado): fica fora quando não se escolhe nada.
        self.item_sabao = ListItem.objects.create(
            list=self.compra, product=sabao, quantity=1, price=None, is_completed=False,
        )

    def repetir(self, lista_id, dados=None):
        return self.client.post(f'/api/lists/{lista_id}/repetir/', dados or {}, format='json')

    def test_cria_lista_em_andamento_com_os_itens_comprados(self):
        resposta = self.repetir(self.compra.id, {'nome': 'Compra da semana'})

        self.assertEqual(resposta.status_code, 201)
        nova = List.objects.get(pk=resposta.data['id'])
        self.assertEqual(nova.name, 'Compra da semana')
        self.assertFalse(nova.is_completed)
        self.assertEqual(nova.budget, Decimal('200'))
        itens = {item.product.name: item for item in nova.items.all()}
        self.assertEqual(set(itens), {'Arroz', 'Banana'})
        self.assertEqual(itens['Arroz'].price, Decimal('25.90'))
        self.assertEqual(itens['Banana'].measure, 'kg')
        self.assertEqual(itens['Banana'].quantity, Decimal('1.235'))
        self.assertFalse(any(item.is_completed for item in itens.values()))

    def test_copia_so_os_itens_escolhidos(self):
        resposta = self.repetir(self.compra.id, {'itens': [self.item_arroz.id, self.item_sabao.id]})

        self.assertEqual(resposta.status_code, 201)
        nomes = sorted(
            ListItem.objects.filter(list_id=resposta.data['id']).values_list('product__name', flat=True)
        )
        self.assertEqual(nomes, ['Arroz', 'Sabão'])

    def test_nome_padrao(self):
        resposta = self.repetir(self.compra.id)
        self.assertEqual(resposta.data['name'], 'Próxima compra')

    def test_nao_muda_a_compra_original(self):
        self.repetir(self.compra.id)
        self.compra.refresh_from_db()
        self.assertTrue(self.compra.is_completed)
        self.assertEqual(self.compra.items.count(), 3)

    def test_sem_itens_da_400(self):
        resposta = self.repetir(self.compra.id, {'itens': []})
        self.assertEqual(resposta.status_code, 400)

    def test_itens_invalidos_da_400(self):
        resposta = self.repetir(self.compra.id, {'itens': 'tudo'})
        self.assertEqual(resposta.status_code, 400)

    def test_item_de_outra_lista_e_ignorado(self):
        outra = List.objects.create(owner=self.usuario, name='Outra')
        item = ListItem.objects.create(list=outra, product=self.item_arroz.product, quantity=1)
        resposta = self.repetir(self.compra.id, {'itens': [item.id]})
        self.assertEqual(resposta.status_code, 400)

    def test_lista_de_outra_pessoa_da_404(self):
        outro = User.objects.create_user(
            email='outro-repetir@exemplo.test', password='senha-de-teste-123', first_name='Outro',
        )
        self.client.force_authenticate(outro)
        resposta = self.repetir(self.compra.id)
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(List.objects.filter(owner=outro).count(), 0)

    def test_exige_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.repetir(self.compra.id).status_code, 401)
