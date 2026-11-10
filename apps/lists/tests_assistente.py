import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx2
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import User
from apps.products.models import Product, Store

from .models import List, ListItem
from .services.assistente import termo_da_pergunta
from .services.ferramentas_compras import buscar_promocoes, mercados_perto
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

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    def test_sem_chave_responde_com_o_mais_barato_e_outras_opcoes(self):
        resposta = self.perguntar('Onde o arroz está mais barato?')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['fonte'], 'sem_ia')
        self.assertIn('R$ 24,90 no Assaí Atacadista', resposta.data['resposta'])
        self.assertIn('Outras opções', resposta.data['resposta'])
        self.assertIn('R$ 26,50', resposta.data['resposta'])

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    def test_sem_chave_produto_sem_preco(self):
        resposta = self.perguntar('onde tem picanha mais barata?')
        self.assertIn('Ainda não tenho preço de "picanha"', resposta.data['resposta'])

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.precos.geocodificar_loja')
    def test_sem_chave_com_localizacao_mostra_distancia(self, _geocodificar):
        resposta = self.perguntar('arroz', lat=-3.130, lng=-60.023)
        self.assertIn('km)', resposta.data['resposta'])

    def test_termo_da_pergunta(self):
        self.assertEqual(termo_da_pergunta('Onde o ARROZ está mais barato hoje?'), 'arroz')
        self.assertEqual(termo_da_pergunta('qual o menor preço do leite integral'), 'leite integral')

    # --- Com o Claude (simulado) ---

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
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

        self.assertEqual(resposta.data['resposta'], 'O arroz está mais barato no Assaí.')
        self.assertEqual(resposta.data['fonte'], 'claude')
        self.assertEqual(resposta.data['fontes'], [])
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

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    def test_recusa_do_claude_cai_nas_frases_prontas(self, cliente):
        cliente.return_value.beta.messages.create.return_value = _resposta([], 'refusal')
        self.assertEqual(self.perguntar('arroz').data['fonte'], 'sem_ia')

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    def test_erro_da_api_cai_nas_frases_prontas(self, cliente):
        pedido = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
        cliente.return_value.beta.messages.create.side_effect = anthropic.APIConnectionError(request=pedido)
        resposta = self.perguntar('arroz')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['fonte'], 'sem_ia')

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'chave-de-teste', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
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

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': '', 'GEMINI_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    def test_limite_de_perguntas(self):
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'assistente': '2/hour'}):
            self.assertEqual(self.perguntar('arroz').status_code, 200)
            self.assertEqual(self.perguntar('arroz').status_code, 200)
            self.assertEqual(self.perguntar('arroz').status_code, 429)


@override_settings(ASSISTENTE_INTERNET=False)
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

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
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

        self.assertEqual(resposta.data['resposta'], 'Arroz mais barato: R$ 24,90 no Assaí.')
        self.assertEqual(resposta.data['fonte'], 'gemini')
        self.assertEqual(vistos['model'], 'gemini-3.8-flash')
        self.assertEqual(vistos['contents'][-1]['role'], 'user')
        self.assertEqual(vistos['dados']['produtos'][0]['lojas'][0]['preco'], '24.90')
        self.assertEqual(vistos['dados']['produtos'][0]['lojas'][0]['loja'], 'Assaí Atacadista')

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': 'chave-de-teste', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_com_as_duas_chaves_usa_o_gemini_primeiro(self, gemini, claude):
        gemini.return_value.models.generate_content.return_value = SimpleNamespace(text='pelo Gemini')
        self.assertEqual(self.perguntar().data['fonte'], 'gemini')
        claude.return_value.beta.messages.create.assert_not_called()

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': 'chave-de-teste', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @override_settings(ASSISTENTE_IA='claude')
    @patch('apps.lists.services.assistente._cliente')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_assistente_ia_claude_inverte_a_ordem(self, gemini, claude):
        claude.return_value.beta.messages.create.return_value = _resposta([_texto('pelo Claude')])
        self.assertEqual(self.perguntar().data['fonte'], 'claude')
        gemini.return_value.models.generate_content.assert_not_called()

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': 'chave-de-teste', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_se_o_gemini_falha_tenta_o_claude(self, gemini, claude):
        gemini.return_value.models.generate_content.side_effect = RuntimeError('cota esgotada')
        claude.return_value.beta.messages.create.return_value = _resposta([_texto('pelo Claude')])
        dados = self.perguntar().data
        self.assertEqual((dados['resposta'], dados['fonte']), ('pelo Claude', 'claude'))

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_gemini_sem_texto_cai_nas_frases_prontas(self, gemini):
        gemini.return_value.models.generate_content.return_value = SimpleNamespace(text=None)
        resposta = self.perguntar()
        self.assertEqual(resposta.data['fonte'], 'sem_ia')
        self.assertIn('R$ 24,90', resposta.data['resposta'])


@override_settings(ASSISTENTE_INTERNET=True)
class AssistenteNaInternetTests(TestCase):
    """Gemini com Google Maps e busca no Google (o padrão com GEMINI_API_KEY)."""

    def setUp(self):
        cache.clear()
        usuario = User.objects.create_user(email='internet@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(usuario)
        loja = Store.objects.create(cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A')
        _preco(Product.objects.create(name='Arroz Camil tipo 1'), loja, '24.90')

    def perguntar(self, **extra):
        dados = {'mensagens': [{'papel': 'usuario', 'texto': 'Onde tem arroz mais barato perto de mim?'}], **extra}
        return self.client.post(URL, dados, format='json')

    def resposta_com_fontes(self, texto):
        metadados = SimpleNamespace(
            grounding_chunks=[
                SimpleNamespace(maps=SimpleNamespace(title='Assaí Atacadista Crespo', uri='https://maps.google.com/?cid=1'), web=None),
                SimpleNamespace(maps=None, web=SimpleNamespace(title='Encarte da semana', uri='https://exemplo.test/encarte')),
            ],
            web_search_queries=['arroz promoção Manaus'],
        )
        return SimpleNamespace(text=texto, candidates=[SimpleNamespace(grounding_metadata=metadados)])

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_usa_maps_e_busca_com_a_localizacao_e_devolve_as_fontes(self, cliente):
        gerar = cliente.return_value.models.generate_content
        gerar.return_value = self.resposta_com_fontes('O Assaí do Crespo fica a 3 km.')

        resposta = self.perguntar(lat=-3.130, lng=-60.023)

        self.assertEqual(resposta.data['resposta'], 'O Assaí do Crespo fica a 3 km.')
        self.assertEqual(resposta.data['fonte'], 'gemini')
        self.assertEqual(
            [f['tipo'] for f in resposta.data['fontes']], ['maps', 'web', 'busca'],
        )
        self.assertIn('google.com/search?q=arroz', resposta.data['fontes'][2]['url'])

        config = gerar.call_args.kwargs['config']
        self.assertIsNotNone(config.tools[0].google_maps)
        self.assertIsNotNone(config.tools[1].google_search)
        local = config.tool_config.retrieval_config.lat_lng
        self.assertEqual((local.latitude, local.longitude), (-3.13, -60.023))
        # Os preços das notas vão como extra no contexto.
        self.assertIn('24.90', config.system_instruction)

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_sem_localizacao_nao_manda_coordenadas(self, cliente):
        gerar = cliente.return_value.models.generate_content
        gerar.return_value = self.resposta_com_fontes('Veja os atacarejos de Manaus.')
        self.perguntar()
        self.assertIsNone(gerar.call_args.kwargs['config'].tool_config)

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'chave-de-teste', 'ANTHROPIC_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''})
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_plano_gratuito_sem_maps_cai_no_modo_das_notas(self, cliente):
        def gerar(model, contents, config):
            if config.tools and not callable(config.tools[0]):
                raise RuntimeError('Grounding indisponível no plano gratuito')
            return SimpleNamespace(text='Pelas notas: R$ 24,90 no Assaí.')

        cliente.return_value.models.generate_content.side_effect = gerar
        resposta = self.perguntar()
        self.assertEqual(resposta.data['resposta'], 'Pelas notas: R$ 24,90 no Assaí.')
        self.assertEqual(resposta.data['fontes'], [])


SEM_CHAVES = {'GEMINI_API_KEY': '', 'ANTHROPIC_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''}


def _chamada(id_, nome, argumentos):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=nome, arguments=json.dumps(argumentos)))


def _resposta_groq(texto=None, chamadas=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=texto, tool_calls=chamadas))])


class AssistenteGroqTests(TestCase):
    def setUp(self):
        cache.clear()
        usuario = User.objects.create_user(email='groq@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(usuario)
        loja = Store.objects.create(cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A')
        _preco(Product.objects.create(name='Arroz Camil tipo 1'), loja, '24.90')

    def perguntar(self, **extra):
        dados = {'mensagens': [{'papel': 'usuario', 'texto': 'Onde tem arroz mais barato perto de mim?'}], **extra}
        return self.client.post(URL, dados, format='json')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GROQ_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente.mercados_perto')
    @patch('apps.lists.services.assistente._cliente_groq')
    def test_groq_usa_as_ferramentas_e_devolve_as_fontes(self, cliente, mercados):
        def perto(origem, raio_km, fontes, lojas):
            fontes.append({'tipo': 'maps', 'titulo': 'Atacadão', 'url': 'https://maps.exemplo/1'})
            lojas.append({'nome': 'Atacadão', 'marca': 'Atacadão', 'icone': 'atacadao',
                          'distancia_km': 1.2, 'url': 'https://maps.exemplo/1'})
            return {'raio_km': raio_km, 'mercados': [{'nome': 'Atacadão', 'distancia_km': 1.2}]}

        mercados.side_effect = perto
        criar = cliente.return_value.chat.completions.create
        criar.side_effect = [
            _resposta_groq(chamadas=[
                _chamada('c1', 'mercados_perto', {'raio_km': 3}),
                _chamada('c2', 'buscar_precos', {'produto': 'arroz', 'dias': 30}),
            ]),
            _resposta_groq('Atacadão a 1,2 km. Numa nota, o arroz saiu por R$ 24,90 no Assaí.'),
        ]

        resposta = self.perguntar(lat=-3.130, lng=-60.023)

        self.assertEqual(resposta.data['fonte'], 'groq')
        self.assertIn('Atacadão', resposta.data['resposta'])
        self.assertEqual(resposta.data['fontes'], [{'tipo': 'maps', 'titulo': 'Atacadão', 'url': 'https://maps.exemplo/1'}])
        self.assertEqual(mercados.call_args.args[0], (-3.13, -60.023))
        mensagens = criar.call_args_list[1].kwargs['messages']
        resultado_notas = json.loads(mensagens[-1]['content'])
        self.assertEqual(resultado_notas['produtos'][0]['lojas'][0]['preco'], '24.90')
        self.assertEqual(criar.call_args_list[0].kwargs['model'], 'openai/gpt-oss-120b')
        # Cartões: a loja do mapa (com logo) e a loja da nota.
        nomes = [l['marca'] or l['nome'] for l in resposta.data['lojas']]
        self.assertIn('Atacadão', nomes)
        self.assertIn('Assaí Atacadista', nomes)
        atacadao = next(l for l in resposta.data['lojas'] if l['nome'] == 'Atacadão')
        self.assertEqual(atacadao['icone'], 'atacadao')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GEMINI_API_KEY': 'chave-de-teste', 'GROQ_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente_groq')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_quando_o_gemini_falha_o_groq_responde(self, gemini, cliente_groq):
        gemini.return_value.models.generate_content.side_effect = RuntimeError('cota do dia esgotada')
        cliente_groq.return_value.chat.completions.create.return_value = _resposta_groq('pelo Groq')
        resposta = self.perguntar()
        self.assertEqual(resposta.data['fonte'], 'groq')
        self.assertEqual(resposta.data['resposta'], 'pelo Groq')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GEMINI_API_KEY': 'chave-de-teste', 'GROQ_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente_groq')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_ia_que_falhou_fica_de_lado_por_um_tempo(self, gemini, cliente_groq):
        gemini.return_value.models.generate_content.side_effect = RuntimeError('muita procura')
        cliente_groq.return_value.chat.completions.create.return_value = _resposta_groq('pelo Groq')
        self.perguntar()
        self.assertEqual(self.perguntar().data['fonte'], 'groq')
        self.assertEqual(gemini.return_value.models.generate_content.call_count, 1)

    @patch.dict('os.environ', {**SEM_CHAVES, 'GEMINI_API_KEY': 'chave-de-teste', 'GROQ_API_KEY': 'chave-de-teste'})
    @override_settings(ASSISTENTE_IA='groq')
    @patch('apps.lists.services.assistente._cliente_groq')
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_assistente_ia_groq_poe_o_groq_primeiro(self, gemini, cliente_groq):
        cliente_groq.return_value.chat.completions.create.return_value = _resposta_groq('pelo Groq')
        self.assertEqual(self.perguntar().data['fonte'], 'groq')
        gemini.return_value.models.generate_content.assert_not_called()

    @patch.dict('os.environ', {**SEM_CHAVES, 'GEMINI_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente_gemini')
    def test_gemini_usa_so_as_ferramentas_das_notas(self, gemini):
        gemini.return_value.models.generate_content.return_value = SimpleNamespace(text='ok')
        self.perguntar()
        config = gemini.return_value.models.generate_content.call_args.kwargs['config']
        self.assertEqual(
            [f.__name__ for f in config.tools],
            ['buscar_precos', 'mercados_perto', 'minhas_listas', 'meus_gastos', 'criar_lista'],
        )
        self.assertNotIn('buscar_promocoes', config.system_instruction)
        self.assertIn('Localização da pessoa: não informada', config.system_instruction)


class FerramentasDeComprasTests(TestCase):
    def setUp(self):
        cache.clear()

    def _nominatim(self, lugares):
        resposta = MagicMock()
        resposta.__enter__.return_value.read.return_value = json.dumps(lugares).encode()
        return resposta

    def test_mercados_perto_ordena_pela_distancia_e_guarda_em_cache(self):
        elementos = [
            {'name': 'Mercado Longe', 'lat': '-3.1400', 'lon': '-60.0234', 'address': {}},
            {'name': 'Atacadão', 'lat': '-3.1310', 'lon': '-60.0240',
             'address': {'road': 'Av. Torquato Tapajós', 'house_number': '10'}},
            {'name': 'Atacadão', 'lat': '-3.1310', 'lon': '-60.0240', 'address': {}},  # repetido: fora
            {'name': '', 'lat': '-3.13', 'lon': '-60.02'},  # sem nome: fora
            {'name': 'Mercado Fora do Raio', 'lat': '-3.20', 'lon': '-60.02'},  # ~7,7 km: fora
        ]
        fontes = []
        with patch('apps.lists.services.ferramentas_compras.urlopen', return_value=self._nominatim(elementos)) as abrir:
            dados = mercados_perto((-3.1303, -60.0234), 3, fontes)
            mercados_perto((-3.1303, -60.0234), 3, [])  # segunda vez: cache
        self.assertEqual(abrir.call_count, 1)
        self.assertEqual([m['nome'] for m in dados['mercados']], ['Atacadão', 'Mercado Longe'])
        self.assertEqual(dados['mercados'][0]['tipo'], 'atacarejo')
        self.assertEqual(dados['mercados'][0]['endereco'], 'Av. Torquato Tapajós 10')
        self.assertNotIn('_lat', dados['mercados'][0])
        self.assertEqual(fontes[0]['titulo'], 'Atacadão')
        self.assertIn('google.com/maps', fontes[0]['url'])

    def test_mercados_perto_so_da_cidade_do_app(self):
        elementos = [
            {'name': 'Supermercado DB', 'lat': '-3.1310', 'lon': '-60.0240', 'address': {'city': 'Manaus'}},
            {'name': 'Mercado de Iranduba', 'lat': '-3.1320', 'lon': '-60.0240', 'address': {'town': 'Iranduba'}},
            {'name': 'Mercado sem cidade', 'lat': '-3.1330', 'lon': '-60.0240', 'address': {}},
        ]
        with patch('apps.lists.services.ferramentas_compras.urlopen', return_value=self._nominatim(elementos)):
            dados = mercados_perto((-3.1303, -60.0234), 3, [])
        self.assertEqual([m['nome'] for m in dados['mercados']], ['Supermercado DB', 'Mercado sem cidade'])

    def test_mercados_perto_sem_localizacao(self):
        self.assertIn('erro', mercados_perto(None))

    def test_mercados_perto_com_erro_no_mapa(self):
        with patch('apps.lists.services.ferramentas_compras.urlopen', side_effect=OSError('fora do ar')):
            self.assertIn('erro', mercados_perto((-3.13, -60.02)))

    @patch.dict('os.environ', {'TAVILY_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.ferramentas_compras._cliente_tavily')
    def test_buscar_promocoes(self, cliente):
        cliente.return_value.search.return_value = {'results': [
            {'title': 'Encarte Atacadão Manaus', 'url': 'https://exemplo.test/encarte', 'content': 'Arroz 5 kg R$ 22,90'},
        ]}
        fontes = []
        dados = buscar_promocoes('arroz', fontes)
        buscar_promocoes('arroz', [])  # cache: não busca de novo
        self.assertEqual(cliente.return_value.search.call_count, 1)
        self.assertIn('Manaus', cliente.return_value.search.call_args.args[0])
        self.assertEqual(dados['resultados'][0]['trecho'], 'Arroz 5 kg R$ 22,90')
        self.assertEqual(fontes, [{'tipo': 'web', 'titulo': 'Encarte Atacadão Manaus', 'url': 'https://exemplo.test/encarte'}])

    @patch.dict('os.environ', {'TAVILY_API_KEY': ''})
    def test_buscar_promocoes_sem_chave(self):
        self.assertIn('erro', buscar_promocoes('arroz'))


class CartoesDaRespostaTests(TestCase):
    """Fotos dos produtos e logos das lojas abaixo da resposta."""

    def setUp(self):
        cache.clear()
        usuario = User.objects.create_user(email='cartoes@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(usuario)
        self.assai = Store.objects.create(
            cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A',
            address='Avenida Rodrigo Otavio, 2595, Crespo, Manaus, AM',
        )

    @patch.dict('os.environ', SEM_CHAVES)
    def test_produto_com_preco_e_foto_e_loja_com_logo_e_rota(self):
        arroz = Product.objects.create(
            name='Arroz Camil tipo 1', package_size='5 kg', image_url='https://img.exemplo/arroz.jpg',
        )
        _preco(arroz, self.assai, '24.90')
        dados = self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'arroz'}]}, format='json').data

        self.assertEqual(dados['produtos'][0]['nome'], 'Arroz Camil tipo 1')
        self.assertEqual(dados['produtos'][0]['imagem_url'], 'https://img.exemplo/arroz.jpg')
        self.assertEqual(dados['produtos'][0]['preco'], '24.90')
        self.assertEqual(dados['produtos'][0]['loja'], 'Assaí Atacadista')
        loja = dados['lojas'][0]
        self.assertEqual((loja['marca'], loja['icone']), ('Assaí Atacadista', 'assai'))
        self.assertIn('google.com/maps/dir', loja['url'])

    @patch.dict('os.environ', SEM_CHAVES)
    def test_sem_foto_no_produto_usa_um_parecido_do_catalogo(self):
        _preco(Product.objects.create(name='ARROZ T1 5KG'), self.assai, '23.90')
        Product.objects.create(name='Arroz Tio João tipo 1', image_url='https://img.exemplo/tio-joao.jpg')
        Product.objects.create(name='Feijão carioca', image_url='https://img.exemplo/feijao.jpg')
        dados = self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'arroz'}]}, format='json').data
        imagens = [p['imagem_url'] for p in dados['produtos']]
        self.assertIn('https://img.exemplo/tio-joao.jpg', imagens)
        self.assertNotIn('https://img.exemplo/feijao.jpg', imagens)

    @patch.dict('os.environ', SEM_CHAVES)
    def test_pergunta_sem_produto_nao_traz_cartoes(self):
        dados = self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'onde tem mais barato?'}]}, format='json').data
        self.assertEqual((dados['produtos'], dados['lojas']), ([], []))


class RedePeloNomeTests(TestCase):
    def test_nomes_do_mapa(self):
        from apps.products.services.redes import rede_pelo_nome
        self.assertEqual(rede_pelo_nome('Supermercado DB')[1], 'db')
        self.assertEqual(rede_pelo_nome('Super DB Cachoeirinha')[1], 'db')
        self.assertEqual(rede_pelo_nome('Atacadão')[1], 'atacadao')
        self.assertEqual(rede_pelo_nome('Assaí Atacadista Crespo')[1], 'assai')
        self.assertEqual(rede_pelo_nome('Carrefour Bairro')[1], 'carrefour')
        self.assertEqual(rede_pelo_nome('Supermercado Fuji'), (None, None))
        self.assertEqual(rede_pelo_nome('Debora Mercadinho'), (None, None))


class MercadosPertoNosCartoesTests(TestCase):
    def setUp(self):
        cache.clear()
        usuario = User.objects.create_user(email='perto-cartao@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(usuario)

    @patch.dict('os.environ', SEM_CHAVES)
    @patch('apps.lists.services.assistente.mercados_perto')
    def test_com_localizacao_os_cartoes_trazem_os_mercados_perto(self, mercados):
        def perto(origem, raio_km, fontes, lojas):
            lojas.append({'nome': 'Supermercado DB', 'marca': None, 'icone': 'db', 'distancia_km': 0.4, 'url': 'https://m/1'})
        mercados.side_effect = perto
        dados = self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'arroz'}], 'lat': -3.13, 'lng': -60.02}, format='json').data
        self.assertEqual(dados['lojas'][0]['nome'], 'Supermercado DB')

    @patch.dict('os.environ', SEM_CHAVES)
    @patch('apps.lists.services.assistente.mercados_perto')
    def test_sem_localizacao_nao_busca_mercados(self, mercados):
        self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'arroz'}]}, format='json')
        mercados.assert_not_called()


class AssistenteMaisCertoTests(TestCase):
    """Busca tolerante, histórico das notas, listas e gastos da pessoa."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user(email='mais@exemplo.test', password='senha-de-teste-123')
        self.outro = User.objects.create_user(email='outro-chat@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)
        self.assai = Store.objects.create(cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A')
        self.db = Store.objects.create(cnpj='04565289000171', name='DB SUPERMERCADOS')
        self.cafe = Product.objects.create(name='CAFE PILAO 500G')
        self.arroz = Product.objects.create(name='Arroz Camil tipo 1', package_size='5 kg')

    def test_busca_flexivel_aceita_palavras_a_mais(self):
        from .services.precos import buscar_precos, buscar_precos_flexivel
        _preco(self.cafe, self.assai, '18.90')
        self.assertEqual(buscar_precos('cafe pilao tradicional 250g'), [])
        achados = buscar_precos_flexivel('cafe pilao tradicional 250g')
        self.assertEqual(achados[0]['produto']['name'], 'CAFE PILAO 500G')

    def test_historico_e_loja_que_costuma_ser_mais_barata(self):
        from .services.assistente import resultado_da_busca
        _preco(self.arroz, self.assai, '24.90')
        _preco(self.arroz, self.assai, '22.90', dias_atras=20)
        _preco(self.arroz, self.db, '23.50')
        produto = resultado_da_busca('arroz')['produtos'][0]
        assai = next(l for l in produto['lojas'] if l['loja'] == 'Assaí Atacadista')
        self.assertEqual(assai['historico_90_dias'], {'menor': '22.90', 'media': '23.90', 'notas': 2})
        # O DB tem o menor preço agora, mas só uma nota: quem costuma ser mais barato é o Assaí.
        self.assertEqual(produto['lojas'][0]['loja'], 'DB Supermercados')
        self.assertEqual(produto['loja_que_costuma_ser_mais_barata']['loja'], 'Assaí Atacadista')

    def test_minhas_listas_mostra_so_as_da_pessoa(self):
        from .services.assistente import executar, nova_coleta
        _preco(self.arroz, self.assai, '24.90')
        minha = List.objects.create(owner=self.usuario, name='Mês', budget=Decimal('100'))
        ListItem.objects.create(list=minha, product=self.arroz, quantity=2, price=Decimal('25'))
        List.objects.create(owner=self.outro, name='Lista do outro')

        dados = executar('minhas_listas', {}, None, nova_coleta(), self.usuario)

        self.assertEqual([l['nome'] for l in dados['listas']], ['Mês'])
        lista = dados['listas'][0]
        self.assertEqual((lista['total_planejado'], lista['saldo']), ('50.00', '50.00'))
        self.assertEqual(lista['melhor_loja_para_a_lista']['loja'], 'Assaí Atacadista')
        self.assertEqual(lista['itens'][0]['mais_barato_agora']['preco'], '24.90')

    def test_meus_gastos(self):
        from .services.assistente import executar, nova_coleta
        agora = timezone.now()
        dentro = List.objects.create(
            owner=self.usuario, name='Feira', budget=Decimal('30'), is_completed=True, completed_at=agora,
        )
        ListItem.objects.create(list=dentro, product=self.arroz, quantity=2, price=Decimal('10'))
        ListItem.objects.create(list=dentro, product=self.cafe, quantity=1, price=Decimal('5'))
        estourou = List.objects.create(
            owner=self.usuario, name='Mês', budget=Decimal('10'), is_completed=True, completed_at=agora,
        )
        ListItem.objects.create(list=estourou, product=self.arroz, quantity=1, price=Decimal('20'))
        alheia = List.objects.create(owner=self.outro, name='Dele', is_completed=True, completed_at=agora)
        ListItem.objects.create(list=alheia, product=self.arroz, quantity=9, price=Decimal('99'))

        dados = executar('meus_gastos', {'meses': 1}, None, nova_coleta(), self.usuario)

        self.assertEqual(dados['total_gasto'], '45.00')
        self.assertEqual(dados['compras_concluidas'], 2)
        self.assertEqual((dados['compras_com_orcamento'], dados['compras_dentro_do_orcamento']), (2, 1))
        self.assertEqual(dados['mais_comprados'][0]['produto'], 'Arroz Camil tipo 1')

    def test_ferramentas_da_pessoa_sem_usuario(self):
        from .services.assistente import executar, nova_coleta
        self.assertIn('erro', executar('minhas_listas', {}, None, nova_coleta()))

    def test_perguntas_sobre_lista_e_gastos_nao_viram_produto(self):
        self.assertEqual(termo_da_pergunta('Em qual mercado minha lista sai mais barata?'), '')
        self.assertEqual(termo_da_pergunta('Quanto gastei este mês?'), '')
        self.assertEqual(termo_da_pergunta('qual mercado vende café mais em conta'), 'cafe')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GROQ_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente_groq')
    def test_pre_busca_vai_no_prompt_e_busca_uma_vez_so(self, cliente):
        from .services import assistente
        _preco(self.arroz, self.assai, '24.90')
        criar = cliente.return_value.chat.completions.create
        criar.return_value = _resposta_groq('No Assaí, R$ 24,90.')

        with patch.object(assistente, 'buscar_precos_flexivel', wraps=assistente.buscar_precos_flexivel) as busca:
            resposta = self.client.post(
                URL, {'mensagens': [{'papel': 'usuario', 'texto': 'onde tem arroz mais barato?'}]}, format='json',
            )

        self.assertEqual(resposta.data['fonte'], 'groq')
        sistema = criar.call_args.kwargs['messages'][0]['content']
        self.assertIn('Dados já buscados pelo app', sistema)
        self.assertIn('24.90', sistema)
        self.assertEqual(criar.call_count, 1)
        self.assertEqual(busca.call_count, 1)  # os cartões reaproveitam a pré-busca
        self.assertEqual(resposta.data['produtos'][0]['nome'], 'Arroz Camil tipo 1')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GROQ_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.assistente._cliente_groq')
    def test_groq_chama_minhas_listas_com_o_usuario_da_pergunta(self, cliente):
        List.objects.create(owner=self.usuario, name='Churrasco')
        List.objects.create(owner=self.outro, name='Lista do outro')
        criar = cliente.return_value.chat.completions.create
        criar.side_effect = [
            _resposta_groq(chamadas=[_chamada('c1', 'minhas_listas', {})]),
            _resposta_groq('Sua lista Churrasco está vazia.'),
        ]
        self.client.post(
            URL, {'mensagens': [{'papel': 'usuario', 'texto': 'Em qual mercado minha lista sai mais barata?'}]},
            format='json',
        )
        resultado = json.loads(criar.call_args_list[1].kwargs['messages'][-1]['content'])
        self.assertEqual([l['nome'] for l in resultado['listas']], ['Churrasco'])
        # Pergunta sem produto: nada de pré-busca no prompt.
        self.assertNotIn('Dados já buscados pelo app', criar.call_args_list[0].kwargs['messages'][0]['content'])

    @patch.dict('os.environ', SEM_CHAVES)
    def test_sem_ia_responde_sobre_gastos_e_listas(self):
        compra = List.objects.create(
            owner=self.usuario, name='Feira', budget=Decimal('30'), is_completed=True, completed_at=timezone.now(),
        )
        ListItem.objects.create(list=compra, product=self.arroz, quantity=2, price=Decimal('10'))
        gastos = self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'Quanto gastei este mês?'}]}, format='json')
        self.assertIn('R$ 20,00 em 1 compra', gastos.data['resposta'])
        self.assertIn('1 de 1 ficaram dentro do orçamento', gastos.data['resposta'])

        lista = List.objects.create(owner=self.usuario, name='Churrasco', budget=Decimal('100'))
        ListItem.objects.create(list=lista, product=self.arroz, quantity=1, price=Decimal('25'))
        resposta = self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'quanto vai dar minha lista?'}]}, format='json')
        self.assertIn('Na lista Churrasco, o total planejado é R$ 25,00 e sobram R$ 75,00', resposta.data['resposta'])


class PromocoesDaInternetTests(TestCase):
    """Por padrão o assistente usa só as notas; a internet é opcional."""

    def setUp(self):
        cache.clear()
        usuario = User.objects.create_user(email='promo@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(usuario)

    def perguntar(self):
        return self.client.post(URL, {'mensagens': [{'papel': 'usuario', 'texto': 'onde tem arroz mais barato?'}]}, format='json')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GROQ_API_KEY': 'chave-de-teste', 'TAVILY_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.ferramentas_compras._cliente_tavily')
    @patch('apps.lists.services.assistente._cliente_groq')
    def test_sem_internet_por_padrao(self, cliente, tavily):
        criar = cliente.return_value.chat.completions.create
        criar.return_value = _resposta_groq('Ainda não tenho nota com arroz.')
        self.perguntar()
        tavily.return_value.search.assert_not_called()
        nomes = [f['function']['name'] for f in criar.call_args.kwargs['tools']]
        self.assertNotIn('buscar_promocoes', nomes)
        self.assertNotIn('promocoes_na_internet', criar.call_args.kwargs['messages'][0]['content'])

    @override_settings(ASSISTENTE_PROMOCOES=True)
    @patch.dict('os.environ', {**SEM_CHAVES, 'GROQ_API_KEY': 'chave-de-teste', 'TAVILY_API_KEY': 'chave-de-teste'})
    @patch('apps.lists.services.ferramentas_compras._cliente_tavily')
    @patch('apps.lists.services.assistente._cliente_groq')
    def test_com_a_configuracao_ligada_busca_na_internet(self, cliente, tavily):
        tavily.return_value.search.return_value = {'results': []}
        criar = cliente.return_value.chat.completions.create
        criar.return_value = _resposta_groq('ok')
        self.perguntar()
        tavily.return_value.search.assert_called_once()
        nomes = [f['function']['name'] for f in criar.call_args.kwargs['tools']]
        self.assertIn('buscar_promocoes', nomes)
