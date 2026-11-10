from django.core.management.base import BaseCommand

from apps.products.models import Product

from ...services.enriquecer import FONTE_ILUSTRATIVA, trocar_fotos_do_tipo


class Command(BaseCommand):
    help = (
        'Troca a foto ilustrativa (Wikipédia) dos produtos cujo tipo tem uma '
        'foto escolhida à mão em FOTO_DO_TIPO. Não usa a rede.'
    )

    def handle(self, *args, **options):
        produtos = Product.objects.filter(foto_fonte=FONTE_ILUSTRATIVA)
        self.stdout.write(f'Fotos trocadas: {trocar_fotos_do_tipo(produtos)}.')
