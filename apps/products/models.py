from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Product(models.Model):
    SOURCE_MANUAL = 'manual'
    SOURCE_OPEN_FOOD_FACTS = 'openfoodfacts'

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Cadastro manual'),
        (SOURCE_OPEN_FOOD_FACTS, 'Open Food Facts'),
    ]

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    unit = models.CharField(
        max_length=50,
        blank=True,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='products',
    )
    brand = models.CharField(
        max_length=100,
        blank=True,
    )
    # Embalagem, ex.: "500 g", "1 L".
    package_size = models.CharField(
        max_length=50,
        blank=True,
    )
    # EAN-8, UPC-A, EAN-13 ou GTIN-14, só dígitos. Vazio quando não há.
    barcode = models.CharField(
        max_length=14,
        blank=True,
    )
    # Endereço da foto (do Open Food Facts); o arquivo não é baixado.
    image_url = models.URLField(
        max_length=500,
        blank=True,
    )
    # De onde vieram nome, marca, embalagem e foto. Open Food Facts exige
    # crédito na tela quando os dados são dele.
    data_source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['barcode'],
                condition=~models.Q(barcode=''),
                name='unique_product_barcode',
            ),
        ]

    def __str__(self):
        return self.name