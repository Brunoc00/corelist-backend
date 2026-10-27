from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.products.models import Product

from .models import List
from .services.nfce import LinkInvalido, NfceErro, ler_pagina, validar_link

CHAVE = '13261012345678000190650080000123451000000017'
LINK = (
    'https://sistemas.sefaz.am.gov.br/nfceweb/consultarNFCe.jsp'
    f'?p={CHAVE}%7C3%7C1'
)


def _item(numero, nome, codigo, qtd, unidade, unitario, total):
    return f'''
<tr id="Item + {numero}">
<td valign="top"><span class="txtTit">{nome}</span><span class="RCod">
    (C&oacute;digo:
    {codigo}
    )
</span>
<br>
<span class="Rqtd"><strong>Qtde.:</strong>{qtd}</span><span class="RUN"><strong>UN: </strong>{unidade}</span><span class="RvlUnit"><strong>Vl. Unit.:</strong>
    &nbsp;
    {unitario}</span></td><td class="txtTit noWrap" valign="top" align="right">
    Vl. Total
    <br>
<span class="valor">{total}</span></td>
</tr>'''


# Mesma estrutura da página da Sefaz-AM, com dados inventados.
PAGINA = f'''<html><head><title>SEFAZ-AM</title></head><body>
<div id="conteudo">
<div class="txtCenter">
<div id="u20" class="txtTopo">MERCADO EXEMPLO LTDA</div>
<div class="text">CNPJ: 12.345.678/0001-90</div>
<div class="text">Rua Exemplo, 100, Centro, Manaus, AM</div>
</div>
<table data-filter="true" id="tabResult">
{_item(1, 'ARROZ TP1 5KG', '111', '1', 'Un', '24,9', '24,90')}
{_item(2, 'TOMATE KG', '222', '1,250', 'KG', '7,99', '9,99')}
{_item(3, 'PRATO FUNDO', '333', '1', 'Un', '7,29', '7,29')}
{_item(4, 'PRATO FUNDO', '333', '1', 'Un', '7,29', '7,29')}
</table>
<div class="txtRight" id="totalNota">
<div class="linhaShade" id="linhaTotal">
<label>Valor a pagar R$:</label><span class="totalNumb txtMax">49,47</span>
</div>
</div>
<div id="infos"><ul><li>
<strong>Emiss&atilde;o: </strong>23/10/2026 14:28:28 - Via Consumidor
</li></ul></div>
</div></body></html>'''


def _resposta(html=PAGINA, url=LINK):
    resposta = MagicMock()
    resposta.read.return_value = html.encode('iso-8859-1')
    resposta.geturl.return_value = url
    resposta.__enter__.return_value = resposta
    return resposta


class ValidarLinkTests(TestCase):
    def test_aceita_link_do_qr_code(self):
        _, chave = validar_link(LINK)
        self.assertEqual(chave, CHAVE)

    def test_aceita_link_impresso_no_qr_code(self):
        # O QR code da nota traz http:// e o "|" sem codificar.
        link, chave = validar_link(
            'http://sistemas.sefaz.am.gov.br/nfceweb/consultarNFCe.jsp'
            f'?p={CHAVE}|3|1'
        )

        self.assertEqual(chave, CHAVE)
        self.assertEqual(
            link,
            'https://sistemas.sefaz.am.gov.br/nfceweb/consultarNFCe.jsp'
            f'?p={CHAVE}%7C3%7C1',
        )

    def test_recusa_outro_site(self):
        for link in [
            'https://exemplo.com/nfceweb/consultarNFCe.jsp?p=' + CHAVE,
            'ftp://sistemas.sefaz.am.gov.br/nfceweb/consultarNFCe.jsp?p=' + CHAVE,
            'https://sistemas.sefaz.am.gov.br.exemplo.com/nfceweb/consultarNFCe.jsp?p=' + CHAVE,
            'https://sistemas.sefaz.am.gov.br/outra-pagina?p=' + CHAVE,
            'https://sistemas.sefaz.am.gov.br:8443/nfceweb/consultarNFCe.jsp?p=' + CHAVE,
        ]:
            with self.subTest(link=link), self.assertRaises(LinkInvalido):
                validar_link(link)

    def test_recusa_link_sem_chave(self):
        with self.assertRaises(LinkInvalido):
            validar_link('https://sistemas.sefaz.am.gov.br/nfceweb/consultarNFCe.jsp?p=123')


class LerPaginaTests(TestCase):
    def test_le_loja_data_total_e_itens(self):
        nota = ler_pagina(PAGINA)

        self.assertEqual(nota['loja'], 'MERCADO EXEMPLO LTDA')
        self.assertEqual(nota['cnpj'], '12.345.678/0001-90')
        self.assertEqual(nota['total'], Decimal('49.47'))
        self.assertEqual(nota['emitida_em'].strftime('%d/%m/%Y %H:%M'), '23/10/2026 14:28')
        self.assertEqual(str(nota['emitida_em'].tzinfo), 'America/Manaus')

    def test_item_por_quilo_vira_peso(self):
        tomate = ler_pagina(PAGINA)['itens'][1]

        self.assertEqual(tomate['medida'], 'kg')
        self.assertEqual(tomate['quantidade'], Decimal('1.250'))
        self.assertEqual(tomate['preco'], Decimal('7.99'))

    def test_itens_repetidos_sao_somados(self):
        itens = ler_pagina(PAGINA)['itens']

        self.assertEqual(len(itens), 3)
        prato = itens[2]
        self.assertEqual(prato['quantidade'], Decimal('2'))
        self.assertEqual(prato['total'], Decimal('14.58'))

    def test_pagina_sem_itens_da_erro(self):
        with self.assertRaises(NfceErro):
            ler_pagina('<html><body>Nota não encontrada</body></html>')


@patch('apps.lists.services.nfce.urlopen')
class NfceApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user(
            email='nota@exemplo.test', password='senha-de-teste-123', first_name='Nota',
        )
        self.outro = User.objects.create_user(
            email='outro@exemplo.test', password='senha-de-teste-123', first_name='Outro',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)

    def test_ler_devolve_a_nota_para_conferir(self, urlopen):
        urlopen.return_value = _resposta()
        Product.objects.create(name='Arroz TP1 5kg')

        resposta = self.client.post('/api/lists/nfce/ler/', {'link': LINK}, format='json')

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['loja'], 'MERCADO EXEMPLO LTDA')
        self.assertEqual(resposta.data['total'], '49.47')
        self.assertEqual(len(resposta.data['itens']), 3)
        self.assertEqual(resposta.data['itens'][0]['produto']['name'], 'Arroz TP1 5kg')
        self.assertIsNone(resposta.data['itens'][1]['produto'])
        self.assertIsNone(resposta.data['lista_importada'])

    def test_link_de_outro_site_nao_e_acessado(self, urlopen):
        resposta = self.client.post(
            '/api/lists/nfce/ler/', {'link': 'https://exemplo.com/x?p=' + CHAVE}, format='json',
        )

        self.assertEqual(resposta.status_code, 400)
        urlopen.assert_not_called()

    def test_sefaz_fora_do_ar_da_502(self, urlopen):
        urlopen.side_effect = TimeoutError()

        resposta = self.client.post('/api/lists/nfce/ler/', {'link': LINK}, format='json')

        self.assertEqual(resposta.status_code, 502)

    def test_redirecionamento_para_fora_da_sefaz_e_recusado(self, urlopen):
        urlopen.return_value = _resposta(url='https://exemplo.com/')

        resposta = self.client.post('/api/lists/nfce/ler/', {'link': LINK}, format='json')

        self.assertEqual(resposta.status_code, 502)

    def test_importar_cria_compra_concluida(self, urlopen):
        urlopen.return_value = _resposta()

        resposta = self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        self.assertEqual(resposta.status_code, 201)
        lista = List.objects.get(id=resposta.data['id'])
        self.assertTrue(lista.is_completed)
        self.assertEqual(lista.owner, self.usuario)
        self.assertEqual(lista.store, 'MERCADO EXEMPLO LTDA')
        self.assertEqual(lista.nfce_key, CHAVE)
        self.assertEqual(lista.items.count(), 3)
        self.assertTrue(all(item.is_completed for item in lista.items.all()))
        # O peso (1,250 kg) é guardado com 2 casas: até 1 centavo de diferença.
        self.assertAlmostEqual(lista.total, Decimal('49.47'), delta=Decimal('0.01'))
        tomate = lista.items.get(product__name='TOMATE KG')
        self.assertEqual(tomate.measure, 'kg')
        self.assertEqual(tomate.product.data_source, Product.SOURCE_NFCE)

    def test_importar_usa_itens_conferidos(self, urlopen):
        urlopen.return_value = _resposta()
        arroz = Product.objects.create(name='Arroz Camil 5 kg')

        resposta = self.client.post('/api/lists/nfce/importar/', {
            'link': LINK,
            'nome': 'Compra do mês',
            'itens': [
                {'nome': 'arroz camil 5 kg', 'quantidade': '1', 'medida': 'unit', 'preco': '24.90'},
            ],
        }, format='json')

        self.assertEqual(resposta.status_code, 201)
        lista = List.objects.get(id=resposta.data['id'])
        self.assertEqual(lista.name, 'Compra do mês')
        self.assertEqual(lista.items.get().product, arroz)

    def test_mesma_nota_nao_entra_duas_vezes(self, urlopen):
        urlopen.return_value = _resposta()
        primeira = self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        segunda = self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        self.assertEqual(segunda.status_code, 409)
        self.assertEqual(segunda.data['list_id'], primeira.data['id'])
        self.assertEqual(List.objects.filter(owner=self.usuario).count(), 1)

        lida = self.client.post('/api/lists/nfce/ler/', {'link': LINK}, format='json')
        self.assertEqual(lida.data['lista_importada'], primeira.data['id'])

    def test_outro_usuario_pode_importar_a_mesma_nota(self, urlopen):
        urlopen.return_value = _resposta()
        self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        self.client.force_authenticate(self.outro)
        resposta = self.client.post('/api/lists/nfce/importar/', {'link': LINK}, format='json')

        self.assertEqual(resposta.status_code, 201)

    def test_exige_login(self, urlopen):
        resposta = APIClient().post('/api/lists/nfce/ler/', {'link': LINK}, format='json')

        self.assertEqual(resposta.status_code, 401)
        urlopen.assert_not_called()
