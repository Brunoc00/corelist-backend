"""Preços da cidade (docs/PLANO_PRECOS_DA_CIDADE.md).

- GET lists/precos/?q=arroz&dias=30   -> produtos com o mais barato agora e o
                                         menor preço do período;
- GET lists/precos/produto/<id>/?dias -> preço atual em cada loja;
- GET lists/<id>/precos/              -> para a lista em andamento: mais
                                         barato de cada item e melhor loja.

Ninguém vê quem comprou: só loja, endereço, preço e data.
"""

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product

from .models import List
from .services.precos import buscar_precos, periodo, precos_da_lista, resumo_do_produto


class PrecosBuscaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        dias = periodo(request.query_params.get('dias'))
        return Response({
            'dias': dias,
            'produtos': buscar_precos(request.query_params.get('q', ''), dias),
        })


class PrecosProdutoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        produto = get_object_or_404(Product, pk=pk)
        dias = periodo(request.query_params.get('dias'))
        return Response(resumo_do_produto(produto, dias))


class PrecosDaListaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        lista = get_object_or_404(List, pk=pk, owner=request.user, is_completed=False)
        return Response(precos_da_lista(lista))
