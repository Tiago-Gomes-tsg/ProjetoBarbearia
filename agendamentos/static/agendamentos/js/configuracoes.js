(function () {
    const HEX_RE = /^#[0-9A-Fa-f]{6}$/;

    // Cada alvo liga campos reais do formulario ao preview correspondente.
    const targets = {
        panel: {
            hexInput: 'inp-hex',
            picker: 'color-picker',
            contrast: 'indicador-contraste',
            button: 'pvc-btn',
            card: 'pvc-card',
            themeInput: 'tema_painel_padrao',
            preview: '[data-preview="panel"]',
            fields: {
                dark: {
                    fundo: 'tema_escuro_fundo',
                    surface: 'tema_escuro_sidebar',
                    card: 'tema_escuro_card',
                    input: 'tema_escuro_input',
                    borda: 'tema_escuro_borda',
                    texto: 'tema_escuro_texto',
                    textoForte: 'tema_escuro_texto_forte',
                    textoSuave: 'tema_escuro_texto_suave',
                },
                light: {
                    fundo: 'tema_claro_fundo',
                    surface: 'tema_claro_sidebar',
                    card: 'tema_claro_card',
                    input: 'tema_claro_input',
                    borda: 'tema_claro_borda',
                    texto: 'tema_claro_texto',
                    textoForte: 'tema_claro_texto_forte',
                    textoSuave: 'tema_claro_texto_suave',
                },
            },
        },
        public: {
            hexInput: 'inp-hex-public',
            picker: 'color-picker-public',
            contrast: 'indicador-contraste-public',
            button: 'pub-btn',
            card: 'pub-card',
            themeInput: 'tema_agendamento_padrao',
            preview: '[data-preview="public"]',
            fields: {
                dark: {
                    fundo: 'agendamento_tema_escuro_fundo',
                    surface: 'agendamento_tema_escuro_sidebar',
                    card: 'agendamento_tema_escuro_card',
                    input: 'agendamento_tema_escuro_input',
                    borda: 'agendamento_tema_escuro_borda',
                    texto: 'agendamento_tema_escuro_texto',
                    textoForte: 'agendamento_tema_escuro_texto_forte',
                    textoSuave: 'agendamento_tema_escuro_texto_suave',
                },
                light: {
                    fundo: 'agendamento_tema_claro_fundo',
                    surface: 'agendamento_tema_claro_sidebar',
                    card: 'agendamento_tema_claro_card',
                    input: 'agendamento_tema_claro_input',
                    borda: 'agendamento_tema_claro_borda',
                    texto: 'agendamento_tema_claro_texto',
                    textoForte: 'agendamento_tema_claro_texto_forte',
                    textoSuave: 'agendamento_tema_claro_texto_suave',
                },
            },
        },
    };

    function textoPorContraste(hex) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return (0.299 * r + 0.587 * g + 0.114 * b) > 140 ? '#121212' : '#ffffff';
    }

    function mudarTab(id) {
        document.querySelectorAll('.cfg-painel').forEach((painel) => {
            painel.classList.toggle('ativo', painel.id === `tab-${id}`);
        });
        document.querySelectorAll('.cfg-tab').forEach((aba) => {
            aba.classList.toggle('ativo', aba.dataset.tab === id);
        });
    }

    function marcarPaleta(target, hex) {
        document.querySelectorAll(`[data-palette="${target}"] .paleta-cor`).forEach((cor) => {
            cor.classList.toggle('sel', cor.dataset.cor.toLowerCase() === hex.toLowerCase());
        });
    }

    function aplicarCor(target, hex) {
        if (!HEX_RE.test(hex)) return;
        const cfg = targets[target];
        if (!cfg) return;

        const texto = textoPorContraste(hex);
        const botao = document.getElementById(cfg.button);
        const card = document.getElementById(cfg.card);
        const indicador = document.getElementById(cfg.contrast);

        if (botao) {
            botao.style.background = hex;
            botao.style.color = texto;
        }
        if (card) card.style.borderLeftColor = hex;

        const preview = document.querySelector(cfg.preview);
        if (preview) {
            preview.style.setProperty('--preview-destaque', hex);
            preview.style.setProperty('--preview-btn-texto', texto);
        }

        if (indicador) {
            indicador.textContent = texto === '#121212'
                ? 'Texto escuro nos botões.'
                : 'Texto claro nos botões.';
        }

        if (target === 'panel') {
            document.documentElement.style.setProperty('--cor-destaque', hex);
            document.documentElement.style.setProperty('--cor-btn-texto', texto);
        }

        marcarPaleta(target, hex);
    }

    function campoValor(nome, fallback) {
        const input = document.querySelector(`[name="${nome}"]`);
        const valor = input?.value || fallback;
        return HEX_RE.test(valor) ? valor : fallback;
    }

    function temaSelecionado(nome) {
        return document.querySelector(`input[name="${nome}"]:checked`)?.value === 'light' ? 'light' : 'dark';
    }

    function aplicarPreviewTema(target) {
        // O preview usa variaveis CSS para refletir as cores antes de salvar no banco.
        const cfg = targets[target];
        if (!cfg) return;

        const preview = document.querySelector(cfg.preview);
        if (!preview) return;

        const tema = temaSelecionado(cfg.themeInput);
        const campos = cfg.fields[tema];

        preview.dataset.previewTheme = tema;
        preview.style.setProperty('--preview-fundo', campoValor(campos.fundo, tema === 'light' ? '#dfe7e3' : '#111413'));
        preview.style.setProperty('--preview-surface', campoValor(campos.surface, tema === 'light' ? '#e8efeb' : '#171a19'));
        preview.style.setProperty('--preview-card', campoValor(campos.card, tema === 'light' ? '#f1f5f0' : '#1d2220'));
        preview.style.setProperty('--preview-input', campoValor(campos.input, tema === 'light' ? '#d4dfda' : '#26302c'));
        preview.style.setProperty('--preview-borda', campoValor(campos.borda, tema === 'light' ? '#afbeb8' : '#2f3a35'));
        preview.style.setProperty('--preview-texto', campoValor(campos.texto, tema === 'light' ? '#24302b' : '#e5ebe8'));
        preview.style.setProperty('--preview-texto-forte', campoValor(campos.textoForte, tema === 'light' ? '#111a17' : '#ffffff'));
        preview.style.setProperty('--preview-texto-suave', campoValor(campos.textoSuave, tema === 'light' ? '#53645d' : '#abb8b2'));
    }

    function setCor(target, hex) {
        if (!HEX_RE.test(hex)) return;
        const cfg = targets[target];
        const input = document.getElementById(cfg.hexInput);
        const picker = document.getElementById(cfg.picker);
        if (input) input.value = hex;
        if (picker) picker.value = hex;
        aplicarCor(target, hex);
    }

    function atualizarPreviewNome() {
        const nome = document.getElementById('inp-nome')?.value || 'Barbearia';
        const slogan = document.getElementById('inp-slogan')?.value || '';

        ['pv-nome', 'pvc-nome', 'pub-nome'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.textContent = nome;
        });
        ['pv-slogan', 'pvc-slogan', 'pub-slogan'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.textContent = slogan;
        });
    }

    function iniciarTabs() {
        document.querySelectorAll('.cfg-tab').forEach((aba) => {
            aba.addEventListener('click', () => mudarTab(aba.dataset.tab));
        });
    }

    function iniciarCores() {
        Object.keys(targets).forEach((target) => {
            const cfg = targets[target];
            const input = document.getElementById(cfg.hexInput);
            const picker = document.getElementById(cfg.picker);

            if (input?.value && HEX_RE.test(input.value)) aplicarCor(target, input.value);
            aplicarPreviewTema(target);

            input?.addEventListener('input', () => {
                if (HEX_RE.test(input.value)) setCor(target, input.value);
            });
            picker?.addEventListener('input', () => setCor(target, picker.value));

            document.querySelectorAll(`input[name="${cfg.themeInput}"]`).forEach((radio) => {
                radio.addEventListener('change', () => aplicarPreviewTema(target));
            });

            Object.values(cfg.fields).forEach((grupo) => {
                Object.values(grupo).forEach((nome) => {
                    document.querySelector(`[name="${nome}"]`)?.addEventListener('input', () => aplicarPreviewTema(target));
                });
            });
        });

        document.querySelectorAll('.paleta-cor').forEach((botao) => {
            botao.addEventListener('click', () => {
                const palette = botao.closest('[data-palette]');
                if (!palette) return;
                setCor(palette.dataset.palette, botao.dataset.cor);
            });
        });
    }

    function iniciarIdentidade() {
        document.getElementById('inp-nome')?.addEventListener('input', atualizarPreviewNome);
        document.getElementById('inp-slogan')?.addEventListener('input', atualizarPreviewNome);
        atualizarPreviewNome();
    }

    function iniciarTomAviso() {
        const select = document.getElementById('aviso-cor');
        const preview = document.getElementById('aviso-preview-card');
        if (!select) return;

        const atualizar = () => {
            const tom = ['azul', 'vermelho', 'amarelo'].includes(select.value)
                ? select.value
                : 'amarelo';
            [select, preview].filter(Boolean).forEach((elemento) => {
                elemento.classList.remove('tom-azul', 'tom-vermelho', 'tom-amarelo');
                elemento.classList.add(`tom-${tom}`);
            });
        };

        select.addEventListener('change', atualizar);
        atualizar();
    }

    function iniciarViaCep() {
        const cep = document.getElementById('cep');
        if (!cep) return;
        const campos = {
            logradouro: document.getElementById('logradouro'),
            bairro: document.getElementById('bairro'),
            cidade: document.getElementById('cidade'),
            uf: document.getElementById('uf'),
            ddd: document.getElementById('ddd'),
        };
        cep.addEventListener('blur', async () => {
            const digits = cep.value.replace(/\D/g, '');
            if (digits.length !== 8) return;
            try {
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 2500);
                const response = await fetch(`https://viacep.com.br/ws/${digits}/json/`, {
                    signal: controller.signal,
                });
                clearTimeout(timeout);
                if (!response.ok) return;
                const data = await response.json();
                if (data.erro) return;
                if (campos.logradouro && !campos.logradouro.value) campos.logradouro.value = data.logradouro || '';
                if (campos.bairro && !campos.bairro.value) campos.bairro.value = data.bairro || '';
                if (campos.cidade && !campos.cidade.value) campos.cidade.value = data.localidade || '';
                if (campos.uf && !campos.uf.value) campos.uf.value = data.uf || '';
                if (campos.ddd && !campos.ddd.value) campos.ddd.value = data.ddd || '';
            } catch (error) {
                // Falha silenciosa: o endereco pode ser preenchido manualmente.
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        iniciarTabs();
        iniciarCores();
        iniciarIdentidade();
        iniciarTomAviso();
        iniciarViaCep();
    });
})();
