from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class RegisterTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_register_saves_first_name(self):
        response = self.client.post(
            '/api/auth/register/',
            {
                'first_name': 'Maria',
                'email': 'maria@example.com',
                'password': 'SenhaForte123!',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['first_name'], 'Maria')

        user = User.objects.get(email='maria@example.com')

        self.assertEqual(user.first_name, 'Maria')
        self.assertTrue(user.check_password('SenhaForte123!'))

    def test_register_rejects_short_password(self):
        response = self.client.post(
            '/api/auth/register/',
            {
                'first_name': 'Maria',
                'email': 'maria@example.com',
                'password': 'curta',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.data)
        self.assertFalse(
            User.objects.filter(email='maria@example.com').exists()
        )


class MeTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            email='maria@example.com',
            password='SenhaForte123!',
            first_name='Maria',
        )

    def test_me_requires_authentication(self):
        response = self.client.get('/api/auth/me/')

        self.assertEqual(response.status_code, 401)

    def test_me_returns_current_user(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/auth/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['email'], 'maria@example.com')
        self.assertEqual(response.data['first_name'], 'Maria')

    def test_user_can_update_name(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.patch(
            '/api/auth/me/',
            {
                'first_name': 'Maria Clara',
                'last_name': 'Souza',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()

        self.assertEqual(self.user.first_name, 'Maria Clara')
        self.assertEqual(self.user.last_name, 'Souza')

    def test_user_cannot_change_email_through_me(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.patch(
            '/api/auth/me/',
            {
                'email': 'outro@example.com',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()

        self.assertEqual(self.user.email, 'maria@example.com')

    def test_update_name_requires_authentication(self):
        response = self.client.patch(
            '/api/auth/me/',
            {
                'first_name': 'Invasor',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 401)

        self.user.refresh_from_db()

        self.assertEqual(self.user.first_name, 'Maria')
