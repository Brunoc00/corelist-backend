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
from urllib.parse import quote, urlencode, urlparse
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

# Sem foto na base aberta: foto da embalagem achada na internet (Tavily),
# primeiro em sites de mercados do Brasil. Só entra uma imagem cuja descrição
# tenha a marca e a variação do produto; o site vai no crédito.
FONTE_INTERNET = 'internet'
SITES_DE_MERCADO = [
    'carrefour.com.br', 'paodeacucar.com', 'extra.com.br', 'atacadao.com.br',
    'assai.com.br', 'tendaatacado.com.br', 'amazon.com.br', 'mercadolivre.com.br',
    'americanas.com.br', 'drogasil.com.br', 'drogaraia.com.br', 'magazineluiza.com.br',
]
# De onde a imagem pode vir: site brasileiro (.br) ou serviço de imagens
# usado pelas lojas online daqui. Fora isso (vídeos, redes sociais, sites de
# outros países), a imagem é descartada.
SERVICOS_DE_IMAGEM_DE_LOJA = (
    'vtexassets.com', 'vteximg.com.br', 'mlstatic.com', 'media-amazon.com',
    'ssl-images-amazon.com', 'raiadrogasil.io', 'fbitsstatic.net', 'mercafacil.com',
    'agilecdn.com.br', 'awsli.com.br', 'mlcdn.com.br',
)


def site_permitido(site):
    site = site.lower()
    return site.endswith('.br') or any(site == s or site.endswith('.' + s) for s in SERVICOS_DE_IMAGEM_DE_LOJA)


# Imagens com vários produtos juntos não servem como foto de um produto.
IMAGEM_COM_VARIOS = ('grid', 'collage', 'assortment', 'several', 'variety of', 'multiple')

# Sem foto exata: foto ilustrativa do tipo (a da página da Wikipédia,
# imagens do Wikimedia Commons em licença livre).
WIKIPEDIA_RESUMO = 'https://pt.wikipedia.org/api/rest_v1/page/summary/'
FONTE_ILUSTRATIVA = 'wikipedia'
# Tipos cuja página da Wikipédia em português não tem foto (ou tem outro
# nome). "en:" usa a Wikipédia em inglês.
TITULO_DA_FOTO = {
    'flocao': 'Flocos de milho', 'haste': 'Cotonete', 'pimenta de cheiro': 'Capsicum chinense',
    'racao': 'Ração animal', 'salgadinho': 'Batata chips', 'desodorante': 'en:Deodorant',
    'locao hidratante': 'en:Moisturizer', 'hidratante': 'en:Moisturizer', 'cobertura': 'Glacê',
}

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


def foto_do_tipo(tipo):
    """Endereço da foto ilustrativa do tipo ("Tomate"), '' se não houver.
    Tenta o tipo e, se for uma página de vários sentidos, "(fruta)" e
    "(alimento)". Cada tipo fica 7 dias em cache."""
    tipo = (tipo or '').strip()
    if not tipo:
        return ''
    chave = 'foto-tipo:' + hashlib.sha256(_sem_acento(tipo).encode()).hexdigest()
    guardada = cache.get(chave)
    if guardada is not None:
        return guardada
    foto = ''
    titulos = [tipo, f'{tipo} (fruta)', f'{tipo} (alimento)']
    if _sem_acento(tipo) in TITULO_DA_FOTO:
        titulos.insert(0, TITULO_DA_FOTO[_sem_acento(tipo)])
    for titulo in titulos:
        base = WIKIPEDIA_RESUMO
        if titulo.startswith('en:'):
            base, titulo = WIKIPEDIA_RESUMO.replace('//pt.', '//en.'), titulo[3:]
        pedido = Request(
            base + quote(titulo.replace(' ', '_')),
            headers={'User-Agent': settings.OPEN_FOOD_FACTS_USER_AGENT},
        )
        try:
            with urlopen(pedido, timeout=TIMEOUT_DA_BUSCA) as resposta:
                dados = json.loads(resposta.read().decode('utf-8'))
        except HTTPError as erro:
            if erro.code == 404:
                continue
            logger.warning('Foto do tipo %s na Wikipédia falhou: %s', tipo, erro)
            return ''
        except (URLError, TimeoutError, ValueError, OSError) as erro:
            logger.warning('Foto do tipo %s na Wikipédia falhou: %s', tipo, erro)
            return ''  # sem cache: tenta de novo depois
        if dados.get('type') == 'disambiguation':
            continue
        endereco = ((dados.get('thumbnail') or {}).get('source') or '').split('?')[0]
        if endereco.startswith('https://'):
            foto = endereco[:500]
            break
    cache.set(chave, foto, 60 * 60 * 24 * 7)
    return foto


def usar_foto_do_tipo(produto):
    """Põe a foto ilustrativa do tipo num produto sem foto; True se pôs."""
    if produto.image_url:
        return False
    foto = foto_do_tipo(tipo_do_produto(produto))
    if not foto:
        return False
    produto.image_url = foto
    produto.foto_fonte = FONTE_ILUSTRATIVA
    produto.save(update_fields=['image_url', 'foto_fonte', 'updated_at'])
    return True


def _cliente_tavily():
    from tavily import TavilyClient
    return TavilyClient(api_key=os.getenv('TAVILY_API_KEY'))


def _palavras_que_distinguem(produto):
    """Palavras do nome que dizem qual é a variação ("erva", "doce",
    "lavanda", "batido"), sem o tipo, a marca, o tamanho e palavras curtas."""
    ignorar = set(_sem_acento(tipo_do_produto(produto)).split()) | set(_sem_acento(produto.brand).split())
    palavras = []
    for palavra in _sem_tamanho(produto.nome_legivel or produto.name).split():
        palavra = _sem_acento(palavra)
        if len(palavra) >= 4 and palavra not in ignorar and not palavra.isdigit():
            palavras.append(palavra)
    return palavras


def _imagem_serve(produto, descricao):
    """A descrição da imagem (em inglês, feita pelo Tavily) fala deste
    produto? Marca obrigatória e ao menos metade das palavras da variação."""
    texto = _sem_acento(descricao)
    if not texto or any(sinal in texto for sinal in IMAGEM_COM_VARIOS):
        return False
    marca = _sem_acento(produto.brand).split()
    if not marca or marca[0] not in texto:
        return False
    variacao = _palavras_que_distinguem(produto)
    if variacao:
        achadas = sum(1 for palavra in variacao if palavra[:5] in texto)
        if achadas * 2 < len(variacao):
            return False
    tamanho = tamanho_normalizado(produto.package_size or produto.nome_legivel or produto.name)
    numero = re.match(r'\d+(\.\d+)?', tamanho)
    if numero and re.search(r'\d', texto) and numero.group(0) not in texto.replace(',', '.'):
        return False
    return True


def foto_da_internet(produto):
    """(endereço da imagem, site) ou None. Sem TAVILY_API_KEY, None."""
    if not os.getenv('TAVILY_API_KEY') or not settings.FOTOS_DA_INTERNET:
        return None
    if not produto.brand:
        return None  # sem marca, não dá para conferir a imagem
    termo = ' '.join(filter(None, [produto.nome_legivel or produto.name, produto.brand]))
    for dominios in (SITES_DE_MERCADO, None):
        try:
            resposta = _cliente_tavily().search(
                termo,
                max_results=5,
                search_depth='basic',
                include_images=True,
                include_image_descriptions=True,
                **({'include_domains': dominios} if dominios else {'country': 'brazil'}),
            )
        except Exception as erro:  # rede, limite do plano grátis, chave inválida
            logger.warning('Busca de foto na internet falhou: %s', erro)
            return None
        for imagem in (resposta or {}).get('images') or []:
            if isinstance(imagem, str):
                continue  # sem descrição não dá para conferir
            endereco = (imagem.get('url') or '').strip()
            site = re.sub(r'^www\.', '', urlparse(endereco).netloc)
            if (
                endereco.startswith('https://')
                and site_permitido(site)
                and _imagem_serve(produto, imagem.get('description') or '')
            ):
                return endereco[:500], site[:100]
    return None


def usar_foto_da_internet(produto):
    """Procura (uma vez) a foto da embalagem na internet; True se achou."""
    achada = foto_da_internet(produto)
    produto.foto_internet_buscada_em = timezone.now()
    campos = ['foto_internet_buscada_em', 'updated_at']
    if achada:
        produto.image_url, produto.foto_credito = achada
        produto.foto_fonte = FONTE_INTERNET
        campos += ['image_url', 'foto_credito', 'foto_fonte']
    produto.save(update_fields=campos)
    return bool(achada)


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
        # Sem foto na base aberta (ou só com a ilustrativa): foto da embalagem
        # na internet, uma vez por produto.
        for produto in produtos:
            sem_foto_da_embalagem = not produto.image_url or produto.foto_fonte == FONTE_ILUSTRATIVA
            if sem_foto_da_embalagem and produto.foto_buscada_em is not None and produto.foto_internet_buscada_em is None:
                fotos += usar_foto_da_internet(produto)
        # Quem ainda ficou sem foto ganha a ilustrativa do tipo.
        for produto in produtos:
            if not produto.image_url and produto.foto_buscada_em is not None:
                fotos += usar_foto_do_tipo(produto)
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
