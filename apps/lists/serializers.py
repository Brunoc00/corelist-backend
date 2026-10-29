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
            'measure',
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
            'store',
            'nfce_key',
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
            'store',
            'nfce_key',
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

class NfceLinkSerializer(serializers.Serializer):
    link = serializers.CharField(max_length=2000)


class NfceItemImportSerializer(serializers.Serializer):
    nome = serializers.CharField(max_length=200)
    quantidade = serializers.DecimalField(
        max_digits=10, decimal_places=3, min_value=Decimal('0.001'),
    )
    medida = serializers.ChoiceField(
        choices=[ListItem.MEASURE_UNIT, ListItem.MEASURE_KG],
    )
    preco = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0'),
    )


class NfceImportSerializer(NfceLinkSerializer):
    nome = serializers.CharField(max_length=200, required=False, allow_blank=True)
    # Itens conferidos pelo usuário. Sem eles, entram os itens da nota.
    itens = NfceItemImportSerializer(many=True, required=False)

    def validate_itens(self, itens):
        if not itens:
            raise serializers.ValidationError('Informe ao menos um item.')
        return itens


class NfceEscolhaSerializer(serializers.Serializer):
    """O que a pessoa confirmou para um item da nota, na conferência."""

    indice = serializers.IntegerField(min_value=0)
    # Item da lista que corresponde a este item da nota (ou nenhum: fora da lista).
    list_item = serializers.IntegerField(required=False, allow_null=True)
    # Produto do catálogo, para itens fora da lista (ou nenhum: cria pelo nome).
    produto = serializers.IntegerField(required=False, allow_null=True)
    nome = serializers.CharField(max_length=200, required=False, allow_blank=True)


class NfceConcluirNaListaSerializer(NfceLinkSerializer):
    itens = NfceEscolhaSerializer(many=True)

    def validate_itens(self, itens):
        if not itens:
            raise serializers.ValidationError('Informe ao menos um item da nota.')

        indices = [item['indice'] for item in itens]
        if len(indices) != len(set(indices)):
            raise serializers.ValidationError('Item da nota repetido.')

        ligados = [item['list_item'] for item in itens if item.get('list_item')]
        if len(ligados) != len(set(ligados)):
            raise serializers.ValidationError(
                'Um item da lista foi ligado a dois itens da nota.'
            )
        return itens
