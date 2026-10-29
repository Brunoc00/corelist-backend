"""GET /api/health/: o servidor está no ar e fala com o banco.

Usado pelo smoke test do CI/CD depois de cada deploy. Não pede login. Mostra
só a situação e a versão (o commit), para o CI/CD conferir que a versão nova
é a que está no ar.
"""

import logging
import os

from django.db import connection
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([])
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:  # noqa: BLE001 - qualquer falha do banco é "erro"
        logger.exception('Health check: banco indisponível')
        return Response({'status': 'erro', 'banco': 'indisponível'}, status=503)

    return Response({
        'status': 'ok',
        'banco': 'ok',
        'versao': os.environ.get('VERSAO', 'local'),
    })
