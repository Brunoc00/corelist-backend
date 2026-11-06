"""Produtos das notas fiscais ganham nome legível, tipo e foto sozinhos.

A nota só traz nomes abreviados ("ARROZ TP1 CAMIL 5KG", "DA MONANGE 150ML").
Depois que a nota é salva, em segundo plano:

1. a IA gratuita (Groq; Gemini de reserva) traduz o nome: tipo ("Arroz"), nome
   legível ("Arroz Camil Tipo 1 5 kg"), marca e tamanho. Sem IA, o tipo sai de
   uma regra simples de abreviações;
2. com o nome legível, a foto é procurada no Open Food Facts (ou no Open
   Beauty Facts, para higiene). Só entra a foto de um produto com nome
   parecido e o mesmo tamanho: melhor ficar sem foto do que com a errada.

A busca por nome dessas bases aceita só 10 pedidos por minuto: há uma pausa
entre as buscas e cada termo fica 1 dia em cache.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from apps.products.models import Product
from apps.products.services.openfoodfacts import codigo_valido

from .reconhecer import normalizar, semelhanca

logger = logging.getLogger(__name__)

LOTE_DA_IA = 25
PAUSA_ENTRE_BUSCAS = 6.5     # segundos: 10 buscas por minuto
TIMEOUT_DA_BUSCA = 10
SEMELHANCA_MINIMA = 0.6

BUSCA_DE_FOTOS = {
    # Busca nova do Open Food Facts (aceita nomes parecidos, não exige todas
    # as palavras) e a busca do Open Beauty Facts (higiene e limpeza).
    'openfoodfacts': 'https://search.openfoodfacts.org/search',
    'openbeautyfacts': 'https://world.openbeautyfacts.org/cgi/search.pl',
}

# Abreviações comuns das notas de Manaus -> tipo do produto.
TIPO_POR_ABREVIACAO = {
    'iog': 'Iogurte', 'lte': 'Leite', 'sh': 'Shampoo', 'co': 'Condicionador',
    'cd': 'Creme dental', 'da': 'Desodorante', 'deo': 'Desodorante', 'st': 'Sabonete',
    'sbt': 'Sabonete', 'dt': 'Detergente', 'la': 'Lã de aço', 'ap': 'Aparelho de barbear',
    'abs': 'Absorvente', 'ph': 'Papel higiênico', 'alf': 'Alface', 'ling': 'Linguiça',
    'salg': 'Salgadinho', 'bisc': 'Biscoito', 'choc': 'Chocolate', 'refri': 'Refrigerante',
    'refrig': 'Refrigerante', 'muss': 'Mussarela', 'amac': 'Amaciante', 'cer': 'Cereal',
    'tanger': 'Tangerina', 'feij': 'Feijão', 'feijao': 'Feijão', 'acucar': 'Açúcar',
    'cafe': 'Café', 'pao': 'Pão', 'macarrao': 'Macarrão', 'oleo': 'Óleo',
}

PROMPT = """Você recebe nomes de produtos como aparecem em notas fiscais de supermercados de Manaus, quase sempre abreviados (ex.: "ARROZ TP1 CAMIL 5KG", "DA MONANGE 150ML FLOR LAV", "IOG VIG 800G BATIDO").
Para cada nome, devolva um JSON no formato {"produtos": [{"i": <índice recebido>, "nome": "...", "tipo": "...", "marca": "...", "tamanho": "..."}]}:
- nome: o nome completo e legível em português (ex.: "Desodorante aerossol Monange Flor de Lavanda 150 ml");
- tipo: o tipo genérico do produto, curto e no singular, com a primeira letra maiúscula (ex.: "Arroz", "Feijão", "Iogurte", "Desodorante", "Papel higiênico", "Carne bovina", "Banana");
- marca: a marca, se estiver no nome; senão "";
- tamanho: o tamanho com a unidade (ex.: "5 kg", "500 g", "1 L", "150 ml"), se estiver no nome; senão "".
Não invente marca nem tamanho que não estejam no nome. Se não souber o tipo, use ""."""


def _sem_acento(texto):
    texto = unicodedata.normalize('NFKD', texto or '')
    return ''.join(c for c in texto if not unicodedata.combining(c)).lower()


def tipo_pelo_nome(nome):
    """Tipo sem IA: abreviação conhecida ou a primeira palavra ("Arroz")."""
    palavras = re.findall(r'[a-z]+', _sem_acento(nome))
    if not palavras:
        return ''
    primeira = palavras[0]
    if primeira in TIPO_POR_ABREVIACAO:
        return TIPO_POR_ABREVIACAO[primeira]
    if len(primeira) < 3:
        return ''
    # Mantém a acentuação do nome original quando houver.
    original = re.findall(r'[^\W\d_]+', nome or '')[0]
    return original.capitalize()


def tipo_do_produto(produto):
    return produto.tipo or tipo_pelo_nome(produto.name)


# --- Tradução dos nomes pela IA -----------------------------------------------

def _cliente_groq():
    import groq
    return groq.Groq(timeout=30.0, max_retries=0)  # lê GROQ_API_KEY do ambiente


def _cliente_gemini():
    from google import genai
    from google.genai import types
    return genai.Client(http_options=types.HttpOptions(
        timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1),
    ))


def _pedir_ao_groq(conteudo):
    resposta = _cliente_groq().chat.completions.create(
        model=settings.ASSISTENTE_MODELO_GROQ,
        messages=[{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': conteudo}],
        response_format={'type': 'json_object'},
        max_completion_tokens=4000,
    )
    return resposta.choices[0].message.content


def _pedir_ao_gemini(conteudo):
    from google.genai import types
    cliente = _cliente_gemini()
    resposta = cliente.models.generate_content(
        model=settings.ASSISTENTE_MODELO_GEMINI,
        contents=conteudo,
        config=types.GenerateContentConfig(
            system_instruction=PROMPT, response_mime_type='application/json',
        ),
    )
    return resposta.text


def _ias():
    ias = []
    if os.getenv('GROQ_API_KEY'):
        ias.append(('groq', _pedir_ao_groq))
    if os.getenv('GEMINI_API_KEY'):
        ias.append(('gemini', _pedir_ao_gemini))
    if settings.ASSISTENTE_IA != 'groq':
        ias.reverse()  # Gemini primeiro, como no assistente
    return ias


def _texto_limpo(valor, tamanho_maximo):
    return re.sub(r'\s+', ' ', str(valor or '')).strip()[:tamanho_maximo]


def traduzir_nomes(nomes):
    """[nome da nota] -> [{nome, tipo, marca, tamanho} | None], na mesma ordem."""
    conteudo = json.dumps([{'i': i, 'nome': nome} for i, nome in enumerate(nomes)], ensure_ascii=False)
    for fonte, pedir in _ias():
        try:
            dados = json.loads(pedir(conteudo) or '{}')
        except Exception as erro:  # cota, rede ou JSON quebrado: tenta a próxima
            logger.warning('Tradução de nomes pelo %s falhou: %s', fonte, erro)
            continue
        traduzidos = [None] * len(nomes)
        for item in (dados.get('produtos') or []) if isinstance(dados, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                indice = int(item.get('i'))
            except (TypeError, ValueError):
                continue
            if 0 <= indice < len(nomes):
                tamanho = _texto_limpo(item.get('tamanho'), 50)
                traduzidos[indice] = {
                    'nome': _texto_limpo(item.get('nome'), 200),
                    'tipo': _texto_limpo(item.get('tipo'), 60),
                    'marca': _texto_limpo(item.get('marca'), 100),
                    # Só um tamanho de verdade ("500 g"), nunca um texto solto.
                    'tamanho': tamanho if re.search(r'\d', tamanho) else '',
                }
        return traduzidos
    return [None] * len(nomes)


def traduzir_produtos(produtos):
    """Preenche tipo, nome legível, marca e embalagem (sem apagar o que já
    havia) e marca o produto como traduzido."""
    agora = timezone.now()
    for inicio in range(0, len(produtos), LOTE_DA_IA):
        lote = produtos[inicio:inicio + LOTE_DA_IA]
        traduzidos = traduzir_nomes([produto.name for produto in lote])
        for produto, dados in zip(lote, traduzidos):
            dados = dados or {}
            produto.tipo = (dados.get('tipo') or tipo_pelo_nome(produto.name))[:60]
            produto.nome_legivel = dados.get('nome', '')
            if not produto.brand and dados.get('marca'):
                produto.brand = dados['marca']
            if not produto.package_size and dados.get('tamanho'):
                produto.package_size = dados['tamanho']
            produto.enriquecido_em = agora
            produto.save(update_fields=[
                'tipo', 'nome_legivel', 'brand', 'package_size', 'enriquecido_em', 'updated_at',
            ])


# --- Foto pelo nome -------------------------------------------------------------

def tamanho_normalizado(texto):
    """'1,5 L' -> '1.5l'; '800G' -> '800g'; '' quando não há."""
    for palavra in normalizar(texto or ''):
        if re.fullmatch(r'\d+(\.\d+)?(kg|g|ml|l)', palavra):
            return palavra
    return ''


def _buscar_na_base(fonte, termo):
    """(achados, foi_na_rede); achados é None quando a base não respondeu."""
    chave = 'foto:' + hashlib.sha256(f'{fonte}:{_sem_acento(termo)}'.encode()).hexdigest()
    achados = cache.get(chave)
    if achados is not None:
        return achados, False
    if fonte == 'openfoodfacts':
        endereco = BUSCA_DE_FOTOS[fonte] + '?' + urlencode({
            'q': termo, 'page_size': 20, 'langs': 'pt',
            'fields': 'code,product_name,brands,quantity,image_front_small_url,image_front_url,countries_tags',
        })
    else:
        endereco = BUSCA_DE_FOTOS[fonte] + '?' + urlencode({
            'search_terms': termo, 'search_simple': 1, 'json': 1, 'page_size': 20,
            'countries_tags_en': 'brazil',
            'fields': 'code,product_name,brands,quantity,image_front_small_url,image_front_url,countries_tags',
        })
    pedido = Request(endereco, headers={'User-Agent': settings.OPEN_FOOD_FACTS_USER_AGENT})
    try:
        with urlopen(pedido, timeout=TIMEOUT_DA_BUSCA) as resposta:
            dados = json.loads(resposta.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as erro:
        logger.warning('Busca de foto no %s falhou: %s', fonte, erro)
        return None, True
    achados = dados.get('hits') or dados.get('products') or []
    cache.set(chave, achados, 60 * 60 * 24)
    return achados, True


def _marcas(achado):
    marcas = achado.get('brands') or ''
    return _sem_acento(', '.join(marcas) if isinstance(marcas, list) else marcas)


def _sem_tamanho(texto):
    return ' '.join(p for p in normalizar(texto) if not p[0].isdigit())


# Formas diferentes do mesmo produto: a foto de uma não serve para a outra.
FORMAS = ('liquido', 'barra', 'po', 'refil', 'capsula', 'spray', 'aerosol', 'aerossol', 'lenco')


def _formas(texto):
    palavras = set(re.findall(r'[a-z]+', _sem_acento(texto)))
    return {forma for forma in FORMAS if forma in palavras}


def _melhor_foto(produto, achados):
    """O achado com foto, mesma marca e nome parecido (tamanho igual conta a
    favor); None se nenhum for bom o bastante."""
    termo = produto.nome_legivel or produto.name
    tamanho = tamanho_normalizado(produto.package_size or termo)
    marca = _sem_acento(produto.brand).split()
    tipo = _sem_acento(tipo_do_produto(produto)).split()
    melhor, nota_melhor = None, 0.0
    for achado in achados or []:
        foto = achado.get('image_front_url') or achado.get('image_front_small_url') or ''
        if not foto.startswith('https://'):
            continue
        # Só produtos vendidos no Brasil (a embalagem é a daqui).
        paises = achado.get('countries_tags')
        if paises is not None and 'en:brazil' not in paises:
            continue
        nome_achado = f"{achado.get('product_name') or ''} {_marcas(achado)}"
        texto_achado = _sem_acento(nome_achado)
        # A marca precisa bater; sem marca na nota, só um nome quase igual.
        if marca and marca[0] not in texto_achado:
            continue
        if _formas(nome_achado) - _formas(termo):
            continue
        nota = semelhanca(_sem_tamanho(termo), _sem_tamanho(nome_achado))
        if not marca:
            nota -= 0.2
        if tipo and tipo[0][:5] in texto_achado:
            nota += 0.1
        tamanho_achado = tamanho_normalizado(achado.get('quantity') or achado.get('product_name'))
        if tamanho and tamanho_achado:
            nota += 0.1 if tamanho == tamanho_achado else -0.1
        if nota > nota_melhor:
            melhor, nota_melhor = achado, nota
    return melhor if nota_melhor >= SEMELHANCA_MINIMA else None


def _termos_da_busca(produto):
    """Do mais certeiro ao mais amplo: tipo + marca, depois o nome legível."""
    tipo, marca = tipo_do_produto(produto), produto.brand
    termos = []
    if tipo and marca:
        termos.append(f'{tipo} {marca}')
    nome = _sem_tamanho(produto.nome_legivel or produto.name)
    if nome:
        termos.append(' '.join(nome.split()[:4]))
    return list(dict.fromkeys(termos))[:2]


def buscar_foto(produto, esperar=True):
    """Procura a foto pelo nome; devolve True se achou. `esperar` faz a pausa
    exigida pela base antes de cada busca nova. Se a base estiver fora do ar,
    o produto não fica marcado como procurado (tenta de novo depois)."""
    categoria = produto.category.name if produto.category_id else ''
    fontes = ['openfoodfacts', 'openbeautyfacts'] if categoria in ('Higiene', 'Limpeza') else ['openfoodfacts']
    escolhido, fonte_escolhida, falhou = None, '', False
    for termo in _termos_da_busca(produto):
        for fonte in fontes:
            achados, foi_na_rede = _buscar_na_base(fonte, termo)
            if foi_na_rede and esperar:
                time.sleep(PAUSA_ENTRE_BUSCAS)
            if achados is None:
                falhou = True
                continue
            escolhido = _melhor_foto(produto, achados)
            if escolhido:
                fonte_escolhida = fonte
                break
        if escolhido:
            break

    if escolhido:
        produto.image_url = (escolhido.get('image_front_url') or escolhido.get('image_front_small_url'))[:500]
        produto.foto_fonte = fonte_escolhida
        codigo = escolhido.get('code') or ''
        if not produto.barcode and codigo_valido(codigo) and not Product.objects.filter(barcode=codigo).exists():
            produto.barcode = codigo
    if escolhido or not falhou:
        produto.foto_buscada_em = timezone.now()
        produto.save(update_fields=['image_url', 'foto_fonte', 'barcode', 'foto_buscada_em', 'updated_at'])
    return bool(escolhido)


# --- Junta tudo --------------------------------------------------------------------

def enriquecer_produtos(ids, com_fotos=True, esperar=True):
    """Traduz os que faltam e procura foto dos que ainda não têm."""
    produtos = list(Product.objects.filter(id__in=ids).select_related('category'))
    traduzir_produtos([p for p in produtos if p.enriquecido_em is None])
    fotos = 0
    if com_fotos:
        for produto in produtos:
            if not produto.image_url and produto.foto_buscada_em is None:
                fotos += buscar_foto(produto, esperar=esperar)
    return {'produtos': len(produtos), 'fotos': fotos}


def enriquecer_em_segundo_plano(ids):
    """Depois que a nota é salva (on_commit): roda numa thread para não
    atrasar a resposta. Em testes (TestCase), o on_commit não dispara."""
    ids = list(ids)
    if not ids or not settings.ENRIQUECER_PRODUTOS:
        return

    def rodar():
        try:
            enriquecer_produtos(ids)
        except Exception:  # nunca derruba nada: só registra
            logger.exception('Enriquecer produtos das notas falhou.')
        finally:
            connection.close()

    transaction.on_commit(lambda: threading.Thread(target=rodar, daemon=True).start())
