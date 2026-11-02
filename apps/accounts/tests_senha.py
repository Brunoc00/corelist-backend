import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

ESQUECI = '/api/auth/senha/esqueci/'
REDEFINIR = '/api/auth/senha/redefinir/'
SENHA_NOVA = 'NovaSenhaForte!2026'


@override_settings(SITE_URL='https://site.exemplo.test')
class EsqueciSenhaTests(TestCase):
    def setUp(self):
        cache.clear()  # contador do limite por IP
        self.client = APIClient()
        self.usuario = User.objects.create_user(
            email='maria@exemplo.test', password='SenhaAntiga!2026', first_name='Maria',
        )

    def pedir_link(self, email='maria@exemplo.test'):
        resposta = self.client.post(ESQUECI, {'email': email}, format='json')
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def link_do_email(self):
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r'https://\S+', mail.outbox[0].body).group(0)
        partes = urlparse(url)
        self.assertEqual(f'{partes.scheme}://{partes.netloc}{partes.path}', 'https://site.exemplo.test/redefinir-senha')
        dados = parse_qs(partes.query)
        return dados['uid'][0], dados['token'][0]

    def redefinir(self, uid, token, senha=SENHA_NOVA):
        return self.client.post(REDEFINIR, {'uid': uid, 'token': token, 'nova_senha': senha}, format='json')

    def test_envia_email_com_o_link(self):
        self.pedir_link('MARIA@exemplo.test')  # e-mail sem diferenciar maiúsculas
        self.assertEqual(mail.outbox[0].to, ['maria@exemplo.test'])
        self.assertIn('Olá, Maria!', mail.outbox[0].body)
        self.link_do_email()

    def test_email_sem_conta_responde_igual_e_nao_envia(self):
        com_conta = self.pedir_link().data
        mail.outbox.clear()
        sem_conta = self.pedir_link('ninguem@exemplo.test').data
        self.assertEqual(com_conta, sem_conta)
        self.assertEqual(mail.outbox, [])

    def test_sem_email_da_400(self):
        self.assertEqual(self.client.post(ESQUECI, {}, format='json').status_code, 400)

    def test_redefine_a_senha_e_entra_com_a_nova(self):
        self.pedir_link()
        uid, token = self.link_do_email()

        self.assertEqual(self.redefinir(uid, token).status_code, 200)

        login = self.client.post('/api/auth/login/', {'email': 'maria@exemplo.test', 'password': SENHA_NOVA}, format='json')
        self.assertEqual(login.status_code, 200)
        antiga = self.client.post('/api/auth/login/', {'email': 'maria@exemplo.test', 'password': 'SenhaAntiga!2026'}, format='json')
        self.assertEqual(antiga.status_code, 401)

    def test_link_so_vale_uma_vez(self):
        self.pedir_link()
        uid, token = self.link_do_email()
        self.redefinir(uid, token)
        segunda = self.redefinir(uid, token, 'OutraSenhaForte!2026')
        self.assertEqual(segunda.status_code, 400)
        self.assertIn('não vale mais', segunda.data['detail'])

    def test_token_errado_da_400(self):
        self.pedir_link()
        uid, _ = self.link_do_email()
        self.assertEqual(self.redefinir(uid, 'token-errado').status_code, 400)
        self.assertEqual(self.redefinir('lixo', 'token-errado').status_code, 400)

    def test_senha_fraca_e_recusada_e_o_link_continua_valendo(self):
        self.pedir_link()
        uid, token = self.link_do_email()
        fraca = self.redefinir(uid, token, '123')
        self.assertEqual(fraca.status_code, 400)
        self.assertIn('nova_senha', fraca.data)
        self.assertEqual(self.redefinir(uid, token).status_code, 200)

    def test_encerra_as_sessoes_abertas(self):
        refresh = RefreshToken.for_user(self.usuario)
        self.pedir_link()
        uid, token = self.link_do_email()
        self.redefinir(uid, token)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh['jti']).exists())
        renovar = self.client.post('/api/auth/login/refresh/', {'refresh': str(refresh)}, format='json')
        self.assertEqual(renovar.status_code, 401)

    def test_falha_no_envio_nao_quebra_a_tela(self):
        with patch('apps.accounts.views_senha.send_mail', side_effect=OSError('SMTP fora do ar')):
            self.assertEqual(self.pedir_link().status_code, 200)

    def test_limite_de_pedidos_por_ip(self):
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'senha': '2/hour'}):
            self.pedir_link()
            self.pedir_link()
            resposta = self.client.post(ESQUECI, {'email': 'maria@exemplo.test'}, format='json')
            self.assertEqual(resposta.status_code, 429)
