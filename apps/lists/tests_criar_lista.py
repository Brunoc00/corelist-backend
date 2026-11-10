from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product, ProdutoDoUsuario

from .models import List, ListItem
from .services.assistente import executar, nova_coleta
from .services.criar_lista import criar_lista, e_pedido_de_lista, itens_do_pedido
from .tests_assistente import SEM_CHAVES, URL, _chamada, _resposta_groq


class CriarListaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user(email='lista@exemplo.test', password='senha-de-teste-123')
        self.outro = User.objects.create_user(email='outro@exemplo.test', password='senha-de-teste-123')
        self.arroz = Product.objects.create(name='ARROZ TP1 CAMIL 5KG')
        antiga = List.objects.create(owner=self.usuario, name='Antiga', is_completed=True)
        ListItem.objects.create(list=antiga, product=self.arroz, quantity=1, price=Decimal('24.90'))

    def test_usa_os_produtos_da_pessoa_e_o_ultimo_preco(self):
        dados = criar_lista(self.usuario, 'Semana', [
            {'produto': 'arroz', 'quantidade': 2},
            {'produto': 'feijão carioca', 'quantidade': 1},
        ], 150)

        lista = List.objects.get(id=dados['lista_criada']['id'])
        self.assertEqual((lista.owner, lista.name, lista.budget, lista.is_completed), (self.usuario, 'Semana', Decimal('150.00'), False))
        itens = list(lista.items.order_by('id'))
        self.assertEqual(itens[0].product, self.arroz)
        self.assertEqual((itens[0].quantity, itens[0].price), (Decimal('2'), Decimal('24.90')))
        # Produto que a pessoa nunca usou: criado e posto em "Meus produtos".
        self.assertEqual(itens[1].product.name, 'Feijão carioca')
        self.assertIsNone(itens[1].price)
        self.assertTrue(ProdutoDoUsuario.objects.filter(usuario=self.usuario, produto=itens[1].product).exists())
        self.assertEqual(dados['lista_criada']['total_planejado'], '49.80')

    def test_nao_usa_produto_do_catalogo_de_outra_pessoa_por_semelhanca(self):
        cafe = Product.objects.create(name='CAFE PILAO 500G')
        ProdutoDoUsuario.objects.create(usuario=self.outro, produto=cafe)

        dados = criar_lista(self.usuario, '', [{'produto': 'café', 'quantidade': 1}])

        item = List.objects.get(id=dados['lista_criada']['id']).items.get()
        self.assertNotEqual(item.product, cafe)
        self.assertTrue(dados['lista_criada']['nome'].startswith('Lista de '))

    def test_abreviacao_da_nota_nao_vira_outro_produto(self):
        # "CER" (cereal) numa nota não pode virar a cerveja pedida.
        cereal = Product.objects.create(name='CER MAT ALCA FOODS CORN SUGAR 60G')
        antiga = List.objects.filter(owner=self.usuario).first()
        ListItem.objects.create(list=antiga, product=cereal, quantity=1, price=Decimal('2.39'))

        dados = criar_lista(self.usuario, 'Festa', [{'produto': 'cerveja', 'quantidade': 12}])

        item = List.objects.get(id=dados['lista_criada']['id']).items.get()
        self.assertEqual(item.product.name, 'Cerveja')
        self.assertIsNone(item.price)
        self.assertEqual(dados['lista_criada']['itens_sem_preco'], 1)

    def test_sem_itens_nao_cria(self):
        self.assertIn('erro', criar_lista(self.usuario, 'Vazia', []))
        self.assertFalse(List.objects.filter(name='Vazia').exists())

    def test_quantidade_invalida_vira_1_e_repetido_soma(self):
        dados = criar_lista(self.usuario, 'X', [
            {'produto': 'arroz', 'quantidade': -3},
            {'produto': 'arroz', 'quantidade': 'muito'},
        ])
        item = List.objects.get(id=dados['lista_criada']['id']).items.get()
        self.assertEqual(item.quantity, Decimal('2'))

    def test_mesma_lista_logo_em_seguida_nao_duplica(self):
        primeira = criar_lista(self.usuario, 'Semana', ['arroz'])
        segunda = criar_lista(self.usuario, 'Semana', ['arroz'])
        self.assertEqual(primeira['lista_criada']['id'], segunda['lista_criada']['id'])
        self.assertEqual(List.objects.filter(name='Semana').count(), 1)

    def test_executar_cria_uma_lista_por_resposta(self):
        coleta = nova_coleta()
        executar('criar_lista', {'nome': 'A', 'itens': [{'produto': 'arroz', 'quantidade': 1}], 'orcamento': None}, None, coleta, self.usuario)
        dados = executar('criar_lista', {'nome': 'B', 'itens': [{'produto': 'café', 'quantidade': 1}], 'orcamento': None}, None, coleta, self.usuario)

        self.assertEqual(dados['lista_criada']['nome'], 'A')
        self.assertFalse(List.objects.filter(name='B').exists())

    def test_sem_usuario_nao_cria(self):
        dados = executar('criar_lista', {'nome': 'A', 'itens': ['arroz'], 'orcamento': None}, None, nova_coleta(), None)
        self.assertIn('erro', dados)

    def test_entende_o_pedido_sem_ia(self):
        self.assertTrue(e_pedido_de_lista('Crie uma lista com arroz e feijão'))
        self.assertTrue(e_pedido_de_lista('monta minha lista do churrasco'))
        self.assertFalse(e_pedido_de_lista('Em qual mercado minha lista sai mais barata?'))
        self.assertEqual(
            itens_do_pedido('Cria uma lista com arroz, 2 feijão e 3x leite'),
            [
                {'produto': 'arroz', 'quantidade': 1},
                {'produto': 'feijão', 'quantidade': '2'},
                {'produto': 'leite', 'quantidade': '3'},
            ],
        )


class CriarListaPeloChatTests(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user(email='chat-lista@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

    def perguntar(self, texto):
        return self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': texto}]}, format='json')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GROQ_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente_groq')
    def test_groq_cria_a_lista_e_o_site_recebe_o_link(self, cliente):
        criar = cliente.return_value.chat.completions.create
        criar.side_effect = [
            _resposta_groq(chamadas=[_chamada('c1', 'criar_lista', {
                'nome': 'Churrasco',
                'itens': [{'produto': 'carvão', 'quantidade': 1}, {'produto': 'picanha', 'quantidade': 2}],
                'orcamento': 200,
            })]),
            _resposta_groq('Criei a lista Churrasco com carvão e picanha.'),
        ]

        resposta = self.perguntar('Monta uma lista pro churrasco de sábado, até 200 reais')

        self.assertEqual(resposta.status_code, 200)
        lista = List.objects.get(owner=self.usuario, name='Churrasco')
        self.assertEqual(lista.budget, Decimal('200.00'))
        self.assertEqual(lista.items.count(), 2)
        self.assertEqual(resposta.data['lista_criada']['id'], lista.id)
        nomes = [f['function']['name'] for f in criar.call_args_list[0].kwargs['tools']]
        self.assertIn('criar_lista', nomes)

    @patch.dict('os.environ', SEM_CHAVES)
    def test_sem_ia_cria_pela_frase(self):
        resposta = self.perguntar('Crie uma lista com arroz, feijão e café')

        lista = List.objects.get(owner=self.usuario)
        self.assertEqual(sorted(i.product.name for i in lista.items.all()), ['Arroz', 'Café', 'Feijão'])
        self.assertEqual(resposta.data['fonte'], 'sem_ia')
        self.assertEqual(resposta.data['lista_criada']['id'], lista.id)
        self.assertIn('Criei a lista', resposta.data['resposta'])

    @patch.dict('os.environ', SEM_CHAVES)
    def test_pergunta_comum_nao_cria_lista(self):
        resposta = self.perguntar('Onde tem arroz mais barato?')
        self.assertIsNone(resposta.data['lista_criada'])
        self.assertFalse(List.objects.filter(owner=self.usuario).exists())
