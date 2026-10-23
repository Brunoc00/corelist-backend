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
        ]
        # A unicidade do código é checada em validate_barcode, com mensagem
        # em português.
        validators = []

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