FROM python:3.12-slim

WORKDIR /app

COPY agents/fundamentals/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY agents/fundamentals/main.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
