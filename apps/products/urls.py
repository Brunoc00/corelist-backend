from django.urls import path

from .views import (
    CategoryListCreateView,
    ProductDetailView,
    ProductListCreateView,
    ProductLookupView,
)


urlpatterns = [
    path(
        'categories/',
        CategoryListCreateView.as_view(),
        name='category-list-create',
    ),
    path(
        'lookup/',
        ProductLookupView.as_view(),
        name='product-lookup',
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