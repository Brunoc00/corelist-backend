"""Importação de compras pela nota fiscal (NFC-e).

- POST lists/nfce/ler/      {link}  -> a nota lida, para o usuário conferir;
- POST lists/nfce/importar/ {link, nome?, itens?} -> cria a compra concluída.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product

from .models import List, ListItem
from .serializers import (
    ListSerializer,
    NfceConcluirNaListaSerializer,
    NfceImportSerializer,
    NfceLinkSerializer,
)
from .services.nfce import LinkInvalido, NfceErro, ler_nota
from .services.precos import registrar_preco
from .services.reconhecer import (
    aprender,
    cruzar_com_lista,
    loja_da_nota,
    sugerir_produto,
)


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
                loja = loja_da_nota(nota)
                lista = List.objects.create(
                    owner=request.user,
                    name=nome[:200],
                    store=nota['loja'][:200],
                    nfce_key=nota['chave'],
                    is_completed=True,
                    completed_at=emitida_em,
                )

                livres = list(nota['itens'])
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

                    # Para os preços da cidade, vale o item da nota (lido no
                    # servidor) com a mesma quantidade, medida e preço.
                    da_nota = next(
                        (
                            livre for livre in livres
                            if livre['medida'] == item['medida']
                            and livre['preco'] == item['preco']
                            and abs(livre['quantidade'] - item['quantidade']) < Decimal('0.001')
                        ),
                        None,
                    )
                    if da_nota is not None:
                        livres.remove(da_nota)
                        aprender(loja, da_nota, produto)
                        registrar_preco(loja, nota, da_nota, produto)
        except IntegrityError:
            # Duas importações da mesma nota ao mesmo tempo.
            return _erro('Esta nota já foi importada.', status.HTTP_409_CONFLICT)

        return Response(
            ListSerializer(lista, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


# --- Nota fiscal dentro de uma lista em andamento -------------------------

def _item_lista_json(item):
    return {
        'id': item.id,
        'nome': item.product.name,
        'quantidade': str(item.quantity),
        'medida': item.measure,
        'preco': None if item.price is None else str(item.price),
    }


class NfceLerNaListaView(APIView):
    """Lê a nota e cruza com a lista: comprados, fora da lista e esquecidos."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        lista = get_object_or_404(
            List, id=pk, owner=request.user, is_completed=False,
        )
        entrada = NfceLinkSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        nota, erro = _ler(entrada.validated_data['link'])
        if erro:
            return erro

        loja = loja_da_nota(nota)
        itens_lista = list(lista.items.select_related('product'))
        ligados = cruzar_com_lista(nota['itens'], itens_lista, loja)

        itens = []
        for indice, item in enumerate(nota['itens']):
            ligado = ligados.get(indice)
            sugestao = None
            if ligado is None:
                produto, motivo, _ = sugerir_produto(item, loja)
                if produto:
                    sugestao = {'id': produto.id, 'name': produto.name, 'motivo': motivo}

            itens.append({
                'indice': indice,
                'nome': item['nome'],
                'codigo_loja': item['codigo_loja'],
                'medida': item['medida'],
                'quantidade': str(item['quantidade']),
                'preco': str(item['preco']),
                'total': str(item['total']),
                'list_item': ligado[0].id if ligado else None,
                'motivo': ligado[1] if ligado else None,
                'produto_sugerido': sugestao,
            })

        ligados_ids = {ligado[0].id for ligado in ligados.values()}
        importada = _ja_importada(request.user, nota['chave'])

        return Response({
            'chave': nota['chave'],
            'loja': nota['loja'],
            'endereco': nota.get('endereco', ''),
            'emitida_em': nota['emitida_em'],
            'total': str(nota['total']),
            'orcamento': None if lista.budget is None else str(lista.budget),
            'itens': itens,
            'itens_lista': [_item_lista_json(item) for item in itens_lista],
            'esquecidos': [item.id for item in itens_lista if item.id not in ligados_ids],
            'lista_importada': importada.id if importada else None,
        })


class NfceConcluirNaListaView(APIView):
    """Conclui a lista com a nota, como a pessoa confirmou na conferência.

    Preço e quantidade vêm sempre da nota lida aqui no servidor.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        lista = get_object_or_404(
            List, id=pk, owner=request.user, is_completed=False,
        )
        entrada = NfceConcluirNaListaSerializer(data=request.data)
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

        itens_lista = {item.id: item for item in lista.items.select_related('product')}

        for escolha in dados['itens']:
            if escolha['indice'] >= len(nota['itens']):
                return _erro('Item da nota inexistente.')
            if escolha.get('list_item') and escolha['list_item'] not in itens_lista:
                return _erro('Item da lista inexistente.')
            if escolha.get('produto') and not Product.objects.filter(id=escolha['produto']).exists():
                return _erro('Produto inexistente.')

        emitida_em = nota['emitida_em'] or timezone.now()

        try:
            with transaction.atomic():
                loja = loja_da_nota(nota)
                comprados = set()

                for escolha in dados['itens']:
                    item = nota['itens'][escolha['indice']]
                    item_lista = itens_lista.get(escolha.get('list_item'))

                    if item_lista is not None:
                        item_lista.quantity = item['quantidade']
                        item_lista.measure = item['medida']
                        item_lista.price = item['preco']
                        item_lista.is_completed = True
                        item_lista.save()
                        comprados.add(item_lista.id)
                        produto = item_lista.product
                    else:
                        nome = (escolha.get('nome') or item['nome']).strip()[:200]
                        escolhido = (
                            Product.objects.filter(id=escolha['produto']).first()
                            if escolha.get('produto') else None
                        )
                        produto = (
                            escolhido
                            or _produto_do_catalogo(nome)
                            or Product.objects.create(
                                name=nome, data_source=Product.SOURCE_NFCE,
                            )
                        )
                        ListItem.objects.create(
                            list=lista,
                            product=produto,
                            quantity=item['quantidade'],
                            measure=item['medida'],
                            price=item['preco'],
                            is_completed=True,
                        )

                    aprender(loja, item, produto)
                    registrar_preco(loja, nota, item, produto)

                # Esquecidos: ficam na lista como "não comprado", sem preço.
                esquecidos = [
                    item for item_id, item in itens_lista.items()
                    if item_id not in comprados
                ]
                for item in esquecidos:
                    item.is_completed = False
                    item.price = None
                    item.save(update_fields=['is_completed', 'price', 'updated_at'])

                lista.store = nota['loja'][:200]
                lista.nfce_key = nota['chave']
                lista.is_completed = True
                lista.completed_at = emitida_em
                lista.save()
        except IntegrityError:
            return _erro('Esta nota já foi importada.', status.HTTP_409_CONFLICT)

        resposta = ListSerializer(lista, context={'request': request}).data
        resposta['esquecidos'] = [item.id for item in esquecidos]
        return Response(resposta)
