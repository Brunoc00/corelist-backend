"""Importação de compras pela nota fiscal (NFC-e).

- POST lists/nfce/ler/      {link}  -> a nota lida, para o usuário conferir;
- POST lists/nfce/importar/ {link, nome?, itens?} -> cria a compra concluída.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product

from .models import List, ListItem
from .serializers import (
    ListSerializer,
    NfceImportSerializer,
    NfceLinkSerializer,
)
from .services.nfce import LinkInvalido, NfceErro, ler_nota


def _produto_do_catalogo(nome):
    return Product.objects.filter(name__iexact=nome.strip()).first()


def _ja_importada(usuario, chave):
    return List.objects.filter(owner=usuario, nfce_key=chave).first()


def _erro(mensagem, codigo=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({'detail': mensagem, **extra}, status=codigo)


def _ler(link):
    """Devolve (nota, None) ou (None, resposta de erro)."""
    try:
        return ler_nota(link), None
    except LinkInvalido as erro:
        return None, _erro(str(erro))
    except NfceErro as erro:
        # A Sefaz falhou ou a página veio diferente do esperado.
        return None, _erro(str(erro), status.HTTP_502_BAD_GATEWAY)


class NfceLerView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        entrada = NfceLinkSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        nota, erro = _ler(entrada.validated_data['link'])
        if erro:
            return erro

        importada = _ja_importada(request.user, nota['chave'])
        itens = []

        for item in nota['itens']:
            produto = _produto_do_catalogo(item['nome'])
            itens.append({
                'nome': item['nome'],
                'codigo_loja': item['codigo_loja'],
                'unidade': item['unidade'],
                'medida': item['medida'],
                'quantidade': str(item['quantidade']),
                'preco': str(item['preco']),
                'total': str(item['total']),
                'produto': (
                    {'id': produto.id, 'name': produto.name}
                    if produto else None
                ),
            })

        return Response({
            'chave': nota['chave'],
            'loja': nota['loja'],
            'cnpj': nota['cnpj'],
            'emitida_em': nota['emitida_em'],
            'total': str(nota['total']),
            'itens': itens,
            'lista_importada': importada.id if importada else None,
        })


class NfceImportarView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        entrada = NfceImportSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data

        nota, erro = _ler(dados['link'])
        if erro:
            return erro

        existente = _ja_importada(request.user, nota['chave'])
        if existente:
            return _erro(
                'Esta nota já foi importada.',
                status.HTTP_409_CONFLICT,
                list_id=existente.id,
            )

        itens = dados.get('itens') or nota['itens']
        emitida_em = nota['emitida_em'] or timezone.now()
        nome = (dados.get('nome') or '').strip() or (
            f"{nota['loja'].title() or 'Compra'} {emitida_em:%d/%m}"
        )

        try:
            with transaction.atomic():
                lista = List.objects.create(
                    owner=request.user,
                    name=nome[:200],
                    store=nota['loja'][:200],
                    nfce_key=nota['chave'],
                    is_completed=True,
                    completed_at=emitida_em,
                )

                for item in itens:
                    nome_item = item['nome'].strip()[:200]
                    produto = _produto_do_catalogo(nome_item) or Product.objects.create(
                        name=nome_item,
                        data_source=Product.SOURCE_NFCE,
                    )
                    ListItem.objects.create(
                        list=lista,
                        product=produto,
                        quantity=item['quantidade'],
                        measure=item['medida'],
                        price=item['preco'],
                        is_completed=True,
                    )
        except IntegrityError:
            # Duas importações da mesma nota ao mesmo tempo.
            return _erro('Esta nota já foi importada.', status.HTTP_409_CONFLICT)

        return Response(
            ListSerializer(lista, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )
