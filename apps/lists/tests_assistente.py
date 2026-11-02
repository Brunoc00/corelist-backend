import json
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx2
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import User
from apps.products.models import Product, Store

from .services.assistente import termo_da_pergunta
from .tests_precos import _preco

URL = '/api/lists/assistente/'


def _texto(texto):
    return SimpleNamespace(type='text', text=texto)


def _resposta(conteudo, stop_reason='end_turn'):
    return SimpleNamespace(content=conteudo, stop_reason=stop_reason)


class AssistenteTests(TestCase):
    def setUp(self):
        cache.clear()  # contador do limite de perguntas
        self.usuario = User.objects.create_user(email='chat@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)
        self.assai = Store.objects.create(
            cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A',
            address='Avenida Rodrigo Otavio, 2595, Crespo, Manaus, AM',
            latitude=-3.1302, longitude=-59.9873,
        )
        self.db = Store.objects.create(cnpj='04565289000171', name='DB SUPERMERCADOS')
        self.arroz = Product.objects.create(name='Arroz Camil tipo 1', package_size='5 kg')
        _preco(self.arroz, self.assai, '24.90')
        _preco(self.arroz, self.db, '26.50')

    def perguntar(self, texto, **extra):
        dados = {'mensagens': [{'papel': 'usuario', 'texto': texto}], **extra}
        return self.client.post(URL, dados, format='json')

    # --- Sem chave: frases prontas com os dados reais ---

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': ''})
    def test_sem_chave_responde_com_o_mais_barato_e_outras_opcoes(self):
        resposta = self.perguntar('Onde o arroz está mais barato?')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['fonte'], 'sem_ia')
        self.assertIn('R$ 24,90 no Assaí Atacadista', resposta.data['resposta'])
        self.assertIn('Outras opções', resposta.data['resposta'])
        self.assertIn('R$ 26,50', resposta.data['resposta'])

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': ''})
    def test_sem_chave_produto_sem_preco(self):
        resposta = self.perguntar('onde tem picanha mais barata?')
        self.assertIn('Ainda não tenho preço de "picanha"', resposta.data['resposta'])

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': ''})
    @patch('apps.lists.services.precos.geocodificar_loja')
    def test_sem_chave_com_localizacao_mostra_distancia(self, _geocodificar):
        resposta = self.perguntar('arroz', lat=-3.130, lng=-60.023)
        self.assertIn('km)', resposta.data['resposta'])

    def test_termo_da_pergunta(self):
        self.assertEqual(termo_da_pergunta('Onde o ARROZ está mais barato hoje?'), 'arroz')
        self.assertEqual(termo_da_pergunta('qual o menor preço do leite integral'), 'leite integral')

    # --- Com o Claude (simulado) ---

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    def test_claude_chama_a_ferramenta_e_responde(self, cliente):
        uso = SimpleNamespace(
            type='tool_use', id='t1', name='buscar_precos', input={'produto': 'arroz', 'dias': 30},
        )
        criar = cliente.return_value.beta.messages.create
        criar.side_effect = [
            _resposta([uso], 'tool_use'),
            _resposta([_texto('O arroz está mais barato no Assaí.')]),
        ]

        resposta = self.perguntar('Onde o arroz está mais barato?')

        self.assertEqual(resposta.data, {'resposta': 'O arroz está mais barato no Assaí.', 'fonte': 'claude'})
        self.assertEqual(criar.call_count, 2)
        primeira = criar.call_args_list[0].kwargs
        self.assertEqual(primeira['model'], 'claude-opus-5-5')
        self.assertEqual(primeira['fallbacks'], 'default')
        self.assertEqual(primeira['tools'][0]['name'], 'buscar_precos')
        # O resultado da ferramenta leva os preços reais do banco.
        resultado = criar.call_args_list[1].kwargs['messages'][-1]['content'][0]
        self.assertEqual(resultado['tool_use_id'], 't1')
        dados = json.loads(resultado['content'])
        self.assertEqual(dados['produtos'][0]['lojas'][0]['loja'], 'Assaí Atacadista')
        self.assertEqual(dados['produtos'][0]['lojas'][0]['preco'], '24.90')

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    def test_recusa_do_claude_cai_nas_frases_prontas(self, cliente):
        cliente.return_value.beta.messages.create.return_value = _resposta([], 'refusal')
        self.assertEqual(self.perguntar('arroz').data['fonte'], 'sem_ia')

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    def test_erro_da_api_cai_nas_frases_prontas(self, cliente):
        pedido = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
        cliente.return_value.beta.messages.create.side_effect = anthropic.APIConnectionError(request=pedido)
        resposta = self.perguntar('arroz')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['fonte'], 'sem_ia')

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    def test_historico_vai_alternado(self, cliente):
        cliente.return_value.beta.messages.create.return_value = _resposta([_texto('ok')])
        mensagens = [
            {'papel': 'assistente', 'texto': 'Olá! Posso ajudar?'},  # começa no assistente: sai
            {'papel': 'usuario', 'texto': 'arroz'},
            {'papel': 'assistente', 'texto': 'R$ 24,90 no Assaí.'},
            {'papel': 'usuario', 'texto': 'e o feijão?'},
        ]
        self.client.post(URL, {'mensagens': mensagens}, format='json')
        enviadas = cliente.return_value.beta.messages.create.call_args.kwargs['messages']
        self.assertEqual([m['role'] for m in enviadas], ['user', 'assistant', 'user'])

    # --- Validação, login e limite ---

    def test_sem_pergunta_da_400(self):
        self.assertEqual(self.client.post(URL, {'mensagens': []}, format='json').status_code, 400)
        self.assertEqual(self.client.post(URL, {}, format='json').status_code, 400)

    def test_exige_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.perguntar('arroz').status_code, 401)

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': ''})
    def test_limite_de_perguntas(self):
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'assistente': '2/hour'}):
            self.assertEqual(self.perguntar('arroz').status_code, 200)
            self.assertEqual(self.perguntar('arroz').status_code, 200)
            self.assertEqual(self.perguntar('arroz').status_code, 429)


class AssistenteGeminiTests(TestCase):
    def setUp(self):
        cache.clear()
        usuario = User.objects.create_user(email='gemini@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(usuario)
        loja = Store.objects.create(cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A')
        _preco(Product.objects.create(name='Arroz Camil tipo 1'), loja, '24.90')

    def perguntar(self, texto='Onde o arroz está mais barato?'):
        return self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': texto}]}, format='json')

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_gemini_usa_a_ferramenta_com_os_precos_reais(self, cliente):
        vistos = {}

        def gerar(model, contents, config):
            # A biblioteca chamaria a função sozinha; aqui o teste chama.
            vistos['dados'] = config.tools[0]('arroz', 30)
            vistos['model'] = model
            vistos['contents'] = contents
            return SimpleNamespace(text='Arroz mais barato: R$ 24,90 no Assaí.')

        cliente.return_value.models.generate_content.side_effect = gerar

        resposta = self.perguntar()

        self.assertEqual(resposta.data, {'resposta': 'Arroz mais barato: R$ 24,90 no Assaí.', 'fonte': 'gemini'})
        self.assertEqual(vistos['model'], 'gemini-3.8-flash')
        self.assertEqual(vistos['contents'][-1]['role'], 'user')
        self.assertEqual(vistos['dados']['produtos'][0]['lojas'][0]['preco'], '24.90')
        self.assertEqual(vistos['dados']['produtos'][0]['lojas'][0]['loja'], 'Assaí Atacadista')

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_com_as_duas_chaves_usa_o_gemini_primeiro(self, gemini, claude):
        gemini.return_value.models.generate_content.return_value = SimpleNamespace(text='pelo Gemini')
        self.assertEqual(self.perguntar().data['fonte'], 'gemini')
        claude.return_value.beta.messages.create.assert_not_called()

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': 'chave-de-teste'})
    @override_settings(ASSISTENTE_IA='claude')
    @patch('apps.lists.services.assistente._cliente')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_assistente_ia_claude_inverte_a_ordem(self, gemini, claude):
        claude.return_value.beta.messages.create.return_value = _resposta([_texto('pelo Claude')])
        self.assertEqual(self.perguntar().data['fonte'], 'claude')
        gemini.return_value.models.generate_content.assert_not_called()

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_se_o_gemini_falha_tenta_o_claude(self, gemini, claude):
        gemini.return_value.models.generate_content.side_effect = RuntimeError('cota esgotada')
        claude.return_value.beta.messages.create.return_value = _resposta([_texto('pelo Claude')])
        self.assertEqual(self.perguntar().data, {'resposta': 'pelo Claude', 'fonte': 'claude'})

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_gemini_sem_texto_cai_nas_frases_prontas(self, gemini):
        gemini.return_value.models.generate_content.return_value = SimpleNamespace(text=None)
        resposta = self.perguntar()
        self.assertEqual(resposta.data['fonte'], 'sem_ia')
        self.assertIn('R$ 24,90', resposta.data['resposta'])
