from rest_framework import serializers

from .models import List, ListItem


class ListSerializer(serializers.ModelSerializer):
    class Meta:
        model = List
        fields = [
            'id',
            'name',
            'budget',
            'created_at',
            'updated_at',
        ]


class ListItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ListItem
        fields = [
            'id',
            'list',
            'product',
            'quantity',
            'price',
            'is_completed',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'list',
            'created_at',
            'updated_at',
        ]

    def validate_list(self, value):
        request = self.context.get('request')

        if request is None:
            raise serializers.ValidationError(
                'O contexto da requisição é obrigatório.'
            )

        if value.owner != request.user:
            raise serializers.ValidationError(
                'Você não tem permissão para usar esta lista.'
            )

        return value