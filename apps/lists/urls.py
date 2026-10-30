from django.urls import path

from .views import (ListDetailView, ListListCreateView, ListItemListCreateView,
                    ListItemDetailView,
                    ListCompleteView,
                    ListHistoryView,
                    ListSummaryView,
                    ListInsightsView,
                    ListPromotionsView,
                    ProdutoBuscaView,
                    )
from .views_precos import PrecosBuscaView, PrecosDaListaView, PrecosProdutoView
from .views_nfce import (
    NfceConcluirNaListaView,
    NfceImportarView,
    NfceLerNaListaView,
    NfceLerView,
)


urlpatterns = [
    path('', ListListCreateView.as_view(), name='list-list-create'),
    path('<int:pk>/', ListDetailView.as_view(), name='list-detail'),
    path('<int:list_id>/items/', ListItemListCreateView.as_view(), name='list-item-list-create'),
    path('<int:list_id>/items/<int:pk>/', ListItemDetailView.as_view(), name='list-item-detail', ),
    path('<int:pk>/complete/',ListCompleteView.as_view(),name='list-complete',),
    path('history/',ListHistoryView.as_view(),name='list-history',),
    path('summary/',ListSummaryView.as_view(),name='list-summary',),
    path('insights/',ListInsightsView.as_view(),name='list-insights',),
    path('promotions/',ListPromotionsView.as_view(),name='list-promotions',),
    path('produtos/', ProdutoBuscaView.as_view(), name='produto-busca'),
    path('precos/', PrecosBuscaView.as_view(), name='precos-busca'),
    path('precos/produto/<int:pk>/', PrecosProdutoView.as_view(), name='precos-produto'),
    path('<int:pk>/precos/', PrecosDaListaView.as_view(), name='lista-precos'),
    path('nfce/ler/', NfceLerView.as_view(), name='nfce-ler'),
    path('nfce/importar/', NfceImportarView.as_view(), name='nfce-importar'),
    path('<int:pk>/nfce/ler/', NfceLerNaListaView.as_view(), name='lista-nfce-ler'),
    path('<int:pk>/nfce/concluir/', NfceConcluirNaListaView.as_view(), name='lista-nfce-concluir'),

]
