FROM python:3.12-alpine
RUN pip install --no-cache-dir flask requests
COPY app.py /app.py
CMD ["python", "/app.py"]
