from decimal import Decimal

from django.db.models import Avg, F, Sum
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


class HistoryComparisonSerializer(serializers.Serializer):
    current_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    historical_average = serializers.DecimalField(
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

    history_comparison = serializers.SerializerMethodField()

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
            'history_comparison',
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

    def get_history_comparison(self, obj):
        completed_lists = (
            List.objects
            .filter(
                owner=obj.owner,
                is_completed=True,
            )
            .exclude(id=obj.id)
            .annotate(
                calculated_total=Sum(
                    F('items__quantity') * F('items__price')
                )
            )
        )

        historical_data = completed_lists.aggregate(
            average=Avg('calculated_total')
        )

        historical_average = (
            historical_data['average']
            or Decimal('0.00')
        )

        current_total = obj.total

        difference = (
            current_total - historical_average
        )

        if historical_average != 0:
            percentage_change = (
                difference
                / historical_average
                * Decimal('100')
            )
        else:
            percentage_change = Decimal('0.00')

        comparison = {
            'current_total': current_total,
            'historical_average': historical_average,
            'difference': difference,
            'percentage_change': percentage_change,
        }

        serializer = HistoryComparisonSerializer(
            comparison
        )

        return serializer.data


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