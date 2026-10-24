import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.products.models import Product
from apps.products.services.openfoodfacts import (
    buscar_por_codigo,
    codigo_valido,
)

User = get_user_model()

# Códigos com dígito verificador correto.
EAN13 = '7891000100103'
EAN13_OUTRO = '7891000100110'
EAN8 = '96385074'

URLOPEN = 'apps.products.services.openfoodfacts.urlopen'


def resposta_off(dados):
    """Simula o retorno de urlopen (usado como context manager)."""
    resposta = MagicMock()
    resposta.__enter__.return_value.read.return_value = (
        json.dumps(dados).encode('utf-8')
    )
    return resposta


PRODUTO_OFF = {
    'status': 1,
    'product': {
        'product_name_pt': 'Café Torrado e Moído',
        'product_name': 'Roasted Coffee',
        'brands': 'Marca Teste, Outra Marca',
        'quantity': '500 g',
        'image_front_small_url': 'https://images.openfoodfacts.org/cafe.jpg',
    },
}


class CodigoValidoTests(TestCase):

    def test_accepts_valid_codes(self):
        self.assertTrue(codigo_valido(EAN13))
        self.assertTrue(codigo_valido(EAN8))
        self.assertTrue(codigo_valido(f'  {EAN13}  '))

    def test_rejects_wrong_check_digit(self):
        self.assertFalse(codigo_valido('7891000100104'))

    def test_rejects_letters_and_wrong_length(self):
        self.assertFalse(codigo_valido('78910001001A3'))
        self.assertFalse(codigo_valido('12345'))
        self.assertFalse(codigo_valido(''))
        self.assertFalse(codigo_valido(None))


class BuscarPorCodigoTests(TestCase):

    def setUp(self):
        cache.clear()

    @override_settings(OPEN_FOOD_FACTS_USER_AGENT='CompraFacilTeste/1.0')
    @patch(URLOPEN)
    def test_maps_fields_and_sends_user_agent(self, mock_urlopen):
        mock_urlopen.return_value = resposta_off(PRODUTO_OFF)

        resultado = buscar_por_codigo(EAN13)

        self.assertEqual(
            resultado,
            {
                'name': 'Café Torrado e Moído',
                'brand': 'Marca Teste',
                'package_size': '500 g',
                'image_url': 'https://images.openfoodfacts.org/cafe.jpg',
            },
        )

        request = mock_urlopen.call_args.args[0]

        self.assertIn(EAN13, request.full_url)
        self.assertEqual(
            request.get_header('User-agent'),
            'CompraFacilTeste/1.0',
        )
        self.assertEqual(mock_urlopen.call_args.kwargs['timeout'], 5)

    @patch(URLOPEN)
    def test_ignores_non_https_image(self, mock_urlopen):
        dados = json.loads(json.dumps(PRODUTO_OFF))
        dados['product']['image_front_small_url'] = 'http://inseguro.com/x.jpg'
        mock_urlopen.return_value = resposta_off(dados)

        self.assertEqual(buscar_por_codigo(EAN13)['image_url'], '')

    @patch(URLOPEN)
    def test_returns_none_when_product_not_found(self, mock_urlopen):
        mock_urlopen.return_value = resposta_off({'status': 0})

        self.assertIsNone(buscar_por_codigo(EAN13))

    @patch(URLOPEN)
    def test_returns_none_on_http_404(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            'url', 404, 'Not Found', None, None,
        )

        self.assertIsNone(buscar_por_codigo(EAN13))

    @patch(URLOPEN)
    def test_returns_none_when_service_is_down(self, mock_urlopen):
        mock_urlopen.side_effect = URLError('sem rede')

        self.assertIsNone(buscar_por_codigo(EAN13))

    @patch(URLOPEN)
    def test_returns_none_without_name(self, mock_urlopen):
        mock_urlopen.return_value = resposta_off({
            'status': 1,
            'product': {'brands': 'Só marca'},
        })

        self.assertIsNone(buscar_por_codigo(EAN13))

    @patch(URLOPEN)
    def test_invalid_code_does_not_call_service(self, mock_urlopen):
        self.assertIsNone(buscar_por_codigo('123'))
        mock_urlopen.assert_not_called()

    @patch(URLOPEN)
    def test_caches_found_and_not_found(self, mock_urlopen):
        mock_urlopen.side_effect = [
            resposta_off(PRODUTO_OFF),
            resposta_off({'status': 0}),
        ]

        buscar_por_codigo(EAN13)
        buscar_por_codigo(EAN13)
        buscar_por_codigo(EAN13_OUTRO)
        buscar_por_codigo(EAN13_OUTRO)

        self.assertEqual(mock_urlopen.call_count, 2)


class ProductLookupTests(TestCase):

    def setUp(self):
        cache.clear()

        self.client = APIClient()
        self.user = User.objects.create_user(
            email='test@example.com',
            password='StrongPassword123!',
        )
        self.client.force_authenticate(user=self.user)

    def test_lookup_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            '/api/products/lookup/', {'barcode': EAN13},
        )

        self.assertEqual(response.status_code, 401)

    def test_lookup_rejects_invalid_code(self):
        response = self.client.get(
            '/api/products/lookup/', {'barcode': '123'},
        )

        self.assertEqual(response.status_code, 400)

    @patch('apps.products.views.buscar_por_codigo')
    def test_lookup_returns_catalog_product_without_calling_service(
            self,
            mock_buscar,
    ):
        produto = Product.objects.create(name='Arroz', barcode=EAN13)

        response = self.client.get(
            '/api/products/lookup/', {'barcode': EAN13},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['origem'], 'catalogo')
        self.assertEqual(response.data['produto']['id'], produto.id)
        mock_buscar.assert_not_called()

    @patch('apps.products.views.buscar_por_codigo')
    def test_lookup_returns_suggestion_without_saving(self, mock_buscar):
        mock_buscar.return_value = {
            'name': 'Café Torrado e Moído',
            'brand': 'Marca Teste',
            'package_size': '500 g',
            'image_url': 'https://images.openfoodfacts.org/cafe.jpg',
        }

        response = self.client.get(
            '/api/products/lookup/', {'barcode': EAN13},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['origem'], 'openfoodfacts')
        self.assertEqual(response.data['sugestao']['barcode'], EAN13)
        self.assertEqual(
            response.data['sugestao']['data_source'], 'openfoodfacts',
        )
        self.assertEqual(response.data['sugestao']['brand'], 'Marca Teste')
        self.assertFalse(Product.objects.filter(barcode=EAN13).exists())

    @patch('apps.products.views.buscar_por_codigo', return_value=None)
    def test_lookup_returns_404_when_not_found(self, mock_buscar):
        response = self.client.get(
            '/api/products/lookup/', {'barcode': EAN13},
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn('detail', response.data)


class ProductBarcodeFieldsTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='test@example.com',
            password='StrongPassword123!',
        )
        self.client.force_authenticate(user=self.user)

    def test_user_can_create_product_from_suggestion(self):
        response = self.client.post(
            '/api/products/',
            {
                'name': 'Café Torrado e Moído',
                'brand': 'Marca Teste',
                'package_size': '500 g',
                'barcode': EAN13,
                'image_url': 'https://images.openfoodfacts.org/cafe.jpg',
                'data_source': 'openfoodfacts',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)

        produto = Product.objects.get(barcode=EAN13)

        self.assertEqual(produto.brand, 'Marca Teste')
        self.assertEqual(produto.package_size, '500 g')
        self.assertEqual(produto.data_source, 'openfoodfacts')

    def test_duplicate_barcode_is_rejected(self):
        Product.objects.create(name='Arroz', barcode=EAN13)

        response = self.client.post(
            '/api/products/',
            {'name': 'Outro arroz', 'barcode': EAN13},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('barcode', response.data)

    def test_invalid_barcode_is_rejected(self):
        response = self.client.post(
            '/api/products/',
            {'name': 'Arroz', 'barcode': '7891000100104'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('barcode', response.data)

    def test_many_products_without_barcode_are_allowed(self):
        for nome in ['Arroz', 'Feijão']:
            response = self.client.post(
                '/api/products/', {'name': nome}, format='json',
            )
            self.assertEqual(response.status_code, 201)

        self.assertEqual(Product.objects.filter(barcode='').count(), 2)

    def test_updating_product_keeps_its_own_barcode(self):
        self.user.is_staff = True
        self.user.save()

        produto = Product.objects.create(name='Arroz', barcode=EAN13)

        response = self.client.patch(
            f'/api/products/{produto.id}/',
            {'name': 'Arroz tipo 1', 'barcode': EAN13},
            format='json',
        )

        self.assertEqual(response.status_code, 200)

    def test_http_image_is_rejected(self):
        response = self.client.post(
            '/api/products/',
            {'name': 'Arroz', 'image_url': 'http://inseguro.com/x.jpg'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('image_url', response.data)
