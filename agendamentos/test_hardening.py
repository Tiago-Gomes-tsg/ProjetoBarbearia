import json
import logging
from tempfile import TemporaryDirectory
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from agendamentos.models import (
    AceiteTermos,
    Barbeiro,
    Cliente,
    ConfiguracaoBarbearia,
    Corte,
    Empresa,
    MembroEmpresa,
    PlanoMensal,
    AssinaturaCliente,
    TransacaoFinanceira,
)
from agendamentos.observability import SafeJsonFormatter, metrics, tracked_cache
from agendamentos.input_safety import _validate_scalar
from agendamentos.tenancy import empresa_context
from agendamentos.views.dashboard_views import _chave_cache_insights


def criar_owner(empresa, username):
    with empresa_context(empresa):
        usuario = User.objects.create_user(username=username, password='Senha-segura-123')
        MembroEmpresa.objects.create(
            usuario=usuario,
            papel=MembroEmpresa.OWNER,
            ativo=True,
        )
        Barbeiro.objects.create(
            usuario=usuario,
            nome=f'Owner {username}',
            telefone='(11) 99999-0000',
            is_dono=True,
        )
        ConfiguracaoBarbearia.objects.get_or_create(
            empresa=empresa,
            defaults={'nome_barbearia': empresa.nome},
        )
        AceiteTermos.objects.create(
            usuario=usuario,
            versao_termos=settings.TERMS_VERSION,
            versao_privacidade=settings.PRIVACY_POLICY_VERSION,
        )
    return usuario


class InputAndObservabilityTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome='Limites', slug='limites')
        self.usuario = criar_owner(self.empresa, 'owner-limites')
        self.client.force_login(self.usuario)

    def test_texto_e_numero_fora_do_limite_falham_antes_da_view(self):
        texto = self.client.post(
            reverse('gerenciar_cupons'),
            {'acao': 'criar', 'codigo': 'X' * 21},
            HTTP_ACCEPT='application/json',
        )
        numero = self.client.post(
            reverse('metas'),
            {'acao': 'salvar_meta', 'meta_cortes': '1000001'},
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(texto.status_code, 400)
        self.assertEqual(texto.json()['campo'], 'codigo')
        self.assertEqual(numero.status_code, 400)
        self.assertEqual(numero.json()['campo'], 'meta_cortes')
        self.assertTrue(texto.headers['X-Request-ID'])

    def test_decimais_brasileiros_e_coordenadas_sao_aceitos_pelo_validador(self):
        casos_validos = [
            ('porcentagem_comissao', '30'),
            ('porcentagem_comissao', '30.00'),
            ('porcentagem_comissao', '30,00'),
            ('porcentagem_comissao', '100'),
            ('porcentagem_comissao', '100.00'),
            ('porcentagem_comissao', '100,00'),
            ('salario_fixo', '0,00'),
            ('latitude', '-22.906847'),
            ('longitude', '-43.172897'),
        ]

        for campo, valor in casos_validos:
            with self.subTest(campo=campo, valor=valor):
                _validate_scalar(campo, valor)

    def test_formulario_html_invalido_redireciona_com_mensagem(self):
        resposta = self.client.post(
            reverse('gerenciar_cupons'),
            {'acao': 'criar', 'codigo': 'X' * 21},
            HTTP_REFERER=reverse('gerenciar_cupons'),
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta['Location'], reverse('gerenciar_cupons'))

    def test_equipe_aceita_comissao_100_sem_salario_fixo(self):
        resposta = self.client.post(
            reverse('adicionar_membro'),
            {
                'nome': 'Profissional Cem',
                'username': 'profissional-cem',
                'email': 'cem@example.com',
                'telefone': '',
                'senha': 'Senha-segura-123',
                'senha_confirmacao': 'Senha-segura-123',
                'tipo_remuneracao': 'COMISSAO',
                'salario_fixo': '',
                'porcentagem_comissao': '100',
                'aceita_agendamentos_online': 'on',
                'receber_confirmacao_whatsapp': 'on',
                't_segunda': 'on',
                't_terca': 'on',
                't_quarta': 'on',
                't_quinta': 'on',
                't_sexta': 'on',
            },
        )

        self.assertEqual(resposta.status_code, 302)
        with empresa_context(self.empresa):
            barbeiro = Barbeiro.objects.get(usuario__username='profissional-cem')
            self.assertEqual(barbeiro.tipo_remuneracao, 'COMISSAO')
            self.assertEqual(barbeiro.salario_fixo, Decimal('0.00'))
            self.assertEqual(barbeiro.porcentagem_comissao, Decimal('100.00'))

    def test_request_id_unico_e_health_check_detalhado(self):
        negado = self.client.get(reverse('health_check'))
        superuser = User.objects.create_superuser(
            username='superuser-monitoramento',
            password='Senha-superuser-123',
            email='super@example.com',
        )
        self.client.force_login(superuser)
        primeira = self.client.get(reverse('health_check'))
        segunda = self.client.get(reverse('health_check'))
        metricas = self.client.get(reverse('metrics_check'))
        self.client.logout()
        readiness = self.client.get(reverse('readiness_check'))

        self.assertEqual(negado.status_code, 404)
        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(primeira.json()['checks']['database']['status'], 'ok')
        self.assertEqual(primeira.json()['checks']['cache']['status'], 'ok')
        self.assertEqual(metricas.status_code, 200)
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(set(readiness.json()), {'status'})
        self.assertNotEqual(
            primeira.headers['X-Request-ID'],
            segunda.headers['X-Request-ID'],
        )

    def test_json_logger_mascara_segredos_e_dados_pessoais(self):
        record = logging.LogRecord(
            'teste',
            logging.ERROR,
            __file__,
            1,
            'falha token=segredo email pessoa@example.com',
            (),
            None,
        )
        record.event = 'masking_test'
        record.telefone = '11999998888'
        payload = json.loads(SafeJsonFormatter().format(record))
        serializado = json.dumps(payload)

        self.assertNotIn('segredo', serializado)
        self.assertNotIn('pessoa@example.com', serializado)
        self.assertNotIn('11999998888', serializado)
        self.assertEqual(payload['level'], 'error')

    def test_cache_registra_hit_e_miss(self):
        cache.clear()
        antes = metrics.snapshot()
        tracked_cache.get('teste:ausente')
        tracked_cache.set('teste:presente', 'ok', timeout=10)
        tracked_cache.get('teste:presente')
        depois = metrics.snapshot()

        self.assertGreater(depois['cache_misses_total'], antes['cache_misses_total'])
        self.assertGreater(depois['cache_hits_total'], antes['cache_hits_total'])


@override_settings(
    ALLOWED_HOSTS=['.testserver', 'testserver'],
    TENANT_BASE_DOMAIN='testserver',
    TENANT_ALLOW_SINGLE_FALLBACK=False,
    GEMINI_INSIGHTS_PERIOD='daily',
)
class InsightTenantIsolationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.empresa_a = Empresa.objects.create(nome='Empresa A', slug='empresa-a')
        self.empresa_b = Empresa.objects.create(nome='Empresa B', slug='empresa-b')
        self.usuario_a = criar_owner(self.empresa_a, 'owner-insight-a')
        self.usuario_b = criar_owner(self.empresa_b, 'owner-insight-b')
        with empresa_context(self.empresa_a):
            TransacaoFinanceira.objects.create(
                tipo='ENTRADA',
                categoria='OUTRAS_RECEITAS',
                valor=Decimal('10.00'),
                data=date.today(),
            )
        with empresa_context(self.empresa_b):
            TransacaoFinanceira.objects.create(
                tipo='ENTRADA',
                categoria='OUTRAS_RECEITAS',
                valor=Decimal('999.00'),
                data=date.today(),
            )

    @patch('agendamentos.views.dashboard_views._gerar_insight_gemini')
    def test_api_e_cache_de_insight_nao_cruzam_empresas(self, gerar):
        gerar.side_effect = lambda prompt: prompt

        self.client.force_login(self.usuario_a)
        resposta_a = self.client.get(
            reverse('api_insights_gemini'),
            HTTP_HOST='empresa-a.testserver',
        )
        self.client.force_login(self.usuario_b)
        resposta_b = self.client.get(
            reverse('api_insights_gemini'),
            HTTP_HOST='empresa-b.testserver',
        )
        self.client.force_login(self.usuario_a)
        resposta_a_cache = self.client.get(
            reverse('api_insights_gemini'),
            HTTP_HOST='empresa-a.testserver',
        )

        self.assertEqual(resposta_a.status_code, 200)
        self.assertIn('R$ 10,00', resposta_a.json()['insight'])
        self.assertNotIn('R$ 999,00', resposta_a.json()['insight'])
        self.assertIn('R$ 999,00', resposta_b.json()['insight'])
        self.assertEqual(resposta_a.json(), resposta_a_cache.json())
        self.assertEqual(gerar.call_count, 2)

    def test_chave_diaria_muda_no_dia_seguinte_e_por_empresa(self):
        hoje = date.today()
        chave_a_hoje = _chave_cache_insights(self.empresa_a, hoje)
        chave_a_amanha = _chave_cache_insights(self.empresa_a, hoje + timedelta(days=1))
        chave_b_hoje = _chave_cache_insights(self.empresa_b, hoje)

        self.assertNotEqual(chave_a_hoje, chave_a_amanha)
        self.assertNotEqual(chave_a_hoje, chave_b_hoje)


@override_settings(
    ALLOWED_HOSTS=['.testserver', 'testserver'],
    TENANT_BASE_DOMAIN='testserver',
    TENANT_ALLOW_SINGLE_FALLBACK=False,
)
class TenantPlatformLifecycleTests(TestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.addCleanup(self.media_dir.cleanup)
        cache.clear()
        self.superuser = User.objects.create_superuser(
            username='super-plataforma-ciclo',
            password='Senha-superuser-123',
            email='super-ciclo@example.com',
        )
        self.empresa = Empresa.objects.create(nome='Tenant Ciclo', slug='tenant-ciclo')
        self.owner = criar_owner(self.empresa, 'owner-tenant-ciclo')
        with empresa_context(self.empresa):
            self.cliente = Cliente.objects.create(
                nome='Cliente Backup',
                telefone='(11) 98888-7777',
            )
            configuracao = ConfiguracaoBarbearia.objects.get(empresa=self.empresa)
            configuracao.aviso_ativo = True
            configuracao.aviso_titulo = 'Aviso do tenant ciclo'
            configuracao.aviso_mensagem = 'Mensagem isolada para o tenant ciclo.'
            configuracao.aviso_cor = 'vermelho'
            configuracao.save()
            barbeiro = Barbeiro.objects.get(usuario=self.owner)
            barbeiro.foto_perfil.save(
                'perfil-backup.jpg',
                ContentFile(b'conteudo-de-teste-da-foto'),
                save=True,
            )
        self.client.force_login(self.superuser)

    def test_superuser_desativa_e_reativa_tenant(self):
        desativar = self.client.post(
            reverse('alternar_empresa', args=[self.empresa.pk]),
            {'novo_status': 'inativo', 'motivo_inativacao': 'Pagamento atrasado'},
        )
        self.empresa.refresh_from_db()
        publico_bloqueado = self.client.get('/', HTTP_HOST='tenant-ciclo.testserver')

        self.assertEqual(desativar.status_code, 302)
        self.assertFalse(self.empresa.ativo)
        self.assertEqual(self.empresa.motivo_inativacao, 'Pagamento atrasado')
        self.assertEqual(publico_bloqueado.status_code, 404)

        ativar = self.client.post(
            reverse('alternar_empresa', args=[self.empresa.pk]),
            {'novo_status': 'ativo'},
        )
        self.empresa.refresh_from_db()
        publico_liberado = self.client.get('/', HTTP_HOST='tenant-ciclo.testserver')

        self.assertEqual(ativar.status_code, 302)
        self.assertTrue(self.empresa.ativo)
        self.assertEqual(publico_liberado.status_code, 200)

    def test_aviso_publico_usa_cor_e_tenant_corretos(self):
        outra = Empresa.objects.create(nome='Outro Tenant', slug='outro-tenant')
        criar_owner(outra, 'owner-outro-aviso')
        with empresa_context(outra):
            configuracao = ConfiguracaoBarbearia.objects.get(empresa=outra)
            configuracao.aviso_ativo = True
            configuracao.aviso_titulo = 'Aviso azul de outro tenant'
            configuracao.aviso_mensagem = 'Mensagem azul isolada.'
            configuracao.aviso_cor = 'azul'
            configuracao.save()

        vermelho = self.client.get('/', HTTP_HOST='tenant-ciclo.testserver')
        azul = self.client.get('/', HTTP_HOST='outro-tenant.testserver')

        self.assertContains(vermelho, 'aviso-tom-vermelho')
        self.assertContains(vermelho, 'Aviso do tenant ciclo')
        self.assertNotContains(vermelho, 'Aviso azul de outro tenant')
        self.assertContains(azul, 'aviso-tom-azul')
        self.assertContains(azul, 'Aviso azul de outro tenant')
        self.assertNotContains(azul, 'Aviso do tenant ciclo')

    def test_limite_da_mensagem_do_aviso_tambem_existe_no_modelo(self):
        with empresa_context(self.empresa):
            configuracao = ConfiguracaoBarbearia.objects.get(empresa=self.empresa)
            configuracao.aviso_mensagem = 'X' * 501
            with self.assertRaises(ValidationError):
                configuracao.save()

    def test_telefone_e_obrigatorio_somente_para_proprietario(self):
        with empresa_context(self.empresa):
            funcionario_user = User.objects.create_user(
                username='funcionario-sem-contato',
                password='Senha-segura-123',
            )
            funcionario = Barbeiro.objects.create(
                usuario=funcionario_user,
                nome='Funcionário sem contato',
                telefone='',
            )
            dono_user = User.objects.create_user(
                username='dono-sem-contato',
                password='Senha-segura-123',
            )
            with self.assertRaises(ValidationError):
                Barbeiro.objects.create(
                    usuario=dono_user,
                    nome='Dono sem contato',
                    telefone='',
                    is_dono=True,
                )

        self.assertEqual(funcionario.telefone, '')

    def test_exporta_exclui_e_restaura_backup_json(self):
        with empresa_context(self.empresa):
            plano = PlanoMensal.objects.create(
                nome='Plano com assinatura protegida',
                valor_mensal=Decimal('149.90'),
            )
            cliente = Cliente.objects.create(
                nome='Cliente Assinante',
                telefone='(11) 90000-9090',
            )
            AssinaturaCliente.objects.create(
                cliente=cliente,
                plano=plano,
                data_inicio=date.today(),
                data_renovacao=date.today() + timedelta(days=30),
            )

        exportacao = self.client.get(
            reverse('exportar_empresa', args=[self.empresa.pk])
        )
        backup = exportacao.content
        nome_foto = Barbeiro.all_objects.get(usuario=self.owner).foto_perfil.name

        exclusao = self.client.post(
            reverse('excluir_empresa', args=[self.empresa.pk]),
            {
                'confirmar_slug': self.empresa.slug,
                'confirmar_exclusao': 'on',
                'senha_superuser': 'Senha-superuser-123',
            },
        )
        self.assertEqual(exportacao.status_code, 200)
        self.assertIn('attachment;', exportacao.headers['Content-Disposition'])
        self.assertEqual(exclusao.status_code, 302)
        self.assertFalse(Empresa.objects.filter(pk=self.empresa.pk).exists())
        self.assertFalse(User.objects.filter(username='owner-tenant-ciclo').exists())

        upload = SimpleUploadedFile(
            'backup_tenant_ciclo.json',
            backup,
            content_type='application/json',
        )
        restauracao = self.client.post(
            reverse('importar_empresa'),
            {
                'backup_json': upload,
                'senha_superuser': 'Senha-superuser-123',
            },
        )

        self.assertEqual(restauracao.status_code, 302)
        empresa_restaurada = Empresa.objects.get(pk=self.empresa.pk)
        usuario_restaurado = User.objects.get(username='owner-tenant-ciclo')
        self.assertTrue(usuario_restaurado.check_password('Senha-segura-123'))
        with empresa_context(empresa_restaurada):
            self.assertTrue(Cliente.objects.filter(nome='Cliente Backup').exists())
            configuracao = ConfiguracaoBarbearia.objects.get(empresa=empresa_restaurada)
            self.assertEqual(configuracao.aviso_cor, 'vermelho')
            barbeiro = Barbeiro.objects.get(usuario=usuario_restaurado)
            self.assertEqual(barbeiro.foto_perfil.name, nome_foto)
            with barbeiro.foto_perfil.open('rb') as foto:
                self.assertEqual(foto.read(), b'conteudo-de-teste-da-foto')

    def test_confirmacao_whatsapp_usa_contato_do_barbeiro_escolhido(self):
        with empresa_context(self.empresa):
            usuario = User.objects.create_user(
                username='barbeiro-whatsapp',
                password='Senha-segura-123',
            )
            MembroEmpresa.objects.create(usuario=usuario, papel=MembroEmpresa.BARBER)
            barbeiro = Barbeiro.objects.create(
                usuario=usuario,
                nome='Barbeiro WhatsApp',
                telefone='(21) 98888-7777',
            )
            corte = Corte.objects.create(
                barbeiro=barbeiro,
                nome='Corte WhatsApp',
                preco=Decimal('75.00'),
            )

        data_agendada = date.today() + timedelta(days=2)
        while data_agendada.weekday() == 6:
            data_agendada += timedelta(days=1)
        resposta = self.client.post(
            '/',
            {
                'nome': 'Cliente Confirmação',
                'telefone': '(11) 97777-6666',
                'barbeiro': str(barbeiro.pk),
                'corte': str(corte.pk),
                'data': data_agendada.isoformat(),
                'horario': '10:00',
            },
            HTTP_HOST='tenant-ciclo.testserver',
        )

        whatsapp = resposta.context['dados_agendamento']['whatsapp']
        mensagem = parse_qs(urlsplit(whatsapp['url']).query)['text'][0]
        self.assertTrue(resposta.context['sucesso'])
        self.assertTrue(whatsapp['url'].startswith('https://wa.me/5521988887777?text='))
        self.assertIn('Barbeiro: Barbeiro WhatsApp', mensagem)
        self.assertIn('Cliente: Cliente Confirmação', mensagem)
        self.assertIn('Telefone do cliente: (11) 97777-6666', mensagem)
        self.assertIn(f'Data: {data_agendada:%d/%m/%Y}', mensagem)
        self.assertIn('Horário: 10:00', mensagem)
        self.assertIn('Serviço: Corte WhatsApp', mensagem)
        self.assertIn('/cancelar/', mensagem)

    def test_whatsapp_sem_telefone_do_funcionario_cai_somente_para_dono_do_tenant(self):
        outra = Empresa.objects.create(nome='Tenant Isolado', slug='tenant-isolado')
        criar_owner(outra, 'owner-isolado')
        with empresa_context(outra):
            dono_outra = Barbeiro.objects.get(is_dono=True)
            dono_outra.telefone = '(31) 99999-3131'
            dono_outra.save()

        with empresa_context(self.empresa):
            usuario = User.objects.create_user(
                username='barbeiro-sem-telefone',
                password='Senha-segura-123',
            )
            MembroEmpresa.objects.create(usuario=usuario, papel=MembroEmpresa.BARBER)
            barbeiro = Barbeiro.objects.create(
                usuario=usuario,
                nome='Barbeiro Sem Telefone',
                telefone='',
            )
            corte = Corte.objects.create(
                barbeiro=barbeiro,
                nome='Corte com Fallback',
                preco=Decimal('60.00'),
            )

        resposta = self.client.post(
            '/',
            {
                'nome': 'Cliente Fallback',
                'telefone': '(11) 96666-5555',
                'barbeiro': str(barbeiro.pk),
                'corte': str(corte.pk),
                'data': (date.today() + timedelta(days=4)).isoformat(),
                'horario': '11:00',
            },
            HTTP_HOST='tenant-ciclo.testserver',
        )

        whatsapp = resposta.context['dados_agendamento']['whatsapp']
        self.assertTrue(whatsapp['usa_contato_dono'])
        self.assertTrue(whatsapp['url'].startswith('https://wa.me/5511999990000?text='))
        self.assertNotIn('5531999993131', whatsapp['url'])

    def test_profissional_oculto_nao_aparece_nem_aceita_post_forjado(self):
        with empresa_context(self.empresa):
            dono = Barbeiro.objects.get(is_dono=True)
            corte = Corte.objects.create(
                barbeiro=dono,
                nome='Serviço apenas interno',
                preco=Decimal('50.00'),
            )
            dono.aceita_agendamentos_online = False
            dono.save()

        pagina = self.client.get('/', HTTP_HOST='tenant-ciclo.testserver')
        forjado = self.client.post(
            '/',
            {
                'nome': 'Cliente Forjado',
                'telefone': '(11) 95555-4444',
                'barbeiro': str(dono.pk),
                'corte': str(corte.pk),
                'data': (date.today() + timedelta(days=4)).isoformat(),
                'horario': '12:00',
            },
            HTTP_HOST='tenant-ciclo.testserver',
        )
        espera_forjada = self.client.post(
            reverse('entrar_lista_espera'),
            data=json.dumps({
                'nome': 'Cliente Forjado',
                'telefone': '(11) 95555-4444',
                'barbeiro_id': dono.pk,
                'data': (date.today() + timedelta(days=4)).isoformat(),
            }),
            content_type='application/json',
            HTTP_HOST='tenant-ciclo.testserver',
        )

        self.assertNotIn(dono, pagina.context['barbeiros'])
        self.assertFalse(forjado.context['sucesso'])
        self.assertIn('inválido', forjado.context['erro_validacao'])
        self.assertEqual(espera_forjada.json()['status'], 'erro')
