import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product, Store
from apps.products.services.geocodificar import distancia_km, geocodificar_loja, tentativas

from .tests_precos import _preco

# Teatro Amazonas, centro de Manaus.
CENTRO = (-3.1303, -60.0234)


class LojasPertoDeMimTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(email='perto@exemplo.test', password='senha-de-teste-123')
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)
        # ~1,6 km e ~11 km do centro; a terceira ainda sem coordenadas.
        self.perto = Store.objects.create(
            cnpj='11111111000111', name='MERCADO PERTO', address='Rua A, 1, Centro, Manaus, AM',
            latitude=-3.1180, longitude=-60.0150,
        )
        self.longe = Store.objects.create(
            cnpj='22222222000122', name='MERCADO LONGE', address='Rua B, 2, Tarumã, Manaus, AM',
            latitude=-3.0300, longitude=-60.0300,
        )
        self.sem_coordenada = Store.objects.create(
            cnpj='33333333000133', name='MERCADO SEM MAPA', address='Rua C, 3, Manaus, AM',
        )
        self.arroz = Product.objects.create(name='Arroz tipo 1')
        _preco(self.arroz, self.perto, '25.90')
        _preco(self.arroz, self.longe, '21.90')
        _preco(self.arroz, self.sem_coordenada, '20.90')

    def buscar(self, **params):
        with patch('apps.lists.services.precos.geocodificar_loja') as geocodificar:
            resposta = self.client.get('/api/lists/precos/', {'q': 'arroz', **params})
        self.geocodificar = geocodificar
        self.assertEqual(resposta.status_code, 200)
        return resposta.data

    def test_sem_localizacao_continua_como_antes(self):
        dados = self.buscar()
        self.assertEqual(len(dados['produtos'][0]['lojas']), 3)
        self.assertNotIn('distancia_km', dados['produtos'][0]['lojas'][0]['loja'])
        self.geocodificar.assert_not_called()

    def test_com_localizacao_mostra_a_distancia_de_cada_loja(self):
        dados = self.buscar(lat=CENTRO[0], lng=CENTRO[1])
        distancias = {l['loja']['nome']: l['loja']['distancia_km'] for l in dados['produtos'][0]['lojas']}
        self.assertAlmostEqual(distancias['MERCADO PERTO'], 1.6, delta=0.3)
        self.assertGreater(distancias['MERCADO LONGE'], 10)
        self.assertIsNone(distancias['MERCADO SEM MAPA'])
        # A loja sem coordenadas é consultada no mapa nesta busca.
        self.geocodificar.assert_called_once_with(self.sem_coordenada)

    def test_raio_deixa_so_as_lojas_perto(self):
        dados = self.buscar(lat=CENTRO[0], lng=CENTRO[1], raio=5)
        self.assertEqual(dados['raio_km'], 5)
        resumo = dados['produtos'][0]
        self.assertEqual([l['loja']['nome'] for l in resumo['lojas']], ['MERCADO PERTO'])
        self.assertEqual(resumo['mais_barato_agora']['loja']['nome'], 'MERCADO PERTO')

    def test_raio_sem_loja_perto_nao_mostra_o_produto(self):
        self.perto.latitude, self.perto.longitude = -2.9, -60.0
        self.perto.save()
        dados = self.buscar(lat=CENTRO[0], lng=CENTRO[1], raio=2)
        self.assertEqual(dados['produtos'], [])

    def test_raio_invalido_e_ignorado(self):
        dados = self.buscar(lat=CENTRO[0], lng=CENTRO[1], raio=7)
        self.assertIsNone(dados['raio_km'])
        self.assertEqual(len(dados['produtos'][0]['lojas']), 3)

    def test_localizacao_invalida_e_ignorada(self):
        dados = self.buscar(lat='abc', lng=CENTRO[1], raio=5)
        self.assertIsNone(dados['raio_km'])
        self.assertNotIn('distancia_km', dados['produtos'][0]['lojas'][0]['loja'])


class GeocodificarTests(TestCase):
    def resposta(self, dados):
        mock = MagicMock()
        mock.__enter__.return_value.read.return_value = json.dumps(dados).encode()
        return mock

    def test_preenche_coordenadas_e_marca_a_tentativa(self):
        loja = Store.objects.create(cnpj='44444444000144', name='X', address='Av. Y, 10, Centro, Manaus, AM')
        with patch('apps.products.services.geocodificar.urlopen',
                   return_value=self.resposta([{'lat': '-3.1', 'lon': '-60.0'}])):
            self.assertEqual(geocodificar_loja(loja), (-3.1, -60.0))
        loja.refresh_from_db()
        self.assertEqual((loja.latitude, loja.longitude), (-3.1, -60.0))
        self.assertIsNotNone(loja.geocoded_at)

    def test_sem_resultado_marca_a_tentativa_sem_coordenadas(self):
        loja = Store.objects.create(cnpj='55555555000155', name='X', address='Rua Z, 5, Manaus, AM')
        with patch('apps.products.services.geocodificar.urlopen', return_value=self.resposta([])), \
             patch('apps.products.services.geocodificar.time.sleep'):
            self.assertIsNone(geocodificar_loja(loja))
        loja.refresh_from_db()
        self.assertIsNone(loja.latitude)
        self.assertIsNotNone(loja.geocoded_at)

    def test_tenta_sem_o_numero(self):
        self.assertEqual(
            tentativas('Avenida Rodrigo Otavio, 2595, Crespo, Manaus, AM'),
            ['Avenida Rodrigo Otavio, 2595, Crespo, Manaus, AM, Brasil',
             'Avenida Rodrigo Otavio, Crespo, Manaus, AM, Brasil'],
        )

    def test_distancia(self):
        self.assertAlmostEqual(distancia_km(CENTRO, CENTRO), 0)
        self.assertAlmostEqual(distancia_km((0, 0), (0, 1)), 111.2, delta=0.2)
