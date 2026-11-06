"""Categoria pelo nome do produto, para os itens que chegam pela nota fiscal.

A nota não traz categoria, só nomes abreviados ("SH PALM 350ML",
"DT PO SONHO 1,6KG", "CEBOLA SC - KG"). Sem categoria, o gasto não aparece em
"Para onde vai seu dinheiro" nem em Meus gastos. Regras simples por palavras;
o que não combina com nada fica sem categoria.
"""

import re
import unicodedata

from ..models import Category

# Abreviações das notas que só valem como a primeira palavra do nome.
PRIMEIRA_PALAVRA = {
    'sh': 'Higiene',   # shampoo
    'co': 'Higiene',   # condicionador
    'cd': 'Higiene',   # creme dental
    'da': 'Higiene',   # desodorante aerossol
    'st': 'Higiene',   # sabonete
    'ap': 'Higiene',   # aparelho de barbear
    'abs': 'Higiene',  # absorvente
    'deo': 'Higiene',
    'ph': 'Higiene',   # papel higiênico
    'dt': 'Limpeza',   # detergente / sabão em pó
    'la': 'Limpeza',   # lã de aço
    'ag': 'Bebidas',   # água
    'refri': 'Bebidas',
    'lte': 'Alimentos',
    'iog': 'Alimentos',
    'alf': 'Alimentos',
    'ling': 'Alimentos',
    'salg': 'Alimentos',
    'bisc': 'Alimentos',
    'choc': 'Alimentos',
    'cer': 'Alimentos',     # cereal matinal
    'muss': 'Alimentos',    # mussarela
    'cobert': 'Alimentos',
    'sbt': 'Higiene',       # sabonete
    'lo': 'Higiene',        # loção
    'sh/cond': 'Higiene',
}

# Começos de palavra, conferidos na ordem (o primeiro que achar vale).
PALAVRAS = [
    ('Higiene', (
        'shampoo', 'condicion', 'sabonete', 'desodor', 'creme dental', 'escova dent',
        'fio dental', 'absorv', 'fralda', 'barbear', 'papel hig', 'cotonete',
        'protetor solar', 'hidratante', 'haste flex',
    )),
    ('Limpeza', (
        'detergente', 'deterg', 'sabao', 'amaciante', 'amac', 'desinfet', 'agua sanit',
        'alvejante', 'bombril', 'esponja', 'multiuso', 'limpa', 'papel toalha', 'saco lixo',
        'saco de lixo', 'veja', 'pinho', 'lustra', 'inseticida',
    )),
    ('Bebidas', (
        'refrigerante', 'refrig', 'coca', 'guarana', 'suco', 'agua mineral', 'agua s/gas',
        'cerveja', 'cerv', 'vinho', 'energetico', 'cha ', 'achocolatado liq', 'nectar',
    )),
    ('Padaria', (
        'pao', 'paes', 'panetone', 'bolo', 'torrada', 'rosca', 'sonho', 'croissant', 'bisnaguinha',
    )),
    ('Alimentos', (
        'arroz', 'feij', 'macarr', 'cafe', 'acucar', 'oleo', 'azeite', 'farinha', 'flocao',
        'fuba', 'leite', 'iogurte', 'queijo', 'manteiga', 'margarina', 'requeij', 'ovo',
        'carne', 'bov', 'frango', 'coxa', 'coxinha', 'peito', 'linguica', 'musculo',
        'patinho', 'acem', 'picanha', 'costela', 'peixe', 'tambaqui', 'tilapia', 'pirarucu',
        'cebola', 'alho', 'tomate', 'batata', 'pimentao', 'pimenta', 'cheiro verde',
        'alface', 'cenoura', 'banana', 'maca', 'tanger', 'laranja', 'limao', 'mamao',
        'abacaxi', 'melancia', 'uva', 'achocolat', 'nescau', 'biscoito', 'bolacha',
        'salgadinho', 'chocolate', 'molho', 'extrato', 'milho', 'ervilha', 'sardinha',
        'atum', 'tekitos', 'nuggets', 'hamburg', 'presunto', 'mortadela', 'salsicha',
        'cereal', 'aveia', 'granola', 'gelatina', 'creme de leite', 'leite cond', 'tempero',
        'sal ', 'vinagre', 'lamen', 'miojo', 'goma', 'tapioca', 'farofa', 'adocante', 'adocyl',
        'pastilha', 'bala ', 'wafer',
    )),
]


def _texto(nome):
    texto = unicodedata.normalize('NFKD', (nome or '').lower())
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return ' ' + re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9/ ]+', ' ', texto)).strip() + ' '


def nome_da_categoria(nome):
    """'SH PALM 350ML' -> 'Higiene'; None quando não dá para saber."""
    texto = _texto(nome)
    palavras = texto.split()
    if not palavras:
        return None
    if palavras[0] in PRIMEIRA_PALAVRA:
        return PRIMEIRA_PALAVRA[palavras[0]]
    for categoria, comecos in PALAVRAS:
        if any(f' {comeco}' in texto for comeco in comecos):
            return categoria
    # Vendido por quilo e sem outra pista: hortifrúti, carne ou peixe.
    if ' kg ' in texto or re.search(r' \d+(\.\d+)?kg ', texto):
        return 'Alimentos'
    return None


def categoria_pelo_nome(nome):
    """A Category do catálogo para o nome (criada se ainda não existir)."""
    nome_categoria = nome_da_categoria(nome)
    if nome_categoria is None:
        return None
    categoria, _ = Category.objects.get_or_create(name=nome_categoria)
    return categoria
