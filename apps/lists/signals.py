"""Usar um produto numa lista (à mão, pela nota fiscal ou repetindo uma
compra) põe o produto nos "Meus produtos" do dono da lista."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.products.models import ProdutoDoUsuario

from .models import ListItem


@receiver(post_save, sender=ListItem)
def produto_vai_para_meus_produtos(sender, instance, created, **kwargs):
    if created:
        ProdutoDoUsuario.objects.get_or_create(
            usuario_id=instance.list.owner_id, produto_id=instance.product_id,
        )
