from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.lists.models import List, ListItem
from apps.products.models import Product

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
