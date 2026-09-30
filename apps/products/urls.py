from django.urls import path

from .views import (
    CategoryListCreateView,
    ProductDetailView,
    ProductListCreateView,
)


urlpatterns = [
    path(
        'categories/',
        CategoryListCreateView.as_view(),
        name='category-list-create',
    ),
    path(
        '',
        ProductListCreateView.as_view(),
        name='product-list-create',
    ),
    path(
        '<int:pk>/',
        ProductDetailView.as_view(),
        name='product-detail',
    ),
]