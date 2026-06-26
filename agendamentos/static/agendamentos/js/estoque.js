(function () {
    function abrirModalExcluirEstoque(url) {
        document.getElementById('formConfirmarExcluirEstoque').action = url;
        document.getElementById('modalExcluirEstoque').style.display = 'flex';
    }

    function fecharModalExcluirEstoque() {
        document.getElementById('modalExcluirEstoque').style.display = 'none';
    }

    window.abrirModalExcluirEstoque = abrirModalExcluirEstoque;
    window.fecharModalExcluirEstoque = fecharModalExcluirEstoque;
})();
