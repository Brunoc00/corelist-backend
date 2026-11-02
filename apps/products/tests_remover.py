from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.lists.models import List, ListItem
from apps.products.models import ObservedPrice, Product, Store

User = get_user_model()


class RemoverProdutoCadastradoPorEnganoTests(TestCase):
    """Quem cadastrou o produto pode removê-lo, se ele não estiver em lista
    nem tiver preço de nota fiscal."""

    def setUp(self):
        self.dono = User.objects.create_user(email='dono@exemplo.test', password='senha-de-teste-123')
        self.outro = User.objects.create_user(email='outro@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.dono)

    def cadastrar(self, nome='Arroz errado'):
        resposta = self.client.post('/api/products/', {'name': nome}, format='json')
        self.assertEqual(resposta.status_code, 201)
        return Product.objects.get(pk=resposta.data['id'])

    def test_cadastro_guarda_quem_criou_e_pode_remover(self):
        resposta = self.client.post('/api/products/', {'name': 'Feijão'}, format='json')
        self.assertTrue(resposta.data['pode_remover'])
        self.assertEqual(Product.objects.get(pk=resposta.data['id']).created_by, self.dono)

    def test_quem_cadastrou_remove(self):
        produto = self.cadastrar()
        resposta = self.client.delete(f'/api/products/{produto.id}/')
        self.assertEqual(resposta.status_code, 204)
        self.assertFalse(Product.objects.filter(pk=produto.id).exists())

    def test_outra_pessoa_nao_remove(self):
        produto = self.cadastrar()
        self.client.force_authenticate(self.outro)
        self.assertEqual(self.client.delete(f'/api/products/{produto.id}/').status_code, 403)
        self.assertTrue(Product.objects.filter(pk=produto.id).exists())
        lista = self.client.get('/api/products/').data
        self.assertFalse(next(p for p in lista if p['id'] == produto.id)['pode_remover'])

    def test_produto_em_lista_nao_sai(self):
        produto = self.cadastrar()
        lista = List.objects.create(name='Mercado', owner=self.outro)
        ListItem.objects.create(list=lista, product=produto)
        self.assertEqual(self.client.delete(f'/api/products/{produto.id}/').status_code, 409)
        dados = self.client.get('/api/products/').data
        self.assertFalse(next(p for p in dados if p['id'] == produto.id)['pode_remover'])

    def test_produto_com_preco_de_nota_nao_sai(self):
        produto = self.cadastrar()
        loja = Store.objects.create(cnpj='12345678000199', name='Mercado Teste')
        ObservedPrice.objects.create(
            product=produto, store=loja, store_code='1', price=Decimal('9.90'),
            observed_at=timezone.now(), nfce_key='1' * 44,
        )
        resposta = self.client.delete(f'/api/products/{produto.id}/')
        self.assertEqual(resposta.status_code, 409)
        self.assertIn('notas fiscais', resposta.data['detail'])
        self.assertTrue(Product.objects.filter(pk=produto.id).exists())

    def test_produto_antigo_sem_dono_so_admin_remove(self):
        produto = Product.objects.create(name='Produto de exemplo')
        self.assertEqual(self.client.delete(f'/api/products/{produto.id}/').status_code, 403)

    def test_quem_cadastrou_continua_sem_poder_editar(self):
        produto = self.cadastrar()
        resposta = self.client.patch(f'/api/products/{produto.id}/', {'name': 'Outro'}, format='json')
        self.assertEqual(resposta.status_code, 403)
