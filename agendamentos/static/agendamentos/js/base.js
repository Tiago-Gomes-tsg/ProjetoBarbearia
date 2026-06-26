(function () {
    function prefereMovimentoReduzido() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function pulsarTema() {
        if (prefereMovimentoReduzido()) return;
        document.body.classList.remove('is-theme-switching');
        void document.body.offsetWidth;
        document.body.classList.add('is-theme-switching');
        setTimeout(() => document.body.classList.remove('is-theme-switching'), 320);
    }

    function toggleGrupo(id) {
        // O estado da sidebar fica no navegador para manter grupos abertos entre paginas.
        const grupo = document.getElementById(id);
        if (!grupo) return;

        const estaAberto = grupo.classList.contains('aberto');
        grupo.classList.toggle('aberto', !estaAberto);

        try {
            const estados = JSON.parse(localStorage.getItem('sb') || '{}');
            estados[id] = !estaAberto;
            localStorage.setItem('sb', JSON.stringify(estados));
        } catch (e) {}
    }

    function restaurarGrupos() {
        try {
            const estados = JSON.parse(localStorage.getItem('sb') || '{}');
            Object.keys(estados).forEach((id) => {
                if (!estados[id]) return;
                const grupo = document.getElementById(id);
                if (grupo) grupo.classList.add('aberto');
            });
        } catch (e) {}

        document.querySelectorAll('.nav-grupo').forEach((grupo) => {
            if (grupo.querySelector('a.ativo')) grupo.classList.add('aberto');
        });
    }

    function aplicarRotuloTema(botao) {
        const temaAtual = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
        const label = botao.querySelector('[data-theme-toggle-label]');
        if (label) label.textContent = temaAtual === 'light' ? 'Modo escuro' : 'Modo claro';
        botao.setAttribute('aria-label', temaAtual === 'light' ? 'Ativar modo escuro' : 'Ativar modo claro');
        botao.dataset.activeTheme = temaAtual;
    }

    function iniciarTema() {
        document.querySelectorAll('[data-theme-toggle]').forEach((botao) => {
            aplicarRotuloTema(botao);
            botao.addEventListener('click', () => {
                const proximoTema = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
                document.documentElement.dataset.theme = proximoTema;
                try {
                    localStorage.setItem(botao.dataset.themeKey || 'barbearia:painel-theme', proximoTema);
                } catch (e) {}
                aplicarRotuloTema(botao);
                pulsarTema();
            });
        });
    }

    function formatarTelefoneBrasil(digitos) {
        const valor = digitos.slice(0, 11);
        if (valor.length <= 2) return valor.length ? `(${valor}` : '';
        if (valor.length <= 6) return `(${valor.slice(0, 2)}) ${valor.slice(2)}`;
        if (valor.length <= 10) return `(${valor.slice(0, 2)}) ${valor.slice(2, 6)}-${valor.slice(6)}`;
        return `(${valor.slice(0, 2)}) ${valor.slice(2, 7)}-${valor.slice(7)}`;
    }

    function aplicarMascaraTelefone(input) {
        const posicaoOriginal = input.selectionStart || input.value.length;
        const digitosAntesCursor = input.value.slice(0, posicaoOriginal).replace(/\D/g, '').length;
        const formatado = formatarTelefoneBrasil(input.value.replace(/\D/g, ''));
        input.value = formatado;

        let novaPosicao = formatado.length;
        let vistos = 0;
        for (let i = 0; i < formatado.length; i += 1) {
            if (/\d/.test(formatado[i])) vistos += 1;
            if (vistos >= digitosAntesCursor) {
                novaPosicao = i + 1;
                break;
            }
        }
        window.requestAnimationFrame(() => input.setSelectionRange(novaPosicao, novaPosicao));
    }

    function iniciarMascarasTelefone() {
        document.querySelectorAll('input[data-phone-mask]').forEach((input) => {
            if (input.value) aplicarMascaraTelefone(input);
            input.addEventListener('input', () => aplicarMascaraTelefone(input));
        });
    }

    window.toggleGrupo = toggleGrupo;
    document.addEventListener('DOMContentLoaded', () => {
        restaurarGrupos();
        iniciarTema();
        iniciarMascarasTelefone();
    });
})();
