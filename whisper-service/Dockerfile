FROM dustynv/faster-whisper:r36.4.0-cu128-24.04

WORKDIR /app

# Install FastAPI and uvicorn (use standard PyPI as fallback)
RUN pip3 install --no-cache-dir --extra-index-url https://pypi.org/simple fastapi uvicorn python-multipart

# Copy server code
COPY server.py /app/server.py

# Expose port
EXPOSE 9000

# Set environment variables
ENV WHISPER_MODEL=base
ENV WHISPER_DEVICE=cuda
ENV WHISPER_COMPUTE_TYPE=float16
ENV PORT=9000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# Run server
CMD ["python3", "/app/server.py"]
