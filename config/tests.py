from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User


class ApiSemCacheTests(TestCase):
    def test_resposta_autenticada_nao_pode_ser_guardada(self):
        usuario = User.objects.create_user(
            email='cache@exemplo.test', password='senha-de-teste-123', first_name='Cache'
        )
        client = APIClient()
        client.force_authenticate(usuario)

        response = client.get('/api/lists/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])

    def test_erro_de_autenticacao_tambem_nao_pode_ser_guardado(self):
        response = APIClient().get('/api/auth/me/')

        self.assertEqual(response.status_code, 401)
        self.assertIn('no-store', response['Cache-Control'])


class HealthTests(TestCase):
    def test_responde_ok_sem_login(self):
        response = APIClient().get('/api/health/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'ok')
        self.assertEqual(response.data['banco'], 'ok')
        self.assertIn('versao', response.data)
        self.assertIn('no-store', response['Cache-Control'])
