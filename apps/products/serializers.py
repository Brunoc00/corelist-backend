from rest_framework import serializers

from .models import Category, Product
from .services.openfoodfacts import codigo_valido, normalizar_codigo


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                'O nome da categoria é obrigatório.'
            )

        if Category.objects.filter(
            name__iexact=value
        ).exists():
            raise serializers.ValidationError(
                'Esta categoria já existe.'
            )

        return value


class ProductSerializer(serializers.ModelSerializer):
    # O site mostra o botão de remover só quando dá para remover.
    pode_remover = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id',
            'name',
            'description',
            'price',
            'unit',
            'category',
            'brand',
            'package_size',
            'barcode',
            'image_url',
            'data_source',
            'created_at',
            'updated_at',
            'pode_remover',
        ]
        # A unicidade do código é checada em validate_barcode, com mensagem
        # em português.
        validators = []

    def get_pode_remover(self, produto):
        request = self.context.get('request')
        usuario = getattr(request, 'user', None)
        if not usuario or not usuario.is_authenticated:
            return False
        if not usuario.is_staff and produto.created_by_id != usuario.id:
            return False
        # Em lista (PROTECT) não sai; com preço de nota, só o administrador.
        # A listagem já traz em_lista/tem_preco anotados (evita uma consulta
        # por produto).
        em_lista = getattr(produto, 'em_lista', None)
        if em_lista is None:
            em_lista = produto.list_items.exists()
        if em_lista:
            return False
        if usuario.is_staff:
            return True
        tem_preco = getattr(produto, 'tem_preco', None)
        if tem_preco is None:
            tem_preco = produto.observed_prices.exists()
        return not tem_preco

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                'O nome do produto é obrigatório.'
            )

        return value

    def validate_barcode(self, value):
        value = normalizar_codigo(value)

        if not value:
            return ''

        if not codigo_valido(value):
            raise serializers.ValidationError(
                'Código de barras inválido. Confira os números.'
            )

        repetidos = Product.objects.filter(barcode=value)

        if self.instance is not None:
            repetidos = repetidos.exclude(pk=self.instance.pk)

        if repetidos.exists():
            raise serializers.ValidationError(
                'Já existe um produto com este código de barras.'
            )

        return value

    def validate_image_url(self, value):
        value = (value or '').strip()

        if value and not value.startswith('https://'):
            raise serializers.ValidationError(
                'A foto precisa ser um endereço https.'
            )

        return value