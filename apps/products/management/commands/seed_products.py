from django.core.management.base import BaseCommand

from apps.products.models import Category, Product


class Command(BaseCommand):
    help = 'Cria categorias e produtos para desenvolvimento.'


    def handle(self, *args, **options):
        categories = {
            'Alimentos': [
                {
                    'name': 'Arroz',
                    'description': 'Arroz branco',
                    'price': '24.90',
                    'unit': '5 kg',
                },
                {
                    'name': 'Feijão',
                    'description': 'Feijão carioca',
                    'price': '8.90',
                    'unit': '1 kg',
                },
                {
                    'name': 'Macarrão',
                    'description': 'Macarrão tradicional',
                    'price': '5.90',
                    'unit': '500 g',
                },
            ],
            'Bebidas': [
                {
                    'name': 'Café',
                    'description': 'Café torrado e moído',
                    'price': '16.90',
                    'unit': '500 g',
                },
                {
                    'name': 'Suco',
                    'description': 'Suco de frutas',
                    'price': '7.90',
                    'unit': '1 L',
                },
            ],
            'Limpeza': [
                {
                    'name': 'Detergente',
                    'description': 'Detergente líquido',
                    'price': '2.99',
                    'unit': '500 ml',
                },
                {
                    'name': 'Sabão em pó',
                    'description': 'Sabão em pó para roupas',
                    'price': '18.90',
                    'unit': '1 kg',
                },
            ],
            'Higiene': [
                {
                    'name': 'Sabonete',
                    'description': 'Sabonete corporal',
                    'price': '3.99',
                    'unit': 'unidade',
                },
                {
                    'name': 'Pasta de dente',
                    'description': 'Creme dental',
                    'price': '6.90',
                    'unit': '90 g',
                },
            ],
            'Padaria': [
                {
                    'name': 'Pão de forma',
                    'description': 'Pão de forma tradicional',
                    'price': '9.90',
                    'unit': '500 g',
                },
            ],
        }

        total_categories = 0
        total_products = 0

        for category_name, products in categories.items():
            category, created = Category.objects.get_or_create(
                name=category_name,
            )

            if created:
                total_categories += 1

            for product_data in products:
                product, created = Product.objects.get_or_create(
                    name=product_data['name'],
                    defaults={
                        'description': product_data['description'],
                        'price': product_data['price'],
                        'unit': product_data['unit'],
                        'category': category,
                    },
                )

                if created:
                    total_products += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Categorias criadas: {total_categories}'
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Produtos criados: {total_products}'
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                'Seed de produtos concluído.'
            )
        )