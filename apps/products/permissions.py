from rest_framework.permissions import SAFE_METHODS, BasePermission


class CatalogPermission(BasePermission):
    """Catálogo global: todo usuário logado lê e cadastra; só administrador
    (is_staff) edita, porque a mudança afeta todos os usuários. Apagar: o
    administrador, ou quem cadastrou o produto (as outras condições ficam na
    view)."""

    message = 'Só um administrador pode editar ou apagar produtos do catálogo.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        if request.method in SAFE_METHODS or request.method == 'POST':
            return True

        if request.method == 'DELETE':
            return True

        return request.user.is_staff

    def has_object_permission(self, request, view, obj):
        if request.method != 'DELETE' or request.user.is_staff:
            return True
        return obj.created_by_id == request.user.id
