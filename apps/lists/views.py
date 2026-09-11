from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

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


class ListItemListCreateView(generics.ListCreateAPIView):
    serializer_class = ListItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ListItem.objects.filter(
            list_id=self.kwargs['list_id'],
            list__owner=self.request.user,
        )

    def perform_create(self, serializer):
        shopping_list = List.objects.get(
            id=self.kwargs['list_id'],
            owner=self.request.user,
        )

        serializer.save(list=shopping_list)