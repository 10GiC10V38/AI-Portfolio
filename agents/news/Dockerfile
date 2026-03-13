FROM python:3.12-slim

WORKDIR /app

COPY agents/news/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY agents/news/main.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
