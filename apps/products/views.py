from django.db.models import Exists, OuterRef, ProtectedError
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.lists.models import ListItem

from .models import Category, ObservedPrice, Product, ProdutoEscondido
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

        # sugestao já traz data_source (a base onde o produto foi achado).
        return Response({
            'origem': 'openfoodfacts',
            'sugestao': {
                **sugestao,
                'barcode': codigo,
            },
        })


class ProductListCreateView(generics.ListCreateAPIView):
    queryset = Product.objects.annotate(
        em_lista=Exists(ListItem.objects.filter(product=OuterRef('pk'))),
        tem_preco=Exists(ObservedPrice.objects.filter(product=OuterRef('pk'))),
    )
    serializer_class = ProductSerializer
    permission_classes = [CatalogPermission]

    def get_queryset(self):
        # Some da tela quem a pessoa escondeu com a lixeira.
        return super().get_queryset().exclude(
            escondido_por__usuario=self.request.user,
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [CatalogPermission]

    def destroy(self, request, *args, **kwargs):
        produto = self.get_object()
        # Quem não é administrador só remove o próprio cadastro feito por
        # engano: produto com preço de nota fiscal alimenta os preços da cidade.
        if not request.user.is_staff and produto.observed_prices.exists():
            return Response(
                {
                    'detail': (
                        'Este produto tem preços de notas fiscais e não pode '
                        'ser apagado.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )
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

class ProductBarcodeView(APIView):
    """POST products/<id>/codigo-de-barras/ {barcode}: completa um produto
    (ex.: um que veio da nota fiscal) lendo o código de barras da embalagem.

    Qualquer pessoa logada pode usar, mas só preenche o que está vazio
    (código, foto, marca e embalagem): o catálogo é de todos.
    """

    def post(self, request, pk):
        produto = Product.objects.filter(pk=pk).first()
        if produto is None:
            return Response({'detail': 'Produto não encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        codigo = normalizar_codigo(request.data.get('barcode'))
        if not codigo_valido(codigo):
            return Response(
                {'detail': 'Código de barras inválido. Confira os números.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if produto.barcode and produto.barcode != codigo:
            return Response(
                {'detail': 'Este produto já tem outro código de barras.'},
                status=status.HTTP_409_CONFLICT,
            )

        dono = Product.objects.filter(barcode=codigo).exclude(pk=produto.pk).first()
        if dono is not None:
            return Response(
                {
                    'detail': f'Esse código de barras já é do produto "{dono.name}".',
                    'produto': ProductSerializer(dono).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        sugestao = buscar_por_codigo(codigo) or {}
        imagem = sugestao.get('image_url') or ''

        produto.barcode = codigo
        if not produto.image_url and imagem.startswith('https://'):
            produto.image_url = imagem[:500]
            produto.data_source = sugestao.get('data_source') or produto.data_source
        if not produto.brand and sugestao.get('brand'):
            produto.brand = sugestao['brand'][:100]
        if not produto.package_size and sugestao.get('package_size'):
            produto.package_size = sugestao['package_size'][:50]
        produto.save()

        return Response({
            'produto': ProductSerializer(produto).data,
            'encontrado': bool(sugestao),
        })


class ProductEsconderView(APIView):
    """POST products/<id>/esconder/: a lixeira de um produto que a pessoa não
    pode apagar (é de outra pessoa, está em lista ou tem preço de nota). O
    produto sai só da tela de produtos e da busca dela."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        produto = get_object_or_404(Product, pk=pk)
        ProdutoEscondido.objects.get_or_create(usuario=request.user, produto=produto)
        return Response(status=status.HTTP_204_NO_CONTENT)
