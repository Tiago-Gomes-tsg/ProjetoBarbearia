from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename


def otimizar_foto_perfil(arquivo, tamanho=(300, 300), qualidade=82):
    if not arquivo:
        return None

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return arquivo

    arquivo.seek(0)
    with Image.open(arquivo) as imagem:
        imagem = ImageOps.exif_transpose(imagem)
        resample = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS')
        imagem = ImageOps.fit(imagem.convert('RGB'), tamanho, method=resample)

        saida = BytesIO()
        imagem.save(saida, format='WEBP', quality=qualidade, method=6)

    nome_base = get_valid_filename(Path(arquivo.name).stem or 'foto-perfil')
    return ContentFile(saida.getvalue(), name=f'{nome_base}.webp')


def sanitizar_imagem_upload(arquivo, tamanho_maximo=(1600, 1600), qualidade=85):
    """Reencoda a imagem, remove EXIF e limita dimensões antes do storage."""

    if not arquivo:
        return None

    from PIL import Image, ImageOps

    arquivo.seek(0)
    with Image.open(arquivo) as imagem:
        imagem = ImageOps.exif_transpose(imagem)
        possui_alpha = imagem.mode in {'RGBA', 'LA'} or 'transparency' in imagem.info
        imagem = imagem.convert('RGBA' if possui_alpha else 'RGB')
        resample = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS')
        imagem.thumbnail(tamanho_maximo, resample=resample)

        saida = BytesIO()
        imagem.save(saida, format='WEBP', quality=qualidade, method=6)

    nome_base = get_valid_filename(Path(arquivo.name).stem or 'imagem')
    return ContentFile(saida.getvalue(), name=f'{nome_base}.webp')
