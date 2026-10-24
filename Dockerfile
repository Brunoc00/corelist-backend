# Imagem do backend (Django + gunicorn) para produção.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuário sem privilégios para rodar o app.
RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000

# A cada subida: aplica migrações, garante categorias/produtos iniciais
# (não duplica) e inicia o gunicorn.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_products && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60 --access-logfile -"]
