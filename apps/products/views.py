from django.db.models import ProtectedError
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, Product
from .permissions import CatalogPermission
from .serializers import CategorySerializer, ProductSerializer
from .services.openfoodfacts import (
    buscar_por_codigo,
    codigo_valido,
    normalizar_codigo,
)


class ProductLookupView(APIView):
    """Busca um produto pelo código de barras.

    1. Já está no catálogo: devolve o produto (sem consultar o Open Food Facts).
    2. Senão, devolve uma sugestão do Open Food Facts, sem salvar nada; o
       usuário confere e cadastra com POST products/.
    """

    def get(self, request):
        codigo = normalizar_codigo(request.query_params.get('barcode'))

        if not codigo_valido(codigo):
            return Response(
                {'detail': 'Código de barras inválido. Confira os números.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        produto = Product.objects.filter(barcode=codigo).first()

        if produto is not None:
            return Response({
                'origem': 'catalogo',
                'produto': ProductSerializer(produto).data,
            })

        sugestao = buscar_por_codigo(codigo)

        if sugestao is None:
            return Response(
                {
                    'detail': (
                        'Produto não encontrado pelo código de barras. '
                        'Cadastre manualmente.'
                    ),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            'origem': 'openfoodfacts',
            'sugestao': {
                **sugestao,
                'barcode': codigo,
                'data_source': Product.SOURCE_OPEN_FOOD_FACTS,
            },
        })


class ProductListCreateView(generics.ListCreateAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [CatalogPermission]


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [CatalogPermission]

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            # ListItem.product usa PROTECT: produto em lista não sai.
            return Response(
                {
                    'detail': (
                        'Este produto está em alguma lista e não pode ser '
                        'apagado.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )


class CategoryListCreateView(generics.ListCreateAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer