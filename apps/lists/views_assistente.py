"""POST lists/assistente/ {mensagens: [{papel, texto}], lat?, lng?}
-> {resposta, fonte}: o chat de preços do Início (services/assistente.py)."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .services.assistente import MensagemInvalida, responder
from .services.precos import localizacao


class AssistenteView(APIView):
    permission_classes = [IsAuthenticated]
    # Cada pergunta é uma chamada paga ao Claude: limite por pessoa.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'assistente'

    def post(self, request):
        origem = localizacao(request.data.get('lat'), request.data.get('lng'))
        try:
            return Response(responder(request.data.get('mensagens'), origem, request.user))
        except MensagemInvalida as erro:
            return Response({'detail': str(erro)}, status=status.HTTP_400_BAD_REQUEST)
