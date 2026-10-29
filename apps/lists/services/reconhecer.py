"""Reconhece qual produto é cada item da nota fiscal.

A nota traz só o código interno da loja e um nome abreviado
("IOG VIG 800G BATIDO"). A ordem de tentativa é:

1. o código da loja é um código de barras válido → produto com esse código;
2. vínculo aprendido (loja + código → produto), gravado quando alguém
   confirmou uma nota daquela loja;
3. mesmo nome no catálogo;
4. nome parecido, aceitando abreviações ("IOG" ~ "Iogurte", "800G" ~ "800 g").

O resultado é uma sugestão: na conferência, a pessoa confirma ou troca.
"""

import re
import unicodedata
from difflib import SequenceMatcher

from apps.products.models import Product, Store, StoreProductCode
from apps.products.services.openfoodfacts import codigo_valido

# Abaixo disso, não sugerimos produto pelo nome.
NOTA_MINIMA = 0.5

# Palavras que não ajudam a reconhecer o produto.
PALAVRAS_VAZIAS = {'de', 'da', 'do', 'das', 'dos', 'com', 'e', 'em', 'a', 'o', 'p', 'c'}


def somente_digitos(texto):
    return re.sub(r'\D', '', texto or '')


def normalizar(nome):
    """'Iogurte Vigor 800 g' -> ['iogurte', 'vigor', '800g']."""
    sem_acento = unicodedata.normalize('NFKD', nome or '')
    sem_acento = ''.join(c for c in sem_acento if not unicodedata.combining(c))
    texto = sem_acento.lower()
    # "800 g", "1,5 kg", "2 L" viram um token só: 800g, 1.5kg, 2l
    texto = re.sub(r'(\d)[,.](\d)', r'\1.\2', texto)
    texto = re.sub(r'(\d)\s+(kg|g|ml|l|un|und)\b', r'\1\2', texto)
    texto = re.sub(r'[^a-z0-9.]+', ' ', texto)
    return [palavra for palavra in texto.split() if palavra not in PALAVRAS_VAZIAS]


def _palavras_combinam(a, b):
    if a == b:
        return True
    # Abreviação: "iog" ~ "iogurte" (mínimo de 3 letras, e não para números).
    curta, longa = sorted((a, b), key=len)
    return len(curta) >= 3 and not curta[0].isdigit() and longa.startswith(curta)


def semelhanca(nome_a, nome_b):
    """De 0 a 1: quanto os dois nomes parecem o mesmo produto."""
    palavras_a, palavras_b = normalizar(nome_a), normalizar(nome_b)
    if not palavras_a or not palavras_b:
        return 0.0

    usadas = set()
    combinadas = 0
    for palavra in palavras_a:
        for indice, outra in enumerate(palavras_b):
            if indice not in usadas and _palavras_combinam(palavra, outra):
                usadas.add(indice)
                combinadas += 1
                break

    por_palavras = combinadas / max(len(palavras_a), len(palavras_b))

    # Números diferentes (800g × 1kg) indicam produto diferente.
    numeros_a = {p for p in palavras_a if p[0].isdigit()}
    numeros_b = {p for p in palavras_b if p[0].isdigit()}
    if numeros_a and numeros_b and not numeros_a & numeros_b:
        por_palavras *= 0.5

    por_letras = SequenceMatcher(
        None, ' '.join(palavras_a), ' '.join(palavras_b),
    ).ratio()

    return round(0.75 * por_palavras + 0.25 * por_letras, 3)


def loja_da_nota(nota):
    """Cria ou atualiza a loja (filial) da nota pelo CNPJ."""
    cnpj = somente_digitos(nota.get('cnpj'))
    if len(cnpj) != 14:
        return None

    loja, _ = Store.objects.update_or_create(
        cnpj=cnpj,
        defaults={
            'name': (nota.get('loja') or '')[:200],
            'address': (nota.get('endereco') or '')[:300],
        },
    )
    return loja


def produto_certo(item, loja=None):
    """Produto com certeza (código de barras ou vínculo aprendido), ou
    (None, None)."""
    codigo = somente_digitos(item.get('codigo_loja'))

    if codigo and codigo_valido(codigo):
        produto = Product.objects.filter(barcode=codigo).first()
        if produto:
            return produto, 'codigo_de_barras'

    if loja and codigo:
        vinculo = (
            StoreProductCode.objects
            .filter(store=loja, store_code=codigo)
            .select_related('product')
            .first()
        )
        if vinculo:
            return vinculo.product, 'aprendido'

    return None, None


def nome_completo(produto):
    return ' '.join(
        parte for parte in (
            produto.name, produto.brand, produto.package_size or produto.unit,
        ) if parte
    )


def semelhanca_com_produto(nome, produto):
    return max(semelhanca(nome, produto.name), semelhanca(nome, nome_completo(produto)))


def sugerir_produto(item, loja=None, candidatos=None):
    """Devolve (produto, motivo, nota) ou (None, None, 0).

    `candidatos`: limita a busca por nome a estes produtos (ex.: os da lista).
    """
    certo, motivo = produto_certo(item, loja)
    if certo and (candidatos is None or certo in candidatos):
        return certo, motivo, 1.0

    nome = item.get('nome', '')
    if candidatos is None:
        exato = Product.objects.filter(name__iexact=nome.strip()).first()
        if exato:
            return exato, 'mesmo_nome', 1.0
        candidatos = Product.objects.only('id', 'name', 'brand', 'package_size', 'unit')

    melhor, melhor_nota = None, 0.0
    for produto in candidatos:
        nota = semelhanca_com_produto(nome, produto)
        if nota > melhor_nota:
            melhor, melhor_nota = produto, nota

    if melhor and melhor_nota >= NOTA_MINIMA:
        return melhor, 'nome_parecido', melhor_nota

    return None, None, 0.0


def aprender(loja, item, produto):
    """Guarda que o código desta loja é este produto."""
    codigo = somente_digitos(item.get('codigo_loja'))
    if loja is None or not codigo or produto is None:
        return

    StoreProductCode.objects.update_or_create(
        store=loja, store_code=codigo, defaults={'product': produto},
    )


def cruzar_com_lista(itens_nota, itens_lista, loja=None):
    """Liga cada item da nota a no máximo um item da lista (e vice-versa).

    Devolve {indice_da_nota: (item_da_lista, motivo, nota)}. Primeiro os
    pares certos (código de barras ou vínculo aprendido), depois os de nome
    mais parecido.
    """
    pares = []
    for indice, item in enumerate(itens_nota):
        certo, motivo = produto_certo(item, loja)
        for item_lista in itens_lista:
            if certo is not None and certo.id == item_lista.product_id:
                pares.append((1.0, indice, item_lista, motivo))
                continue
            nota = semelhanca_com_produto(item['nome'], item_lista.product)
            if nota >= NOTA_MINIMA:
                pares.append((nota, indice, item_lista, 'nome_parecido'))

    pares.sort(key=lambda par: par[0], reverse=True)

    ligados, usados = {}, set()
    for nota, indice, item_lista, motivo in pares:
        if indice in ligados or item_lista.id in usados:
            continue
        ligados[indice] = (item_lista, motivo, nota)
        usados.add(item_lista.id)

    return ligados
