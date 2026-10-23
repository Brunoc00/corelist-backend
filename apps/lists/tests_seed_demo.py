from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.lists.management.commands.seed_demo import (
    DEMO_EMAIL,
    DEMO_SENHA,
)
from apps.lists.models import List
from apps.lists.services.promotions import get_promotions

BUSCAR = 'apps.lists.management.commands.seed_demo.buscar_por_codigo'


class SeedDemoTests(TestCase):

    def rodar(self):
        call_command('seed_demo', stdout=StringIO())

    @override_settings(DEBUG=False)
    def test_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            self.rodar()

        self.assertFalse(User.objects.filter(email=DEMO_EMAIL).exists())

    @override_settings(DEBUG=True)
    @patch(BUSCAR, return_value=None)
    def test_creates_demo_account_with_data(self, mock_buscar):
        self.rodar()

        usuario = User.objects.get(email=DEMO_EMAIL)

        self.assertTrue(usuario.check_password(DEMO_SENHA))
        self.assertEqual(
            List.objects.filter(owner=usuario, is_completed=True).count(),
            3,
        )
        self.assertEqual(
            List.objects.filter(owner=usuario, is_completed=False).count(),
            1,
        )
        # A lista em andamento tem produtos abaixo da média do histórico.
        self.assertGreater(len(get_promotions(usuario)), 0)

    @override_settings(DEBUG=True)
    @patch(BUSCAR, return_value=None)
    def test_running_twice_does_not_duplicate(self, mock_buscar):
        self.rodar()
        self.rodar()

        self.assertEqual(User.objects.filter(email=DEMO_EMAIL).count(), 1)
        self.assertEqual(
            List.objects.filter(owner__email=DEMO_EMAIL).count(),
            4,
        )
