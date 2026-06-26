(function () {
    function abrirModal(url) {
        const modal = document.getElementById('modalExcluir');
        document.getElementById('formConfirmarExcluir').action = url;
        if (modal.parentElement !== document.body) {
            document.body.appendChild(modal);
        }
        modal.style.display = 'flex';
        document.body.classList.add('modal-aberto');
    }

    function fecharModal() {
        document.getElementById('modalExcluir').style.display = 'none';
        document.body.classList.remove('modal-aberto');
    }

    window.abrirModal = abrirModal;
    window.fecharModal = fecharModal;
})();
