from django.conf import settings
from django.db import models

from apps.products.models import Product


class List(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lists',
    )
    name = models.CharField(max_length=200)
    budget = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    # Compra importada da nota fiscal (NFC-e): loja e chave de acesso.
    store = models.CharField(max_length=200, blank=True, default='')
    nfce_key = models.CharField(max_length=44, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # A mesma nota não entra duas vezes no histórico do usuário.
            models.UniqueConstraint(
                fields=['owner', 'nfce_key'],
                condition=~models.Q(nfce_key=''),
                name='unique_nfce_por_usuario',
            ),
        ]

    @property
    def total(self):
        total = 0

        for item in self.items.all():
            if item.subtotal is not None:
                total += item.subtotal

        return total

    def __str__(self):
        return self.name


class ListItem(models.Model):
    list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name='items',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='list_items',
    )
    # 3 casas: peso em kg com gramas (0,724 kg), como vem na nota fiscal.
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=1,
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    # Como o usuário compra: por unidade (2 pacotes) ou por peso (1,5 kg).
    # Em "kg", quantity é o peso e price é o preço do kg.
    MEASURE_UNIT = 'unit'
    MEASURE_KG = 'kg'
    MEASURE_CHOICES = [
        (MEASURE_UNIT, 'Unidade'),
        (MEASURE_KG, 'Peso (kg)'),
    ]
    measure = models.CharField(
        max_length=10,
        choices=MEASURE_CHOICES,
        default=MEASURE_UNIT,
    )
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def subtotal(self):
        if self.price is None:
            return None

        return self.quantity * self.price

    def __str__(self):
        return f'{self.product.name} - {self.list.name}'
