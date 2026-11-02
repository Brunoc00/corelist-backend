"""Esqueci a senha.

1. POST auth/senha/esqueci/ {email}: manda um e-mail com o link
   <SITE_URL>/redefinir-senha?uid=...&token=... (vale PASSWORD_RESET_TIMEOUT).
   A resposta é sempre a mesma, exista a conta ou não, para não revelar
   quais e-mails têm conta.
2. POST auth/senha/redefinir/ {uid, token, nova_senha}: troca a senha. O link
   deixa de valer (o token depende da senha) e as sessões abertas em outros
   aparelhos são encerradas.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

logger = logging.getLogger(__name__)
User = get_user_model()

RESPOSTA_ESQUECI = (
    'Se existir uma conta com esse e-mail, enviamos um link para criar uma '
    'senha nova. Confira também a caixa de spam.'
)
LINK_INVALIDO = 'Este link não vale mais. Peça um novo em "Esqueci minha senha".'


def link_de_redefinicao(usuario):
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    return f'{settings.SITE_URL}/redefinir-senha?uid={uid}&token={token}'


def _mandar_email(usuario):
    horas = settings.PASSWORD_RESET_TIMEOUT // 3600
    nome = usuario.first_name or 'tudo bem'
    send_mail(
        subject='CompraFácil: criar uma senha nova',
        message=(
            f'Olá, {nome}!\n\n'
            'Recebemos um pedido para criar uma senha nova na sua conta do '
            'CompraFácil. Abra o link abaixo para escolher a nova senha:\n\n'
            f'{link_de_redefinicao(usuario)}\n\n'
            f'O link vale por {horas} horas e só pode ser usado uma vez.\n'
            'Se não foi você, ignore este e-mail: sua senha continua a mesma.\n'
        ),
        from_email=None,  # DEFAULT_FROM_EMAIL
        recipient_list=[usuario.email],
    )


class EsqueciSenhaView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'senha'

    def post(self, request):
        email = str(request.data.get('email') or '').strip()
        if not email:
            return Response({'detail': 'Informe o e-mail da conta.'}, status=status.HTTP_400_BAD_REQUEST)

        usuario = User.objects.filter(email__iexact=email, is_active=True).first()
        if usuario is not None:
            try:
                _mandar_email(usuario)
            except Exception:  # SMTP fora do ar, senha de app errada etc.
                logger.exception('Esqueci a senha: falha ao enviar o e-mail.')

        return Response({'detail': RESPOSTA_ESQUECI})


class RedefinirSenhaView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'senha'

    def post(self, request):
        try:
            pk = force_str(urlsafe_base64_decode(str(request.data.get('uid') or '')))
            usuario = User.objects.get(pk=pk, is_active=True)
        except (ValueError, TypeError, OverflowError, User.DoesNotExist):
            usuario = None

        token = str(request.data.get('token') or '')
        if usuario is None or not default_token_generator.check_token(usuario, token):
            return Response({'detail': LINK_INVALIDO}, status=status.HTTP_400_BAD_REQUEST)

        nova = str(request.data.get('nova_senha') or '')
        try:
            validate_password(nova, usuario)
        except ValidationError as erro:
            return Response({'nova_senha': list(erro.messages)}, status=status.HTTP_400_BAD_REQUEST)

        usuario.set_password(nova)
        usuario.save(update_fields=['password'])

        # Quem tinha a senha antiga (ou um aparelho perdido) sai da conta.
        for pendente in OutstandingToken.objects.filter(user=usuario):
            BlacklistedToken.objects.get_or_create(token=pendente)

        return Response({'detail': 'Senha alterada. Entre com a senha nova.'})
