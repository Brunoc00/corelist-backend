from django.core.management.base import BaseCommand

from apps.products.models import Product
from apps.products.services.categorizar import categoria_pelo_nome


class Command(BaseCommand):
    help = 'Dá categoria, pelo nome, aos produtos sem categoria (ex.: os das notas fiscais).'

    def handle(self, *args, **options):
        feitos = 0
        for produto in Product.objects.filter(category__isnull=True):
            categoria = categoria_pelo_nome(produto.name)
            if categoria is not None:
                produto.category = categoria
                produto.save(update_fields=['category'])
                feitos += 1
        self.stdout.write(f'Produtos com categoria nova: {feitos}.')
