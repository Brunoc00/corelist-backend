from django.utils.cache import add_never_cache_headers


class ApiSemCacheMiddleware:
    """Marca as respostas de /api/ para nenhum cache guardar.

    O frontend no Amplify repassa /api/ para este servidor pela CDN. Sem este
    cabeçalho, a CDN poderia guardar a resposta de um usuário (as listas dele,
    por exemplo) e entregá-la a outro.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/api/'):
            add_never_cache_headers(response)
        return response
