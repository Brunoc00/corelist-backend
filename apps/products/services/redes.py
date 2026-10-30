"""Redes de supermercado conhecidas em Manaus.

A nota traz a razão social da empresa ("SENDAS DISTRIBUIDORA S/A"), que nem
sempre é o nome que as pessoas conhecem ("Assaí Atacadista"). Aqui ficam as
redes confirmadas, com o nome conhecido e a chave do ícone, que o site guarda
em public/lojas/<chave>.png. Loja fora da lista mostra as iniciais.
"""

import re
import unicodedata

# (palavras da razão social, nome conhecido, chave do ícone)
REDES = [
    (('SENDAS DISTRIBUIDORA',), 'Assaí Atacadista', 'assai'),
    (('ATACADAO',), 'Atacadão', 'atacadao'),
    (('CARREFOUR',), 'Carrefour', 'carrefour'),
    (('DB SUPERMERCADO', 'D B SUPERMERCADO'), 'DB Supermercados', 'db'),
]


def _normalizar(texto):
    sem_acento = unicodedata.normalize('NFKD', texto or '')
    sem_acento = ''.join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', sem_acento.upper()).strip()


def rede_da_loja(razao_social):
    """Devolve (nome conhecido, chave do ícone) ou (None, None)."""
    nome = _normalizar(razao_social)
    for palavras, conhecido, chave in REDES:
        if any(palavra in nome for palavra in palavras):
            return conhecido, chave
    return None, None
