"""Preços da cidade (docs/PLANO_PRECOS_DA_CIDADE.md).

- GET lists/precos/?q=arroz&dias=30[&lat=&lng=&raio=5] -> produtos com o mais barato agora e o
                                         menor preço do período;
- GET lists/precos/produto/<id>/?dias -> preço atual em cada loja;
- GET lists/precos/tipos/?q=          -> tipos de produto (Arroz, Café...) com
                                         quantas opções e lojas e o menor preço;
- GET lists/precos/?tipo=Arroz        -> as opções (marca e tamanho) desse tipo,
                                         cada uma com as lojas e o preço por kg/L;
- GET lists/<id>/precos/              -> para a lista em andamento: mais
                                         barato de cada item e melhor loja.

Ninguém vê quem comprou: só loja, endereço, preço e data.
"""

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product
from apps.products.services.geocodificar import endereco_da_coordenada

from .models import List
from .services.tipos import opcoes_do_tipo, tipos_com_preco
from .services.precos import (
    buscar_precos,
    localizacao,
    periodo,
    precos_da_lista,
    raio,
    resumo_do_produto,
)


class PrecosBuscaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        dias = periodo(params.get('dias'))
        # Perto de mim: lat/lng do aparelho (opcional) e raio em km (2, 5, 10).
        origem = localizacao(params.get('lat'), params.get('lng'))
        raio_km = raio(params.get('raio')) if origem else None
        tipo = (params.get('tipo') or '').strip()[:60]
        if tipo:
            return Response({
                'dias': dias,
                'raio_km': raio_km,
                'tipo': tipo,
                'produtos': opcoes_do_tipo(tipo, dias, origem=origem, raio_km=raio_km),
            })
        return Response({
            'dias': dias,
            'raio_km': raio_km,
            'produtos': buscar_precos(params.get('q', ''), dias, origem=origem, raio_km=raio_km),
        })


class PrecosTiposView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'tipos': tipos_com_preco(request.query_params.get('q', ''))})


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


class OndeEstouView(APIView):
    """GET lists/onde-estou/?lat=&lng= -> endereço aproximado da pessoa (rua,
    bairro e cidade), para mostrar junto da localização."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        origem = localizacao(request.query_params.get('lat'), request.query_params.get('lng'))
        if origem is None:
            return Response({'detail': 'Localização inválida.'}, status=400)
        return Response(endereco_da_coordenada(*origem) or {'texto': None})
