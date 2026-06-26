from agendamentos.models import ConfiguracaoBarbearia


def configuracao_global(request):
    """
    Disponibiliza o objeto 'cfg' em TODOS os templates do projeto.
    Uso no template:  {{ cfg.nome_barbearia }}, {{ cfg.cor_destaque }}, etc.
    """
    empresa = getattr(request, 'empresa', None)
    cfg = None
    if empresa is not None:
        cfg = ConfiguracaoBarbearia.objects.filter(empresa=empresa).first()
    return {
        'empresa': empresa,
        'cfg': cfg,
        'membro_empresa': getattr(request, 'membro_empresa', None),
    }
