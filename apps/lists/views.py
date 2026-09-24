from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import List, ListItem
from .serializers import ListItemSerializer, ListSerializer


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
        return List.objects.filter(owner=self.request.user)


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
        )

        serializer.save(list=shopping_list)


class ListItemDetailView(generics.RetrieveUpdateDestroyAPIView):
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
