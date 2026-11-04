from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.lists.models import List, ListItem
from apps.products.models import ObservedPrice, Product, ProdutoDoUsuario, Store

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
        # E nem vê o produto na tela dela.
        self.assertNotIn(produto.id, [p['id'] for p in self.client.get('/api/products/').data])

    def test_nao_apaga_se_outra_pessoa_tambem_tem(self):
        produto = self.cadastrar()
        ProdutoDoUsuario.objects.create(usuario=self.outro, produto=produto)
        dados = self.client.get('/api/products/').data
        self.assertFalse(next(p for p in dados if p['id'] == produto.id)['pode_remover'])
        self.assertEqual(self.client.delete(f'/api/products/{produto.id}/').status_code, 409)
        self.assertTrue(Product.objects.filter(pk=produto.id).exists())

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


class EsconderProdutoTests(TestCase):
    """Lixeira nos produtos que a pessoa não pode apagar: somem só para ela."""

    def setUp(self):
        self.pessoa = User.objects.create_user(email='pessoa@exemplo.test', password='senha-de-teste-123')
        self.outra = User.objects.create_user(email='outra@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.pessoa)
        self.antigo = Product.objects.create(name='Arroz antigo')
        for usuario in (self.pessoa, self.outra):
            ProdutoDoUsuario.objects.create(usuario=usuario, produto=self.antigo)

    def ids(self):
        return [p['id'] for p in self.client.get('/api/products/').data]

    def test_esconde_so_para_quem_pediu(self):
        resposta = self.client.post(f'/api/products/{self.antigo.id}/esconder/')
        self.assertEqual(resposta.status_code, 204)
        self.assertNotIn(self.antigo.id, self.ids())
        self.assertTrue(Product.objects.filter(pk=self.antigo.id).exists())
        self.client.force_authenticate(self.outra)
        self.assertIn(self.antigo.id, self.ids())

    def test_esconder_duas_vezes_nao_da_erro(self):
        self.client.post(f'/api/products/{self.antigo.id}/esconder/')
        self.assertEqual(self.client.post(f'/api/products/{self.antigo.id}/esconder/').status_code, 204)

    def test_produto_em_lista_pode_ser_escondido_e_continua_na_lista(self):
        lista = List.objects.create(name='Mercado', owner=self.pessoa)
        item = ListItem.objects.create(list=lista, product=self.antigo)
        self.client.post(f'/api/products/{self.antigo.id}/esconder/')
        self.assertNotIn(self.antigo.id, self.ids())
        self.assertTrue(ListItem.objects.filter(pk=item.pk).exists())

    def test_escondido_some_da_busca_da_lista(self):
        self.client.post(f'/api/products/{self.antigo.id}/esconder/')
        nomes = [p['name'] for p in self.client.get('/api/lists/produtos/', {'q': 'arroz'}).data]
        self.assertNotIn('Arroz antigo', nomes)

    def test_produto_inexistente_da_404(self):
        self.assertEqual(self.client.post('/api/products/999999/esconder/').status_code, 404)

    def test_exige_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(f'/api/products/{self.antigo.id}/esconder/').status_code, 401)


class MeusProdutosTests(TestCase):
    """Cada pessoa tem o próprio catálogo: conta nova começa vazia."""

    def setUp(self):
        self.eu = User.objects.create_user(email='eu@exemplo.test', password='senha-de-teste-123')
        self.outro = User.objects.create_user(email='ele@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.eu)

    def ids(self):
        return [p['id'] for p in self.client.get('/api/products/').data]

    def test_conta_nova_comeca_sem_produtos(self):
        Product.objects.create(name='Produto antigo de exemplo')
        self.client.force_authenticate(self.outro)
        self.client.post('/api/products/', {'name': 'Do outro'}, format='json')
        self.client.force_authenticate(self.eu)
        self.assertEqual(self.ids(), [])

    def test_cadastrado_aparece_so_para_quem_cadastrou(self):
        resposta = self.client.post('/api/products/', {'name': 'Meu arroz'}, format='json')
        self.assertIn(resposta.data['id'], self.ids())
        self.client.force_authenticate(self.outro)
        self.assertNotIn(resposta.data['id'], self.ids())

    def test_usar_numa_lista_poe_nos_meus_produtos(self):
        produto = Product.objects.create(name='Leite')
        lista = List.objects.create(name='Mercado', owner=self.eu)
        ListItem.objects.create(list=lista, product=produto)
        self.assertIn(produto.id, self.ids())

    def test_adicionar_produto_que_ja_existe(self):
        produto = Product.objects.create(name='Café', barcode='7891234567895')
        resposta = self.client.post(f'/api/products/{produto.id}/adicionar/')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['id'], produto.id)
        self.assertIn(produto.id, self.ids())
        # Duas vezes não duplica.
        self.client.post(f'/api/products/{produto.id}/adicionar/')
        self.assertEqual(ProdutoDoUsuario.objects.filter(usuario=self.eu, produto=produto).count(), 1)

    def test_lixeira_tira_dos_meus_produtos_e_volta_se_usar_de_novo(self):
        produto = Product.objects.create(name='Feijão')
        lista = List.objects.create(name='Mercado', owner=self.eu)
        ListItem.objects.create(list=lista, product=produto)
        self.client.post(f'/api/products/{produto.id}/esconder/')
        self.assertNotIn(produto.id, self.ids())
        ListItem.objects.create(list=lista, product=produto, quantity=2)
        self.assertIn(produto.id, self.ids())
