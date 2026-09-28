from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError
from rest_framework.test import APIClient

from apps.lists.models import List, ListItem
from apps.lists.schemas import (
    InsightResponseSchema,
    InsightSchema,
)
from apps.lists.services.gemini import (
    serialize_context,
    generate_with_gemini,
    validate_insights,
)
from apps.lists.services.insights import (
    build_insights_context,
    generate_insights,
)
from apps.products.models import Product, Category

User = get_user_model()


class ListItemTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            email='test@example.com',
            password='StrongPassword123!',
        )

        self.other_user = User.objects.create_user(
            email='other@example.com',
            password='StrongPassword123!',
        )

        self.client.force_authenticate(user=self.user)

        self.shopping_list = List.objects.create(
            name='Lista de teste',
            owner=self.user,
        )

        self.product = Product.objects.create(
            name='Arroz',
        )

        self.item = ListItem.objects.create(
            list=self.shopping_list,
            product=self.product,
            quantity=2,
        )

    @patch(
        'apps.lists.services.insights.generate_with_gemini'
    )
    def test_generate_insights_uses_gemini_provider(
            self,
            mock_generate_with_gemini,
    ):
        mock_generate_with_gemini.return_value = [
            {
                'type': 'spending',
                'title': 'Compra abaixo da média',
                'message': (
                    'Sua compra está abaixo '
                    'da média histórica.'
                ),
                'severity': 'info',
            }
        ]

        insights = generate_insights(
            user=self.user,
        )

        self.assertEqual(
            insights,
            mock_generate_with_gemini.return_value,
        )

        mock_generate_with_gemini.assert_called_once()

    def test_user_can_create_list_with_budget(self):
        response = self.client.post(
            '/api/lists/',
            {
                'name': 'Compras do mês',
                'budget': '200.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data['name'],
            'Compras do mês',
        )
        self.assertEqual(
            response.data['budget'],
            '200.00',
        )

    def test_user_can_list_items(self):
        response = self.client.get(
            f'/api/lists/{self.shopping_list.id}/items/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]['product'],
            self.product.id,
        )

    def test_user_cannot_list_items_from_another_users_list(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(
            f'/api/lists/{self.shopping_list.id}/items/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_user_can_create_item(self):
        response = self.client.post(
            f'/api/lists/{self.shopping_list.id}/items/',
            {
                'product': self.product.id,
                'quantity': 3,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data['product'],
            self.product.id,
        )
        self.assertEqual(
            response.data['quantity'],
            '3.00',
        )

    def test_user_cannot_create_item_in_another_users_list(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.post(
            f'/api/lists/{self.shopping_list.id}/items/',
            {
                'product': self.product.id,
                'quantity': 3,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 404)

    def test_user_can_retrieve_item(self):
        response = self.client.get(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.item.id)
        self.assertEqual(
            response.data['product'],
            self.product.id,
        )
        self.assertEqual(
            response.data['quantity'],
            '2.00',
        )

    def test_user_can_update_item(self):
        response = self.client.patch(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/',
            {
                'is_completed': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_completed'])

        self.item.refresh_from_db()

        self.assertTrue(self.item.is_completed)

    def test_user_can_delete_item(self):
        response = self.client.delete(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/'
        )

        self.assertEqual(response.status_code, 204)

        self.assertFalse(
            ListItem.objects.filter(
                id=self.item.id,
            ).exists()
        )

    def test_user_cannot_retrieve_item_from_another_users_list(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/'
        )

        self.assertEqual(response.status_code, 404)

    def test_user_cannot_update_item_from_another_users_list(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.patch(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/',
            {
                'is_completed': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 404)

    def test_user_cannot_delete_item_from_another_users_list(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.delete(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/'
        )

        self.assertEqual(response.status_code, 404)

        self.assertTrue(
            ListItem.objects.filter(
                id=self.item.id,
            ).exists()
        )

    def test_user_can_create_item_with_price(self):
        response = self.client.post(
            f'/api/lists/{self.shopping_list.id}/items/',
            {
                'product': self.product.id,
                'quantity': 2,
                'price': '12.50',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data['product'],
            self.product.id,
        )
        self.assertEqual(
            response.data['quantity'],
            '2.00',
        )
        self.assertEqual(
            response.data['price'],
            '12.50',
        )
        self.assertEqual(
            response.data['subtotal'],
            '25.00',
        )

    def test_list_item_calculates_subtotal(self):
        self.item.price = '30.00'
        self.item.save()

        self.item.refresh_from_db()

        self.assertEqual(
            self.item.subtotal,
            60,
        )

    def test_user_can_complete_list(self):
        response = self.client.post(
            f'/api/lists/{self.shopping_list.id}/complete/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_completed'])
        self.assertIsNotNone(
            response.data['completed_at']
        )

        self.shopping_list.refresh_from_db()

        self.assertTrue(
            self.shopping_list.is_completed
        )
        self.assertIsNotNone(
            self.shopping_list.completed_at
        )

    def test_user_cannot_complete_another_users_list(self):
        self.client.force_authenticate(
            user=self.other_user
        )

        response = self.client.post(
            f'/api/lists/{self.shopping_list.id}/complete/'
        )

        self.assertEqual(response.status_code, 404)

        self.shopping_list.refresh_from_db()

        self.assertFalse(
            self.shopping_list.is_completed
        )
        self.assertIsNone(
            self.shopping_list.completed_at
        )

    def test_user_can_list_completed_lists(self):
        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.get(
            '/api/lists/history/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]['id'],
            self.shopping_list.id,
        )
        self.assertTrue(
            response.data[0]['is_completed']
        )

    def test_history_does_not_include_active_lists(self):
        response = self.client.get(
            '/api/lists/history/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_user_cannot_see_another_users_completed_list_history(
            self,
    ):
        List.objects.create(
            name='Lista do outro usuário',
            owner=self.other_user,
            is_completed=True,
        )

        response = self.client.get(
            '/api/lists/history/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_history_includes_list_items(self):
        self.item.price = '30.00'
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.get(
            '/api/lists/history/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        history_list = response.data[0]

        self.assertEqual(
            len(history_list['items']),
            1,
        )

        self.assertEqual(
            history_list['items'][0]['product'],
            self.product.id,
        )

        self.assertEqual(
            history_list['items'][0]['quantity'],
            '2.00',
        )

        self.assertEqual(
            history_list['items'][0]['price'],
            '30.00',
        )

        self.assertEqual(
            history_list['items'][0]['subtotal'],
            '60.00',
        )

    def test_history_returns_list_total(self):
        self.item.price = '30.00'
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.get(
            '/api/lists/history/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        self.assertEqual(
            response.data[0]['total'],
            '60.00',
        )

    def test_summary_returns_total_spent(self):
        self.item.price = 30
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.get(
            '/api/lists/summary/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )
        self.assertEqual(
            response.data['total'],
            '60.00',
        )
        self.assertEqual(
            response.data['lists_count'],
            1,
        )
        self.assertEqual(
            response.data['average_purchase'],
            '60.00',
        )

    def test_summary_returns_zero_when_there_are_no_completed_lists(
            self,
    ):
        response = self.client.get(
            '/api/lists/summary/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )
        self.assertEqual(
            response.data['total'],
            '0.00',
        )
        self.assertEqual(
            response.data['lists_count'],
            0,
        )
        self.assertEqual(
            response.data['average_purchase'],
            '0.00',
        )

    def test_summary_calculates_average_purchase(self):
        self.item.price = 30
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.save()

        second_list = List.objects.create(
            name='Segunda compra',
            owner=self.user,
            is_completed=True,
        )

        ListItem.objects.create(
            list=second_list,
            product=self.product,
            quantity=2,
            price=20,
        )

        response = self.client.get(
            '/api/lists/summary/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )
        self.assertEqual(
            response.data['total'],
            '100.00',
        )
        self.assertEqual(
            response.data['lists_count'],
            2,
        )
        self.assertEqual(
            response.data['average_purchase'],
            '50.00',
        )

    def test_summary_returns_monthly_spending(self):
        self.item.price = 30
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.completed_at = (
            timezone.make_aware(
                datetime(
                    2026,
                    8,
                    15,
                    12,
                    0,
                )
            )
        )
        self.shopping_list.save()

        second_list = List.objects.create(
            name='Compra de setembro',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.make_aware(
                datetime(
                    2026,
                    9,
                    15,
                    12,
                    0,
                )
            ),
        )

        ListItem.objects.create(
            list=second_list,
            product=self.product,
            quantity=2,
            price=20,
        )

        response = self.client.get(
            '/api/lists/summary/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data['monthly'],
            [
                {
                    'month': '2026-08',
                    'total': '60.00',
                },
                {
                    'month': '2026-09',
                    'total': '40.00',
                },
            ],
        )

    def test_user_cannot_complete_list_using_patch(self):
        response = self.client.patch(
            f'/api/lists/{self.shopping_list.id}/',
            {
                'is_completed': True,
            },
            format='json',
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.shopping_list.refresh_from_db()

        self.assertFalse(
            self.shopping_list.is_completed
        )
        self.assertIsNone(
            self.shopping_list.completed_at
        )

    def test_user_cannot_update_item_from_completed_list(
            self,
    ):
        self.item.price = '30.00'
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.patch(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/',
            {
                'price': '10.00',
            },
            format='json',
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.item.refresh_from_db()

        self.assertEqual(
            self.item.price,
            30,
        )

    def test_user_cannot_delete_item_from_completed_list(
            self,
    ):
        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.delete(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/'
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertTrue(
            ListItem.objects.filter(
                id=self.item.id,
            ).exists()
        )

    def test_user_cannot_create_item_in_completed_list(
            self,
    ):
        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.post(
            f'/api/lists/{self.shopping_list.id}/items/',
            {
                'product': self.product.id,
                'quantity': 3,
                'price': '10.00',
            },
            format='json',
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertEqual(
            ListItem.objects.filter(
                list=self.shopping_list,
            ).count(),
            1,
        )

    def test_user_cannot_update_completed_list(self):
        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.patch(
            f'/api/lists/{self.shopping_list.id}/',
            {
                'name': 'Nome alterado',
                'budget': '999.00',
            },
            format='json',
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.shopping_list.refresh_from_db()

        self.assertEqual(
            self.shopping_list.name,
            'Lista de teste',
        )

    def test_user_cannot_delete_completed_list(self):
        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.delete(
            f'/api/lists/{self.shopping_list.id}/'
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertTrue(
            List.objects.filter(
                id=self.shopping_list.id,
            ).exists()
        )

    def test_summary_returns_spending_by_category(self):
        food_category = Category.objects.create(
            name='Alimentos',
        )

        cleaning_category = Category.objects.create(
            name='Limpeza',
        )

        self.product.category = food_category
        self.product.save()

        self.item.price = 30
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.save()

        cleaning_product = Product.objects.create(
            name='Detergente',
            category=cleaning_category,
        )

        ListItem.objects.create(
            list=self.shopping_list,
            product=cleaning_product,
            quantity=2,
            price=10,
        )

        response = self.client.get(
            '/api/lists/summary/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data['categories'],
            [
                {
                    'category': 'Alimentos',
                    'total': '60.00',
                },
                {
                    'category': 'Limpeza',
                    'total': '20.00',
                },
            ],
        )

    def test_summary_returns_top_products(self):
        self.item.quantity = 5
        self.item.price = 10
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.save()

        second_product = Product.objects.create(
            name='Feijão',
        )

        ListItem.objects.create(
            list=self.shopping_list,
            product=second_product,
            quantity=2,
            price=8,
        )

        response = self.client.get(
            '/api/lists/summary/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data['top_products'],
            [
                {
                    'product': self.product.name,
                    'quantity': '5.00',
                },
                {
                    'product': 'Feijão',
                    'quantity': '2.00',
                },
            ],
        )

    def test_summary_returns_period_comparison(self):
        self.item.quantity = 2
        self.item.price = 50
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.completed_at = (
            timezone.make_aware(
                datetime(
                    2026,
                    8,
                    15,
                    12,
                    0,
                )
            )
        )
        self.shopping_list.save()

        second_list = List.objects.create(
            name='Compra de setembro',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.make_aware(
                datetime(
                    2026,
                    9,
                    15,
                    12,
                    0,
                )
            ),
        )

        ListItem.objects.create(
            list=second_list,
            product=self.product,
            quantity=2,
            price=40,
        )

        response = self.client.get(
            '/api/lists/summary/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data['period_comparison'],
            {
                'previous_month': '2026-08',
                'previous_total': '100.00',
                'current_month': '2026-09',
                'current_total': '80.00',
                'difference': '-20.00',
                'percentage_change': '-20.00',
            },
        )

    def test_active_list_returns_current_total(self):
        self.item.quantity = 2
        self.item.price = 30
        self.item.save()

        second_product = Product.objects.create(
            name='Feijão',
        )

        ListItem.objects.create(
            list=self.shopping_list,
            product=second_product,
            quantity=3,
            price=10,
        )

        response = self.client.get(
            f'/api/lists/{self.shopping_list.id}/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )
        self.assertEqual(
            response.data['total'],
            '90.00',
        )

    def test_active_list_returns_budget(self):
        self.shopping_list.budget = '200.00'
        self.shopping_list.save()

        response = self.client.get(
            f'/api/lists/{self.shopping_list.id}/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )
        self.assertEqual(
            response.data['budget'],
            '200.00',
        )

    def test_active_list_returns_comparison_with_history(
            self,
    ):
        self.item.quantity = 2
        self.item.price = 60
        self.item.save()

        first_completed_list = List.objects.create(
            name='Compra anterior 1',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.make_aware(
                datetime(
                    2026,
                    8,
                    10,
                    12,
                    0,
                )
            ),
        )

        ListItem.objects.create(
            list=first_completed_list,
            product=self.product,
            quantity=2,
            price=50,
        )

        second_completed_list = List.objects.create(
            name='Compra anterior 2',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.make_aware(
                datetime(
                    2026,
                    9,
                    10,
                    12,
                    0,
                )
            ),
        )

        ListItem.objects.create(
            list=second_completed_list,
            product=self.product,
            quantity=2,
            price=100,
        )

        response = self.client.get(
            f'/api/lists/{self.shopping_list.id}/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data['history_comparison'],
            {
                'current_total': '120.00',
                'historical_average': '150.00',
                'difference': '-30.00',
                'percentage_change': '-20.00',
            },
        )

    @patch(
        'apps.lists.views.generate_insights'
    )
    def test_user_can_get_ai_insights(
            self,
            mock_generate_insights,
    ):
        mock_generate_insights.return_value = []

        response = self.client.get(
            '/api/lists/insights/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertIn(
            'insights',
            response.data,
        )

        self.assertEqual(
            response.data['insights'],
            [],
        )

    @patch(
        'apps.lists.views.generate_insights'
    )
    def test_ai_insights_uses_insight_service(
            self,
            mock_generate_insights,
    ):
        mock_generate_insights.return_value = [
            {
                'type': 'spending',
                'title': 'Compra abaixo da média',
                'message': (
                    'Sua compra atual está abaixo '
                    'da média histórica.'
                ),
                'severity': 'info',
            }
        ]

        response = self.client.get(
            '/api/lists/insights/'
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data['insights'],
            mock_generate_insights.return_value,
        )

        mock_generate_insights.assert_called_once()

    def test_build_insights_context_returns_historical_average(
            self,
    ):
        first_completed_list = List.objects.create(
            name='Compra anterior 1',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.now(),
        )

        ListItem.objects.create(
            list=first_completed_list,
            product=self.product,
            quantity=2,
            price=50,
        )

        second_completed_list = List.objects.create(
            name='Compra anterior 2',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.now(),
        )

        ListItem.objects.create(
            list=second_completed_list,
            product=self.product,
            quantity=2,
            price=100,
        )

        context = build_insights_context(
            user=self.user,
        )

        self.assertEqual(
            context['historical_average'],
            Decimal('150.00'),
        )

    def test_build_insights_context_returns_current_total(
            self,
    ):
        self.item.quantity = 2
        self.item.price = 60
        self.item.save()

        context = build_insights_context(
            user=self.user,
        )

        self.assertEqual(
            context['current_total'],
            Decimal('120.00'),
        )

    def test_build_insights_context_returns_current_budget(
            self,
    ):
        self.shopping_list.budget = Decimal('200.00')
        self.shopping_list.save()

        context = build_insights_context(
            user=self.user,
        )

        self.assertEqual(
            context['budget'],
            Decimal('200.00'),
        )

    def test_build_insights_context_returns_budget_remaining(
            self,
    ):
        self.shopping_list.budget = Decimal('200.00')
        self.shopping_list.save()

        self.item.quantity = 2
        self.item.price = 60
        self.item.save()

        context = build_insights_context(
            user=self.user,
        )

        self.assertEqual(
            context['budget_remaining'],
            Decimal('80.00'),
        )

    def test_build_insights_context_returns_historical_difference(
            self,
    ):
        self.item.quantity = 2
        self.item.price = 60
        self.item.save()

        completed_list = List.objects.create(
            name='Compra anterior',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.now(),
        )

        ListItem.objects.create(
            list=completed_list,
            product=self.product,
            quantity=3,
            price=50,
        )

        context = build_insights_context(
            user=self.user,
        )

        self.assertEqual(
            context['historical_difference'],
            Decimal('-30.00'),
        )

    def test_build_insights_context_returns_historical_percentage_change(
            self,
    ):
        self.item.quantity = 2
        self.item.price = 60
        self.item.save()

        completed_list = List.objects.create(
            name='Compra anterior',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.now(),
        )

        ListItem.objects.create(
            list=completed_list,
            product=self.product,
            quantity=3,
            price=50,
        )

        context = build_insights_context(
            user=self.user,
        )

        self.assertEqual(
            context[
                'historical_percentage_change'
            ],
            Decimal('-20.00'),
        )

    def test_build_insights_context_returns_zero_percentage_without_history(
            self,
    ):
        self.item.quantity = 2
        self.item.price = 60
        self.item.save()

        context = build_insights_context(
            user=self.user,
        )

        self.assertEqual(
            context['historical_average'],
            Decimal('0.00'),
        )

        self.assertEqual(
            context[
                'historical_percentage_change'
            ],
            Decimal('0.00'),
        )

    def test_validate_insights_accepts_valid_insights(self):
        insights = [
            {
                'type': 'spending',
                'title': 'Compra abaixo da média',
                'message': (
                    'Sua compra está abaixo '
                    'da média histórica.'
                ),
                'severity': 'info',
            }
        ]

        validated_insights = validate_insights(
            insights=insights,
        )

        self.assertEqual(
            validated_insights,
            insights,
        )

    def test_validate_insights_rejects_missing_fields(self):
        insights = [
            {
                'type': 'spending',
                'title': 'Compra abaixo da média',
                'severity': 'info',
            }
        ]

        validated_insights = validate_insights(
            insights=insights,
        )

        self.assertEqual(
            validated_insights,
            [],
        )

    def test_validate_insights_rejects_invalid_severity(self):
        insights = [
            {
                'type': 'spending',
                'title': 'Compra acima da média',
                'message': (
                    'Sua compra está acima '
                    'da média histórica.'
                ),
                'severity': 'banana',
            }
        ]

        validated_insights = validate_insights(
            insights=insights,
        )

        self.assertEqual(
            validated_insights,
            [],
        )

    def test_insight_schema_accepts_valid_data(self):
        insight = InsightSchema(
            type='spending',
            title='Compra abaixo da média',
            message=(
                'Sua compra está abaixo '
                'da média histórica.'
            ),
            severity='info',
        )

        self.assertEqual(
            insight.type,
            'spending',
        )

        self.assertEqual(
            insight.severity,
            'info',
        )

    def test_insight_schema_rejects_invalid_severity(self):
        with self.assertRaises(ValidationError):
            InsightSchema(
                type='spending',
                title='Compra acima da média',
                message=(
                    'Sua compra está acima '
                    'da média histórica.'
                ),
                severity='banana',
            )

    @patch(
        'apps.lists.services.gemini.genai.Client'
    )
    def test_generate_with_gemini_calls_gemini_client(
            self,
            mock_client_class,
    ):
        generate_with_gemini(
            context={
                'historical_average': Decimal('150.00'),
                'current_total': Decimal('120.00'),
                'budget': Decimal('200.00'),
                'budget_remaining': Decimal('80.00'),
                'historical_difference': Decimal('-30.00'),
                'historical_percentage_change': Decimal('-20.00'),
            }
        )

        mock_client_class.assert_called_once()

    @patch(
        'apps.lists.services.gemini.genai.Client'
    )
    def test_generate_with_gemini_calls_interactions_create(
            self,
            mock_client_class,
    ):
        mock_client = mock_client_class.return_value

        generate_with_gemini(
            context={
                'historical_average': Decimal('150.00'),
                'current_total': Decimal('120.00'),
                'budget': Decimal('200.00'),
                'budget_remaining': Decimal('80.00'),
                'historical_difference': Decimal('-30.00'),
                'historical_percentage_change': Decimal('-20.00'),
            }
        )

        mock_client.interactions.create.assert_called_once()

    def test_serialize_context_converts_decimals_to_strings(
            self,
    ):
        context = {
            'historical_average': Decimal('150.00'),
            'current_total': Decimal('120.00'),
            'budget': Decimal('200.00'),
            'budget_remaining': Decimal('80.00'),
            'historical_difference': Decimal('-30.00'),
            'historical_percentage_change': Decimal('-20.00'),
        }

        serialized_context = serialize_context(
            context=context,
        )

        self.assertEqual(
            serialized_context,
            {
                'historical_average': '150.00',
                'current_total': '120.00',
                'budget': '200.00',
                'budget_remaining': '80.00',
                'historical_difference': '-30.00',
                'historical_percentage_change': '-20.00',
            },
        )

    @patch(
        'apps.lists.services.gemini.genai.Client'
    )
    def test_generate_with_gemini_sends_context_to_gemini(
            self,
            mock_client_class,
    ):
        mock_client = mock_client_class.return_value

        generate_with_gemini(
            context={
                'historical_average': Decimal('150.00'),
                'current_total': Decimal('120.00'),
                'budget': Decimal('200.00'),
                'budget_remaining': Decimal('80.00'),
                'historical_difference': Decimal('-30.00'),
                'historical_percentage_change': Decimal('-20.00'),
            }
        )

        call_kwargs = (
            mock_client
            .interactions
            .create
            .call_args
            .kwargs
        )

        prompt = call_kwargs['input']

        self.assertIn(
            '150.00',
            prompt,
        )

        self.assertIn(
            '120.00',
            prompt,
        )

        self.assertIn(
            '200.00',
            prompt,
        )

        self.assertIn(
            '80.00',
            prompt,
        )

        self.assertIn(
            '-30.00',
            prompt,
        )

        self.assertIn(
            '-20.00',
            prompt,
        )

    @patch(
        'apps.lists.services.gemini.genai.Client'
    )
    def test_generate_with_gemini_returns_validated_insights(
            self,
            mock_client_class,
    ):
        mock_client = mock_client_class.return_value

        mock_response = (
            mock_client
            .interactions
            .create
            .return_value
        )

        mock_response.output_text = (
            '{'
            '"insights": ['
            '{'
            '"type": "spending",'
            '"title": "Compra abaixo da média",'
            '"message": "Sua compra atual está abaixo da média histórica.",'
            '"severity": "info"'
            '}'
            ']'
            '}'
        )

        insights = generate_with_gemini(
            context={
                'historical_average': Decimal('150.00'),
                'current_total': Decimal('120.00'),
                'budget': Decimal('200.00'),
                'budget_remaining': Decimal('80.00'),
                'historical_difference': Decimal('-30.00'),
                'historical_percentage_change': Decimal('-20.00'),
            }
        )

        self.assertEqual(
            insights,
            [
                {
                    'type': 'spending',
                    'title': 'Compra abaixo da média',
                    'message': (
                        'Sua compra atual está abaixo '
                        'da média histórica.'
                    ),
                    'severity': 'info',
                }
            ],
        )

    @patch(
        'apps.lists.services.gemini.genai.Client'
    )
    def test_generate_with_gemini_requests_structured_output(
            self,
            mock_client_class,
    ):
        mock_client = mock_client_class.return_value

        generate_with_gemini(
            context={
                'historical_average': Decimal('150.00'),
                'current_total': Decimal('120.00'),
                'budget': Decimal('200.00'),
                'budget_remaining': Decimal('80.00'),
                'historical_difference': Decimal('-30.00'),
                'historical_percentage_change': Decimal('-20.00'),
            }
        )

        call_kwargs = (
            mock_client
            .interactions
            .create
            .call_args
            .kwargs
        )

        response_format = call_kwargs[
            'response_format'
        ]

        self.assertEqual(
            response_format['type'],
            'text',
        )

        self.assertEqual(
            response_format['mime_type'],
            'application/json',
        )

        self.assertEqual(
            response_format['schema'],
            InsightResponseSchema.model_json_schema(),
        )

    @patch(
        'apps.lists.services.gemini.genai.Client'
    )
    def test_generate_with_gemini_returns_empty_list_on_provider_error(
            self,
            mock_client_class,
    ):
        mock_client = mock_client_class.return_value

        mock_client.interactions.create.side_effect = (
            Exception('Gemini unavailable')
        )

        insights = generate_with_gemini(
            context={
                'historical_average': Decimal('150.00'),
                'current_total': Decimal('120.00'),
                'budget': Decimal('200.00'),
                'budget_remaining': Decimal('80.00'),
                'historical_difference': Decimal('-30.00'),
                'historical_percentage_change': Decimal('-20.00'),
            }
        )

        self.assertEqual(
            insights,
            [],
        )

    @patch(
        'apps.lists.services.gemini.genai.Client'
    )
    def test_generate_with_gemini_returns_empty_list_on_invalid_response(
            self,
            mock_client_class,
    ):
        mock_client = mock_client_class.return_value

        mock_response = (
            mock_client
            .interactions
            .create
            .return_value
        )

        mock_response.output_text = None

        insights = generate_with_gemini(
            context={
                'historical_average': Decimal('150.00'),
                'current_total': Decimal('120.00'),
                'budget': Decimal('200.00'),
                'budget_remaining': Decimal('80.00'),
                'historical_difference': Decimal('-30.00'),
                'historical_percentage_change': Decimal('-20.00'),
            }
        )

        self.assertEqual(
            insights,
            [],
        )