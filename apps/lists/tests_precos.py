from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import ObservedPrice, Product, Store

from .models import List, ListItem
from .tests_nfce import CHAVE, LINK, _resposta


def _preco(produto, loja, valor, dias_atras=0, medida='unit', chave=None):
    return ObservedPrice.objects.create(
        product=produto,
        store=loja,
        price=Decimal(valor),
        measure=medida,
        observed_at=timezone.now() - timedelta(days=dias_atras, minutes=5),
        nfce_key=chave or f'{dias_atras:02d}{loja.id:04d}{produto.id:06d}'.ljust(44, '0'),
    )


class PrecosDaCidadeTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            email='precos@exemplo.test', password='senha-de-teste-123', first_name='Preço',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

        self.sendas = Store.objects.create(
            cnpj='06057223044209', name='SENDAS DISTRIBUIDORA S/A',
            address='Avenida Rodrigo Otavio, 2595, Crespo, Manaus, AM',
        )
        self.atacadao = Store.objects.create(cnpj='75315333000109', name='ATACADAO S.A.')
        self.db = Store.objects.create(cnpj='04565289000171', name='DB SUPERMERCADOS')

        self.arroz = Product.objects.create(name='Arroz Camil tipo 1', package_size='5 kg')
        self.feijao = Product.objects.create(name='Feijão carioca', package_size='1 kg')
        self.tomate = Product.objects.create(name='Tomate')
        self.sem_preco = Product.objects.create(name='Arroz integral')

    def produto(self, produto, dias=None):
        params = {} if dias is None else {'dias': dias}
        return self.client.get(f'/api/lists/precos/produto/{produto.id}/', params).data

    def test_mais_barato_agora_ignora_preco_com_mais_de_um_dia(self):
        _preco(self.arroz, self.sendas, '24.90')
        _preco(self.arroz, self.atacadao, '26.49')
        _preco(self.arroz, self.db, '22.90', dias_atras=9)  # mais barato, mas velho

        dados = self.produto(self.arroz)

        self.assertEqual(dados['mais_barato_agora']['loja']['nome'], 'SENDAS DISTRIBUIDORA S/A')
        self.assertEqual(dados['mais_barato_agora']['preco'], '24.90')
        self.assertEqual(
            dados['mais_barato_agora']['loja']['endereco'],
            'Avenida Rodrigo Otavio, 2595, Crespo, Manaus, AM',
        )
        nomes = [linha['loja']['nome'] for linha in dados['lojas']]
        self.assertEqual(nomes, ['SENDAS DISTRIBUIDORA S/A', 'ATACADAO S.A.', 'DB SUPERMERCADOS'])
        self.assertTrue(dados['lojas'][2]['desatualizado'])
        self.assertFalse(dados['lojas'][0]['desatualizado'])

    def test_vale_o_ultimo_preco_de_cada_loja(self):
        _preco(self.arroz, self.sendas, '21.00', dias_atras=5)
        _preco(self.arroz, self.sendas, '24.90')

        dados = self.produto(self.arroz)

        self.assertEqual(len(dados['lojas']), 1)
        self.assertEqual(dados['lojas'][0]['preco'], '24.90')

    def test_menor_preco_depende_do_periodo(self):
        _preco(self.arroz, self.sendas, '24.90')
        _preco(self.arroz, self.db, '22.90', dias_atras=9)
        _preco(self.arroz, self.atacadao, '19.90', dias_atras=60)

        self.assertEqual(self.produto(self.arroz, 7)['menor_no_periodo']['preco'], '24.90')
        self.assertEqual(self.produto(self.arroz, 30)['menor_no_periodo']['preco'], '22.90')
        noventa = self.produto(self.arroz, 90)['menor_no_periodo']
        self.assertEqual(noventa['preco'], '19.90')
        self.assertEqual(noventa['loja']['nome'], 'ATACADAO S.A.')

    def test_periodo_invalido_usa_30_dias(self):
        resposta = self.client.get('/api/lists/precos/', {'q': 'arroz', 'dias': '365'})

        self.assertEqual(resposta.data['dias'], 30)

    def test_sem_preco_atual_nao_tem_mais_barato_agora(self):
        _preco(self.arroz, self.db, '22.90', dias_atras=3)

        dados = self.produto(self.arroz)

        self.assertIsNone(dados['mais_barato_agora'])
        self.assertEqual(len(dados['lojas']), 1)

    def test_busca_so_mostra_produtos_com_preco(self):
        _preco(self.arroz, self.sendas, '24.90')

        resposta = self.client.get('/api/lists/precos/', {'q': 'arroz'})

        nomes = [linha['produto']['name'] for linha in resposta.data['produtos']]
        self.assertEqual(nomes, ['Arroz Camil tipo 1'])

    def test_sem_texto_mostra_os_precos_mais_recentes(self):
        _preco(self.feijao, self.sendas, '8.99', dias_atras=2)
        _preco(self.arroz, self.sendas, '24.90')

        resposta = self.client.get('/api/lists/precos/')

        nomes = [linha['produto']['name'] for linha in resposta.data['produtos']]
        self.assertEqual(nomes, ['Arroz Camil tipo 1', 'Feijão carioca'])

    def test_ninguem_ve_quem_comprou(self):
        _preco(self.arroz, self.sendas, '24.90')

        texto = str(self.client.get('/api/lists/precos/', {'q': 'arroz'}).data)

        self.assertNotIn('precos@exemplo.test', texto)
        self.assertNotIn('owner', texto)
        self.assertNotIn('nfce_key', texto)

    def test_exige_login(self):
        self.assertEqual(APIClient().get('/api/lists/precos/').status_code, 401)


class PrecosDaListaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            email='lista@exemplo.test', password='senha-de-teste-123', first_name='Lista',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

        self.sendas = Store.objects.create(cnpj='06057223044209', name='SENDAS')
        self.atacadao = Store.objects.create(cnpj='75315333000109', name='ATACADAO')
        self.arroz = Product.objects.create(name='Arroz')
        self.feijao = Product.objects.create(name='Feijão')
        self.tomate = Product.objects.create(name='Tomate')

        self.lista = List.objects.create(owner=self.usuario, name='Semana')
        self.item_arroz = ListItem.objects.create(list=self.lista, product=self.arroz, quantity=2)
        self.item_feijao = ListItem.objects.create(list=self.lista, product=self.feijao, quantity=1)
        self.item_tomate = ListItem.objects.create(
            list=self.lista, product=self.tomate, quantity=Decimal('1.5'), measure='kg',
        )

    def precos(self):
        return self.client.get(f'/api/lists/{self.lista.id}/precos/').data

    def test_mais_barato_de_cada_item(self):
        _preco(self.arroz, self.sendas, '24.90')
        _preco(self.arroz, self.atacadao, '23.50')

        dados = self.precos()

        arroz = next(item for item in dados['itens'] if item['item'] == self.item_arroz.id)
        self.assertEqual(arroz['mais_barato_agora']['loja']['nome'], 'ATACADAO')
        self.assertEqual(arroz['lojas_com_preco'], 2)

    def test_so_compara_a_mesma_medida(self):
        _preco(self.tomate, self.sendas, '2.50', medida='unit')  # tomate por unidade
        _preco(self.tomate, self.atacadao, '7.99', medida='kg')

        dados = self.precos()

        tomate = next(item for item in dados['itens'] if item['item'] == self.item_tomate.id)
        self.assertEqual(tomate['mais_barato_agora']['loja']['nome'], 'ATACADAO')
        self.assertEqual(tomate['lojas_com_preco'], 1)

    def test_melhor_loja_prioriza_cobertura_e_depois_total(self):
        # Sendas tem os 3 itens; Atacadão só o arroz, mais barato.
        _preco(self.arroz, self.sendas, '24.90')
        _preco(self.feijao, self.sendas, '8.00')
        _preco(self.tomate, self.sendas, '8.00', medida='kg')
        _preco(self.arroz, self.atacadao, '20.00')

        melhor = self.precos()['melhor_loja']

        self.assertEqual(melhor[0]['loja']['nome'], 'SENDAS')
        self.assertEqual(melhor[0]['itens'], 3)
        # 2 × 24,90 + 1 × 8,00 + 1,5 kg × 8,00
        self.assertEqual(melhor[0]['total'], '69.80')
        self.assertEqual(melhor[1]['loja']['nome'], 'ATACADAO')
        self.assertEqual(melhor[1]['itens'], 1)

    def test_conta_precos_desatualizados_da_loja(self):
        _preco(self.arroz, self.sendas, '24.90', dias_atras=4)

        self.assertEqual(self.precos()['melhor_loja'][0]['desatualizados'], 1)

    def test_lista_de_outra_pessoa_da_404(self):
        outro = User.objects.create_user(
            email='outro@exemplo.test', password='senha-de-teste-123', first_name='Outro',
        )
        self.client.force_authenticate(outro)

        self.assertEqual(
            self.client.get(f'/api/lists/{self.lista.id}/precos/').status_code, 404,
        )


@patch('apps.lists.services.nfce.urlopen')
class RegistrarPrecosTests(TestCase):
    """A página de exemplo (tests_nfce.PAGINA): ARROZ TP1 5KG 24,90, TOMATE KG
    7,99/kg e PRATO FUNDO 2× 7,29, na loja de CNPJ 12.345.678/0001-90."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user(
            email='nota@exemplo.test', password='senha-de-teste-123', first_name='Nota',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

    def test_importar_pelo_menu_grava_os_precos_da_nota(self, urlopen):
        urlopen.return_value = _resposta()

        self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        precos = ObservedPrice.objects.order_by('price')
        self.assertEqual(
            [(p.product.name, str(p.price), p.measure) for p in precos],
            [('PRATO FUNDO', '7.29', 'unit'), ('TOMATE KG', '7.99', 'kg'), ('ARROZ TP1 5KG', '24.90', 'unit')],
        )
        self.assertTrue(all(p.store.cnpj == '12345678000190' for p in precos))
        self.assertEqual(precos[0].observed_at.strftime('%d/%m/%Y'), '23/10/2026')

    def test_preco_digitado_nao_entra_mesmo_com_itens_editados(self, urlopen):
        urlopen.return_value = _resposta()

        self.client.post('/api/lists/nfce/importar/', {
            'link': LINK,
            'itens': [
                # Nome editado: o preço continua valendo (é o da nota).
                {'nome': 'Arroz Camil 5 kg', 'quantidade': '1', 'medida': 'unit', 'preco': '24.90'},
                # Preço que não está na nota: não vira preço da cidade.
                {'nome': 'Coisa', 'quantidade': '1', 'medida': 'unit', 'preco': '1.00'},
            ],
        }, format='json')

        self.assertEqual(
            list(ObservedPrice.objects.values_list('product__name', 'price')),
            [('Arroz Camil 5 kg', Decimal('24.90'))],
        )

    def test_concluir_na_lista_grava_os_precos(self, urlopen):
        urlopen.return_value = _resposta()
        lista = List.objects.create(owner=self.usuario, name='Vazia')

        self.client.post(
            f'/api/lists/{lista.id}/nfce/concluir/',
            {'link': LINK, 'itens': [{'indice': 0}, {'indice': 1}]},
            format='json',
        )

        self.assertEqual(ObservedPrice.objects.count(), 2)

    def test_mesma_nota_de_duas_pessoas_nao_duplica(self, urlopen):
        urlopen.return_value = _resposta()
        self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        outra = User.objects.create_user(
            email='outra@exemplo.test', password='senha-de-teste-123', first_name='Outra',
        )
        self.client.force_authenticate(outra)
        self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        self.assertEqual(ObservedPrice.objects.count(), 3)


class RegistrarPrecosAntigosTests(TestCase):
    def test_leva_compras_antigas_para_os_precos_sem_duplicar(self):
        usuario = User.objects.create_user(
            email='antigo@exemplo.test', password='senha-de-teste-123', first_name='Antigo',
        )
        arroz = Product.objects.create(name='Arroz')
        leite = Product.objects.create(name='Leite')
        lista = List.objects.create(
            owner=usuario, name='Compra antiga', store='MERCADO EXEMPLO LTDA',
            nfce_key=CHAVE, is_completed=True, completed_at=timezone.now(),
        )
        ListItem.objects.create(
            list=lista, product=arroz, quantity=1, price=Decimal('24.90'), is_completed=True,
        )
        # Não comprado: não vira preço.
        ListItem.objects.create(list=lista, product=leite, quantity=1, is_completed=False)

        call_command('registrar_precos_antigos', verbosity=0)
        call_command('registrar_precos_antigos', verbosity=0)

        preco = ObservedPrice.objects.get()
        self.assertEqual(preco.product, arroz)
        self.assertEqual(preco.store.cnpj, '12345678000190')
        self.assertEqual(preco.store.name, 'MERCADO EXEMPLO LTDA')
