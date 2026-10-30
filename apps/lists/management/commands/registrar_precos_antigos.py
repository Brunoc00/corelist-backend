"""Leva para os preços da cidade as notas importadas antes de 29/10/2026.

As compras importadas guardam a chave da nota (nfce_key), que traz o CNPJ da
loja. O preço de cada item comprado vira um preço observado, com a data da
compra. Pode rodar mais de uma vez: não duplica.

    python manage.py registrar_precos_antigos
"""

from django.core.management.base import BaseCommand

from apps.lists.models import List
from apps.lists.services.precos import loja_pela_chave, registrar_preco


class Command(BaseCommand):
    help = 'Registra nos preços da cidade os itens das notas já importadas.'

    def handle(self, *args, **options):
        total = 0
        listas = List.objects.exclude(nfce_key='').filter(is_completed=True)

        for lista in listas.prefetch_related('items__product'):
            loja = loja_pela_chave(lista.nfce_key, lista.store)
            nota = {'chave': lista.nfce_key, 'emitida_em': lista.completed_at}

            for item in lista.items.all():
                if not item.is_completed or item.price is None:
                    continue
                registrar_preco(loja, nota, {
                    'codigo_loja': '',
                    'preco': item.price,
                    'medida': item.measure,
                }, item.product)
                total += 1

        self.stdout.write(self.style.SUCCESS(f'Itens verificados: {total}.'))
