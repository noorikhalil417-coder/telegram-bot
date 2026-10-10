FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py models.py remittance_store.py ./
COPY services/ ./services/
COPY utils/ ./utils/
COPY webapp/ ./webapp/

CMD ["python", "bot.py"]
