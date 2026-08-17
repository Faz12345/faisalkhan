FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn
COPY . .
EXPOSE 8000
CMD ["uvicorn","app.backend.server:app","--host","0.0.0.0","--port","8000"]