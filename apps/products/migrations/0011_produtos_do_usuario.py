"""Catálogo por pessoa ("Meus produtos").

Quem já tem conta fica com os produtos que cadastrou ou usou nas próprias
listas (menos os que tirou com a lixeira). Os outros produtos somem da tela
dessa pessoa, mas continuam no banco (listas e preços da cidade de outros)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def preencher(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    ProdutoEscondido = apps.get_model('products', 'ProdutoEscondido')
    ProdutoDoUsuario = apps.get_model('products', 'ProdutoDoUsuario')
    ListItem = apps.get_model('lists', 'ListItem')

    pares = set(
        Product.objects.filter(created_by__isnull=False).values_list('created_by_id', 'id')
    )
    pares |= set(ListItem.objects.values_list('list__owner_id', 'product_id'))
    pares -= set(ProdutoEscondido.objects.values_list('usuario_id', 'produto_id'))

    ProdutoDoUsuario.objects.bulk_create(
        [ProdutoDoUsuario(usuario_id=u, produto_id=p) for u, p in pares],
        batch_size=1000,
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0010_coordenadas_da_loja'),
        ('lists', '0008_quantidade_com_gramas'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProdutoDoUsuario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('produto', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='no_catalogo_de', to='products.product')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('usuario', 'produto'), name='produto_do_usuario_unico')],
            },
        ),
        migrations.RunPython(preencher, migrations.RunPython.noop),
        migrations.DeleteModel(name='ProdutoEscondido'),
    ]
