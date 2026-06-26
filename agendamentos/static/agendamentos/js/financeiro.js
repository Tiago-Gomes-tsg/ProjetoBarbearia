(function () {
    function filtrarCategorias() {
        const tipo = document.getElementById('id_tipo');
        const categoriaSelect = document.getElementById('id_categoria');
        if (!tipo || !categoriaSelect) return;

        let primeiraOpcaoVisivel = null;
        Array.from(categoriaSelect.options).forEach((opcao) => {
            const tipoOpcao = opcao.getAttribute('data-tipo');
            const visivel = tipoOpcao === tipo.value;
            opcao.style.display = visivel ? 'block' : 'none';
            if (visivel && !primeiraOpcaoVisivel) primeiraOpcaoVisivel = opcao.value;
        });

        if (primeiraOpcaoVisivel) categoriaSelect.value = primeiraOpcaoVisivel;
    }

    window.filtrarCategorias = filtrarCategorias;
    document.addEventListener('DOMContentLoaded', filtrarCategorias);
})();
