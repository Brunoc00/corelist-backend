from decimal import Decimal

from django.db.models import F, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone

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


class ListListCreateView(generics.ListCreateAPIView):
    serializer_class = ListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return List.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ListDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return List.objects.filter(
            owner=self.request.user,
            is_completed=False,
        )


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
            context={'request': request},
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )


class ListItemListCreateView(generics.ListCreateAPIView):
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
            is_completed=False,
        )

        serializer.save(list=shopping_list)


class ListItemDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ListItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ListItem.objects.filter(
            list__owner=self.request.user,
            list_id=self.kwargs['list_id'],
            list__is_completed=False,
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
                    total_spent['total'] / lists_count
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
                'month': item['month'].strftime('%Y-%m'),
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
                'category': item['product__category__name'],
                'total': item['total'],
            }
            for item in categories_query
        ]

        summary = {
            'total': total_spent['total'],
            'lists_count': lists_count,
            'average_purchase': average_purchase,
            'monthly': monthly,
            'categories': categories,
        }

        serializer = ListSummarySerializer(summary)

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )
