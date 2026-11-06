from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.products.models import Product

from ...services.enriquecer import enriquecer_produtos


class Command(BaseCommand):
    help = (
        'Dá nome legível, tipo e foto aos produtos das notas fiscais que ainda '
        'não têm (a busca de fotos faz uma pausa de 6,5 s entre os pedidos).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=40, help='Máximo de produtos nesta rodada.')
        parser.add_argument('--sem-fotos', action='store_true', help='Só traduz os nomes.')

    def handle(self, *args, **options):
        pendentes = (
            Product.objects
            .filter(Q(data_source=Product.SOURCE_NFCE) | Q(observed_prices__isnull=False))
            .filter(
                Q(enriquecido_em__isnull=True)
                | Q(image_url='')
                | Q(foto_fonte='wikipedia', foto_internet_buscada_em__isnull=True)
            )
            .distinct()
            .order_by('-id')
            .values_list('id', flat=True)[:options['limite']]
        )
        resultado = enriquecer_produtos(list(pendentes), com_fotos=not options['sem_fotos'])
        self.stdout.write(
            f"Produtos vistos: {resultado['produtos']}. Fotos novas: {resultado['fotos']}."
        )
