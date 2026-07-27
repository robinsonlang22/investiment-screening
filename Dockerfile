FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app.py ./
COPY api ./api
COPY engine ./engine
COPY rules ./rules

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
