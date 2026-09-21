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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1,
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
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
