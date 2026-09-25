from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.lists.models import List, ListItem
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
        self.assertEqual(response.data['product'], self.product.id)
        self.assertEqual(response.data['quantity'], '3.00')

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
        self.assertEqual(response.data['quantity'], '2.00')

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
            ListItem.objects.filter(id=self.item.id).exists()
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
            ListItem.objects.filter(id=self.item.id).exists()
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
        self.assertIsNotNone(response.data['completed_at'])

        self.shopping_list.refresh_from_db()

        self.assertTrue(self.shopping_list.is_completed)
        self.assertIsNotNone(self.shopping_list.completed_at)

    def test_user_cannot_complete_another_users_list(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.post(
            f'/api/lists/{self.shopping_list.id}/complete/'
        )

        self.assertEqual(response.status_code, 404)

        self.shopping_list.refresh_from_db()

        self.assertFalse(self.shopping_list.is_completed)
        self.assertIsNone(self.shopping_list.completed_at)

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

    def test_user_cannot_see_another_users_completed_list_history(self):
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

        response = self.client.get('/api/lists/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], '60.00')
        self.assertEqual(response.data['lists_count'], 1)
        self.assertEqual(response.data['average_purchase'], '60.00')

    def test_summary_returns_zero_when_there_are_no_completed_lists(self):
        response = self.client.get('/api/lists/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], '0.00')
        self.assertEqual(response.data['lists_count'], 0)
        self.assertEqual(response.data['average_purchase'], '0.00')

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

        response = self.client.get('/api/lists/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], '100.00')
        self.assertEqual(response.data['lists_count'], 2)
        self.assertEqual(response.data['average_purchase'], '50.00')

    def test_summary_returns_monthly_spending(self):
        self.item.price = 30
        self.item.save()

        self.shopping_list.is_completed = True
        self.shopping_list.completed_at = timezone.make_aware(
            datetime(2026, 8, 15, 12, 0)
        )
        self.shopping_list.save()

        second_list = List.objects.create(
            name='Compra de setembro',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.make_aware(
                datetime(2026, 9, 15, 12, 0)
            ),
        )

        ListItem.objects.create(
            list=second_list,
            product=self.product,
            quantity=2,
            price=20,
        )

        response = self.client.get('/api/lists/summary/')

        self.assertEqual(response.status_code, 200)

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

        self.assertEqual(response.status_code, 200)

        self.shopping_list.refresh_from_db()

        self.assertFalse(self.shopping_list.is_completed)
        self.assertIsNone(self.shopping_list.completed_at)

    def test_user_cannot_update_item_from_completed_list(self):
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

        self.assertEqual(response.status_code, 404)

        self.item.refresh_from_db()

        self.assertEqual(
            self.item.price,
            30,
        )

    def test_user_cannot_delete_item_from_completed_list(self):
        self.shopping_list.is_completed = True
        self.shopping_list.save()

        response = self.client.delete(
            f'/api/lists/{self.shopping_list.id}/items/{self.item.id}/'
        )

        self.assertEqual(response.status_code, 404)

        self.assertTrue(
            ListItem.objects.filter(id=self.item.id).exists()
        )

    def test_user_cannot_create_item_in_completed_list(self):
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

        self.assertEqual(response.status_code, 404)

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

        self.assertEqual(response.status_code, 404)

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

        self.assertEqual(response.status_code, 404)

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

        response = self.client.get('/api/lists/summary/')

        self.assertEqual(response.status_code, 200)

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

        response = self.client.get('/api/lists/summary/')

        self.assertEqual(response.status_code, 200)

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
        self.shopping_list.completed_at = timezone.make_aware(
            datetime(2026, 8, 15, 12, 0)
        )
        self.shopping_list.save()

        second_list = List.objects.create(
            name='Compra de setembro',
            owner=self.user,
            is_completed=True,
            completed_at=timezone.make_aware(
                datetime(2026, 9, 15, 12, 0)
            ),
        )

        ListItem.objects.create(
            list=second_list,
            product=self.product,
            quantity=2,
            price=40,
        )

        response = self.client.get('/api/lists/summary/')

        self.assertEqual(response.status_code, 200)

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
