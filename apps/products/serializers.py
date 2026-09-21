from rest_framework import serializers

from .models import Category, Product


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

        if Category.objects.filter(name__iexact=value).exists():
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
            'created_at',
            'updated_at',
        ]