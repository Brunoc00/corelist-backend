from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Product(models.Model):
    SOURCE_MANUAL = 'manual'
    SOURCE_OPEN_FOOD_FACTS = 'openfoodfacts'
    SOURCE_OPEN_BEAUTY_FACTS = 'openbeautyfacts'
    SOURCE_OPEN_PRODUCTS_FACTS = 'openproductsfacts'
    SOURCE_NFCE = 'nfce'

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Cadastro manual'),
        (SOURCE_OPEN_FOOD_FACTS, 'Open Food Facts'),
        (SOURCE_OPEN_BEAUTY_FACTS, 'Open Beauty Facts'),
        (SOURCE_OPEN_PRODUCTS_FACTS, 'Open Products Facts'),
        (SOURCE_NFCE, 'Nota fiscal'),
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

class Store(models.Model):
    """Loja (filial) identificada pelo CNPJ da nota fiscal."""

    cnpj = models.CharField(max_length=14, unique=True)  # só dígitos
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} ({self.cnpj})'


class StoreProductCode(models.Model):
    """O que o app aprendeu: o código interno de uma loja é este produto.

    Gravado quando alguém confirma uma nota fiscal; nas próximas notas
    daquela loja, o item já vem ligado ao produto.
    """

    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='product_codes',
    )
    store_code = models.CharField(max_length=60)
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='store_codes',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'store_code'],
                name='unique_codigo_por_loja',
            ),
        ]

    def __str__(self):
        return f'{self.store_id}:{self.store_code} -> {self.product_id}'


class ObservedPrice(models.Model):
    """Preço que apareceu numa nota fiscal: base dos preços da cidade.

    Não guarda quem comprou, só loja, produto, preço e data da nota. A mesma
    nota importada por duas pessoas não duplica o preço.
    """

    MEASURE_UNIT = 'unit'
    MEASURE_KG = 'kg'

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='observed_prices',
    )
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='observed_prices',
    )
    store_code = models.CharField(max_length=60, blank=True, default='')
    # Preço da unidade, ou do kg quando measure = 'kg'.
    price = models.DecimalField(max_digits=10, decimal_places=2)
    measure = models.CharField(max_length=10, default=MEASURE_UNIT)
    # Data de emissão da nota (quando o preço valia).
    observed_at = models.DateTimeField()
    nfce_key = models.CharField(max_length=44)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['product', '-observed_at']),
            models.Index(fields=['store', 'product', '-observed_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['nfce_key', 'product', 'store_code', 'price'],
                name='preco_unico_por_nota',
            ),
        ]

    def __str__(self):
        return f'{self.product_id} @ {self.store_id}: {self.price} ({self.observed_at:%d/%m/%Y})'
