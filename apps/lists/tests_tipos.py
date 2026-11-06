import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product, Store

from .services import enriquecer
from .tests_precos import _preco

SEM_CHAVES = {'GEMINI_API_KEY': '', 'ANTHROPIC_API_KEY': '', 'GROQ_API_KEY': '', 'TAVILY_API_KEY': ''}


def _resposta_http(dados):
    return BytesIO(json.dumps(dados).encode())


class TraducaoDosNomesTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_tipo_sem_ia_pelas_abreviacoes(self):
        self.assertEqual(enriquecer.tipo_pelo_nome('IOG VIG 800G BATIDO'), 'Iogurte')
        self.assertEqual(enriquecer.tipo_pelo_nome('DA MONANGE 150ML'), 'Desodorante')
        self.assertEqual(enriquecer.tipo_pelo_nome('ARROZ TP1 CAMIL 5KG'), 'Arroz')
        self.assertEqual(enriquecer.tipo_pelo_nome('Feijão carioca'), 'Feijão')

    @patch.dict('os.environ', {**SEM_CHAVES, 'GROQ_API_KEY': 'chave-de-teste'})
    @override_settings(ASSISTENTE_IA='groq')
    @patch('apps.lists.services.enriquecer._cliente_groq')
    def test_ia_traduz_e_nao_apaga_o_que_ja_havia(self, cliente):
        arroz = Product.objects.create(name='ARROZ TP1 CAMIL 5KG', data_source=Product.SOURCE_NFCE)
        cafe = Product.objects.create(name='CAFE PILAO 500G', brand='Pilão Original')
        cliente.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps({'produtos': [
                {'i': 0, 'nome': 'Arroz Camil Tipo 1 5 kg', 'tipo': 'Arroz', 'marca': 'Camil', 'tamanho': '5 kg'},
                {'i': 1, 'nome': 'Café Pilão 500 g', 'tipo': 'Café', 'marca': 'Pilão', 'tamanho': 'grande'},
            ]})),
        )])

        enriquecer.traduzir_produtos([arroz, cafe])

        arroz.refresh_from_db()
        cafe.refresh_from_db()
        self.assertEqual((arroz.tipo, arroz.nome_legivel, arroz.brand, arroz.package_size),
                         ('Arroz', 'Arroz Camil Tipo 1 5 kg', 'Camil', '5 kg'))
        self.assertEqual(cafe.brand, 'Pilão Original')  # já tinha: fica
        self.assertEqual(cafe.package_size, '')  # "grande" não é tamanho
        self.assertIsNotNone(arroz.enriquecido_em)

    @patch.dict('os.environ', SEM_CHAVES)
    def test_sem_ia_usa_a_regra(self):
        iogurte = Product.objects.create(name='IOG VIG 800G BATIDO')
        enriquecer.traduzir_produtos([iogurte])
        iogurte.refresh_from_db()
        self.assertEqual(iogurte.tipo, 'Iogurte')
        self.assertEqual(iogurte.nome_legivel, '')


class FotoPeloNomeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.arroz = Product.objects.create(
            name='ARROZ TP1 CAMIL 5KG', tipo='Arroz', nome_legivel='Arroz Camil Tipo 1 5 kg',
            brand='Camil', package_size='5 kg',
        )

    def buscar(self, achados):
        with patch('apps.lists.services.enriquecer.urlopen', side_effect=lambda *a, **k: _resposta_http({'hits': achados})):
            return enriquecer.buscar_foto(self.arroz, esperar=False)

    def test_acha_a_foto_do_mesmo_produto(self):
        achou = self.buscar([
            {'code': '7896006711155', 'product_name': 'Arroz Tipo 1 Camil 5kg', 'brands': ['CAMIL'],
             'quantity': '5kg', 'image_front_url': 'https://images.openfoodfacts.org/arroz.jpg',
             'countries_tags': ['en:brazil']},
        ])
        self.arroz.refresh_from_db()
        self.assertTrue(achou)
        self.assertEqual(self.arroz.image_url, 'https://images.openfoodfacts.org/arroz.jpg')
        self.assertEqual(self.arroz.foto_fonte, 'openfoodfacts')
        self.assertEqual(self.arroz.barcode, '7896006711155')

    def test_nao_usa_outra_marca_outro_pais_nem_outra_forma(self):
        achou = self.buscar([
            {'product_name': 'Arroz Tipo 1 Tio João 5kg', 'brands': ['Tio João'], 'quantity': '5kg',
             'image_front_url': 'https://x/1.jpg', 'countries_tags': ['en:brazil']},
            {'product_name': 'Arroz Camil 5kg', 'brands': ['Camil'], 'quantity': '5kg',
             'image_front_url': 'https://x/2.jpg', 'countries_tags': ['en:france']},
            {'product_name': 'Arroz Camil em pó 5kg', 'brands': ['Camil'], 'quantity': '5kg',
             'image_front_url': 'https://x/3.jpg', 'countries_tags': ['en:brazil']},
        ])
        self.arroz.refresh_from_db()
        self.assertFalse(achou)
        self.assertEqual(self.arroz.image_url, '')
        self.assertIsNotNone(self.arroz.foto_buscada_em)

    def test_base_fora_do_ar_tenta_de_novo_depois(self):
        from urllib.error import URLError
        with patch('apps.lists.services.enriquecer.urlopen', side_effect=URLError('fora do ar')):
            self.assertFalse(enriquecer.buscar_foto(self.arroz, esperar=False))
        self.arroz.refresh_from_db()
        self.assertIsNone(self.arroz.foto_buscada_em)


class MenorPrecoPorTipoTests(TestCase):
    def setUp(self):
        cache.clear()
        usuario = User.objects.create_user(email='tipos@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(usuario)
        self.assai = Store.objects.create(cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A')
        self.db = Store.objects.create(cnpj='04565289000171', name='DB SUPERMERCADOS')
        # O mesmo arroz com o nome de cada loja, e outro arroz (outro tamanho).
        self.camil_assai = Product.objects.create(
            name='ARROZ TP1 CAMIL 5KG', tipo='Arroz', nome_legivel='Arroz Camil Tipo 1 5 kg',
            brand='Camil', package_size='5 kg',
        )
        self.camil_db = Product.objects.create(
            name='ARROZ CAMIL T1 5KG', tipo='Arroz', nome_legivel='Arroz Camil Tipo 1 5 kg',
            brand='Camil', package_size='5 kg', image_url='https://x/camil.jpg', foto_fonte='openfoodfacts',
        )
        self.tio_joao = Product.objects.create(
            name='ARROZ T JOAO 1KG', tipo='Arroz', nome_legivel='Arroz Tio João Tipo 1 1 kg',
            brand='Tio João', package_size='1 kg',
        )
        self.cafe = Product.objects.create(name='CAFE PILAO 500G', tipo='Café', brand='Pilão', package_size='500 g')
        _preco(self.camil_assai, self.assai, '24.90')
        _preco(self.camil_db, self.db, '26.50')
        _preco(self.tio_joao, self.db, '6.49')
        _preco(self.cafe, self.assai, '16.90')

    def test_lista_os_tipos(self):
        tipos = self.client.get('/api/lists/precos/tipos/').data['tipos']
        arroz = next(t for t in tipos if t['tipo'] == 'Arroz')
        self.assertEqual((arroz['opcoes'], arroz['lojas']), (2, 2))
        self.assertEqual(arroz['a_partir_de']['preco'], '6.49')
        self.assertEqual(arroz['image_url'], 'https://x/camil.jpg')
        self.assertEqual(tipos[0]['tipo'], 'Arroz')  # mais opções primeiro
        filtrados = self.client.get('/api/lists/precos/tipos/', {'q': 'caf'}).data['tipos']
        self.assertEqual([t['tipo'] for t in filtrados], ['Café'])

    def test_opcoes_do_tipo_juntam_o_mesmo_produto_e_comparam_por_kg(self):
        opcoes = self.client.get('/api/lists/precos/', {'tipo': 'arroz'}).data['produtos']
        self.assertEqual(len(opcoes), 2)
        camil = next(o for o in opcoes if o['produto']['brand'] == 'Camil')
        self.assertEqual(camil['produtos_juntados'], 2)
        self.assertEqual([l['preco'] for l in camil['lojas']], ['24.90', '26.50'])
        self.assertEqual(camil['produto']['image_url'], 'https://x/camil.jpg')
        self.assertEqual(camil['produto']['name'], 'Arroz Camil Tipo 1 5 kg')
        self.assertEqual(camil['preco_por'], {'valor': '4.98', 'unidade': 'kg'})
        # Ordem pelo preço por kg: Camil (R$ 4,98/kg) antes do Tio João (R$ 6,49/kg).
        self.assertEqual([o['produto']['brand'] for o in opcoes], ['Camil', 'Tio João'])


class EnriquecerDepoisDaNotaTests(TestCase):
    @override_settings(ENRIQUECER_PRODUTOS=True)
    @patch('apps.lists.services.enriquecer.threading.Thread')
    def test_roda_depois_de_salvar(self, thread):
        produto = Product.objects.create(name='ARROZ TP1 CAMIL 5KG')
        with self.captureOnCommitCallbacks(execute=True):
            enriquecer.enriquecer_em_segundo_plano([produto.id])
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()

    @override_settings(ENRIQUECER_PRODUTOS=False)
    @patch('apps.lists.services.enriquecer.threading.Thread')
    def test_desligado_nao_roda(self, thread):
        with self.captureOnCommitCallbacks(execute=True):
            enriquecer.enriquecer_em_segundo_plano([1])
        thread.assert_not_called()
