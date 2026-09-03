FROM python:3.11-slim

WORKDIR /app

# No system build deps needed — all Python packages in requirements.txt
# ship as pre-compiled binary wheels for Python 3.11-slim; no C compiler required.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data dir for audit log persistence
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
