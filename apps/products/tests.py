from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.products.models import Category, Product

User = get_user_model()


class ProductTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            email='test@example.com',
            password='StrongPassword123!',
        )

        self.client.force_authenticate(user=self.user)

        self.category = Category.objects.create(
            name='Alimentos',
        )

        self.product = Product.objects.create(
            name='Arroz',
            description='Arroz branco tipo 1, pacote de 5kg',
            price=27.50,
            category=self.category,
        )

    def test_user_can_list_products(self):
        response = self.client.get(
            '/api/products/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]['name'],
            'Arroz',
        )

    def test_user_can_create_product(self):
        response = self.client.post(
            '/api/products/',
            {
                'name': 'Leite',
                'description': 'Leite integral 1 litro',
                'price': 6.50,
                'category': self.category.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['name'], 'Leite')
        self.assertEqual(response.data['price'], '6.50')
        self.assertEqual(
            response.data['category'],
            self.category.id,
        )

    def test_user_can_retrieve_product(self):
        response = self.client.get(
            f'/api/products/{self.product.id}/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['id'],
            self.product.id,
        )
        self.assertEqual(
            response.data['name'],
            'Arroz',
        )

    def tornar_admin(self):
        self.user.is_staff = True
        self.user.save()

    def test_regular_user_cannot_update_product(self):
        response = self.client.patch(
            f'/api/products/{self.product.id}/',
            {
                'price': '30.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 403)

        self.product.refresh_from_db()

        self.assertEqual(str(self.product.price), '27.50')

    def test_regular_user_cannot_delete_product(self):
        response = self.client.delete(
            f'/api/products/{self.product.id}/'
        )

        self.assertEqual(response.status_code, 403)

        self.assertTrue(
            Product.objects.filter(
                id=self.product.id
            ).exists()
        )

    def test_admin_can_update_product(self):
        self.tornar_admin()

        response = self.client.patch(
            f'/api/products/{self.product.id}/',
            {
                'price': '30.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['price'], '30.00')

    def test_admin_can_delete_product(self):
        self.tornar_admin()

        response = self.client.delete(
            f'/api/products/{self.product.id}/'
        )

        self.assertEqual(response.status_code, 204)

        self.assertFalse(
            Product.objects.filter(
                id=self.product.id
            ).exists()
        )

    def test_admin_cannot_delete_product_used_in_a_list(self):
        from apps.lists.models import List, ListItem

        self.tornar_admin()

        lista = List.objects.create(name='Mercado', owner=self.user)
        ListItem.objects.create(list=lista, product=self.product)

        response = self.client.delete(
            f'/api/products/{self.product.id}/'
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn('detail', response.data)
        self.assertTrue(
            Product.objects.filter(id=self.product.id).exists()
        )

    def test_unauthenticated_user_cannot_list_products(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            '/api/products/'
        )

        self.assertEqual(response.status_code, 401)

    def test_user_cannot_create_product_without_name(self):
        response = self.client.post(
            '/api/products/',
            {
                'description': 'Produto sem nome',
                'price': 10.00,
                'category': self.category.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'name',
            response.data,
        )

    def test_user_cannot_create_product_with_invalid_price(self):
        response = self.client.post(
            '/api/products/',
            {
                'name': 'Produto inválido',
                'description': 'Preço inválido',
                'price': 'abc',
                'category': self.category.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'price',
            response.data,
        )

    def test_user_cannot_create_product_with_invalid_category(self):
        response = self.client.post(
            '/api/products/',
            {
                'name': 'Produto inválido',
                'description': 'Categoria inexistente',
                'price': 10.00,
                'category': 9999,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'category',
            response.data,
        )

    def test_user_cannot_update_product_with_invalid_category(self):
        self.tornar_admin()

        response = self.client.patch(
            f'/api/products/{self.product.id}/',
            {
                'category': 9999,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'category',
            response.data,
        )

    def test_user_can_create_product_with_unit(self):
        response = self.client.post(
            '/api/products/',
            {
                'name': 'Arroz',
                'description': 'Arroz branco',
                'price': 12.00,
                'unit': '5 kg',
                'category': self.category.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['name'], 'Arroz')
        self.assertEqual(response.data['unit'], '5 kg')

    def test_user_can_list_categories(self):
        response = self.client.get(
            '/api/categories/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]['name'],
            'Alimentos',
        )

    def test_user_can_create_category(self):
        response = self.client.post(
            '/api/categories/',
            {
                'name': 'Bebidas',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data['name'],
            'Bebidas',
        )

    def test_unauthenticated_user_cannot_list_categories(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            '/api/categories/'
        )

        self.assertEqual(response.status_code, 401)

    def test_unauthenticated_user_cannot_create_category(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            '/api/categories/',
            {
                'name': 'Bebidas',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 401)

    def test_user_cannot_create_duplicate_category(self):
        response = self.client.post(
            '/api/categories/',
            {
                'name': 'alimentos',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)
