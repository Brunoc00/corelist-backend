from rest_framework.permissions import SAFE_METHODS, BasePermission


class CatalogPermission(BasePermission):
    """Catálogo global: todo usuário logado lê e cadastra; só administrador
    (is_staff) edita ou apaga, porque a mudança afeta todos os usuários."""

    message = 'Só um administrador pode editar ou apagar produtos do catálogo.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        if request.method in SAFE_METHODS or request.method == 'POST':
            return True

        return request.user.is_staff
