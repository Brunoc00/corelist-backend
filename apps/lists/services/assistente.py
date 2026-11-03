"""Assistente de compras do Início: a pessoa pergunta onde comprar algo mais
barato (ex.: "onde tem arroz mais barato perto de mim?") e a IA responde pelo
que foi perguntado.

A IA nunca inventa loja nem preço: ela chama ferramentas do app e só escreve a
resposta. Ferramentas (as mesmas para todas as IAs):
- mercados_perto: supermercados e atacarejos ao redor da pessoa (OpenStreetMap);
- buscar_promocoes: promoções e encartes na internet (Tavily);
- buscar_precos: preços das notas fiscais lidas no app (extra).

IAs, todas opcionais, na ordem: Gemini (GEMINI_API_KEY, plano gratuito), Groq
(GROQ_API_KEY, plano gratuito) e Claude (ANTHROPIC_API_KEY, pago).
ASSISTENTE_IA=groq ou claude põe essa primeiro. Se uma falhar (cota do dia,
erro), tenta a próxima; sem nenhuma, responde com frases prontas a partir dos
preços das notas. Com faturamento ativo no Google, ASSISTENTE_INTERNET=1 usa
antes o Gemini com Google Maps e busca do Google.
"""

import json
import logging
import os
import re
import unicodedata
from decimal import Decimal
from urllib.parse import urlencode

import anthropic
import groq
from django.conf import settings
from django.utils import timezone
from google import genai
from google.genai import errors as erros_gemini
from google.genai import types as tipos_gemini

from apps.products.models import Product

from .busca import pontuar
from .ferramentas_compras import buscar_promocoes, mercados_perto
from .precos import VALIDADE_DO_PRECO, buscar_precos, link_como_chegar

logger = logging.getLogger(__name__)

MAX_MENSAGENS = 10          # histórico enviado ao modelo
MAX_CARACTERES = 1000       # por mensagem
MAX_VOLTAS_DE_FERRAMENTA = 5
LOJAS_POR_PRODUTO = 5
MAX_FONTES = 8
MAX_PRODUTOS = 3
MAX_LOJAS = 5

SISTEMA = """Você é o assistente de compras do CompraFácil, um app de lista de compras de Manaus (AM).
A pessoa pergunta onde comprar algo mais barato. Responda pelo que ela perguntou, usando as ferramentas:
- mercados_perto: supermercados e atacarejos perto da localização dela (nome, distância, endereço);
- buscar_promocoes: promoções, encartes e preços publicados na internet para o produto em Manaus;
- buscar_precos: preços vistos em notas fiscais lidas no app (com loja e data).

Como responder:
- Para "onde tem X mais barato perto de mim": chame mercados_perto, buscar_promocoes e buscar_precos.
- Comece pelo que é mais útil: o melhor preço encontrado (de onde veio e quando) e os mercados mais próximos, com a distância.
- Preço da internet: diga o nome do site ou do mercado e avise que pode ter mudado ou valer só na promoção.
- Preço de nota fiscal: diga "visto numa nota fiscal em DD/MM". Se "desatualizado": true, avise que pode ter mudado. Preço por kg: "R$ X/kg".
- Nunca invente mercado, endereço, distância ou preço, nem cite mercados que você conhece de memória: use só o que as ferramentas devolverem. Se nenhuma achar preço, diga isso e indique os mercados mais próximos que mercados_perto devolveu (atacarejos costumam ser mais baratos), para a pessoa conferir.
- Preço da internet: os trechos de páginas de loja costumam trazer preços de vários produtos misturados. Só use um preço que apareça claramente junto do nome e da embalagem do mesmo produto (ex.: "Arroz Camil 5 kg R$ 22,29"), e escreva a embalagem. Desconfie de preço muito abaixo do normal (ex.: 5 kg de arroz por menos de R$ 10): nesse caso, não use.
- Site de rede nacional (ex.: Carrefour, Assaí) pode ter preço diferente na loja de Manaus: avise.
- Se a localização não foi informada, peça para tocar em "Usar minha localização".
- Responda em português do Brasil, em texto simples: poucas frases ou uma lista curta com hífen, sem negrito, sem asteriscos e sem tabelas. Sem links no texto: as fontes aparecem separadas no app.
- Assuntos fora de compras e preços: responda em uma frase que você só ajuda com compras."""

ESPECIFICACOES = [
    {
        'name': 'buscar_precos',
        'description': (
            'Preços de um produto vistos em notas fiscais lidas no app, nas lojas de '
            'Manaus: as lojas do mais barato ao mais caro (preço, medida, data, se '
            'pode ter mudado e distância quando conhecida) e o menor preço do período.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'produto': {'type': 'string', 'description': 'Nome curto do produto (ex.: "arroz").'},
                'dias': {'type': 'integer', 'enum': [7, 30, 90], 'description': 'Período; use 30 se a pessoa não disser.'},
            },
            'required': ['produto', 'dias'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'mercados_perto',
        'description': (
            'Supermercados e atacarejos perto da localização da pessoa (OpenStreetMap), '
            'do mais perto ao mais longe, com distância em km e endereço quando houver.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'raio_km': {'type': 'integer', 'description': 'Raio da busca em km, de 1 a 10; use 3 se a pessoa não disser.'},
            },
            'required': ['raio_km'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'buscar_promocoes',
        'description': (
            'Busca na internet promoções, encartes e preços publicados do produto em '
            'supermercados de Manaus. Devolve título, endereço da página e um trecho.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'produto': {'type': 'string', 'description': 'Nome curto do produto (ex.: "arroz 5 kg").'},
            },
            'required': ['produto'],
            'additionalProperties': False,
        },
    },
]


class MensagemInvalida(ValueError):
    pass


# --- Histórico -------------------------------------------------------------

def preparar_mensagens(mensagens):
    """Valida o histórico que o site manda ([{papel, texto}]) e converte para
    o formato das APIs: alternado, começando e terminando no usuário."""
    if not isinstance(mensagens, list) or not mensagens:
        raise MensagemInvalida('Escreva uma pergunta.')

    convertidas = []
    for mensagem in mensagens[-MAX_MENSAGENS:]:
        if not isinstance(mensagem, dict):
            raise MensagemInvalida('Mensagem inválida.')
        papel = {'usuario': 'user', 'assistente': 'assistant'}.get(mensagem.get('papel'))
        texto = str(mensagem.get('texto') or '').strip()[:MAX_CARACTERES]
        if papel is None or not texto:
            continue
        if convertidas and convertidas[-1]['role'] == papel:
            convertidas[-1]['content'] += '\n' + texto
        else:
            convertidas.append({'role': papel, 'content': texto})

    while convertidas and convertidas[0]['role'] != 'user':
        convertidas.pop(0)
    if not convertidas or convertidas[-1]['role'] != 'user':
        raise MensagemInvalida('Escreva uma pergunta.')
    return convertidas


def _sistema(origem):
    onde = 'informada' if origem else 'não informada'
    return f'{SISTEMA}\n\nData de hoje: {timezone.localdate():%d/%m/%Y}. Localização da pessoa: {onde}.'


# --- Ferramentas -------------------------------------------------------------

def _data(valor):
    return timezone.localtime(valor).strftime('%d/%m/%Y')


def resultado_da_busca(produto, dias=30, origem=None):
    """Preços das notas fiscais para a IA (JSON enxuto)."""
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        dias = 30
    resumos = buscar_precos(produto, dias if dias in (7, 30, 90) else 30, limite=5, origem=origem)
    produtos = []
    for resumo in resumos:
        menor = resumo['menor_no_periodo']
        produtos.append({
            'produto': resumo['produto']['name'],
            'embalagem': resumo['produto']['embalagem'] or None,
            'lojas': [
                {
                    'loja': linha['loja']['marca'] or linha['loja']['nome'],
                    'endereco': linha['loja']['endereco'] or None,
                    'preco': linha['preco'],
                    'medida': linha['medida'],
                    'visto_em': _data(linha['data']),
                    'desatualizado': linha['desatualizado'],
                    'distancia_km': linha['loja'].get('distancia_km'),
                }
                for linha in resumo['lojas'][:LOJAS_POR_PRODUTO]
            ],
            'menor_preco_no_periodo': None if menor is None else {
                'dias': menor['dias'],
                'preco': menor['preco'],
                'medida': menor['medida'],
                'loja': menor['loja']['marca'] or menor['loja']['nome'],
                'visto_em': _data(menor['data']),
            },
        })
    return {'busca': produto, 'produtos': produtos}


def nova_coleta():
    """O que as ferramentas juntam para mostrar abaixo da resposta."""
    return {'fontes': [], 'lojas': []}


def executar(nome, argumentos, origem, coleta):
    """Roda a ferramenta pedida pela IA e devolve um dict serializável."""
    argumentos = argumentos if isinstance(argumentos, dict) else {}
    if nome == 'buscar_precos':
        dados = resultado_da_busca(argumentos.get('produto', ''), argumentos.get('dias', 30), origem)
    elif nome == 'mercados_perto':
        dados = mercados_perto(origem, argumentos.get('raio_km', 3), coleta['fontes'], coleta['lojas'])
    elif nome == 'buscar_promocoes':
        dados = buscar_promocoes(argumentos.get('produto', ''), coleta['fontes'])
    else:
        dados = {'erro': f'Ferramenta desconhecida: {nome}'}
    return json.loads(json.dumps(dados, ensure_ascii=False, default=str))


def _sem_repetir(fontes):
    unicas, vistas = [], set()
    for fonte in fontes:
        if fonte['url'] not in vistas:
            vistas.add(fonte['url'])
            unicas.append(fonte)
    return unicas[:MAX_FONTES]


def _texto_simples(texto):
    """O chat mostra texto puro: tira negrito/itálico em markdown e títulos."""
    texto = re.sub(r'\*\*|__', '', texto or '')
    texto = re.sub(r'(?m)^#{1,6}\s*', '', texto)
    texto = re.sub(r'(?m)^\s*\*\s+', '- ', texto)
    return texto.strip()


def _saida(texto, coleta):
    texto = _texto_simples(texto)
    return {'texto': texto, 'coleta': coleta} if texto else None


def produtos_para_mostrar(termo, origem=None):
    """Até 3 produtos com foto para o cartão: primeiro os que têm preço nas
    notas (com o menor preço); senão, produtos parecidos do catálogo."""
    if not termo:
        return []
    produtos, vistos = [], set()
    for resumo in buscar_precos(termo, limite=MAX_PRODUTOS, origem=origem):
        produto = resumo['produto']
        lojas = sorted(resumo['lojas'], key=lambda linha: Decimal(linha['preco']))
        melhor = lojas[0] if lojas else None
        vistos.add(produto['id'])
        produtos.append({
            'id': produto['id'],
            'nome': produto['name'],
            'embalagem': produto['embalagem'] or None,
            'imagem_url': produto['image_url'] or None,
            'preco': melhor['preco'] if melhor else None,
            'medida': melhor['medida'] if melhor else None,
            'loja': (melhor['loja']['marca'] or melhor['loja']['nome']) if melhor else None,
        })
    if not any(p['imagem_url'] for p in produtos):
        com_foto = [
            (pontuar(termo, produto), produto)
            for produto in Product.objects.exclude(image_url='').exclude(id__in=vistos)
        ]
        com_foto = sorted((par for par in com_foto if par[0] > 0), key=lambda par: -par[0])
        for _, produto in com_foto[:MAX_PRODUTOS - len(produtos)]:
            produtos.append({
                'id': produto.id, 'nome': produto.name,
                'embalagem': (produto.package_size or produto.unit or '').strip() or None,
                'imagem_url': produto.image_url, 'preco': None, 'medida': None, 'loja': None,
            })
    return produtos[:MAX_PRODUTOS]


def lojas_das_notas(termo, origem=None):
    """Lojas onde o produto apareceu nas notas, com logo e rota."""
    if not termo:
        return []
    lojas = []
    for resumo in buscar_precos(termo, limite=MAX_PRODUTOS, origem=origem):
        for linha in resumo['lojas']:
            loja = linha['loja']
            lojas.append({
                'nome': loja['nome'], 'marca': loja['marca'], 'icone': loja['icone'],
                'distancia_km': loja.get('distancia_km'), 'url': link_como_chegar(loja),
            })
    return lojas


def _juntar_lojas(*listas):
    juntas, vistas = [], set()
    for lista in listas:
        for loja in lista:
            chave = (loja['marca'] or loja['nome']).lower(), loja.get('distancia_km')
            if chave not in vistas:
                vistas.add(chave)
                juntas.append(loja)
    return juntas[:MAX_LOJAS]


# --- Gemini (plano gratuito, com as ferramentas do app) ---------------------

def _cliente_gemini():
    # Lê GEMINI_API_KEY do ambiente. Uma tentativa só e 25 s no máximo: se o
    # Google estiver com "muita procura" (503), o Groq assume logo.
    return genai.Client(http_options=tipos_gemini.HttpOptions(
        timeout=25_000,
        retry_options=tipos_gemini.HttpRetryOptions(attempts=1),
    ))


def responder_com_gemini(mensagens, origem=None):
    """A biblioteca do Gemini chama as funções sozinha quando o modelo pede."""
    coleta = nova_coleta()

    def buscar_precos(produto: str, dias: int) -> dict:
        """Preços de um produto vistos em notas fiscais lidas no app, nas lojas
        de Manaus (preço, medida, data, se pode ter mudado e distância).

        Args:
            produto: nome curto do produto (ex.: "arroz").
            dias: período do menor preço: 7, 30 ou 90 (use 30 se a pessoa não disser).
        """
        return executar('buscar_precos', {'produto': produto, 'dias': dias}, origem, coleta)

    def mercados_perto(raio_km: int) -> dict:
        """Supermercados e atacarejos perto da localização da pessoa
        (OpenStreetMap), do mais perto ao mais longe, com distância e endereço.

        Args:
            raio_km: raio da busca em km, de 1 a 10 (use 3 se a pessoa não disser).
        """
        return executar('mercados_perto', {'raio_km': raio_km}, origem, coleta)

    def buscar_promocoes(produto: str) -> dict:
        """Busca na internet promoções, encartes e preços publicados do produto
        em supermercados de Manaus (título, endereço da página e trecho).

        Args:
            produto: nome curto do produto (ex.: "arroz 5 kg").
        """
        return executar('buscar_promocoes', {'produto': produto}, origem, coleta)

    # Guardar o cliente numa variável: se ele for descartado no meio da
    # chamada, a conexão fecha ("client has been closed").
    cliente = _cliente_gemini()
    resposta = cliente.models.generate_content(
        model=settings.ASSISTENTE_MODELO_GEMINI,
        contents=[
            {'role': 'user' if m['role'] == 'user' else 'model', 'parts': [{'text': m['content']}]}
            for m in mensagens
        ],
        config=tipos_gemini.GenerateContentConfig(
            system_instruction=_sistema(origem),
            tools=[buscar_precos, mercados_perto, buscar_promocoes],
            max_output_tokens=2000,
            automatic_function_calling=tipos_gemini.AutomaticFunctionCallingConfig(
                maximum_remote_calls=MAX_VOLTAS_DE_FERRAMENTA,
            ),
        ),
    )
    return _saida(resposta.text, coleta)


# --- Groq (plano gratuito, reserva do Gemini) -------------------------------

def _cliente_groq():
    return groq.Groq(timeout=40.0, max_retries=1)  # lê GROQ_API_KEY do ambiente


def responder_com_groq(mensagens, origem=None):
    coleta = nova_coleta()
    ferramentas = [
        {'type': 'function', 'function': {
            'name': e['name'], 'description': e['description'], 'parameters': e['parameters'],
        }}
        for e in ESPECIFICACOES
    ]
    conversa = [{'role': 'system', 'content': _sistema(origem)}] + [
        {'role': m['role'], 'content': m['content']} for m in mensagens
    ]
    cliente = _cliente_groq()

    for _ in range(MAX_VOLTAS_DE_FERRAMENTA):
        resposta = cliente.chat.completions.create(
            model=settings.ASSISTENTE_MODELO_GROQ,
            messages=conversa,
            tools=ferramentas,
            tool_choice='auto',
            max_completion_tokens=2000,
        )
        mensagem = resposta.choices[0].message
        if not mensagem.tool_calls:
            return _saida(mensagem.content, coleta)

        conversa.append({
            'role': 'assistant',
            'content': mensagem.content or '',
            'tool_calls': [
                {'id': c.id, 'type': 'function', 'function': {'name': c.function.name, 'arguments': c.function.arguments}}
                for c in mensagem.tool_calls
            ],
        })
        for chamada in mensagem.tool_calls:
            try:
                argumentos = json.loads(chamada.function.arguments or '{}')
            except ValueError:
                argumentos = {}
            dados = executar(chamada.function.name, argumentos, origem, coleta)
            conversa.append({
                'role': 'tool', 'tool_call_id': chamada.id,
                'content': json.dumps(dados, ensure_ascii=False),
            })
    return None


# --- Claude (pago; só com ANTHROPIC_API_KEY) ---------------------------------

def _cliente():
    return anthropic.Anthropic(timeout=60.0, max_retries=2)


def _texto(resposta):
    return '\n'.join(b.text for b in resposta.content if b.type == 'text').strip()


def responder_com_claude(mensagens, origem=None):
    coleta = nova_coleta()
    ferramentas = [
        {'name': e['name'], 'description': e['description'], 'strict': True, 'input_schema': e['parameters']}
        for e in ESPECIFICACOES
    ]
    cliente = _cliente()
    conversa = list(mensagens)

    for _ in range(MAX_VOLTAS_DE_FERRAMENTA):
        resposta = cliente.beta.messages.create(
            model=settings.ASSISTENTE_MODELO,
            max_tokens=4000,
            system=_sistema(origem),
            tools=ferramentas,
            messages=conversa,
            # Chat simples: pouco raciocínio basta e responde mais rápido.
            output_config={'effort': 'low'},
            # Se o modelo recusar por política, a API tenta o modelo indicado.
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
        )
        if resposta.stop_reason == 'refusal':
            return None
        if resposta.stop_reason != 'tool_use':
            return _saida(_texto(resposta), coleta)

        conversa.append({'role': 'assistant', 'content': resposta.content})
        conversa.append({'role': 'user', 'content': [
            {
                'type': 'tool_result', 'tool_use_id': bloco.id,
                'content': json.dumps(executar(bloco.name, bloco.input, origem, coleta), ensure_ascii=False),
            }
            for bloco in resposta.content if bloco.type == 'tool_use'
        ]})
    return _saida(_texto(resposta), coleta)


# --- Gemini com Google Maps e busca do Google (precisa de faturamento) ------

SISTEMA_INTERNET = """Você é o assistente de compras do CompraFácil, um app de lista de compras de Manaus (AM).
A pessoa pergunta onde comprar algo mais barato. Responda pelo que ela perguntou, usando:
- o Google Maps para achar supermercados e atacarejos perto da localização dela (nome, bairro e distância aproximada);
- a busca do Google para preços e promoções publicados (encartes, sites e redes sociais dos mercados de Manaus).

Regras:
- Nunca invente preço. Preço da internet: diga de onde veio e que pode ter mudado ou valer só na promoção.
- Se não achar preço do produto, diga isso e indique os 2 ou 3 mercados mais próximos que costumam ter preço baixo (atacarejos), para a pessoa conferir.
- Se o contexto trouxer "preços de notas fiscais" do app, cite-os como um extra ("visto numa nota fiscal em DD/MM"), sem depender deles.
- Responda em português do Brasil, em poucas frases ou uma lista curta, sem tabelas. Sem links no texto: as fontes aparecem separadas no app.
- Assuntos fora de compras e preços: responda em uma frase que você só ajuda com compras."""


def _fontes_do_google(resposta):
    """Links que o Google exige mostrar junto da resposta (Maps e busca)."""
    fontes = []
    for candidato in (getattr(resposta, 'candidates', None) or [])[:1]:
        metadados = getattr(candidato, 'grounding_metadata', None)
        for trecho in (getattr(metadados, 'grounding_chunks', None) or []):
            for tipo in ('maps', 'web'):
                origem = getattr(trecho, tipo, None)
                url = getattr(origem, 'uri', None)
                if origem and url:
                    fontes.append({'tipo': tipo, 'titulo': origem.title or url, 'url': url})
        for consulta in (getattr(metadados, 'web_search_queries', None) or [])[:3]:
            url = 'https://www.google.com/search?' + urlencode({'q': consulta})
            fontes.append({'tipo': 'busca', 'titulo': consulta, 'url': url})
    return fontes


def responder_com_gemini_na_internet(mensagens, origem=None):
    termo = termo_da_pergunta(mensagens[-1]['content'])
    notas = resultado_da_busca(termo, 30, origem)['produtos'] if termo else []
    onde = (
        f'Localização da pessoa: latitude {origem[0]:.3f}, longitude {origem[1]:.3f} (Manaus).'
        if origem else 'A pessoa está em Manaus (localização exata não informada).'
    )
    contexto = (
        f'\n\n{onde}\nData de hoje: {timezone.localdate():%d/%m/%Y}.'
        '\nPreços de notas fiscais do app (extra, pode estar vazio): '
        + json.dumps(notas, ensure_ascii=False, default=str)
    )
    config_ferramentas = None
    if origem:
        config_ferramentas = tipos_gemini.ToolConfig(
            retrieval_config=tipos_gemini.RetrievalConfig(
                lat_lng=tipos_gemini.LatLng(latitude=origem[0], longitude=origem[1]),
                language_code='pt_BR',
            ),
        )

    cliente = _cliente_gemini()
    resposta = cliente.models.generate_content(
        model=settings.ASSISTENTE_MODELO_GEMINI,
        contents=[
            {'role': 'user' if m['role'] == 'user' else 'model', 'parts': [{'text': m['content']}]}
            for m in mensagens
        ],
        config=tipos_gemini.GenerateContentConfig(
            system_instruction=SISTEMA_INTERNET + contexto,
            tools=[
                tipos_gemini.Tool(google_maps=tipos_gemini.GoogleMaps()),
                tipos_gemini.Tool(google_search=tipos_gemini.GoogleSearch()),
            ],
            tool_config=config_ferramentas,
            max_output_tokens=2000,
        ),
    )
    return _saida(resposta.text, {'fontes': _fontes_do_google(resposta), 'lojas': []})


# --- Sem IA (sem chave ou com erro) ----------------------------------------

PALAVRAS_DE_PERGUNTA = {
    'onde', 'esta', 'estao', 'mais', 'barato', 'barata', 'baratos', 'baratas',
    'menor', 'preco', 'precos', 'qual', 'quais', 'quanto', 'custa', 'custam',
    'encontro', 'acho', 'comprar', 'compro', 'tem', 'o', 'a', 'os', 'as', 'de',
    'do', 'da', 'dos', 'das', 'em', 'no', 'na', 'um', 'uma', 'e', 'pra', 'para',
    'perto', 'mim', 'aqui', 'hoje', 'agora', 'me', 'diga', 'sabe', 'loja',
    'lojas', 'mercado', 'supermercado', 'que', 'eu', 'posso', 'quero', 'ta',
}


def termo_da_pergunta(pergunta):
    sem_acento = unicodedata.normalize('NFKD', pergunta.lower())
    sem_acento = ''.join(c for c in sem_acento if not unicodedata.combining(c))
    palavras = re.findall(r'[a-z0-9]+', sem_acento)
    return ' '.join(p for p in palavras if p not in PALAVRAS_DE_PERGUNTA)


def _preco_texto(linha):
    valor = f"R$ {Decimal(linha['preco']):.2f}".replace('.', ',')
    return valor + ('/kg' if linha['medida'] == 'kg' else '')


def _loja_texto(linha):
    loja = linha['loja']
    nome = loja['marca'] or loja['nome']
    distancia = loja.get('distancia_km')
    return f'{nome} (a {str(distancia).replace(".", ",")} km)' if distancia is not None else nome


def responder_sem_ia(pergunta, origem=None):
    termo = termo_da_pergunta(pergunta)
    if not termo:
        return 'Me diga o nome de um produto, por exemplo: "onde o arroz está mais barato?".'

    resumos = [r for r in buscar_precos(termo, limite=1, origem=origem) if r['lojas']]
    if not resumos:
        return (
            f'Ainda não tenho preço de "{termo}" nas notas fiscais. Leia a nota da sua '
            'próxima compra no app para alimentar os preços da cidade.'
        )

    resumo = resumos[0]
    agora = timezone.now()
    lojas = sorted(resumo['lojas'], key=lambda linha: Decimal(linha['preco']))
    melhor = lojas[0]
    frase = (
        f"{resumo['produto']['name']}: o mais barato é {_preco_texto(melhor)} no "
        f"{_loja_texto(melhor)}, visto em {_data(melhor['data'])}"
    )
    if agora - melhor['data'] > VALIDADE_DO_PRECO:
        frase += ' (pode ter mudado)'
    frase += '.'
    outras = [f'{_loja_texto(l)}: {_preco_texto(l)}' for l in lojas[1:4]]
    if outras:
        frase += ' Outras opções: ' + '; '.join(outras) + '.'
    return frase


# --- Entrada -----------------------------------------------------------------

ERROS_ESPERADOS = (erros_gemini.APIError, groq.APIError, anthropic.APIError)


def _tentar(nome, funcao):
    def tentar(mensagens, origem):
        try:
            return funcao(mensagens, origem)
        except ERROS_ESPERADOS as erro:  # cota do dia, chave, rede
            logger.warning('Assistente: %s falhou: %s', nome, erro)
        except Exception:  # erro inesperado: registra e tenta a próxima IA
            logger.exception('Assistente: falha inesperada em %s.', nome)
        return None
    return tentar


def ias_disponiveis():
    """[(fonte, função)] das IAs com chave, na ordem de preferência."""
    ias = []
    if os.getenv('GEMINI_API_KEY'):
        if settings.ASSISTENTE_INTERNET:
            ias.append(('gemini', _tentar('Gemini com Google', responder_com_gemini_na_internet)))
        ias.append(('gemini', _tentar('Gemini', responder_com_gemini)))
    if os.getenv('GROQ_API_KEY'):
        ias.append(('groq', _tentar('Groq', responder_com_groq)))
    if os.getenv('ANTHROPIC_API_KEY'):
        ias.append(('claude', _tentar('Claude', responder_com_claude)))
    if settings.ASSISTENTE_IA in ('groq', 'claude'):
        ias.sort(key=lambda ia: ia[0] != settings.ASSISTENTE_IA)
    return ias


def responder(mensagens, origem=None):
    """{'resposta', 'fonte': 'gemini' | 'groq' | 'claude' | 'sem_ia', 'fontes',
    'produtos' (com foto), 'lojas' (com logo e rota)}."""
    convertidas = preparar_mensagens(mensagens)
    pergunta = convertidas[-1]['content']
    termo = termo_da_pergunta(pergunta)

    texto, fonte, coleta = None, 'sem_ia', nova_coleta()
    for nome, responder_com_ia in ias_disponiveis():
        saida = responder_com_ia(convertidas, origem)
        if saida:
            texto, fonte, coleta = saida['texto'], nome, saida['coleta']
            break
    if texto is None:
        texto = responder_sem_ia(pergunta, origem)
    if origem and termo and not coleta['lojas']:
        # A IA às vezes não pede os mercados perto: o app busca para os cartões.
        mercados_perto(origem, 3, None, coleta['lojas'])

    return {
        'resposta': texto,
        'fonte': fonte,
        'fontes': _sem_repetir(coleta['fontes']),
        'produtos': produtos_para_mostrar(termo, origem),
        'lojas': _juntar_lojas(lojas_das_notas(termo, origem), coleta['lojas']),
    }
