"""Assistente de preços do Início: a pessoa pergunta onde um produto está mais
barato e a IA responde com os preços reais das notas fiscais.

Os preços nunca vêm do modelo: ele chama a ferramenta `buscar_precos`, que usa
os mesmos dados da tela "Produtos com menor preço" (services/precos.py), e só
escreve a resposta. IAs, na ordem: Gemini (GEMINI_API_KEY, plano gratuito) e
Claude (ANTHROPIC_API_KEY); ASSISTENTE_IA=claude inverte. Sem chave, ou se as
chamadas falharem, a resposta sai de frases prontas a partir dos mesmos dados.
"""

import json
import logging
import os
import re
import unicodedata
from decimal import Decimal

import anthropic
from django.conf import settings
from google import genai
from google.genai import errors as erros_gemini
from google.genai import types as tipos_gemini
from django.utils import timezone

from .precos import VALIDADE_DO_PRECO, buscar_precos

logger = logging.getLogger(__name__)

MAX_MENSAGENS = 10          # histórico enviado ao modelo
MAX_CARACTERES = 1000       # por mensagem
MAX_VOLTAS_DE_FERRAMENTA = 4
LOJAS_POR_PRODUTO = 5

SISTEMA = """Você é o assistente de preços do CompraFácil, um app de lista de compras de Manaus (AM).
Os preços vêm de notas fiscais (NFC-e) que as pessoas leram no app: cada preço tem loja, data e, quando a pessoa libera a localização, a distância até ela.

Como responder:
- Para saber onde algo está mais barato, use a ferramenta buscar_precos. Nunca invente preço, loja ou data: use só o que a ferramenta devolver.
- Diga primeiro onde está mais barato (loja, preço e quando foi visto). Depois sugira 2 ou 3 outras lojas com preço baixo, do mais barato ao mais caro, com a distância quando houver.
- Preço com "desatualizado": true foi visto há mais de 1 dia: avise que pode ter mudado.
- Preço por kg: escreva "R$ X/kg".
- Se a ferramenta não achar o produto, diga que ainda não há preço dele e sugira ler a nota fiscal da próxima compra no app para alimentar os preços da cidade.
- Responda em português do Brasil, em poucas frases, sem tabelas nem markdown pesado.
- Assuntos fora de compras, preços e do app: responda em uma frase que você só ajuda com preços e compras."""

FERRAMENTAS = [{
    'name': 'buscar_precos',
    'description': (
        'Busca os preços de um produto nas lojas de Manaus, a partir das notas '
        'fiscais lidas no app. Devolve os produtos parecidos com o texto, cada '
        'um com as lojas do mais barato ao mais caro (preço, medida, data, se '
        'pode ter mudado e distância quando conhecida) e o menor preço do período.'
    ),
    'strict': True,
    'input_schema': {
        'type': 'object',
        'properties': {
            'produto': {
                'type': 'string',
                'description': 'Nome do produto, curto, como a pessoa falaria (ex.: "arroz", "leite integral").',
            },
            'dias': {
                'type': 'integer',
                'enum': [7, 30, 90],
                'description': 'Período do menor preço; use 30 se a pessoa não disser.',
            },
        },
        'required': ['produto', 'dias'],
        'additionalProperties': False,
    },
}]


class MensagemInvalida(ValueError):
    pass


# --- Histórico -------------------------------------------------------------

def preparar_mensagens(mensagens):
    """Valida o histórico que o site manda ([{papel, texto}]) e converte para
    o formato da API: alternado, começando e terminando no usuário."""
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


# --- Ferramenta ------------------------------------------------------------

def _data(valor):
    return timezone.localtime(valor).strftime('%d/%m/%Y')


def resultado_da_busca(produto, dias=30, origem=None):
    """O que a ferramenta devolve ao modelo (JSON enxuto)."""
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


# --- Claude ----------------------------------------------------------------

def _cliente():
    return anthropic.Anthropic(timeout=60.0, max_retries=2)


def _texto(resposta):
    return '\n'.join(b.text for b in resposta.content if b.type == 'text').strip()


def responder_com_claude(mensagens, origem=None):
    """Conversa com o Claude, executando a ferramenta de preços quando ele
    pedir. Devolve o texto final, ou None se ele recusar."""
    cliente = _cliente()
    conversa = list(mensagens)

    for _ in range(MAX_VOLTAS_DE_FERRAMENTA):
        resposta = cliente.beta.messages.create(
            model=settings.ASSISTENTE_MODELO,
            max_tokens=4000,
            system=SISTEMA,
            tools=FERRAMENTAS,
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
            return _texto(resposta)

        conversa.append({'role': 'assistant', 'content': resposta.content})
        resultados = []
        for bloco in resposta.content:
            if bloco.type != 'tool_use':
                continue
            if bloco.name != 'buscar_precos':
                resultados.append({
                    'type': 'tool_result', 'tool_use_id': bloco.id,
                    'content': 'Ferramenta desconhecida.', 'is_error': True,
                })
                continue
            entrada = bloco.input if isinstance(bloco.input, dict) else {}
            dados = resultado_da_busca(entrada.get('produto', ''), entrada.get('dias', 30), origem)
            resultados.append({
                'type': 'tool_result', 'tool_use_id': bloco.id,
                'content': json.dumps(dados, ensure_ascii=False, default=str),
            })
        conversa.append({'role': 'user', 'content': resultados})

    # Muitas voltas: devolve o que tiver de texto.
    return _texto(resposta) or None


# --- Gemini ------------------------------------------------------------------

def _cliente_gemini():
    return genai.Client()  # lê GEMINI_API_KEY do ambiente


def responder_com_gemini(mensagens, origem=None):
    """Conversa com o Gemini. A biblioteca chama a função buscar_precos
    sozinha quando o modelo pede e devolve o texto final."""

    def buscar_precos(produto: str, dias: int) -> dict:
        """Busca os preços de um produto nas lojas de Manaus, a partir das notas
        fiscais lidas no app. Devolve os produtos parecidos com o texto, cada um
        com as lojas do mais barato ao mais caro (preço, medida, data, se pode ter
        mudado e distância quando conhecida) e o menor preço do período.

        Args:
            produto: nome do produto, curto, como a pessoa falaria (ex.: "arroz").
            dias: período do menor preço: 7, 30 ou 90 (use 30 se a pessoa não disser).
        """
        dados = resultado_da_busca(produto, dias, origem)
        # A biblioteca manda o retorno como JSON: datas e decimais já são texto.
        return json.loads(json.dumps(dados, ensure_ascii=False, default=str))

    conteudo = [
        {'role': 'user' if m['role'] == 'user' else 'model', 'parts': [{'text': m['content']}]}
        for m in mensagens
    ]
    resposta = _cliente_gemini().models.generate_content(
        model=settings.ASSISTENTE_MODELO_GEMINI,
        contents=conteudo,
        config=tipos_gemini.GenerateContentConfig(
            system_instruction=SISTEMA,
            tools=[buscar_precos],
            max_output_tokens=2000,
            automatic_function_calling=tipos_gemini.AutomaticFunctionCallingConfig(
                maximum_remote_calls=MAX_VOLTAS_DE_FERRAMENTA,
            ),
        ),
    )
    return (resposta.text or '').strip() or None


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

def _com_gemini(mensagens, origem):
    try:
        return responder_com_gemini(mensagens, origem)
    except erros_gemini.APIError as erro:
        logger.warning('Assistente: erro na API do Gemini: %s', erro)
    except Exception:  # erro da biblioteca ou da função: cai no próximo
        logger.exception('Assistente: falha ao responder com o Gemini.')
    return None


def _com_claude(mensagens, origem):
    try:
        return responder_com_claude(mensagens, origem)
    except anthropic.APIError as erro:
        logger.warning('Assistente: erro na API do Claude: %s', erro)
    return None


def ias_disponiveis():
    """[(fonte, função)] das IAs com chave, na ordem de preferência."""
    ias = []
    if os.getenv('GEMINI_API_KEY'):
        ias.append(('gemini', _com_gemini))
    if os.getenv('ANTHROPIC_API_KEY'):
        ias.append(('claude', _com_claude))
    if settings.ASSISTENTE_IA == 'claude':
        ias.sort(key=lambda ia: ia[0] != 'claude')
    return ias


def responder(mensagens, origem=None):
    """{'resposta': texto, 'fonte': 'gemini' | 'claude' | 'sem_ia'}."""
    convertidas = preparar_mensagens(mensagens)
    pergunta = convertidas[-1]['content']

    for fonte, responder_com_ia in ias_disponiveis():
        texto = responder_com_ia(convertidas, origem)
        if texto:
            return {'resposta': texto, 'fonte': fonte}

    return {'resposta': responder_sem_ia(pergunta, origem), 'fonte': 'sem_ia'}
