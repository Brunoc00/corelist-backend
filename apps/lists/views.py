from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .services.promotions import get_promotions

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import List, ListItem
from .serializers import (
    ListItemSerializer,
    ListSerializer,
    ListSummarySerializer,
)
from .services.busca import buscar
from .services.insights import generate_insights


class ListListCreateView(generics.ListCreateAPIView):
    serializer_class = ListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return List.objects.filter(
            owner=self.request.user,
        )

    def perform_create(self, serializer):
        serializer.save(
            owner=self.request.user,
        )


class ListDetailView(
    generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = ListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        listas = List.objects.filter(owner=self.request.user)

        # Compra concluída não é lida nem editada aqui (só pelo histórico),
        # mas pode ser excluída, por exemplo uma nota importada por engano.
        if self.request.method == 'DELETE':
            return listas

        return listas.filter(is_completed=False)


class ListCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        shopping_list = get_object_or_404(
            List,
            id=pk,
            owner=request.user,
        )

        if shopping_list.is_completed:
            return Response(
                {
                    'detail': 'Esta lista já está concluída.'
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        shopping_list.is_completed = True
        shopping_list.completed_at = timezone.now()

        shopping_list.save(
            update_fields=[
                'is_completed',
                'completed_at',
                'updated_at',
            ]
        )

        serializer = ListSerializer(
            shopping_list,
            context={
                'request': request,
            },
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )


# Os itens continuam editáveis depois que a lista é concluída, para o
# usuário corrigir preços e quantidades da compra. A lista em si
# (nome, orçamento, exclusão) fica bloqueada em ListDetailView.
class ListItemListCreateView(
    generics.ListCreateAPIView
):
    serializer_class = ListItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ListItem.objects.filter(
            list_id=self.kwargs['list_id'],
            list__owner=self.request.user,
        )

    def perform_create(self, serializer):
        shopping_list = get_object_or_404(
            List,
            id=self.kwargs['list_id'],
            owner=self.request.user,
        )

        serializer.save(
            list=shopping_list,
        )


class ListItemDetailView(
    generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = ListItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ListItem.objects.filter(
            list__owner=self.request.user,
            list_id=self.kwargs['list_id'],
        )


class ListHistoryView(generics.ListAPIView):
    serializer_class = ListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return List.objects.filter(
            owner=self.request.user,
            is_completed=True,
        )


class ListSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        completed_items = ListItem.objects.filter(
            list__owner=request.user,
            list__is_completed=True,
        )

        total_spent = completed_items.aggregate(
            total=Coalesce(
                Sum(
                    F('quantity') * F('price')
                ),
                Decimal('0.00'),
            )
        )

        lists_count = List.objects.filter(
            owner=request.user,
            is_completed=True,
        ).count()

        if lists_count > 0:
            average_purchase = (
                total_spent['total']
                / lists_count
            )
        else:
            average_purchase = Decimal('0.00')

        monthly_query = (
            completed_items
            .filter(
                list__completed_at__isnull=False,
            )
            .annotate(
                month=TruncMonth(
                    'list__completed_at'
                )
            )
            .values('month')
            .annotate(
                total=Sum(
                    F('quantity') * F('price')
                )
            )
            .order_by('month')
        )

        monthly = [
            {
                'month': item['month'].strftime(
                    '%Y-%m'
                ),
                'total': item['total'],
            }
            for item in monthly_query
        ]

        categories_query = (
            completed_items
            .filter(
                product__category__isnull=False,
            )
            .values(
                'product__category__name',
            )
            .annotate(
                total=Sum(
                    F('quantity') * F('price')
                )
            )
            .order_by(
                'product__category__name',
            )
        )

        categories = [
            {
                'category': item[
                    'product__category__name'
                ],
                'total': item['total'],
            }
            for item in categories_query
        ]

        top_products_query = (
            completed_items
            .values(
                'product__name',
            )
            .annotate(
                quantity=Sum('quantity')
            )
            .order_by(
                '-quantity',
                'product__name',
            )
        )

        top_products = [
            {
                'product': item[
                    'product__name'
                ],
                'quantity': item['quantity'],
            }
            for item in top_products_query
        ]

        if len(monthly) >= 2:
            previous_period = monthly[-2]
            current_period = monthly[-1]

            previous_total = (
                previous_period['total']
            )

            current_total = (
                current_period['total']
            )

            difference = (
                current_total
                - previous_total
            )

            if previous_total != 0:
                percentage_change = (
                    difference
                    / previous_total
                    * Decimal('100')
                )
            else:
                percentage_change = (
                    Decimal('0.00')
                )

            period_comparison = {
                'previous_month':
                    previous_period['month'],
                'previous_total':
                    previous_total,
                'current_month':
                    current_period['month'],
                'current_total':
                    current_total,
                'difference':
                    difference,
                'percentage_change':
                    percentage_change,
            }

        elif len(monthly) == 1:
            current_period = monthly[0]

            period_comparison = {
                'previous_month': '',
                'previous_total':
                    Decimal('0.00'),
                'current_month':
                    current_period['month'],
                'current_total':
                    current_period['total'],
                'difference':
                    current_period['total'],
                'percentage_change':
                    Decimal('0.00'),
            }

        else:
            period_comparison = {
                'previous_month': '',
                'previous_total':
                    Decimal('0.00'),
                'current_month': '',
                'current_total':
                    Decimal('0.00'),
                'difference':
                    Decimal('0.00'),
                'percentage_change':
                    Decimal('0.00'),
            }

        summary = {
            'total': total_spent['total'],
            'lists_count': lists_count,
            'average_purchase': average_purchase,
            'monthly': monthly,
            'categories': categories,
            'top_products': top_products,
            'period_comparison':
                period_comparison,
        }

        serializer = ListSummarySerializer(
            summary,
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )


class ListInsightsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        insights = generate_insights(
            user=request.user,
        )

        return Response(
            {
                'insights': insights,
            },
            status=status.HTTP_200_OK,
        )


class ListPromotionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        promotions = get_promotions(
            user=request.user,
        )

        return Response(
            {
                'promotions': promotions,
            },
            status=status.HTTP_200_OK,
        )

class ProdutoBuscaView(APIView):
    """GET lists/produtos/?q=arr: produtos para adicionar à lista, com o
    último preço que a pessoa pagou. Sem q, os que ela mais compra."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(buscar(request.user, request.query_params.get('q', '')))


class ListRepetirView(APIView):
    """POST lists/<id>/repetir/ {nome?, itens?: [ids]}: cria uma lista nova
    em andamento com os produtos de uma compra (a próxima compra).

    Copia produto, quantidade e medida; o preço pago vira o preço planejado.
    Sem `itens`, copia todos os itens comprados.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        origem = get_object_or_404(List, pk=pk, owner=request.user)

        ids = request.data.get('itens')
        itens = origem.items.select_related('product').order_by('id')
        if ids is None:
            itens = itens.filter(is_completed=True)
        else:
            if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
                return Response(
                    {'detail': 'Informe os itens como uma lista de números.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            itens = itens.filter(id__in=ids)

        itens = list(itens)
        if not itens:
            return Response(
                {'detail': 'Escolha ao menos um produto para a próxima lista.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        nome = str(request.data.get('nome') or '').strip()[:200] or 'Próxima compra'

        with transaction.atomic():
            nova = List.objects.create(owner=request.user, name=nome, budget=origem.budget)
            ListItem.objects.bulk_create([
                ListItem(
                    list=nova,
                    product=item.product,
                    quantity=item.quantity,
                    measure=item.measure,
                    price=item.price,
                )
                for item in itens
            ])

        return Response(
            ListSerializer(nova, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )
