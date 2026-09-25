from django.urls import path

from .views import (ListDetailView, ListListCreateView, ListItemListCreateView,
                    ListItemDetailView,
                    ListCompleteView,
                    ListHistoryView,
                    ListSummaryView)


urlpatterns = [
    path('', ListListCreateView.as_view(), name='list-list-create'),
    path('<int:pk>/', ListDetailView.as_view(), name='list-detail'),
    path('<int:list_id>/items/', ListItemListCreateView.as_view(), name='list-item-list-create'),
    path('<int:list_id>/items/<int:pk>/', ListItemDetailView.as_view(), name='list-item-detail', ),
    path('<int:pk>/complete/',ListCompleteView.as_view(),name='list-complete',),
    path('history/',ListHistoryView.as_view(),name='list-history',),
    path('summary/',ListSummaryView.as_view(),name='list-summary',),

]
