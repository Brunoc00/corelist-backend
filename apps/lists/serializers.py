from rest_framework import serializers

from .models import List, ListItem


class ListItemSerializer(serializers.ModelSerializer):
    subtotal = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = ListItem
        fields = [
            'id',
            'list',
            'product',
            'quantity',
            'price',
            'subtotal',
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


class ListSerializer(serializers.ModelSerializer):
    items = ListItemSerializer(
        many=True,
        read_only=True,
    )

    total = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = List
        fields = [
            'id',
            'name',
            'budget',
            'is_completed',
            'completed_at',
            'items',
            'total',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'is_completed',
            'completed_at',
            'created_at',
            'updated_at',
        ]


class MonthlySummarySerializer(serializers.Serializer):
    month = serializers.CharField()

    total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )


class CategorySummarySerializer(serializers.Serializer):
    category = serializers.CharField()

    total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )


class TopProductSummarySerializer(serializers.Serializer):
    product = serializers.CharField()

    quantity = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )


class PeriodComparisonSerializer(serializers.Serializer):
    previous_month = serializers.CharField()

    previous_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    current_month = serializers.CharField()

    current_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    difference = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    percentage_change = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )


class ListSummarySerializer(serializers.Serializer):
    total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    lists_count = serializers.IntegerField()

    average_purchase = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    monthly = MonthlySummarySerializer(
        many=True,
    )

    categories = CategorySummarySerializer(
        many=True,
    )

    top_products = TopProductSummarySerializer(
        many=True,
    )

    period_comparison = PeriodComparisonSerializer()