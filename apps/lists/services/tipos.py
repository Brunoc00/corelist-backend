"""Menor preço por tipo de produto: "Arroz" -> marcas e tamanhos, cada um com
as lojas do mais barato ao mais caro.

O mesmo produto aparece com nomes diferentes em cada loja ("ARROZ TP1 CAMIL
5KG" e "ARROZ CAMIL T1 5KG"). Aqui eles viram uma opção só quando têm a mesma
marca, o mesmo tamanho e nomes parecidos. O preço por kg ou por litro deixa
comparar tamanhos diferentes.
"""

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

from apps.products.models import Product

from .busca import pontuar
from .enriquecer import tamanho_normalizado, tipo_do_produto
from .precos import PERIODO_PADRAO, _resumos, ultimos_por_loja
from .reconhecer import semelhanca

SEMELHANCA_PARA_JUNTAR = 0.7
CENTAVO = Decimal('0.01')


def _chave(texto):
    texto = unicodedata.normalize('NFKD', texto or '')
    return ''.join(c for c in texto if not unicodedata.combining(c)).strip().lower()


def _produtos_com_preco():
    return list(Product.objects.filter(observed_prices__isnull=False).distinct().select_related('category'))


def _nome(produto):
    return produto.nome_legivel or produto.name


def _quantidade_base(produto):
    """(quantidade, 'kg' | 'L') pelo tamanho da embalagem, ou None."""
    tamanho = tamanho_normalizado(produto.package_size or _nome(produto))
    achado = re.fullmatch(r'(\d+(?:\.\d+)?)(kg|g|ml|l)', tamanho)
    if not achado:
        return None
    valor, unidade = Decimal(achado.group(1)), achado.group(2)
    if valor <= 0:
        return None
    if unidade == 'g':
        return valor / 1000, 'kg'
    if unidade == 'ml':
        return valor / 1000, 'L'
    return valor, 'kg' if unidade == 'kg' else 'L'


def preco_por_base(linha, produto):
    """R$ por kg ou por litro, para comparar tamanhos diferentes."""
    if linha is None:
        return None
    preco = Decimal(linha['preco'])
    if linha['medida'] == 'kg':
        return {'valor': str(preco.quantize(CENTAVO)), 'unidade': 'kg'}
    base = _quantidade_base(produto)
    if base is None:
        return None
    quantidade, unidade = base
    return {'valor': str((preco / quantidade).quantize(CENTAVO, ROUND_HALF_UP)), 'unidade': unidade}


def _combina_com_o_texto(texto, tipo, produto):
    if not texto:
        return True
    return _chave(tipo).startswith(_chave(texto)) or pontuar(texto, produto) >= 1


def _agrupar_opcoes(produtos):
    """[[produto, ...], ...]: mesma marca, mesmo tamanho e nome parecido."""
    grupos = []
    for produto in produtos:
        marca = _chave(produto.brand)
        tamanho = tamanho_normalizado(produto.package_size or _nome(produto))
        for grupo in grupos:
            primeiro = grupo[0]
            if (
                _chave(primeiro.brand) == marca
                and tamanho_normalizado(primeiro.package_size or _nome(primeiro)) == tamanho
                and semelhanca(_nome(primeiro), _nome(produto)) >= SEMELHANCA_PARA_JUNTAR
            ):
                grupo.append(produto)
                break
        else:
            grupos.append([produto])
    return grupos


def tipos_com_preco(texto='', limite=40):
    """Os tipos com preço nas notas, dos que têm mais opções aos que têm menos."""
    texto = (texto or '').strip()
    por_tipo = {}
    for produto in _produtos_com_preco():
        tipo = tipo_do_produto(produto)
        if tipo and _combina_com_o_texto(texto, tipo, produto):
            por_tipo.setdefault(_chave(tipo), {'tipo': tipo, 'produtos': []})['produtos'].append(produto)

    ultimos = ultimos_por_loja([p.id for grupo in por_tipo.values() for p in grupo['produtos']])
    tipos = []
    for grupo in por_tipo.values():
        lojas, precos = set(), []
        for produto in grupo['produtos']:
            for observado in ultimos.get(produto.id, {}).values():
                lojas.add(observado.store_id)
                precos.append(observado)
        mais_barato = min(precos, key=lambda o: o.price) if precos else None
        foto = next((p.image_url for p in grupo['produtos'] if p.image_url), '')
        tipos.append({
            'tipo': grupo['tipo'],
            'opcoes': len(_agrupar_opcoes(grupo['produtos'])),
            'lojas': len(lojas),
            'a_partir_de': None if mais_barato is None else {
                'preco': str(mais_barato.price),
                'medida': mais_barato.measure,
            },
            'image_url': foto,
        })
    tipos.sort(key=lambda t: (-t['opcoes'], -t['lojas'], _chave(t['tipo'])))
    return tipos[:limite]


def _juntar_resumos(grupo, resumos_por_id):
    """Um resumo só para o mesmo produto vindo de lojas diferentes."""
    resumos = [resumos_por_id[p.id] for p in grupo if p.id in resumos_por_id]
    if not resumos:
        return None
    principal = next((p for p in grupo if p.image_url), grupo[0])

    por_loja = {}
    for resumo in resumos:
        for linha in resumo['lojas']:
            atual = por_loja.get(linha['loja']['id'])
            if atual is None or linha['data'] > atual['data']:
                por_loja[linha['loja']['id']] = linha
    lojas = sorted(por_loja.values(), key=lambda linha: (linha['desatualizado'], Decimal(linha['preco'])))
    atuais = [linha for linha in lojas if not linha['desatualizado']]
    menores = [r['menor_no_periodo'] for r in resumos if r['menor_no_periodo']]

    produto = dict(resumos_por_id.get(principal.id, resumos[0])['produto'])
    produto['name'] = _nome(principal)
    referencia = atuais[0] if atuais else (lojas[0] if lojas else None)
    return {
        'produto': produto,
        'mais_barato_agora': atuais[0] if atuais else None,
        'menor_no_periodo': min(menores, key=lambda m: Decimal(m['preco'])) if menores else None,
        'lojas': lojas,
        'preco_por': preco_por_base(referencia, principal),
        'produtos_juntados': len(grupo),
    }


def opcoes_do_tipo(tipo, dias=PERIODO_PADRAO, origem=None, raio_km=None):
    """As opções (marca e tamanho) de um tipo, da mais barata por kg/L à mais
    cara; quem não tem tamanho conhecido vem no fim, pelo preço."""
    chave = _chave(tipo)
    produtos = [p for p in _produtos_com_preco() if _chave(tipo_do_produto(p)) == chave]
    resumos_por_id = {r['produto']['id']: r for r in _resumos(produtos, dias, origem, raio_km)}
    opcoes = [
        opcao for opcao in (_juntar_resumos(g, resumos_por_id) for g in _agrupar_opcoes(produtos))
        if opcao and opcao['lojas']
    ]

    def ordem(opcao):
        referencia = opcao['mais_barato_agora'] or opcao['lojas'][0]
        por_base = opcao['preco_por']
        return (por_base is None, Decimal(por_base['valor']) if por_base else Decimal(referencia['preco']))

    opcoes.sort(key=ordem)
    return opcoes
