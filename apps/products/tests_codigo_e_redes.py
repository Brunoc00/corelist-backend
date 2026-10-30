from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Product
from .services.redes import rede_da_loja

CODIGO = '7891000100103'  # válido
SUGESTAO = {
    'name': 'Leite Moça',
    'brand': 'Nestlé',
    'package_size': '395 g',
    'image_url': 'https://images.openfoodfacts.org/leite.jpg',
    'barcode': CODIGO,
    'data_source': 'openfoodfacts',
}


class RedesTests(TestCase):
    def test_reconhece_redes_pela_razao_social(self):
        self.assertEqual(rede_da_loja('SENDAS DISTRIBUIDORA S/A'), ('Assaí Atacadista', 'assai'))
        self.assertEqual(rede_da_loja('Atacadão S.A.'), ('Atacadão', 'atacadao'))
        self.assertEqual(rede_da_loja('DB SUPERMERCADOS LTDA'), ('DB Supermercados', 'db'))

    def test_loja_desconhecida_fica_sem_rede(self):
        self.assertEqual(rede_da_loja('A C RODRIGUES DA SILVA - EPP'), (None, None))


@patch('apps.products.views.buscar_por_codigo')
class CompletarPeloCodigoTests(TestCase):
    def setUp(self):
        usuario = User.objects.create_user(
            email='foto@exemplo.test', password='senha-de-teste-123', first_name='Foto',
        )
        self.client = APIClient()
        self.client.force_authenticate(usuario)
        self.produto = Product.objects.create(name='LT COND MOCA 395G', data_source='nfce')

    def completar(self, codigo=CODIGO, produto=None):
        produto = produto or self.produto
        return self.client.post(
            f'/api/products/{produto.id}/codigo-de-barras/', {'barcode': codigo}, format='json',
        )

    def test_pessoa_comum_completa_foto_marca_e_embalagem(self, buscar):
        buscar.return_value = SUGESTAO

        resposta = self.completar()

        self.assertEqual(resposta.status_code, 200)
        self.produto.refresh_from_db()
        self.assertEqual(self.produto.barcode, CODIGO)
        self.assertEqual(self.produto.image_url, SUGESTAO['image_url'])
        self.assertEqual(self.produto.brand, 'Nestlé')
        self.assertEqual(self.produto.package_size, '395 g')
        self.assertEqual(self.produto.name, 'LT COND MOCA 395G')  # o nome não muda

    def test_nao_sobrescreve_o_que_ja_existe(self, buscar):
        buscar.return_value = SUGESTAO
        self.produto.brand = 'Moça'
        self.produto.save()

        self.completar()

        self.produto.refresh_from_db()
        self.assertEqual(self.produto.brand, 'Moça')

    def test_codigo_sem_cadastro_aberto_ainda_e_guardado(self, buscar):
        buscar.return_value = None

        resposta = self.completar()

        self.assertFalse(resposta.data['encontrado'])
        self.produto.refresh_from_db()
        self.assertEqual(self.produto.barcode, CODIGO)
        self.assertEqual(self.produto.image_url, '')

    def test_codigo_de_outro_produto_da_409(self, buscar):
        Product.objects.create(name='Leite Moça', barcode=CODIGO)

        resposta = self.completar()

        self.assertEqual(resposta.status_code, 409)
        self.assertIn('Leite Moça', resposta.data['detail'])
        buscar.assert_not_called()

    def test_produto_que_ja_tem_outro_codigo_da_409(self, buscar):
        self.produto.barcode = '7896006711155'
        self.produto.save()

        self.assertEqual(self.completar().status_code, 409)

    def test_codigo_invalido_da_400(self, buscar):
        self.assertEqual(self.completar('123').status_code, 400)

    def test_exige_login(self, buscar):
        resposta = APIClient().post(
            f'/api/products/{self.produto.id}/codigo-de-barras/', {'barcode': CODIGO},
        )
        self.assertEqual(resposta.status_code, 401)
