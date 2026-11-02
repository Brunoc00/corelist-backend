"""Coordenadas das lojas que ainda não têm (roda no deploy).

Consulta o OpenStreetMap uma loja por vez, respeitando o limite de 1 consulta
por segundo. Lojas já tentadas não são consultadas de novo.
"""

import time

from django.core.management.base import BaseCommand

from apps.products.models import Store
from apps.products.services.geocodificar import PAUSA_ENTRE_CONSULTAS, geocodificar_loja


class Command(BaseCommand):
    help = 'Busca latitude/longitude das lojas pelo endereço (OpenStreetMap).'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=50)

    def handle(self, *args, **opcoes):
        lojas = Store.objects.filter(latitude__isnull=True, geocoded_at__isnull=True).exclude(address='')
        lojas = list(lojas[:opcoes['limite']])
        achadas = 0
        for indice, loja in enumerate(lojas):
            if indice:
                time.sleep(PAUSA_ENTRE_CONSULTAS)
            if geocodificar_loja(loja):
                achadas += 1
        self.stdout.write(f'Lojas consultadas: {len(lojas)}; com coordenadas: {achadas}.')
