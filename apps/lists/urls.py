from django.urls import path

from .views import ListDetailView, ListListCreateView, ListItemListCreateView


urlpatterns = [
    path('', ListListCreateView.as_view(), name='list-list-create'),
    path('<int:pk>/', ListDetailView.as_view(), name='list-detail'),
path('<int:list_id>/items/',ListItemListCreateView.as_view(),name='list-item-list-create'),
]