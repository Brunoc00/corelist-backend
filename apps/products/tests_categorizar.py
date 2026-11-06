from django.core.management import call_command
from django.test import TestCase

from apps.products.models import Category, Product
from apps.products.services.categorizar import nome_da_categoria


class CategoriaPeloNomeTests(TestCase):
    def test_nomes_das_notas(self):
        casos = {
            'SH PALM NATURALS 350ML CERAMIDAS FORCE': 'Higiene',
            'CD SORRISO 70G TRIPLA LIMPEZA': 'Higiene',
            'DT PO SONHO SH 1,6kg LAV': 'Limpeza',
            'SABAO BAR GUAMA 180G': 'Limpeza',
            'PAPEL TOALHA SCALA USO DIARIO': 'Limpeza',
            'REFRIG COCA COLA 2L': 'Bebidas',
            'PAO FRANCES kg TG.': 'Padaria',
            'CEBOLA SC - KG': 'Alimentos',
            'IOG VIG 800G BATIDO': 'Alimentos',
            'Feijão carioca': 'Alimentos',
            'TANGER MURC kg': 'Alimentos',
        }
        for nome, esperado in casos.items():
            self.assertEqual(nome_da_categoria(nome), esperado, nome)

    def test_sem_pista_fica_sem_categoria(self):
        self.assertIsNone(nome_da_categoria('PRATO VASTRAL SOBREM'))
        self.assertIsNone(nome_da_categoria(''))

    def test_comando_preenche_so_quem_nao_tem(self):
        limpeza = Category.objects.create(name='Limpeza')
        sem = Product.objects.create(name='SABAO BAR GUAMA 180G')
        com = Product.objects.create(name='SH PALM 350ML', category=limpeza)  # já tinha
        call_command('categorizar_produtos', verbosity=0)
        sem.refresh_from_db()
        com.refresh_from_db()
        self.assertEqual(sem.category.name, 'Limpeza')
        self.assertEqual(com.category, limpeza)
