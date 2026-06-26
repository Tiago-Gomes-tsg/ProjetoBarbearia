from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from decimal import Decimal

from .models import Empresa, TENANT_SLUG_VALIDATOR, validar_telefone_whatsapp


class EmpresaOnboardingForm(forms.Form):
    """Dados mínimos para entregar um tenant utilizável ao proprietário."""

    TIMEZONES = (
        ('America/Sao_Paulo', 'Brasília / São Paulo'),
        ('America/Manaus', 'Manaus'),
        ('America/Cuiaba', 'Cuiabá'),
        ('America/Recife', 'Recife'),
        ('America/Fortaleza', 'Fortaleza'),
        ('America/Belem', 'Belém'),
        ('America/Rio_Branco', 'Rio Branco'),
    )

    nome_empresa = forms.CharField(label='Nome da barbearia', max_length=120)
    slug = forms.CharField(
        label='Subdomínio',
        max_length=63,
        help_text='Ex.: navalha-premium. Será usado em navalha-premium.seudominio.com.',
    )
    slogan = forms.CharField(label='Slogan', max_length=100, required=False)
    timezone = forms.ChoiceField(label='Fuso horário', choices=TIMEZONES)
    cor_destaque = forms.RegexField(
        label='Cor principal',
        regex=r'^#[0-9A-Fa-f]{6}$',
        initial='#c89b3c',
        widget=forms.TextInput(attrs={'type': 'color'}),
    )

    nome_dono = forms.CharField(label='Nome do proprietário', max_length=100)
    telefone_dono = forms.CharField(
        label='Telefone/WhatsApp do proprietário',
        max_length=20,
        validators=[validar_telefone_whatsapp],
        help_text='Obrigatório. Use DDD, por exemplo (11) 99999-9999.',
        widget=forms.TextInput(attrs={'type': 'tel', 'inputmode': 'tel', 'data-phone-mask': '1'}),
    )
    username_dono = forms.CharField(label='Login do proprietário', max_length=150)
    email_dono = forms.EmailField(label='E-mail do proprietário', max_length=254)
    senha_dono = forms.CharField(
        label='Senha inicial',
        max_length=128,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    confirmar_senha = forms.CharField(
        label='Confirmar senha',
        max_length=128,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    hora_inicio = forms.TimeField(
        label='Início do expediente',
        initial='09:00',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    hora_fim = forms.TimeField(
        label='Fim do expediente',
        initial='18:00',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    intervalo_minutos = forms.TypedChoiceField(
        label='Intervalo da agenda',
        choices=((15, '15 minutos'), (20, '20 minutos'), (30, '30 minutos'),
                 (45, '45 minutos'), (60, '60 minutos')),
        coerce=int,
        initial=30,
    )
    trabalha_sabado = forms.BooleanField(label='Atende aos sábados', required=False, initial=True)
    aceita_agendamentos_dono = forms.BooleanField(
        label='Proprietário também atende clientes',
        required=False,
        initial=True,
        help_text='Desmarque se ele apenas administra a equipe.',
    )
    servico_inicial = forms.CharField(label='Serviço inicial', max_length=100, initial='Corte')
    preco_servico = forms.DecimalField(
        label='Preço inicial',
        min_value=Decimal('0.01'),
        max_value=Decimal('9999.99'),
        max_digits=6,
        decimal_places=2,
        initial='50.00',
    )
    senha_superuser = forms.CharField(
        label='Confirme sua senha de superuser',
        max_length=128,
        help_text='Confirmação obrigatória para criar um novo tenant e seu proprietário.',
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )

    def clean_slug(self):
        slug = self.cleaned_data['slug'].strip().lower()
        TENANT_SLUG_VALIDATOR(slug)
        if slug in settings.TENANT_RESERVED_SUBDOMAINS:
            raise ValidationError('Este subdomínio é reservado pela plataforma.')
        if Empresa.objects.filter(slug=slug).exists():
            raise ValidationError('Já existe uma empresa com este subdomínio.')
        return slug

    def clean_username_dono(self):
        username = self.cleaned_data['username_dono'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('Este login já está sendo utilizado.')
        return username

    def clean_email_dono(self):
        email = self.cleaned_data['email_dono'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Este e-mail já está sendo utilizado.')
        return email

    def clean(self):
        cleaned = super().clean()
        senha = cleaned.get('senha_dono')
        confirmar = cleaned.get('confirmar_senha')
        if senha and confirmar and senha != confirmar:
            self.add_error('confirmar_senha', 'As senhas não conferem.')
        elif senha:
            candidato = User(
                username=cleaned.get('username_dono', ''),
                email=cleaned.get('email_dono', ''),
            )
            try:
                validate_password(senha, user=candidato)
            except ValidationError as exc:
                self.add_error('senha_dono', exc)

        inicio = cleaned.get('hora_inicio')
        fim = cleaned.get('hora_fim')
        if inicio and fim and inicio >= fim:
            self.add_error('hora_fim', 'O fim do expediente deve ser posterior ao início.')
        return cleaned


class EmpresaGestaoForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = (
            'plano_contratado',
            'status_pagamento',
            'valor_mensal',
            'proximo_vencimento',
            'observacoes_internas',
        )
        labels = {
            'plano_contratado': 'Plano/contrato',
            'status_pagamento': 'Pagamento',
            'valor_mensal': 'Valor mensal (R$)',
            'proximo_vencimento': 'Próximo vencimento',
            'observacoes_internas': 'Observações internas',
        }
        widgets = {
            'proximo_vencimento': forms.DateInput(attrs={'type': 'date'}),
            'observacoes_internas': forms.Textarea(attrs={'rows': 3, 'maxlength': 500}),
        }
